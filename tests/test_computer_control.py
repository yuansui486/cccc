import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from no1.computer_control.lease import ComputerControlLease, LeaseConflict
from no1.computer_control.mcp import (
    MCPOutcomeUnknown,
    MCPToolExecutionError,
    MCPUnavailable,
    WINDOWS_MCP_STDIO_LIMIT_BYTES,
    WindowsMCPSetup,
    WindowsMCPSession,
    normalize_tool_result,
)
from no1.computer_control.isolation import codex_windows_mcp_disable_args
from no1.computer_control.models import WorkflowDefinition, WorkflowTrigger, computer_control_permissions
from no1.computer_control.requests import ComputerRequestStore
from no1.computer_control.recording import RecordingStore
from no1.computer_control.risk import annotate_catalog, workflow_risk
from no1.computer_control.runtime import WorkflowRunner
from no1.computer_control.storage import RevisionConflict, WorkflowStore
from no1.computer_control.triggers import should_confirm_element, validate_trigger
from no1.daemon.messaging.actor_turn_rendering import build_actor_delivery_text
from no1.kernel.group import create_group
from no1.kernel.registry import load_registry
from no1.ports.mcp.toolspecs import MCP_TOOLS
from no1.ports.mcp.server import _MCP_EXTRA_CONTENT_KEY, _attach_computer_artifacts
from no1.ports.mcp import main as mcp_main
from no1.daemon.computer_control_ops import try_handle_computer_control_op


class TestComputerControl(unittest.TestCase):
    @staticmethod
    def _message_workflow() -> dict:
        return {
            "name": "send message",
            "inputs": {"message": {"type": "string", "default": "测试"}},
            "nodes": [
                {"id": "s", "type": "start"},
                {"id": "type", "type": "action", "tool": "Type", "arguments": {"text": "${inputs.message}"}},
                {"id": "wait", "type": "wait", "duration_seconds": 0.5},
                {"id": "e", "type": "end"},
            ],
            "edges": [
                {"source": "s", "target": "type"},
                {"source": "type", "target": "wait"},
                {"source": "wait", "target": "e"},
            ],
        }

    def test_workflow_contract_is_exposed_and_runtime_inputs_override_defaults(self):
        definition = WorkflowDefinition.model_validate(self._message_workflow())
        self.assertEqual(WorkflowRunner._effective_inputs(definition, {}), {"message": "测试"})
        self.assertEqual(WorkflowRunner._effective_inputs(definition, {"message": "override"}), {"message": "override"})
        self.assertIsNone(definition.max_run_seconds)
        self.assertIsNone(definition.nodes[1].timeout_seconds)
        self.assertTrue(definition.auto_verify)

        spec = next(item for item in MCP_TOOLS if item.get("name") == "onecolleague_computer_workflow")
        schema = spec["inputSchema"]
        definition_schema = schema["properties"]["definition"]
        self.assertIn("nodes", definition_schema["properties"])
        self.assertIn("edges", definition_schema["properties"])
        self.assertIn("WorkflowNode", schema["$defs"])
        self.assertIn("duration_seconds", schema["$defs"]["WorkflowNode"]["properties"])
        self.assertIn("top-level edges", definition_schema["description"])

    def test_workflow_validation_rejects_common_agent_aliases(self):
        invalid_next = self._message_workflow()
        invalid_next["nodes"][0]["next"] = "type"
        with self.assertRaisesRegex(ValueError, "top-level edges"):
            WorkflowDefinition.model_validate(invalid_next)

        invalid_duration = self._message_workflow()
        invalid_duration["nodes"][2].pop("duration_seconds")
        invalid_duration["nodes"][2]["duration"] = 1
        with self.assertRaisesRegex(ValueError, "duration_seconds"):
            WorkflowDefinition.model_validate(invalid_duration)

        invalid_template = self._message_workflow()
        invalid_template["nodes"][1]["arguments"]["text"] = "{{inputs.message}}"
        with self.assertRaisesRegex(ValueError, "mustache syntax"):
            WorkflowDefinition.model_validate(invalid_template)

    def test_computer_control_permissions_default_on_and_allow_explicit_opt_out(self):
        self.assertEqual(
            computer_control_permissions({}),
            {
                "allow_high_risk": True,
                "allow_publish": True,
                "allow_trust": True,
                "allow_unattended_triggers": True,
            },
        )
        self.assertFalse(computer_control_permissions({"allow_trust": False})["allow_trust"])

    def test_active_recording_authorization_does_not_expire(self):
        with tempfile.TemporaryDirectory() as td:
            store = WorkflowStore(Path(td))
            group = create_group(load_registry(), title="long recording auth")
            requests = ComputerRequestStore(store)
            requests.append(group.group_id, {
                "request_id": "req-long",
                "actor_id": "foreman",
                "status": "exploring",
                "recording_id": "rec_long",
                "created_ts": time.time() - 7200,
            })
            self.assertEqual(
                requests.require_authorized(group.group_id, "req-long", "foreman")["recording_id"],
                "rec_long",
            )

    def test_workflow_graph_and_secret_constraints(self):
        with self.assertRaises(ValueError):
            WorkflowDefinition.model_validate({"name": "bad", "nodes": [{"id": "s", "type": "start"}, {"id": "a", "type": "action", "tool": "x", "arguments": {"password": "plain"}}, {"id": "e", "type": "end"}], "edges": [{"source": "s", "target": "a"}, {"source": "a", "target": "e"}]})
        with self.assertRaises(ValueError):
            WorkflowDefinition.model_validate({"name": "loop", "nodes": [{"id": "s", "type": "start"}, {"id": "l", "type": "loop"}, {"id": "e", "type": "end"}], "edges": [{"source": "s", "target": "l"}, {"source": "l", "target": "e"}]})

    def test_mcp_tool_failures_are_not_normal_results(self):
        with self.assertRaisesRegex(MCPToolExecutionError, "target missing"):
            normalize_tool_result("Type", {"isError": True, "content": [{"type": "text", "text": "target missing"}]})
        with self.assertRaisesRegex(MCPToolExecutionError, "退出状态码 1"):
            normalize_tool_result(
                "PowerShell",
                {"isError": False, "content": [{"type": "text", "text": "Status Code: 1\n未找到微信窗口"}]},
            )

    def test_codex_session_disables_direct_windows_mcp_without_editing_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config.toml"
            config.write_text(
                '[mcp_servers.windows-mcp]\ncommand = "uvx"\nargs = ["windows-mcp", "serve"]\n'
                '[mcp_servers.other]\ncommand = "other-server"\n',
                encoding="utf-8",
            )
            original = config.read_text(encoding="utf-8")
            args = codex_windows_mcp_disable_args({"CODEX_HOME": str(root)})
            self.assertIn("mcp_servers.windows-mcp.enabled=false", args)
            self.assertNotIn("mcp_servers.other.enabled=false", args)
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_store_revision_trust_and_lease_isolation(self):
        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="desktop")
            definition = WorkflowDefinition.model_validate({"name": "ok", "nodes": [{"id": "s", "type": "start"}, {"id": "e", "type": "end"}], "edges": [{"source": "s", "target": "e"}]})
            store = WorkflowStore(Path(td))
            created = store.create(group.group_id, definition)
            with self.assertRaises(RevisionConflict):
                store.update(group.group_id, created["manifest"]["workflow_id"], definition, expected_revision=99)
            store.trust(group.group_id, created["manifest"]["workflow_id"], 1, fingerprint="fp", permissions=["all_windows_mcp_tools"])
            self.assertEqual(store.get(group.group_id, created["manifest"]["workflow_id"])["manifest"]["trusted"]["1"]["fingerprint"], "fp")
            settings = store.settings(group.group_id, current_fingerprint="fp")
            self.assertTrue(settings["auto_publish_and_trust"])
            finalized = store.auto_finalize(group.group_id, created["manifest"]["workflow_id"], 1, fingerprint="fp")
            self.assertEqual(finalized["manifest"]["published_version"], 1)
            self.assertEqual(store.effective_version(finalized["manifest"], "fp"), 1)
            changed = store.settings(group.group_id, current_fingerprint="new-fp")
            self.assertFalse(changed["reauthorization_required"])
            self.assertEqual(changed["approved_fingerprint"], "new-fp")
            lease = ComputerControlLease(Path(td))
            lease.acquire(group_id=group.group_id, actor_id="a", run_id="r")
            with self.assertRaises(LeaseConflict):
                lease.acquire(group_id="other", actor_id="b", run_id="r2")
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old

    def test_trigger_validation_and_debounce(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            validate_trigger(WorkflowTrigger(id="f", type="file", config={"path": str(root / "in")}), group_root=root)
            with self.assertRaises(ValueError):
                validate_trigger(WorkflowTrigger(id="f", type="file", config={"path": str(Path.home())}), group_root=root)
        fire, hits = should_confirm_element(False, True, 0)
        self.assertFalse(fire)
        fire, hits = should_confirm_element(True, True, hits)
        self.assertTrue(fire)
        self.assertEqual(hits, 2)

    def test_request_authorization_risk_and_optimization_proposal(self):
        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="requests")
            store = WorkflowStore(Path(td))
            requests = ComputerRequestStore(store)
            definition = WorkflowDefinition.model_validate({
                "name": "system task",
                "nodes": [
                    {"id": "s", "type": "start"},
                    {"id": "a", "type": "action", "tool": "PowerShell", "arguments": {"command": "Get-Date"}},
                    {"id": "e", "type": "end"},
                ],
                "edges": [{"source": "s", "target": "a"}, {"source": "a", "target": "e"}],
            })
            created = store.create(group.group_id, definition, created_by="foreman")
            workflow_id = created["manifest"]["workflow_id"]
            requests.append(group.group_id, {"request_id": "req1", "actor_id": "foreman", "mode": "create_and_run", "workflow_id": workflow_id, "status": "accepted", "created_ts": time.time()})
            self.assertEqual(requests.require_authorized(group.group_id, "req1", "foreman")["workflow_id"], workflow_id)
            with self.assertRaises(PermissionError):
                requests.require_authorized(group.group_id, "req1", "another")
            self.assertEqual(workflow_risk(definition.model_dump(mode="json"))["level"], "high")
            self.assertEqual(annotate_catalog([{"name": "Snapshot"}])[0]["risk_level"], "low")

            proposal = store.create_proposal(group.group_id, workflow_id, definition, base_version=1, created_by="foreman", summary="recovery")
            accepted = store.decide_proposal(group.group_id, workflow_id, proposal["proposal_id"], accept=True)
            self.assertEqual(accepted["status"], "accepted")
            self.assertEqual(store.get(group.group_id, workflow_id)["version"], 2)
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old

    def test_structured_chat_contract_is_rendered_separately(self):
        rendered = build_actor_delivery_text(
            text="请整理桌面文件",
            priority="normal",
            reply_required=False,
            event_id="evt1",
            refs=[],
            attachments=[],
            computer_control_request={
                "request_id": "req1",
                "mode": "create_and_run",
                "actor_id": "foreman",
                "allow_high_risk": True,
            },
        )
        self.assertIn("请整理桌面文件", rendered)
        self.assertIn("电脑控制执行契约", rendered)
        self.assertIn("完整电脑权限", rendered)
        self.assertIn("onecolleague_computer_recording", rendered)
        self.assertIn("禁止用裸 windows-mcp.*", rendered)
        self.assertIn("record=true", rendered)
        self.assertIn("不得将任务标记 done", rendered)

    def test_session_sync_and_async_calls_use_the_same_owner_loop(self):
        session = WindowsMCPSession()
        seen = []

        async def catalog_owned():
            seen.append(("catalog", id(asyncio.get_running_loop())))
            return [{"name": "Snapshot"}]

        async def call_owned(name, arguments, *, timeout):
            seen.append((name, id(asyncio.get_running_loop())))
            return {"ok": True}

        session._catalog_owned = catalog_owned
        session._call_tool_owned = call_owned
        self.assertEqual(session.catalog_sync()[0]["name"], "Snapshot")
        asyncio.run(session.call_tool("Snapshot", {}))
        self.assertEqual(seen[0][1], seen[1][1])
        self.assertTrue(WindowsMCPSession._is_transport_error(AttributeError("'NoneType' object has no attribute 'send'")))
        self.assertTrue(WindowsMCPSession._is_transport_error(ValueError("Separator is not found, and chunk exceed the limit")))

    def test_session_only_replays_snapshot_after_transport_restart(self):
        async def exercise(tool):
            session = WindowsMCPSession()
            calls = []

            async def request_owned(method, params, *, timeout):
                calls.append((method, params))
                if len(calls) == 1:
                    raise MCPUnavailable("broken pipe")
                return {"content": [{"type": "text", "text": "ok"}]}

            async def stop_owned():
                return None

            async def start_owned():
                return [{"name": "Snapshot"}, {"name": "Click"}, {"name": "Type"}]

            session._request_owned = request_owned
            session._stop_owned = stop_owned
            session._start_owned = start_owned
            try:
                result = await session.call_tool(tool, {"loc": [10, 20]} if tool != "Snapshot" else {})
                return result, calls, session.transport_restarts, None
            except Exception as exc:
                return None, calls, session.transport_restarts, exc

        result, calls, restarts, error = asyncio.run(exercise("Snapshot"))
        self.assertIsNone(error)
        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(restarts, 1)

        for tool in ("Click", "Type"):
            result, calls, restarts, error = asyncio.run(exercise(tool))
            self.assertIsNone(result)
            self.assertIsInstance(error, MCPOutcomeUnknown)
            self.assertEqual(len(calls), 1, f"{tool} must never be replayed")
            self.assertEqual(restarts, 1)

    def test_session_restart_is_stop_start_only_and_returns_diagnostics(self):
        session = WindowsMCPSession()
        session.configure(Path("C:/fake/windows-mcp.exe"), "test-version")
        session.started_at = 100.0
        calls = []

        async def stop_unlocked():
            calls.append("stop")
            session._process = None
            session._tools = []

        async def start_unlocked():
            calls.append("start")
            process = Mock()
            process.returncode = None
            session._process = process
            session._tools = [{"name": "Snapshot"}]
            session.started_at = 200.0
            return list(session._tools)

        session._stop_unlocked = stop_unlocked
        session._start_unlocked = start_unlocked
        result = session.restart_sync()
        self.assertEqual(calls, ["stop", "start"])
        self.assertEqual(result["previous_started_at"], 100.0)
        self.assertEqual(result["started_at"], 200.0)
        self.assertEqual(result["transport_restarts"], 1)
        self.assertTrue(result["session_running"])
        self.assertEqual(result["tool_count"], 1)

    def test_daemon_session_restart_rejects_active_lease(self):
        with tempfile.TemporaryDirectory() as td:
            lease = ComputerControlLease(Path(td))
            lease.acquire(group_id="group", actor_id="actor", run_id="rec_active", observe_only=False)
            fake = Mock()
            fake.lease = lease
            fake.setup.status.return_value = {"phase": "ready", "in_progress": False}
            with patch("no1.daemon.computer_control_ops.get_services", return_value=fake), patch(
                "no1.daemon.computer_control_ops.ensure_home", return_value=Path(td)
            ):
                response, _ = try_handle_computer_control_op(
                    "computer_control",
                    {"command": "setup", "action": "restart_session", "group_id": "_global"},
                )
            self.assertFalse(response.ok)
            self.assertEqual(response.error.code, "computer_control_busy")
            fake.session.restart_sync.assert_not_called()

    def test_daemon_session_restart_uses_temporary_lease_and_releases_it(self):
        with tempfile.TemporaryDirectory() as td:
            lease = ComputerControlLease(Path(td))
            fake = Mock()
            fake.lease = lease
            fake.setup.status.return_value = {"phase": "ready", "in_progress": False, "fingerprint": "fp"}
            fake.session.restart_sync.return_value = {
                "started_at": 123.0,
                "transport_restarts": 2,
                "session_running": True,
                "tool_count": 19,
                "version": "latest",
            }
            fake.session.catalog_sync.return_value = [{"name": "Snapshot"}]
            fake.setup.refresh_catalog.return_value = {"phase": "ready", "fingerprint": "fp"}
            with patch("no1.daemon.computer_control_ops.get_services", return_value=fake), patch(
                "no1.daemon.computer_control_ops.ensure_home", return_value=Path(td)
            ):
                response, _ = try_handle_computer_control_op(
                    "computer_control",
                    {"command": "setup", "action": "restart_session", "group_id": "_global"},
                )
            self.assertTrue(response.ok)
            self.assertTrue(response.result["result"]["session_running"])
            self.assertEqual(response.result["result"]["fingerprint"], "fp")
            self.assertFalse(lease.status()["active"])
            fake.session.restart_sync.assert_called_once_with(timeout=None)
            fake.setup.refresh_catalog.assert_called_once_with([{"name": "Snapshot"}])

    def test_session_parses_json_rpc_lines_larger_than_the_default_asyncio_limit(self):
        class FakeStdin:
            def write(self, value):
                self.value = value

            async def drain(self):
                return None

        async def exercise():
            session = WindowsMCPSession()
            reader = asyncio.StreamReader(limit=WINDOWS_MCP_STDIO_LIMIT_BYTES)
            large_value = "x" * (128 * 1024)
            reader.feed_data(
                (
                    json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"value": large_value}})
                    + "\n"
                ).encode("utf-8")
            )
            reader.feed_eof()
            process = Mock()
            process.stdin = FakeStdin()
            process.stdout = reader
            process.returncode = None
            session._process = process
            result = await session._request_unlocked("tools/call", {}, timeout=1)
            self.assertEqual(result["value"], large_value)

        asyncio.run(exercise())

    def test_recording_stores_image_artifacts_without_returning_inline_base64(self):
        image_data = base64.b64encode(b"fake-png").decode("ascii")

        class ImageSession:
            transport_restarts = 0

            def catalog_sync(self):
                return [{"name": "Screenshot"}]

            def call_tool_sync(self, name, arguments, *, timeout):
                return {
                    "content": [
                        {"type": "text", "text": "captured"},
                        {"type": "image", "data": image_data, "mimeType": "image/png"},
                    ]
                }

        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="recording-image")
            store = WorkflowStore(Path(td))
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-image",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                    "allow_high_risk": True,
                },
            )
            recordings = RecordingStore(
                Path(td),
                store,
                requests,
                ComputerControlLease(Path(td)),
                ImageSession(),
            )
            recording = recordings.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-image",
                name="capture",
            )
            response = recordings.call(
                group.group_id,
                recording["recording_id"],
                actor_id="foreman",
                tool="Screenshot",
                arguments={},
                record=False,
            )
            encoded = json.dumps(response, ensure_ascii=False)
            self.assertNotIn(image_data, encoded)
            artifact = response["result"]["content"][1]
            self.assertEqual(artifact["type"], "image_artifact")
            artifact_path = store.state_root(group.group_id) / "recordings" / artifact["path"]
            self.assertEqual(artifact_path.read_bytes(), b"fake-png")
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old

    def test_computer_artifacts_are_exposed_as_mcp_images_with_path_containment(self):
        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="mcp-image")
            root = group.path / "state" / "computer-control" / "recordings"
            image_path = root / "artifacts" / "rec_test" / "ev_1.png"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"image-bytes")
            value = {
                "result": {
                    "type": "image_artifact",
                    "path": "artifacts/rec_test/ev_1.png",
                    "mime_type": "image/png",
                }
            }
            enriched = _attach_computer_artifacts(value, group_id=group.group_id, bucket="recordings")
            self.assertEqual(
                enriched[_MCP_EXTRA_CONTENT_KEY][0]["data"],
                base64.b64encode(b"image-bytes").decode("ascii"),
            )
            escaped = _attach_computer_artifacts(
                {
                    "result": {
                        "type": "image_artifact",
                        "path": "..\\outside.png",
                        "mime_type": "image/png",
                    }
                },
                group_id=group.group_id,
                bucket="recordings",
            )
            self.assertNotIn(_MCP_EXTRA_CONTENT_KEY, escaped)
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old

    def test_mcp_main_emits_extra_image_content_without_leaking_internal_marker(self):
        image_data = base64.b64encode(b"image-bytes").decode("ascii")
        with patch.object(
            mcp_main,
            "handle_tool_call",
            return_value={
                "ok": True,
                "result": {"artifact_id": "ev_1"},
                _MCP_EXTRA_CONTENT_KEY: [
                    {"type": "image", "data": image_data, "mimeType": "image/png"}
                ],
            },
        ):
            response = mcp_main.handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "onecolleague_computer_recording", "arguments": {}},
                }
            )
        content = response["result"]["content"]
        self.assertEqual(content[1]["type"], "image")
        self.assertEqual(content[1]["data"], image_data)
        self.assertNotIn(_MCP_EXTRA_CONTENT_KEY, content[0]["text"])

    def test_recording_only_appends_successful_recorded_calls_and_checks_templates(self):
        class FakeSession:
            transport_restarts = 0
            successful_calls = []

            def catalog_sync(self):
                return [{"name": "Snapshot"}, {"name": "Type"}]

            def call_tool_sync(self, name, arguments, *, timeout):
                if arguments.get("fail"):
                    raise RuntimeError("failed")
                self.successful_calls.append(name)
                return {"content": [{"type": "text", "text": arguments.get("text", "ok")}]}

            async def call_tool(self, name, arguments, *, timeout):
                self.successful_calls.append(name)
                return {"content": [{"type": "text", "text": arguments.get("text", "ok")}]}

        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="recording")
            store = WorkflowStore(Path(td))
            requests = ComputerRequestStore(store)
            requests.append(group.group_id, {
                "request_id": "req-record",
                "actor_id": "foreman",
                "mode": "create_and_run",
                "status": "accepted",
                "created_ts": time.time(),
                "allow_high_risk": True,
            })
            session = FakeSession()
            recordings = RecordingStore(Path(td), store, requests, ComputerControlLease(Path(td)), session)
            value = recordings.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-record",
                name="recorded task",
                inputs={"message": {"type": "string", "default": "hello"}},
            )
            recording_id = value["recording_id"]
            suspended = recordings.suspend(group.group_id, recording_id, actor_id="foreman")
            self.assertEqual(suspended["status"], "suspended")
            resumed = recordings.resume(group.group_id, recording_id, actor_id="foreman")
            self.assertTrue(resumed["requires_snapshot_baseline"])
            with self.assertRaisesRegex(RuntimeError, "Snapshot"):
                recordings.call(group.group_id, recording_id, actor_id="foreman", tool="Type", arguments={"text": "early"})
            recordings.call(group.group_id, recording_id, actor_id="foreman", tool="Snapshot", arguments={}, record=False)
            self.assertEqual(len(recordings.get(group.group_id, recording_id)["steps"]), 0)
            with self.assertRaisesRegex(ValueError, "do not resolve"):
                recordings.call(
                    group.group_id,
                    recording_id,
                    actor_id="foreman",
                    tool="Type",
                    arguments={"text": "different"},
                    workflow_arguments={"text": "${inputs.message}"},
                )
            with self.assertRaisesRegex(RuntimeError, "failed"):
                recordings.call(group.group_id, recording_id, actor_id="foreman", tool="Type", arguments={"fail": True})
            recordings.call(
                group.group_id,
                recording_id,
                actor_id="foreman",
                tool="Type",
                arguments={"text": "hello"},
                workflow_arguments={"text": "${inputs.message}"},
            )
            committed = recordings.commit(group.group_id, recording_id, actor_id="foreman")
            self.assertEqual(len(committed["recording"]["steps"]), 1)
            self.assertEqual(committed["workflow"]["definition"]["nodes"][1]["arguments"]["text"], "${inputs.message}")
            runner = WorkflowRunner(Path(td), store, ComputerControlLease(Path(td)), session)
            run = runner.start_sync(
                group.group_id,
                committed["workflow"]["manifest"]["workflow_id"],
                actor_id="foreman",
                version=1,
                inputs={},
                authorization=requests.get(group.group_id, "req-record"),
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                run = runner.get(group.group_id, run["run_id"])
                if run["status"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(run["status"], "published")
            self.assertEqual(session.successful_calls.count("Type"), 2)
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old

    def test_replay_awaits_verification_then_auto_publishes_and_trusts(self):
        class ReplaySession:
            async def call_tool(self, name, arguments, *, timeout):
                return {"sent": arguments["text"]}

        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="verify")
            store = WorkflowStore(Path(td))
            definition = WorkflowDefinition.model_validate({
                "name": "send",
                "inputs": {"message": "hello"},
                "auto_verify": False,
                "nodes": [
                    {"id": "s", "type": "start"},
                    {
                        "id": "a",
                        "type": "action",
                        "tool": "Type",
                        "arguments": {"text": "${inputs.message}"},
                        "success_condition": {"source": "result", "path": "sent", "operator": "equals", "expected": "hello"},
                    },
                    {"id": "e", "type": "end"},
                ],
                "edges": [{"source": "s", "target": "a"}, {"source": "a", "target": "e"}],
            })
            created = store.create(group.group_id, definition)
            runner = WorkflowRunner(Path(td), store, ComputerControlLease(Path(td)), ReplaySession())
            run = runner.start_sync(
                group.group_id,
                created["manifest"]["workflow_id"],
                actor_id="foreman",
                version=1,
                inputs={},
                authorization={"request_id": "req", "allow_publish": True, "allow_trust": True, "allow_unattended_triggers": True},
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                run = runner.get(group.group_id, run["run_id"])
                if run["status"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(run["status"], "awaiting_verification")
            verified = runner.verify(
                group.group_id,
                run["run_id"],
                actor_id="foreman",
                passed=True,
                summary="message visible",
                evidence_ids=["ev_final"],
                fingerprint="fp",
            )
            self.assertEqual(verified["status"], "published")
            manifest = store.get(group.group_id, created["manifest"]["workflow_id"])["manifest"]
            self.assertEqual(manifest["published_version"], 1)
            self.assertEqual(manifest["trusted"]["1"]["fingerprint"], "fp")
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old

    def test_transport_failure_does_not_enter_adaptive_recovery(self):
        from no1.computer_control.mcp import MCPUnavailable

        class BrokenSession:
            transport_restarts = 1

            async def call_tool(self, name, arguments, *, timeout):
                raise MCPUnavailable("transport lost")

        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="transport")
            store = WorkflowStore(Path(td))
            definition = WorkflowDefinition.model_validate({
                "name": "broken",
                "nodes": [
                    {"id": "s", "type": "start"},
                    {"id": "a", "type": "action", "tool": "Type", "arguments": {"text": "x"}, "adaptive": True},
                    {"id": "e", "type": "end"},
                ],
                "edges": [{"source": "s", "target": "a"}, {"source": "a", "target": "e"}],
            })
            created = store.create(group.group_id, definition)
            runner = WorkflowRunner(Path(td), store, ComputerControlLease(Path(td)), BrokenSession())
            runner._wait_for_recovery = AsyncMock()
            run = runner.start_sync(group.group_id, created["manifest"]["workflow_id"], actor_id="a", version=1, inputs={})
            deadline = time.time() + 5
            while time.time() < deadline:
                run = runner.get(group.group_id, run["run_id"])
                if run["status"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(run["status"], "failed")
            runner._wait_for_recovery.assert_not_awaited()
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old

    def test_tool_error_result_fails_replay_and_cannot_be_verified(self):
        class ToolErrorSession:
            async def call_tool(self, name, arguments, *, timeout):
                return {"isError": True, "content": [{"type": "text", "text": "Either loc or label must be provided."}]}

        old = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            group = create_group(load_registry(), title="tool-error")
            store = WorkflowStore(Path(td))
            definition = WorkflowDefinition.model_validate({
                "name": "invalid target",
                "nodes": [
                    {"id": "s", "type": "start"},
                    {"id": "a", "type": "action", "tool": "Type", "arguments": {"text": "x"}, "adaptive": False},
                    {"id": "e", "type": "end"},
                ],
                "edges": [{"source": "s", "target": "a"}, {"source": "a", "target": "e"}],
            })
            created = store.create(group.group_id, definition)
            runner = WorkflowRunner(Path(td), store, ComputerControlLease(Path(td)), ToolErrorSession())
            run = runner.start_sync(group.group_id, created["manifest"]["workflow_id"], actor_id="a", version=1, inputs={})
            deadline = time.time() + 5
            while time.time() < deadline:
                run = runner.get(group.group_id, run["run_id"])
                if run["status"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["events"][-1]["status"], "failed")
            self.assertIn("loc or label", run["events"][-1]["error"]["message"])

            run.update({"status": "awaiting_verification", "metrics": {"replay_success": True}, "error": None})
            run["events"][-1].update({"status": "completed", "result": {"isError": True}})
            runner._write(group.group_id, run)
            with self.assertRaisesRegex(ValueError, "失败步骤"):
                runner.verify(group.group_id, run["run_id"], actor_id="a", passed=True, summary="", evidence_ids=[], fingerprint="fp")
        if old is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old


class _SetupSession:
    def __init__(self):
        self.running = False
        self.logs = []
        self._process = None
        self.configured = None

    def configure(self, executable, version):
        self.configured = (executable, version)

    async def stop(self):
        self.running = False

    async def start(self):
        self.running = True
        return [{"name": "Snapshot", "inputSchema": {"type": "object"}}]


class TestWindowsMCPSetup(unittest.IsolatedAsyncioTestCase):
    def test_command_search_path_refreshes_machine_and_user_registry_paths(self):
        machine_root = str(Path(tempfile.gettempdir()) / "machine-python")
        user_root = str(Path(tempfile.gettempdir()) / "user-python")
        process_root = str(Path(tempfile.gettempdir()) / "stale-process-path")
        machine_hive = object()
        user_hive = object()

        class RegistryKey:
            def __init__(self, hive):
                self.hive = hive

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        registry = Mock(HKEY_LOCAL_MACHINE=machine_hive, HKEY_CURRENT_USER=user_hive)
        registry.OpenKey.side_effect = lambda hive, _name: RegistryKey(hive)
        registry.QueryValueEx.side_effect = lambda key, _name: (
            (machine_root if key.hive is machine_hive else user_root),
            1,
        )
        with patch("no1.computer_control.mcp.sys.platform", "win32"), patch.dict(
            "sys.modules", {"winreg": registry}
        ), patch.dict("no1.computer_control.mcp.os.environ", {"PATH": process_root}, clear=False):
            result = WindowsMCPSetup._command_search_path().split(os.pathsep)

        self.assertEqual(result[:3], [machine_root, user_root, process_root])

    def test_python_commands_skip_frozen_host_and_prefer_real_python_launcher(self):
        def which(name, *, path=None):
            self.assertEqual(path, r"C:\FreshPath")
            return {
                "py": r"C:\Python\py.exe",
                "python": r"C:\Python\python.exe",
            }.get(name)

        with patch("no1.computer_control.mcp.sys.executable", r"C:\OneColleague\onecolleague.exe"), patch(
            "no1.computer_control.mcp.shutil.which", side_effect=which
        ), patch(
            "no1.computer_control.mcp.WindowsMCPSetup._command_search_path", return_value=r"C:\FreshPath"
        ):
            commands = WindowsMCPSetup._python_commands()

        self.assertEqual(commands[:2], [[r"C:\Python\py.exe", "-3"], [r"C:\Python\python.exe"]])
        self.assertNotIn([r"C:\OneColleague\onecolleague.exe"], commands)

    def test_python_commands_find_standard_windows_install_outside_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            python = root / "Programs" / "Python" / "Python313" / "python.exe"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
            environment = {
                "LOCALAPPDATA": str(root),
                "WINDIR": str(root / "Windows"),
                "ProgramFiles": "",
                "ProgramFiles(x86)": "",
            }
            with patch("no1.computer_control.mcp.sys.platform", "win32"), patch(
                "no1.computer_control.mcp.sys.executable", str(root / "onecolleague.exe")
            ), patch("no1.computer_control.mcp.shutil.which", return_value=None), patch.dict(
                "no1.computer_control.mcp.os.environ", environment, clear=False
            ):
                commands = WindowsMCPSetup._python_commands()

            self.assertIn([str(python)], commands)
            self.assertNotIn([str(root / "onecolleague.exe")], commands)

    def test_python_commands_keep_real_python_after_stale_windows_alias(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            alias_root = root / "WindowsApps"
            python_root = root / "Python313"
            alias = alias_root / "python.exe"
            python = python_root / "python.exe"
            alias_root.mkdir()
            python_root.mkdir()
            alias.write_bytes(b"")
            python.write_bytes(b"")
            search_path = os.pathsep.join((str(alias_root), str(python_root)))

            with patch("no1.computer_control.mcp.sys.executable", str(root / "onecolleague.exe")), patch(
                "no1.computer_control.mcp.shutil.which", return_value=str(alias)
            ), patch(
                "no1.computer_control.mcp.WindowsMCPSetup._command_search_path", return_value=search_path
            ):
                commands = WindowsMCPSetup._python_commands()

            self.assertIn([str(alias)], commands)
            self.assertIn([str(python)], commands)

    def test_find_uv_ignores_missing_user_base_in_frozen_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            uv_name = "uv.exe" if os.name == "nt" else "uv"
            uv_path = root / "Scripts" / uv_name
            uv_path.parent.mkdir(parents=True, exist_ok=True)
            uv_path.write_bytes(b"")
            setup = WindowsMCPSetup(root, _SetupSession(), WorkflowStore(root))
            setup._python_commands = Mock(return_value=[[str(root / "python.exe")]])

            with patch("no1.computer_control.mcp.site.USER_BASE", None), patch(
                "no1.computer_control.mcp.shutil.which", return_value=None
            ):
                result = setup._find_uv()

            self.assertEqual(result, uv_path)

    async def test_missing_python_commands_returns_actionable_uv_error(self):
        with tempfile.TemporaryDirectory() as td:
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            setup._uv_candidates = Mock(return_value=[])
            setup._python_commands = Mock(return_value=[])

            with self.assertRaisesRegex(MCPUnavailable, "未找到可用的 Python 命令"):
                await setup._ensure_uv()

            self.assertEqual(setup.status().get("python_candidates"), [])

    async def test_python_probe_discovers_system_and_user_script_directories(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            scripts = root / "Python313" / "Scripts"
            user_scripts = root / "Roaming" / "Python" / "Python313" / "Scripts"
            setup = WindowsMCPSetup(root, _SetupSession(), WorkflowStore(root))
            setup._run_command = AsyncMock(
                return_value="launcher noise\n"
                + json.dumps(
                    {
                        "executable": str(root / "Python313" / "python.exe"),
                        "script_dirs": [str(scripts), str(user_scripts)],
                    }
                )
            )

            result = await setup._python_script_dirs(["py.exe", "-3"])

            self.assertEqual(result, [scripts, user_scripts])
            self.assertEqual(setup._run_command.await_args.args[0][:3], ["py.exe", "-3", "-c"])

    def test_python_reported_script_directory_precedes_stale_uv_on_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stale = root / "old" / "uv.exe"
            installed = root / "Python313" / "Scripts" / "uv.exe"
            stale.parent.mkdir(parents=True)
            installed.parent.mkdir(parents=True)
            stale.write_bytes(b"")
            installed.write_bytes(b"")
            setup = WindowsMCPSetup(root, _SetupSession(), WorkflowStore(root))
            setup._python_commands = Mock(return_value=[])

            with patch("no1.computer_control.mcp.shutil.which", return_value=str(stale)):
                candidates = setup._uv_candidates(extra_roots=[installed.parent])

            self.assertEqual(candidates[:2], [installed, stale])

    async def test_windows_9009_command_failure_has_actionable_error(self):
        process = Mock(returncode=9009)
        process.communicate = AsyncMock(return_value=(b"", b""))
        with tempfile.TemporaryDirectory() as td:
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            with patch("no1.computer_control.mcp.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                with self.assertRaisesRegex(MCPUnavailable, "Windows.*9009"):
                    await setup._run_command(["python.EXE", "-m", "pip"], timeout=1)

    async def test_missing_executable_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as td:
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            with patch(
                "no1.computer_control.mcp.asyncio.create_subprocess_exec",
                AsyncMock(side_effect=FileNotFoundError(2, "missing")),
            ):
                with self.assertRaisesRegex(MCPUnavailable, "python.exe.*无法启动"):
                    await setup._run_command(["python.exe", "-m", "pip"], timeout=1)

    async def test_latest_package_is_installed_without_a_version_constraint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            session = _SetupSession()
            setup = WindowsMCPSetup(root, session, WorkflowStore(root))
            executable = setup._windows_mcp_executable()
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_bytes(b"")
            metadata = setup.tool_dir / "windows-mcp" / "Lib" / "site-packages" / "windows_mcp-1.2.3.dist-info" / "METADATA"
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text("Name: windows-mcp\nVersion: 1.2.3\n", encoding="utf-8")
            setup._ensure_uv = AsyncMock(return_value=Path("uv.exe"))
            setup._run_command = AsyncMock(return_value="")

            with patch("no1.computer_control.mcp.os.name", "nt"):
                await setup.ensure(force=True)
                result = await setup.wait()

            self.assertEqual(result["phase"], "ready")
            self.assertEqual(result["version"], "1.2.3")
            command = setup._run_command.await_args.args[0]
            self.assertIn("windows-mcp", command)
            self.assertFalse(any("windows-mcp==" in item for item in command))
            self.assertEqual(session.configured, (executable, "1.2.3"))

    async def test_missing_uv_is_installed_with_pip_and_found_without_path_restart(self):
        with tempfile.TemporaryDirectory() as td:
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            installed = Path(td) / "Scripts" / "uv.exe"
            setup._uv_candidates = Mock(side_effect=[[], [installed]])
            setup._python_commands = Mock(return_value=[["python"]])
            setup._python_script_dirs = AsyncMock(return_value=[installed.parent])
            setup._run_command = AsyncMock(side_effect=[RuntimeError("user site disabled"), "", "uv 0.8.0"])

            result = await setup._ensure_uv()

            self.assertEqual(result, installed)
            calls = [call.args[0] for call in setup._run_command.await_args_list]
            self.assertEqual(calls[0], ["python", "-m", "pip", "install", "--user", "uv"])
            self.assertEqual(calls[1], ["python", "-m", "pip", "install", "uv"])

    async def test_broken_uv_on_path_falls_back_to_pip_installed_uv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            broken = root / "old" / "uv.exe"
            installed = root / "Python313" / "Scripts" / "uv.exe"
            setup = WindowsMCPSetup(root, _SetupSession(), WorkflowStore(root))
            setup._uv_candidates = Mock(side_effect=[[broken], [installed]])
            setup._python_commands = Mock(return_value=[["python"]])
            setup._python_script_dirs = AsyncMock(return_value=[installed.parent])
            setup._run_command = AsyncMock(side_effect=[RuntimeError("broken uv"), "", "uv 0.8.0"])

            result = await setup._ensure_uv()

            self.assertEqual(result, installed)
            calls = [call.args[0] for call in setup._run_command.await_args_list]
            self.assertEqual(calls[0], [str(broken), "--version"])
            self.assertEqual(calls[1], ["python", "-m", "pip", "install", "--user", "uv"])
            self.assertEqual(calls[2], [str(installed), "--version"])


if __name__ == "__main__":
    unittest.main()
