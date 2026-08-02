import asyncio
import base64
import inspect
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from no1.computer_control.lease import ComputerControlLease, LeaseConflict
from no1.computer_control.derived_authority import DerivedAuthorityStore
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
from no1.computer_control.services import (
    ComputerControlServices,
    issue_daemon_computer_control_owner,
    start_daemon_services,
)
from no1.computer_control.storage import RevisionConflict, WorkflowStore
from no1.computer_control.triggers import should_confirm_element, validate_trigger
from no1.daemon.messaging.actor_turn_rendering import build_actor_delivery_text
from no1.kernel.group import create_group
from no1.kernel.registry import load_registry
from no1.ports.mcp.toolspecs import MCP_TOOLS
from no1.ports.mcp.server import (
    _MCP_EXTRA_CONTENT_KEY,
    _attach_computer_artifacts,
    _authorize_local_computer_control_tool_call,
    _handle_onecolleague_namespace,
    handle_tool_call,
    list_tools_for_caller,
)
from no1.kernel.capabilities import CORE_BASIC_TOOLS, WEB_MODEL_CORE_TOOLS
from no1.ports.mcp import main as mcp_main
from no1.daemon.computer_control_ops import try_handle_computer_control_op
from no1.util.file_lock import acquire_lockfile, release_lockfile
from no1.ports.web.routes.computer_control import _require_local_computer_control_admin


def _canonical_home_env(home: str | Path, **extra: str):
    canonical_home = str(Path(home).expanduser().resolve())
    return patch.dict(
        os.environ,
        {"ONECOLLEAGUE_HOME": canonical_home, "CCCC_HOME": canonical_home, **extra},
        clear=False,
    )


class TestCanonicalHomeFixture(unittest.TestCase):
    def test_canonical_home_env_restores_both_variables_after_exception(self) -> None:
        from no1.paths import onecolleague_home

        sentinels = {"ONECOLLEAGUE_HOME": "outer-one", "CCCC_HOME": "outer-cccc"}
        with patch.dict(os.environ, sentinels, clear=False):
            with tempfile.TemporaryDirectory() as td:
                canonical_home = Path(td).resolve()
                with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                    with _canonical_home_env(canonical_home):
                        self.assertEqual(os.environ["ONECOLLEAGUE_HOME"], str(canonical_home))
                        self.assertEqual(os.environ["CCCC_HOME"], str(canonical_home))
                        self.assertEqual(onecolleague_home(), canonical_home)
                        raise RuntimeError("fixture failure")
                self.assertEqual(os.environ["ONECOLLEAGUE_HOME"], "outer-one")
                self.assertEqual(os.environ["CCCC_HOME"], "outer-cccc")
            self.assertFalse(canonical_home.exists())


class TestComputerControlServiceConstruction(unittest.TestCase):
    def test_runner_receives_the_service_run_authority_store(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            services = ComputerControlServices(Path(td))
            self.assertIs(services.runner.run_authorities, services.run_authorities)


class TestComputerControlRunSurfaceDispatch(unittest.TestCase):
    @staticmethod
    def _service() -> Mock:
        service = Mock()
        service.runner.get.return_value = {
            "origin": "legacy_internal",
            "actor_id": "actor",
            "authorization": {},
        }
        service.runner.cancel_sync.return_value = {"status": "cancelled"}
        service.runner.recovery_context.return_value = {"status": "recovering"}
        service.runner.verify.return_value = {
            "status": "verified",
            "authorization": {},
        }
        service.runner.submit_recovery.return_value = {"accepted": True}
        service.runner.decide_approval.return_value = {"approved": True}
        service.setup.status.return_value = {"fingerprint": "fp"}
        return service

    def test_local_web_legacy_run_actions_keep_the_legacy_state_machine(self) -> None:
        service = self._service()
        actions = ("status", "cancel", "recovery", "verify", "recover", "approve")
        with patch("no1.daemon.computer_control_ops.get_services", return_value=service):
            for action in actions:
                with self.subTest(action=action):
                    response, _ = try_handle_computer_control_op(
                        "computer_control",
                        {
                            "command": "run",
                            "action": action,
                            "group_id": "g",
                            "actor_id": "actor",
                            "run_id": "legacy-run",
                            "node_id": "approval",
                            "caller_surface": "local_web",
                        },
                    )
                    self.assertTrue(response.ok, response.error)
        service.runner.cancel_sync.assert_called_once()
        service.runner.recovery_context.assert_called_once()
        service.runner.verify.assert_called_once()
        service.runner.submit_recovery.assert_called_once()
        service.runner.decide_approval.assert_called_once()
        service.runner.cancel_manual_sync.assert_not_called()
        service.runner.recovery_context_manual.assert_not_called()
        service.runner.verify_manual.assert_not_called()
        service.runner.submit_recovery_manual.assert_not_called()
        service.runner.decide_approval_manual.assert_not_called()

    def test_local_web_manual_run_is_rejected_before_any_mutator(self) -> None:
        service = self._service()
        service.runner.get.side_effect = PermissionError(
            "manual actor run requires operation authority"
        )
        actions = ("status", "cancel", "recovery", "verify", "recover", "approve")
        with patch("no1.daemon.computer_control_ops.get_services", return_value=service):
            for action in actions:
                with self.subTest(action=action):
                    response, _ = try_handle_computer_control_op(
                        "computer_control",
                        {
                            "command": "run",
                            "action": action,
                            "group_id": "g",
                            "actor_id": "actor",
                            "run_id": "manual-run",
                            "node_id": "approval",
                            "caller_surface": "local_web",
                        },
                    )
                    self.assertFalse(response.ok)
                    self.assertEqual(response.error.code, "permission_denied")
        for method in (
            service.runner.cancel_sync,
            service.runner.recovery_context,
            service.runner.verify,
            service.runner.submit_recovery,
            service.runner.decide_approval,
            service.runner.cancel_manual_sync,
            service.runner.recovery_context_manual,
            service.runner.verify_manual,
            service.runner.submit_recovery_manual,
            service.runner.decide_approval_manual,
        ):
            method.assert_not_called()


class TestComputerControl(unittest.TestCase):
    def _authorize_legacy_runner(self, home: Path, runner: WorkflowRunner) -> None:
        lock = acquire_lockfile(
            home / "daemon" / "onecolleagued.lock",
            blocking=False,
        )
        self.addCleanup(release_lockfile, lock)
        owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
        self.addCleanup(lambda retained_owner=owner: None)
        service = ComputerControlServices(home, role="daemon")
        service.runner = runner
        self.addCleanup(service.stop_daemon)
        with patch.object(
            service.recordings,
            "_recover_after_restart",
        ), patch.object(
            runner,
            "_recover_manual_runs_after_restart",
        ), patch.object(
            service.recordings,
            "_start_watchdog",
        ), patch.object(service.scheduler, "start_daemon"):
            service.start_daemon(owner)

    @staticmethod
    def _recover_recordings(home: Path, recordings: RecordingStore) -> None:
        lock = acquire_lockfile(home / "daemon" / "onecolleagued.lock", blocking=False)
        try:
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            recordings._recover_after_restart(owner)
        finally:
            release_lockfile(lock)

    @staticmethod
    def _recording_security(
        home: Path,
        requests: ComputerRequestStore,
        group_id: str,
        request_id: str,
        actor_id: str,
    ):
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            get_actor_turn_generation,
            get_current_turn_grant,
            get_daemon_turn_issuer_epoch,
            invalidate_turn_grant,
            turn_delivery_grant_receipt,
            validate_turn_grant_receipt,
        )
        from no1.kernel.group import load_group
        from no1.kernel.ledger import append_event

        group = load_group(group_id)
        if group is None:
            raise AssertionError("recording security fixture requires a persisted group")
        request = requests.get(group_id, request_id) or {}
        provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        event_request = {
            key: value
            for key, value in request.items()
            if key
            in {
                "request_id",
                "actor_id",
                "mode",
                "workflow_id",
                "allow_high_risk",
                "allow_publish",
                "allow_trust",
                "allow_unattended_triggers",
                "allow_workflow_edit",
            }
        }
        event = append_event(
            group.ledger_path,
            kind="chat.message",
            group_id=group_id,
            scope_key="",
            by="user",
            data=ChatMessageData(
                text="record",
                to=[actor_id],
                computer_control_request=event_request,
                turn_provenance=provenance,
            ).model_dump(),
        )
        requests.update(
            group_id,
            request_id,
            event_id=str(event["id"]),
            local_request_id=str(provenance.local_request_id),
        )
        if get_current_turn_grant(group, actor_id) is not None:
            invalidate_turn_grant(group, actor_id, reason="test_next_root")
        attempt = begin_turn_delivery_attempt(
            group,
            actor_id=actor_id,
            event_ids=[str(event["id"])],
            binding={"transport": "test"},
        )
        receipt = turn_delivery_grant_receipt(attempt)
        finalize_turn_delivery_attempt(group, actor_id=actor_id, attempt=attempt)
        root = validate_turn_grant_receipt(group, actor_id, turn_grant_receipt=receipt)
        if root is None:
            raise AssertionError("recording security fixture failed to validate root")
        start_claim = requests.activate_recording_start_for_turn_claim(
            group_id,
            request_id,
            actor_id,
            claim=root,
        )
        authorities = DerivedAuthorityStore(
            home,
            issuer_epoch_provider=get_daemon_turn_issuer_epoch,
            generation_provider=get_actor_turn_generation,
        )
        return authorities, start_claim

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

    def test_activated_workflow_create_preserves_request_target_and_records_created_resource(self):
        from no1.daemon.computer_control_ops import _workflow

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="activated-workflow-create", topic="")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-create",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "workflow_id": "",
                    "status": "accepted",
                    "allow_workflow_edit": True,
                    "created_ts": time.time(),
                },
            )
            requests._append_turn_activation(
                group.group_id,
                "req-create",
                activation={"authority_id": "turnauth_create"},
            )
            service = Mock()
            service.store = store
            service.requests = requests
            service.setup.status.return_value = {}
            service.session.catalog_sync.return_value = [{"name": "Type"}]
            definition = self._message_workflow()
            definition["nodes"][1]["arguments"]["label"] = 1

            created = _workflow(
                service,
                {
                    "action": "create",
                    "request_id": "req-create",
                    "definition": definition,
                },
                group.group_id,
                "foreman",
            )

            workflow_id = str(created["manifest"]["workflow_id"])
            request = requests.get(group.group_id, "req-create") or {}
            self.assertTrue(workflow_id)
            self.assertEqual(request["workflow_id"], "")
            self.assertEqual(request["created_workflow_id"], workflow_id)
            self.assertEqual(request["status"], "draft_created")
            before_rebind = requests._path(group.group_id).read_bytes()
            with self.assertRaisesRegex(PermissionError, "lifecycle resource is immutable"):
                requests.mark_workflow_created(
                    group.group_id,
                    "req-create",
                    created_workflow_id="wf_other",
                )
            self.assertEqual(requests._path(group.group_id).read_bytes(), before_rebind)

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

    def test_computer_control_permissions_default_off_and_require_explicit_opt_in(self):
        self.assertEqual(
            computer_control_permissions({}),
            {
                "allow_high_risk": False,
                "allow_publish": False,
                "allow_trust": False,
                "allow_unattended_triggers": False,
                "allow_workflow_edit": False,
            },
        )
        permissions = computer_control_permissions({"allow_trust": True, "allow_workflow_edit": True})
        self.assertTrue(permissions["allow_trust"])
        self.assertTrue(permissions["allow_workflow_edit"])
        self.assertFalse(permissions["allow_publish"])

    def test_computer_control_tools_are_not_core_or_web_model_tools(self):
        names = {
            "onecolleague_computer_control_catalog",
            "onecolleague_computer_recording",
            "onecolleague_computer_workflow",
            "onecolleague_computer_run",
        }
        self.assertTrue(names.isdisjoint(CORE_BASIC_TOOLS))
        self.assertTrue(names.isdisjoint(WEB_MODEL_CORE_TOOLS))

    def test_computer_control_mcp_rejects_remote_and_web_model_contexts(self):
        tool = "onecolleague_computer_run"
        for source in ("bridge", "remote", "web_model", "im", ""):
            with self.subTest(source=source), patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id="g", actor_id="peer", source=source),
            ):
                with self.assertRaisesRegex(Exception, "trusted local MCP actors"):
                    _authorize_local_computer_control_tool_call(tool)
        for group_id, actor_id in (("", "peer"), ("g", ""), ("g", "user")):
            with self.subTest(group_id=group_id, actor_id=actor_id), patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id=group_id, actor_id=actor_id, source="local_mcp"),
            ):
                with self.assertRaisesRegex(Exception, "bound local actor"):
                    _authorize_local_computer_control_tool_call(tool)
        with patch(
            "no1.ports.mcp.server._runtime_context",
            return_value=Mock(group_id="g", actor_id="peer", source="local_mcp"),
        ), patch("no1.ports.mcp.server.load_group", return_value=Mock()), patch(
            "no1.ports.mcp.server.find_actor", return_value={"id": "peer", "runtime": "codex"}
        ):
            self.assertEqual(_authorize_local_computer_control_tool_call(tool), ("g", "peer"))
        with patch(
            "no1.ports.mcp.server._runtime_context",
            return_value=Mock(group_id="g", actor_id="peer", source="local_mcp"),
        ), patch("no1.ports.mcp.server.load_group", return_value=Mock()), patch(
            "no1.ports.mcp.server.find_actor", return_value={"id": "peer", "runtime": "web_model"}
        ):
            with self.assertRaisesRegex(Exception, "Web Model"):
                _authorize_local_computer_control_tool_call(tool)

    def test_computer_control_mcp_daemon_failure_has_no_direct_service_fallback(self):
        source = inspect.getsource(_handle_onecolleague_namespace)
        self.assertNotIn("get_services", source)
        self.assertNotIn("service.runner", source)
        with patch(
            "no1.ports.mcp.server._authorize_local_computer_control_tool_call",
            return_value=("g", "actor"),
        ), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            side_effect=RuntimeError("daemon unavailable"),
        ), patch("no1.computer_control.services.get_services") as get_services:
            with self.assertRaisesRegex(RuntimeError, "daemon unavailable"):
                handle_tool_call(
                    "onecolleague_computer_run",
                    {"action": "status", "run_id": "run"},
                )
        get_services.assert_not_called()

    def test_list_tools_exposes_computer_control_only_to_bound_local_standard_actor(self):
        from no1.kernel.actors import add_actor
        from no1.kernel.group import load_group

        computer_tools = {
            "onecolleague_computer_control_catalog",
            "onecolleague_computer_recording",
            "onecolleague_computer_workflow",
            "onecolleague_computer_run",
        }
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td, CCCC_MCP_TOOL_PROFILE=""):
            group_id = create_group(load_registry(), title="local-computer-tools", topic="").group_id
            group = load_group(group_id)
            self.assertIsNotNone(group)
            assert group is not None
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            daemon_lock = acquire_lockfile(
                Path(td) / "daemon" / "onecolleagued.lock",
                blocking=False,
            )
            self.addCleanup(release_lockfile, daemon_lock)
            start_daemon_services(Path(td), lock_handle=daemon_lock)

            with patch(
                "no1.ports.mcp.server._call_daemon_or_raise",
                side_effect=RuntimeError("daemon unavailable"),
            ), patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id=group_id, actor_id="peer", source="local_mcp"),
            ):
                names = {str(item.get("name") or "") for item in list_tools_for_caller()}
            self.assertTrue(computer_tools.issubset(names))

            def call_computer_control_daemon(request, *, timeout_s=None):
                response, _ = try_handle_computer_control_op(request.get("op"), request.get("args") or {})
                self.assertTrue(response.ok, getattr(response, "error", None))
                return response.result or {}

            with patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id=group_id, actor_id="peer", source="local_mcp"),
            ), patch(
                "no1.ports.mcp.server._call_daemon_or_raise",
                side_effect=call_computer_control_daemon,
            ):
                workflow_list = handle_tool_call(
                    "onecolleague_computer_workflow",
                    {"action": "list"},
                )
            self.assertTrue(workflow_list.get("ok"))
            self.assertEqual((workflow_list.get("result") or {}).get("workflows"), [])

            for source in ("bridge", "remote", "web_model", "im", "viewer", ""):
                with self.subTest(source=source), patch(
                    "no1.ports.mcp.server._call_daemon_or_raise",
                    side_effect=RuntimeError("daemon unavailable"),
                ), patch(
                    "no1.ports.mcp.server._runtime_context",
                    return_value=Mock(group_id=group_id, actor_id="peer", source=source),
                ):
                    names = {str(item.get("name") or "") for item in list_tools_for_caller()}
                self.assertTrue(computer_tools.isdisjoint(names))

            actor = next(item for item in group.doc.get("actors") or [] if item.get("id") == "peer")
            actor["runtime"] = "web_model"
            group.save()
            with patch(
                "no1.ports.mcp.server._call_daemon_or_raise",
                side_effect=RuntimeError("daemon unavailable"),
            ), patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id=group_id, actor_id="peer", source="local_mcp"),
            ):
                names = {str(item.get("name") or "") for item in list_tools_for_caller()}
            self.assertTrue(computer_tools.isdisjoint(names))

            actor["runtime"] = "codex"
            group.save()
            with patch.dict(os.environ, {"CCCC_MCP_TOOL_PROFILE": "full"}, clear=False), patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id=group_id, actor_id="peer", source="remote"),
            ):
                names = {str(item.get("name") or "") for item in list_tools_for_caller()}
            self.assertTrue(computer_tools.isdisjoint(names))

    def test_computer_control_mcp_passes_presented_receipt_unchanged(self):
        from no1.kernel.actors import add_actor

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            group = create_group(load_registry(), title="receipt-passthrough", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            receipt = {
                "v": 1,
                "issuer_epoch": "daemon_epoch",
                "group_id": group.group_id,
                "actor_id": "peer",
                "attempt_id": "turnattempt_1",
                "generation": 1,
                "event_ids": ["event_1"],
                "binding": {"transport": "codex_app"},
                "authorization_binding": {
                    "authority_id": "turnauth_1",
                    "secret_digest": "d" * 64,
                    "transport": "codex_app",
                },
                "authorization_secret": "s" * 32,
            }
            calls = []

            def call_daemon(request, *, timeout_s=None):
                calls.append((request, timeout_s))
                return {"ok": True, "result": {}}

            cases = (
                ("onecolleague_computer_control_catalog", {}),
                ("onecolleague_computer_recording", {"action": "get", "recording_id": "rec"}),
                ("onecolleague_computer_workflow", {"action": "list"}),
                ("onecolleague_computer_run", {"action": "status", "run_id": "run"}),
            )
            with patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id=group.group_id, actor_id="peer", source="local_mcp"),
            ), patch("no1.ports.mcp.server._call_daemon_or_raise", side_effect=call_daemon):
                for tool_name, arguments in cases:
                    handle_tool_call(tool_name, {**arguments, "turn_grant_receipt": receipt})

            self.assertEqual(len(calls), len(cases))
            for request, timeout_s in calls:
                self.assertIsNone(timeout_s)
                self.assertEqual((request.get("args") or {}).get("turn_grant_receipt"), receipt)

    def test_daemon_computer_control_rejects_missing_or_untrusted_surface(self):
        for surface in (None, "bridge", "remote", "web_model", "viewer", "im"):
            args = {"command": "catalog", "group_id": "_global"}
            if surface is not None:
                args["caller_surface"] = surface
            response, _ = try_handle_computer_control_op("computer_control", args)
            self.assertFalse(response.ok)
            self.assertEqual(response.error.code, "permission_denied")

    def test_daemon_live_actions_require_presented_grant_before_services(self):
        from no1.kernel.actors import add_actor
        from no1.kernel.group import load_group

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            group = create_group(load_registry(), title="claim-gate", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            cases = [
                {"command": "catalog"},
                {"command": "recording", "action": "get", "recording_id": "rec"},
                {"command": "workflow", "action": "validate", "definition": self._message_workflow()},
                {"command": "run", "action": "status", "run_id": "run"},
                {"command": "run", "action": "start", "workflow_id": "trusted"},
                {"command": "picker", "action": "status", "session_id": "pick"},
                {"command": "element_snapshot", "capture_id": "capture"},
                {"command": "setup", "action": "ensure"},
            ]
            with patch("no1.daemon.computer_control_ops.get_services") as get_services:
                for case in cases:
                    with self.subTest(case=case):
                        response, _ = try_handle_computer_control_op(
                            "computer_control",
                            {
                                **case,
                                "group_id": group.group_id,
                                "actor_id": "peer",
                                "caller_surface": "local_mcp",
                            },
                        )
                        self.assertFalse(response.ok)
                        self.assertEqual(response.error.code, "permission_denied")
                get_services.assert_not_called()

            fake = Mock()
            fake.store.list.return_value = []
            fake.lease.status.return_value = {"active": False}
            fake.setup.status.return_value = {"phase": "ready"}
            with patch("no1.daemon.computer_control_ops.get_services", return_value=fake):
                for command, action in (("workflow", "list"), ("lease", "status"), ("setup", "status")):
                    with self.subTest(exempt=(command, action)):
                        response, _ = try_handle_computer_control_op(
                            "computer_control",
                            {
                                "command": command,
                                "action": action,
                                "group_id": group.group_id,
                                "actor_id": "peer",
                                "caller_surface": "local_mcp",
                            },
                        )
                        self.assertTrue(response.ok, response.error)
            self.assertIsNotNone(load_group(group.group_id))

    def test_daemon_valid_claim_activates_request_before_recording_start(self):
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_grant_receipt,
        )
        from no1.kernel.actors import add_actor
        from no1.kernel.ledger import append_event

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            group = create_group(load_registry(), title="request-order", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            request_payload = {
                "request_id": "req-order",
                "actor_id": "peer",
                "mode": "create_and_run",
                "workflow_id": "",
            }
            event = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="user",
                data=ChatMessageData(
                    text="record",
                    to=["peer"],
                    computer_control_request=request_payload,
                    turn_provenance=provenance,
                ).model_dump(),
            )
            requests = ComputerRequestStore(WorkflowStore(Path(td)))
            requests.append(
                group.group_id,
                {
                    **request_payload,
                    "event_id": str(event["id"]),
                    "local_request_id": str(provenance.local_request_id),
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "codex_app"},
            )
            receipt = turn_delivery_grant_receipt(attempt)
            finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)

            fake = Mock()
            fake.requests = requests

            def recording_start(*_args, **_kwargs):
                from no1.computer_control.authorization import RecordingStartClaim

                activated = requests.get(group.group_id, "req-order") or {}
                self.assertEqual(
                    (activated.get("turn_authorization") or {}).get("generation"),
                    (receipt or {}).get("generation"),
                )
                self.assertIsInstance(_kwargs.get("start_claim"), RecordingStartClaim)
                return {"recording_id": "rec-order", "status": "exploring"}

            fake.recordings.start.side_effect = recording_start
            with patch("no1.daemon.computer_control_ops.get_services", return_value=fake):
                response, _ = try_handle_computer_control_op(
                    "computer_control",
                    {
                        "command": "recording",
                        "action": "start",
                        "group_id": group.group_id,
                        "actor_id": "peer",
                        "request_id": "req-order",
                        "caller_surface": "local_mcp",
                        "turn_grant_receipt": receipt,
                    },
                )
            self.assertTrue(response.ok, response.error)
            fake.recordings.start.assert_called_once()

            persisted = requests.get(group.group_id, "req-order") or {}
            persisted_text = requests._path(group.group_id).read_text(encoding="utf-8")
            self.assertTrue((persisted.get("turn_authorization") or {}).get("secret_digest"))
            self.assertNotIn(str((receipt or {}).get("authorization_secret") or ""), persisted_text)
            self.assertNotIn("authorization_secret", persisted_text)

    def test_daemon_recording_operation_receipt_replaces_root_until_generation_changes(self):
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_grant_receipt,
        )
        from no1.kernel.actors import add_actor
        from no1.kernel.ledger import append_event

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            group = create_group(load_registry(), title="recording-derived", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            request_payload = {
                "request_id": "req-derived",
                "actor_id": "peer",
                "mode": "create_and_run",
                "workflow_id": "",
            }
            event = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="user",
                data=ChatMessageData(
                    text="record",
                    to=["peer"],
                    computer_control_request=request_payload,
                    turn_provenance=provenance,
                ).model_dump(),
            )
            requests = ComputerRequestStore(WorkflowStore(Path(td)))
            requests.append(
                group.group_id,
                {
                    **request_payload,
                    "event_id": str(event["id"]),
                    "local_request_id": str(provenance.local_request_id),
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "codex_app"},
            )
            root_receipt = turn_delivery_grant_receipt(attempt)
            finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)
            daemon_lock = acquire_lockfile(
                Path(td) / "daemon" / "onecolleagued.lock",
                blocking=False,
            )
            self.addCleanup(release_lockfile, daemon_lock)
            start_daemon_services(Path(td), lock_handle=daemon_lock)

            started, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "recording",
                    "action": "start",
                    "group_id": group.group_id,
                    "actor_id": "peer",
                    "request_id": "req-derived",
                    "name": "derived",
                    "caller_surface": "local_mcp",
                    "turn_grant_receipt": root_receipt,
                },
            )
            self.assertTrue(started.ok, started.error)
            started_value = started.result["result"]
            operation_receipt = started_value["operation_receipt"]
            recording_id = started_value["recording_id"]

            loaded, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "recording",
                    "action": "get",
                    "group_id": group.group_id,
                    "actor_id": "peer",
                    "recording_id": recording_id,
                    "caller_surface": "local_mcp",
                    "recording_authority_receipt": operation_receipt,
                },
            )
            self.assertTrue(loaded.ok, loaded.error)
            self.assertEqual(loaded.result["result"]["recording_id"], recording_id)

            other_group = create_group(load_registry(), title="receipt-cross-group", topic="")
            other_authority_root = (
                other_group.path / "state" / "computer-control" / "derived-authorities"
            )
            traversal_group = f"../../receipt_escape_{Path(td).name}"
            traversal_root = (
                Path(td)
                / "groups"
                / traversal_group
                / "state"
                / "computer-control"
                / "derived-authorities"
            ).resolve()
            self.assertFalse(other_authority_root.exists())
            self.assertFalse(traversal_root.exists())
            for label, forged_group, forbidden_root in (
                ("cross_group", other_group.group_id, other_authority_root),
                ("path_traversal", traversal_group, traversal_root),
            ):
                with self.subTest(label=label), patch(
                    "no1.daemon.computer_control_ops.get_services"
                ) as get_services:
                    rejected, _ = try_handle_computer_control_op(
                        "computer_control",
                        {
                            "command": "recording",
                            "action": "get",
                            "group_id": group.group_id,
                            "actor_id": "peer",
                            "recording_id": recording_id,
                            "caller_surface": "local_mcp",
                            "recording_authority_receipt": {
                                **operation_receipt,
                                "group_id": forged_group,
                            },
                        },
                    )
                self.assertFalse(rejected.ok)
                self.assertEqual(rejected.error.code, "permission_denied")
                get_services.assert_not_called()
                self.assertFalse(forbidden_root.exists())

            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (group.path / "state" / "computer-control").rglob("*.json*")
            )
            self.assertNotIn(operation_receipt["authorization_secret"], persisted)
            next_attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "codex_app"},
            )
            self.assertGreater(next_attempt["generation"], operation_receipt["generation"])
            with patch("no1.daemon.computer_control_ops.get_services") as get_services:
                rejected, _ = try_handle_computer_control_op(
                    "computer_control",
                    {
                        "command": "recording",
                        "action": "get",
                        "group_id": group.group_id,
                        "actor_id": "peer",
                        "recording_id": recording_id,
                        "caller_surface": "local_mcp",
                        "recording_authority_receipt": operation_receipt,
                    },
                )
            self.assertFalse(rejected.ok)
            self.assertEqual(rejected.error.code, "permission_denied")
            get_services.assert_not_called()

    def test_daemon_recording_abort_requires_exact_stop_owner_before_services(self):
        from no1.kernel.actors import add_actor

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-stop-owner", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            add_actor(group, actor_id="other", title="Other", runtime="codex", runner="headless")
            group.save()
            other_group = create_group(load_registry(), title="recording-stop-owner-other", topic="")
            add_actor(other_group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            other_group.save()
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-stop-owner",
                    "actor_id": "peer",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-stop-owner", "peer"
            )
            lease = ComputerControlLease(home)
            recordings = RecordingStore(home, store, requests, authorities, lease, Mock())
            started = recordings.start(
                group.group_id,
                actor_id="peer",
                request_id="req-stop-owner",
                start_claim=start_claim,
                name="stop owner",
            )
            recording_id = started["recording_id"]
            with patch.object(recordings, "_read") as read_recording, patch.object(
                authorities, "begin_stop"
            ) as begin_stop:
                with self.assertRaisesRegex(PermissionError, "stop owner claim is required"):
                    recordings.abort(
                        group.group_id,
                        recording_id,
                        actor_id="peer",
                        stop_claim=None,
                    )
            read_recording.assert_not_called()
            begin_stop.assert_not_called()
            authority_path = next(
                (store.state_root(group.group_id) / "derived-authorities").glob("*.json")
            )
            recording_path = store.state_root(group.group_id) / "recordings" / f"{recording_id}.json"

            def snapshot() -> tuple[bytes, bytes, bytes]:
                return (
                    authority_path.read_bytes(),
                    recording_path.read_bytes(),
                    lease.path.read_bytes(),
                )

            before = snapshot()
            for label, candidate_group, candidate_actor in (
                ("cross_actor", group.group_id, "other"),
                ("cross_group", other_group.group_id, "peer"),
            ):
                with self.subTest(label=label), patch(
                    "no1.daemon.computer_control_ops.get_services"
                ) as get_services:
                    rejected, _ = try_handle_computer_control_op(
                        "computer_control",
                        {
                            "command": "recording",
                            "action": "abort",
                            "group_id": candidate_group,
                            "actor_id": candidate_actor,
                            "recording_id": recording_id,
                            "caller_surface": "local_mcp",
                        },
                    )
                self.assertFalse(rejected.ok)
                self.assertEqual(rejected.error.code, "permission_denied")
                get_services.assert_not_called()
                self.assertEqual(snapshot(), before)

    def test_daemon_rejects_request_fact_tampering_before_services(self):
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_grant_receipt,
        )
        from no1.kernel.actors import add_actor
        from no1.kernel.ledger import append_event

        permission_names = tuple(computer_control_permissions({}))
        cases = [
            ("missing_request_id", None),
            ("expired", {"created_ts": time.time() - 7200}),
            ("actor", {"actor_id": "other"}),
            ("copied_request_id", {"copied_request_id": "req-copy"}),
            ("mode", {"current_patch": {"mode": "run_existing"}}),
            ("workflow_id", {"current_patch": {"workflow_id": "wf-escalated"}}),
            *((name, {"current_patch": {name: True}}) for name in permission_names),
        ]
        for label, mutation in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
                group = create_group(load_registry(), title=f"request-facts-{label}", topic="")
                add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
                group.save()
                provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
                event_request = {
                    "request_id": "req-original",
                    "actor_id": "peer",
                    "mode": "create_and_run",
                    "workflow_id": "",
                    **computer_control_permissions({}),
                }
                if mutation and "actor_id" in mutation:
                    event_request["actor_id"] = mutation["actor_id"]
                event = append_event(
                    group.ledger_path,
                    kind="chat.message",
                    group_id=group.group_id,
                    scope_key="",
                    by="user",
                    data=ChatMessageData(
                        text="record",
                        to=["peer"],
                        computer_control_request=event_request,
                        turn_provenance=provenance,
                    ).model_dump(),
                )
                request_id = str((mutation or {}).get("copied_request_id") or "req-original")
                requests = ComputerRequestStore(WorkflowStore(Path(td)))
                if label != "missing_request_id":
                    requests.append(
                        group.group_id,
                        {
                            **event_request,
                            "request_id": request_id,
                            "event_id": str(event["id"]),
                            "local_request_id": str(provenance.local_request_id),
                            "status": "accepted",
                            "created_ts": float((mutation or {}).get("created_ts") or time.time()),
                        },
                    )
                    current_patch = (mutation or {}).get("current_patch")
                    if isinstance(current_patch, dict):
                        requests.update(group.group_id, request_id, **current_patch)
                attempt = begin_turn_delivery_attempt(
                    group,
                    actor_id="peer",
                    event_ids=[str(event["id"])],
                    binding={"transport": "codex_app"},
                )
                receipt = turn_delivery_grant_receipt(attempt)
                finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)

                with patch("no1.daemon.computer_control_ops.get_services") as get_services:
                    response, _ = try_handle_computer_control_op(
                        "computer_control",
                        {
                            "command": "recording",
                            "action": "start",
                            "group_id": group.group_id,
                            "actor_id": "peer",
                            "request_id": "" if label == "missing_request_id" else request_id,
                            "caller_surface": "local_mcp",
                            "turn_grant_receipt": receipt,
                        },
                    )
                self.assertFalse(response.ok)
                self.assertEqual(response.error.code, "permission_denied")
                get_services.assert_not_called()
                if label != "missing_request_id":
                    persisted = requests.get(group.group_id, request_id) or {}
                    self.assertNotIn("turn_authorization", persisted)

    def test_trusted_workflow_start_still_uses_live_turn_claim(self):
        from no1.computer_control.authorization import RunStartClaim
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_grant_receipt,
        )
        from no1.kernel.actors import add_actor
        from no1.kernel.ledger import append_event

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="trusted-run-claim", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            store = WorkflowStore(home)
            created = store.create(
                group.group_id,
                WorkflowDefinition.model_validate(self._message_workflow()),
            )
            workflow_id = str(created["manifest"]["workflow_id"])
            store.publish(group.group_id, workflow_id, 1)
            store.trust(
                group.group_id,
                workflow_id,
                1,
                fingerprint="fp-current",
                permissions=["all_windows_mcp_tools"],
            )
            requests = ComputerRequestStore(store)
            provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            request = {
                "request_id": "req-trusted-run",
                "actor_id": "peer",
                "mode": "run_existing",
                "workflow_id": workflow_id,
                "inputs": {},
            }
            event = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="user",
                data=ChatMessageData(
                    text="run trusted",
                    to=["peer"],
                    computer_control_request=request,
                    turn_provenance=provenance,
                ).model_dump(),
            )
            requests.append(
                group.group_id,
                {
                    **request,
                    "event_id": str(event["id"]),
                    "local_request_id": str(provenance.local_request_id),
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "claude_app"},
            )
            receipt = turn_delivery_grant_receipt(attempt)
            finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)

            fake = Mock()
            fake.requests = requests
            fake.setup.status.return_value = {"fingerprint": "fp-current"}
            fake.store = store
            fake.runner.start_manual_sync.return_value = {
                "origin": "manual_actor",
                "run_id": "run-trusted",
                "status": "running",
            }
            with patch("no1.daemon.computer_control_ops.get_services", return_value=fake):
                response, _ = try_handle_computer_control_op(
                    "computer_control",
                    {
                        "command": "run",
                        "action": "start",
                        "group_id": group.group_id,
                        "actor_id": "peer",
                        "workflow_id": workflow_id,
                        "version": 1,
                        "request_id": "req-trusted-run",
                        "inputs": {},
                        "caller_surface": "local_mcp",
                        "turn_grant_receipt": receipt,
                    },
                )
            self.assertTrue(response.ok, response.error)
            fake.runner.start_manual_sync.assert_called_once()
            self.assertIsInstance(
                fake.runner.start_manual_sync.call_args.kwargs["start_claim"],
                RunStartClaim,
            )

    def test_manual_run_start_missing_request_is_rejected_before_service_construction(self):
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_grant_receipt,
        )
        from no1.kernel.actors import add_actor
        from no1.kernel.ledger import append_event

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            group = create_group(load_registry(), title="missing-run-request", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            event = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="user",
                data=ChatMessageData(
                    text="run without request id",
                    to=["peer"],
                    turn_provenance=provenance,
                ).model_dump(),
            )
            attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "test"},
            )
            receipt = turn_delivery_grant_receipt(attempt)
            finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)
            with patch("no1.daemon.computer_control_ops.get_services") as get_services, patch(
                "no1.daemon.computer_control_ops.ComputerRequestStore"
            ) as request_store, patch(
                "no1.daemon.computer_control_ops.WorkflowStore"
            ) as workflow_store:
                response, _ = try_handle_computer_control_op(
                    "computer_control",
                    {
                        "command": "run",
                        "action": "start",
                        "group_id": group.group_id,
                        "actor_id": "peer",
                        "workflow_id": "wf_missing",
                        "version": 1,
                        "caller_surface": "local_mcp",
                        "turn_grant_receipt": receipt,
                    },
                )
            self.assertFalse(response.ok)
            self.assertEqual(response.error.code, "permission_denied")
            get_services.assert_not_called()
            request_store.assert_not_called()
            workflow_store.assert_not_called()

    def test_untrusted_mcp_run_start_uses_manual_origin_and_private_run_projection(self):
        from no1.computer_control.authorization import RunStartClaim
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_grant_receipt,
        )
        from no1.kernel.actors import add_actor
        from no1.kernel.ledger import append_event

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="untrusted-run-writeback", topic="")
            add_actor(group, actor_id="peer", title="Peer", runtime="codex", runner="headless")
            group.save()
            store = WorkflowStore(home)
            created = store.create(
                group.group_id,
                WorkflowDefinition.model_validate(self._message_workflow()),
            )
            workflow_id = str(created["manifest"]["workflow_id"])
            requests = ComputerRequestStore(store)
            provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            request = {
                "request_id": "req-untrusted-run",
                "actor_id": "peer",
                "mode": "create_and_run",
                "workflow_id": "",
                "inputs": {},
                "allow_high_risk": True,
            }
            event = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="user",
                data=ChatMessageData(
                    text="run untrusted workflow",
                    to=["peer"],
                    computer_control_request=request,
                    turn_provenance=provenance,
                ).model_dump(),
            )
            requests.append(
                group.group_id,
                {
                    **request,
                    "event_id": str(event["id"]),
                    "local_request_id": str(provenance.local_request_id),
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            requests.mark_workflow_created(
                group.group_id,
                "req-untrusted-run",
                created_workflow_id=workflow_id,
                status="draft_created",
            )
            attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "codex_app"},
            )
            receipt = turn_delivery_grant_receipt(attempt)
            finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)

            fake = Mock()
            fake.requests = requests
            fake.setup.status.return_value = {"fingerprint": "fp-current"}
            fake.store = store

            def start_manual_sync(*args, **kwargs):
                self.assertIsInstance(kwargs.get("start_claim"), RunStartClaim)
                requests.mark_run_started(
                    group.group_id,
                    "req-untrusted-run",
                    run_id="run-untrusted",
                    status="running",
                )
                return {"origin": "manual_actor", "run_id": "run-untrusted", "status": "running"}

            fake.runner.start_manual_sync.side_effect = start_manual_sync

            def call_computer_control_daemon(message, *, timeout_s=None):
                self.assertIsNone(timeout_s)
                response, _ = try_handle_computer_control_op(
                    message.get("op"),
                    message.get("args") or {},
                )
                self.assertTrue(response.ok, getattr(response, "error", None))
                return response.result or {}

            with patch(
                "no1.ports.mcp.server._runtime_context",
                return_value=Mock(group_id=group.group_id, actor_id="peer", source="local_mcp"),
            ), patch(
                "no1.ports.mcp.server._call_daemon_or_raise",
                side_effect=call_computer_control_daemon,
            ), patch(
                "no1.daemon.computer_control_ops.get_services",
                return_value=fake,
            ):
                result = handle_tool_call(
                    "onecolleague_computer_run",
                    {
                        "action": "start",
                        "workflow_id": workflow_id,
                        "version": 1,
                        "request_id": "req-untrusted-run",
                        "inputs": {},
                        "turn_grant_receipt": receipt,
                    },
                )

            self.assertTrue(result.get("ok"))
            self.assertEqual((result.get("result") or {}).get("run_id"), "run-untrusted")
            fake.runner.start_manual_sync.assert_called_once()
            self.assertEqual((result.get("result") or {}).get("origin"), "manual_actor")
            persisted = requests.get(group.group_id, "req-untrusted-run") or {}
            self.assertEqual(persisted.get("status"), "running")
            self.assertEqual(persisted.get("run_id"), "run-untrusted")

    def test_request_activation_binds_local_event_to_exact_grant_without_secret(self):
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            load_event_turn_provenance,
            turn_delivery_grant_receipt,
            validate_turn_grant_receipt,
        )
        from no1.kernel.ledger import append_event

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            group = create_group(load_registry(), title="request-claim", topic="")
            provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            request_payload = {
                "request_id": "req-local",
                "actor_id": "peer",
                "mode": "create_and_run",
                "workflow_id": "",
            }
            event = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="user",
                data=ChatMessageData(
                    text="record this workflow",
                    to=["peer"],
                    computer_control_request=request_payload,
                    turn_provenance=provenance,
                ).model_dump(),
            )
            store = WorkflowStore(Path(td))
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    **request_payload,
                    "event_id": str(event["id"]),
                    "local_request_id": str(provenance.local_request_id),
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "pty"},
            )
            receipt = turn_delivery_grant_receipt(attempt)
            finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)
            claim = validate_turn_grant_receipt(group, "peer", turn_grant_receipt=receipt)
            self.assertIsNotNone(claim)
            activated = requests.activate_for_turn_claim(
                group.group_id,
                "req-local",
                "peer",
                claim=claim,
                provenance=load_event_turn_provenance(group, str(event["id"])),
                ledger_event=event,
            )
            self.assertEqual(activated["turn_authorization"]["generation"], claim["generation"])
            request_text = (group.path / "state" / "computer-control" / "requests.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(str((receipt or {}).get("authorization_secret") or ""), request_text)
            self.assertNotIn("authorization_secret", request_text)
            for field, value in (
                ("actor_id", "other"),
                ("mode", "run_existing"),
                ("workflow_id", "wf_escalated"),
                ("event_id", "event_other"),
                ("local_request_id", "localreq_other"),
                ("allow_high_risk", True),
                ("allow_publish", True),
                ("allow_trust", True),
                ("allow_unattended_triggers", True),
                ("allow_workflow_edit", True),
            ):
                for operation in ("append", "update"):
                    with self.subTest(operation=operation, immutable_field=field):
                        before = requests._path(group.group_id).read_bytes()
                        with self.assertRaisesRegex(PermissionError, "facts are immutable"):
                            if operation == "append":
                                requests.append(
                                    group.group_id,
                                    {"request_id": "req-local", field: value},
                                )
                            else:
                                requests.update(group.group_id, "req-local", **{field: value})
                        self.assertEqual(requests._path(group.group_id).read_bytes(), before)
            lifecycle = requests.mark_recording_started(
                group.group_id,
                "req-local",
                recording_id="rec_lifecycle",
                status="exploring",
            )
            self.assertEqual(lifecycle["recording_id"], "rec_lifecycle")
            before_lifecycle_change = requests._path(group.group_id).read_bytes()
            with self.assertRaisesRegex(PermissionError, "lifecycle resource is immutable"):
                requests.mark_recording_started(
                    group.group_id,
                    "req-local",
                    recording_id="rec_other",
                )
            self.assertEqual(requests._path(group.group_id).read_bytes(), before_lifecycle_change)
            run_lifecycle = requests.mark_run_started(
                group.group_id,
                "req-local",
                run_id="run_lifecycle",
                status="initializing",
            )
            self.assertEqual(run_lifecycle["run_id"], "run_lifecycle")
            before_run_idempotent = requests._path(group.group_id).read_bytes()
            same_run = requests.mark_run_started(
                group.group_id,
                "req-local",
                run_id="run_lifecycle",
                status="running",
            )
            self.assertEqual(same_run["run_id"], "run_lifecycle")
            self.assertEqual(
                requests._path(group.group_id).read_bytes(),
                before_run_idempotent,
            )
            with self.assertRaisesRegex(PermissionError, "lifecycle resource is immutable"):
                requests.mark_run_started(
                    group.group_id,
                    "req-local",
                    run_id="run_other",
                )
            self.assertEqual(
                requests._path(group.group_id).read_bytes(),
                before_run_idempotent,
            )

            for label, patch_value in (
                ("legacy", {"local_request_id": ""}),
                ("copied_event", {"event_id": "event-missing"}),
            ):
                request_id = "req-" + label
                requests.append(
                    group.group_id,
                    {
                        "request_id": request_id,
                        "actor_id": "peer",
                        "event_id": str(event["id"]),
                        "local_request_id": str(provenance.local_request_id),
                        "mode": "create_and_run",
                        "status": "accepted",
                        "created_ts": time.time(),
                        **patch_value,
                    },
                )
                candidate = requests.get(group.group_id, request_id) or {}
                candidate_provenance = load_event_turn_provenance(group, str(candidate.get("event_id") or ""))
                with self.subTest(label=label), self.assertRaises(PermissionError):
                    requests.activate_for_turn_claim(
                        group.group_id,
                        request_id,
                        "peer",
                        claim=claim,
                        provenance=candidate_provenance,
                        ledger_event=None,
                    )

    def test_request_activation_is_atomic_with_public_append_across_store_instances(self):
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            load_event_turn_provenance,
            turn_delivery_grant_receipt,
            validate_turn_grant_receipt,
        )
        from no1.kernel.ledger import append_event

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            group = create_group(load_registry(), title="request-activation-race", topic="")
            provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            request_payload = {
                "request_id": "req-race",
                "actor_id": "peer",
                "mode": "create_and_run",
                "workflow_id": "",
            }
            event = append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="user",
                data=ChatMessageData(
                    text="record",
                    to=["peer"],
                    computer_control_request=request_payload,
                    turn_provenance=provenance,
                ).model_dump(),
            )
            store = WorkflowStore(Path(td))
            seed = ComputerRequestStore(store)
            seed.append(
                group.group_id,
                {
                    **request_payload,
                    "event_id": str(event["id"]),
                    "local_request_id": str(provenance.local_request_id),
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            attempt = begin_turn_delivery_attempt(
                group,
                actor_id="peer",
                event_ids=[str(event["id"])],
                binding={"transport": "pty"},
            )
            receipt = turn_delivery_grant_receipt(attempt)
            finalize_turn_delivery_attempt(group, actor_id="peer", attempt=attempt)
            claim = validate_turn_grant_receipt(group, "peer", turn_grant_receipt=receipt)
            self.assertIsNotNone(claim)
            writer = ComputerRequestStore(store)
            activator = ComputerRequestStore(store)
            writer_read = threading.Event()
            allow_writer = threading.Event()
            activation_done = threading.Event()
            writer_errors = []
            activation_errors = []
            original_read = writer._get_unlocked
            intercepted = False

            def blocked_read(group_id, request_id):
                nonlocal intercepted
                value = original_read(group_id, request_id)
                if request_id == "req-race" and not intercepted:
                    intercepted = True
                    writer_read.set()
                    allow_writer.wait(5)
                return value

            def append_permission() -> None:
                try:
                    writer.append(
                        group.group_id,
                        {"request_id": "req-race", "allow_publish": True},
                    )
                except Exception as exc:
                    writer_errors.append(exc)

            def activate() -> None:
                try:
                    activator.activate_for_turn_claim(
                        group.group_id,
                        "req-race",
                        "peer",
                        claim=claim,
                        provenance=load_event_turn_provenance(group, str(event["id"])),
                        ledger_event=event,
                    )
                except Exception as exc:
                    activation_errors.append(exc)
                finally:
                    activation_done.set()

            with patch.object(writer, "_get_unlocked", side_effect=blocked_read):
                writer_thread = threading.Thread(target=append_permission)
                writer_thread.start()
                self.assertTrue(writer_read.wait(2))
                activation_thread = threading.Thread(target=activate)
                activation_thread.start()
                self.assertFalse(activation_done.wait(0.1))
                allow_writer.set()
                writer_thread.join(5)
                activation_thread.join(5)

            self.assertFalse(writer_thread.is_alive())
            self.assertFalse(activation_thread.is_alive())
            self.assertFalse(writer_errors)
            self.assertEqual(len(activation_errors), 1)
            self.assertIsInstance(activation_errors[0], PermissionError)
            final = seed.get(group.group_id, "req-race") or {}
            self.assertTrue(final["allow_publish"])
            self.assertNotIn("turn_authorization", final)

    def test_request_store_rejects_public_authority_writes_without_disk_changes(self):
        with tempfile.TemporaryDirectory() as td:
            home = str(Path(td) / "onecolleague-home")
            with patch.dict(os.environ, {"ONECOLLEAGUE_HOME": home}, clear=False):
                group = create_group(load_registry(), title="reserved-request-authority", topic="")
                requests = ComputerRequestStore(WorkflowStore(Path(td)))
                path = requests._path(group.group_id)
                for field in sorted(ComputerRequestStore.RESERVED_AUTHORITY_FIELDS):
                    with self.subTest(operation="append", field=field):
                        before = path.read_bytes() if path.exists() else None
                        with self.assertRaisesRegex(PermissionError, "authority fields are reserved"):
                            requests.append(
                                group.group_id,
                                {
                                    "request_id": "req-forged",
                                    "actor_id": "peer",
                                    field: {"authority_id": "forged"},
                                },
                            )
                        after = path.read_bytes() if path.exists() else None
                        self.assertEqual(after, before)

                requests.append(
                    group.group_id,
                    {
                        "request_id": "req-existing",
                        "actor_id": "peer",
                        "status": "accepted",
                        "created_ts": time.time(),
                    },
                )
                for field in sorted(ComputerRequestStore.RESERVED_AUTHORITY_FIELDS):
                    with self.subTest(operation="update", field=field):
                        before = path.read_bytes()
                        with self.assertRaisesRegex(PermissionError, "authority fields are reserved"):
                            requests.update(
                                group.group_id,
                                "req-existing",
                                **{field: {"authority_id": "forged"}},
                            )
                        self.assertEqual(path.read_bytes(), before)

    def test_web_computer_control_rejects_viewer_and_remote_clients(self):
        local_request = Mock(client=Mock(host="127.0.0.1"))
        with self.assertRaisesRegex(Exception, "read-only"):
            _require_local_computer_control_admin(Mock(read_only=True), local_request)
        remote_request = Mock(client=Mock(host="203.0.113.9"))
        with self.assertRaisesRegex(Exception, "loopback"):
            _require_local_computer_control_admin(Mock(read_only=False), remote_request)
        with patch("no1.ports.web.routes.computer_control.require_admin", side_effect=Exception("admin access required")):
            with self.assertRaisesRegex(Exception, "admin access required"):
                _require_local_computer_control_admin(Mock(read_only=False), local_request)

    def test_active_recording_authorization_does_not_expire(self):
        with tempfile.TemporaryDirectory() as td:
            home = str(Path(td) / "onecolleague-home")
            with patch.dict(os.environ, {"ONECOLLEAGUE_HOME": home}, clear=False):
                group = create_group(load_registry(), title="long recording auth")
                store = WorkflowStore(Path(td))
                requests = ComputerRequestStore(store)
                requests.append(group.group_id, {
                    "request_id": "req-long",
                    "actor_id": "foreman",
                    "status": "accepted",
                    "created_ts": time.time() - 7200,
                })
                requests.mark_recording_started(
                    group.group_id,
                    "req-long",
                    recording_id="rec_long",
                )
                authorized = requests.require_authorized(group.group_id, "req-long", "foreman")
                self.assertEqual(authorized["recording_id"], "rec_long")
                for key in (
                    "allow_high_risk",
                    "allow_publish",
                    "allow_trust",
                    "allow_unattended_triggers",
                    "allow_workflow_edit",
                ):
                    self.assertFalse(authorized[key], key)

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
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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
                    {"command": "setup", "action": "restart_session", "group_id": "_global", "caller_surface": "local_web"},
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
                    {"command": "setup", "action": "restart_session", "group_id": "_global", "caller_surface": "local_web"},
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

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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
            authorities, start_claim = self._recording_security(
                Path(td), requests, group.group_id, "req-image", "foreman"
            )
            recordings = RecordingStore(
                Path(td),
                store,
                requests,
                authorities,
                ComputerControlLease(Path(td)),
                ImageSession(),
            )
            recording = recordings.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-image",
                start_claim=start_claim,
                name="capture",
            )
            authority = authorities.validate_active_receipt(
                recording["operation_receipt"],
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording["recording_id"],
                expected_kind="recording",
            )
            response = recordings.call(
                group.group_id,
                recording["recording_id"],
                actor_id="foreman",
                authority=authority,
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
    def test_recording_start_failure_rolls_back_authority_lease_and_resource(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-start-rollback")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-rollback",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-rollback", "foreman"
            )
            lease = ComputerControlLease(home)
            session = Mock()
            recordings = RecordingStore(home, store, requests, authorities, lease, session)

            with patch.object(lease, "activate_reservation", side_effect=RuntimeError("activate failed")):
                with self.assertRaisesRegex(RuntimeError, "activate failed"):
                    recordings.start(
                        group.group_id,
                        actor_id="foreman",
                        request_id="req-rollback",
                        start_claim=start_claim,
                        name="rollback",
                    )

            recording_files = list((store.state_root(group.group_id) / "recordings").glob("rec_*.json"))
            self.assertEqual(len(recording_files), 1)
            self.assertEqual(json.loads(recording_files[0].read_text(encoding="utf-8"))["status"], "start_failed")
            authority_files = list(
                (store.state_root(group.group_id) / "derived-authorities").glob("*.json")
            )
            self.assertEqual(len(authority_files), 1)
            self.assertEqual(json.loads(authority_files[0].read_text(encoding="utf-8"))["state"], "revoked")
            self.assertFalse(lease.status()["active"])
            self.assertFalse(recordings._active)
            session.catalog_sync.assert_not_called()

    def test_recording_root_claim_derives_only_one_resource_after_abort(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-single-derivation", topic="")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-single",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-single", "foreman"
            )
            lease = ComputerControlLease(home)
            recordings = RecordingStore(home, store, requests, authorities, lease, Mock())
            started = recordings.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-single",
                start_claim=start_claim,
                name="single",
            )
            recording_id = started["recording_id"]
            stop_claim = authorities.validate_recording_stop_owner(
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            recordings.abort(
                group.group_id,
                recording_id,
                actor_id="foreman",
                stop_claim=stop_claim,
            )
            authority_root = store.state_root(group.group_id) / "derived-authorities"
            recording_root = store.state_root(group.group_id) / "recordings"

            def snapshot() -> tuple[dict[str, bytes], dict[str, bytes], bytes | None]:
                return (
                    {path.name: path.read_bytes() for path in authority_root.glob("*.json")},
                    {path.name: path.read_bytes() for path in recording_root.glob("*.json")},
                    lease.path.read_bytes() if lease.path.exists() else None,
                )

            before = snapshot()
            with self.assertRaisesRegex(PermissionError, "root claim"):
                authorities.begin_recording(
                    group_id=group.group_id,
                    actor_id="foreman",
                    resource_id="rec_parallel",
                    request_id="req-single",
                    start_claim=start_claim,
                )
            self.assertEqual(snapshot(), before)
            with self.assertRaisesRegex(PermissionError, "already derived"):
                recordings.start(
                    group.group_id,
                    actor_id="foreman",
                    request_id="req-single",
                    start_claim=start_claim,
                    name="parallel",
                )
            self.assertEqual(snapshot(), before)

    def test_recording_service_restart_suspends_and_same_generation_receipt_can_resume(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-restart")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-restart",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-restart", "foreman"
            )
            lease = ComputerControlLease(home)
            first = RecordingStore(home, store, requests, authorities, lease, Mock())
            started = first.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-restart",
                start_claim=start_claim,
                name="restart",
            )
            recording_id = started["recording_id"]
            receipt = started["operation_receipt"]
            self.assertTrue(lease.status()["active"])

            restarted = RecordingStore(home, store, requests, authorities, lease, Mock())
            self.assertEqual(first._read(group.group_id, recording_id)["status"], "exploring")
            self._recover_recordings(home, restarted)
            suspended = restarted._read(group.group_id, recording_id, actor_id="foreman")
            self.assertEqual(suspended["status"], "suspended")
            self.assertEqual(suspended["suspend_reason"], "service_restart")
            self.assertFalse(lease.status()["active"])
            self.assertFalse(restarted._active)
            suspended_claim = authorities.validate_suspended_receipt(
                receipt,
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            resumed = restarted.resume(
                group.group_id,
                recording_id,
                actor_id="foreman",
                authority=suspended_claim,
            )
            self.assertEqual(resumed["status"], "exploring")
            self.assertTrue(lease.status()["active"])

    def test_recording_restart_finishes_suspended_authority_prefix_and_can_resume(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-suspended-prefix", topic="")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-suspended-prefix",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-suspended-prefix", "foreman"
            )
            lease = ComputerControlLease(home)
            first = RecordingStore(home, store, requests, authorities, lease, Mock())
            started = first.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-suspended-prefix",
                start_claim=start_claim,
                name="suspended prefix",
            )
            recording_id = started["recording_id"]
            active = authorities.validate_active_receipt(
                started["operation_receipt"],
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            authorities.suspend(active)
            first._active.clear()
            self.assertTrue(lease.status()["active"])

            restarted = RecordingStore(home, store, requests, authorities, lease, Mock())
            self.assertEqual(first._read(group.group_id, recording_id)["status"], "exploring")
            self._recover_recordings(home, restarted)

            recovered = restarted._read(group.group_id, recording_id, actor_id="foreman")
            self.assertEqual(recovered["status"], "suspended")
            self.assertFalse(lease.status()["active"])
            suspended = authorities.validate_suspended_receipt(
                started["operation_receipt"],
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            resumed = restarted.resume(
                group.group_id,
                recording_id,
                actor_id="foreman",
                authority=suspended,
            )
            self.assertEqual(resumed["status"], "exploring")

    def test_recording_restart_treats_terminating_authority_as_stop_prefix(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-terminating-prefix", topic="")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-terminating-prefix",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-terminating-prefix", "foreman"
            )
            lease = ComputerControlLease(home)
            first = RecordingStore(home, store, requests, authorities, lease, Mock())
            started = first.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-terminating-prefix",
                start_claim=start_claim,
                name="terminating prefix",
            )
            recording_id = started["recording_id"]
            authorities.begin_stop(
                group_id=group.group_id,
                actor_id="foreman",
                resource_id=recording_id,
            )
            first._active.clear()
            self.assertTrue(lease.status()["active"])

            restarted = RecordingStore(home, store, requests, authorities, lease, Mock())
            self.assertEqual(first._read(group.group_id, recording_id)["status"], "exploring")
            self._recover_recordings(home, restarted)

            recovered = restarted._read(group.group_id, recording_id, actor_id="foreman")
            self.assertEqual(recovered["status"], "aborted")
            self.assertEqual(
                authorities.persisted_record(group.group_id, recording_id)["state"],
                "revoked",
            )
            self.assertFalse(lease.status()["active"])

    def test_recording_restart_converges_initializing_and_orphan_pending_prefixes(self):
        for phase in ("orphan_pending", "initializing_pending", "initializing_active"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
                home = Path(td)
                group = create_group(load_registry(), title=f"recording-{phase}", topic="")
                store = WorkflowStore(home)
                requests = ComputerRequestStore(store)
                request_id = f"req-{phase}"
                requests.append(
                    group.group_id,
                    {
                        "request_id": request_id,
                        "actor_id": "foreman",
                        "mode": "create_and_run",
                        "status": "accepted",
                        "created_ts": time.time(),
                    },
                )
                authorities, start_claim = self._recording_security(
                    home, requests, group.group_id, request_id, "foreman"
                )
                lease = ComputerControlLease(home)
                recording_id = "rec_" + phase.replace("_", "")
                issue = authorities.begin_recording(
                    group_id=group.group_id,
                    actor_id="foreman",
                    resource_id=recording_id,
                    request_id=request_id,
                    start_claim=start_claim,
                )
                if phase != "orphan_pending":
                    recording_path = store.state_root(group.group_id) / "recordings" / f"{recording_id}.json"
                    recording_path.parent.mkdir(parents=True)
                    recording_path.write_text(
                        json.dumps(
                            {
                                "recording_id": recording_id,
                                "group_id": group.group_id,
                                "actor_id": "foreman",
                                "request_id": request_id,
                                "status": "initializing",
                            }
                        ),
                        encoding="utf-8",
                    )
                    lease.reserve(
                        group_id=group.group_id,
                        actor_id="foreman",
                        run_id=recording_id,
                        authority=issue.claim,
                    )
                    if phase == "initializing_active":
                        active = authorities.activate(issue.claim)
                        lease.activate_reservation(pending=issue.claim, active=active)

                restarted = RecordingStore(home, store, requests, authorities, lease, Mock())
                self._recover_recordings(home, restarted)

                self.assertEqual(
                    authorities.persisted_record(group.group_id, recording_id)["state"],
                    "revoked",
                )
                self.assertFalse(lease.status()["active"])
                if phase == "orphan_pending":
                    self.assertFalse(
                        (store.state_root(group.group_id) / "recordings" / f"{recording_id}.json").exists()
                    )
                else:
                    recovered = restarted._read(group.group_id, recording_id, actor_id="foreman")
                    self.assertEqual(recovered["status"], "start_failed")

    def test_recording_restart_does_not_release_other_group_with_same_resource_id(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-owner-g1", topic="")
            other_group = create_group(load_registry(), title="recording-owner-g2", topic="")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-owner-g1",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-owner-g1", "foreman"
            )
            lease = ComputerControlLease(home)
            first = RecordingStore(home, store, requests, authorities, lease, Mock())
            started = first.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-owner-g1",
                start_claim=start_claim,
                name="owner g1",
            )
            recording_id = started["recording_id"]
            active_g1 = authorities.validate_active_receipt(
                started["operation_receipt"],
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            self.assertTrue(lease.release(run_id=recording_id, authority=active_g1))
            first._active.clear()

            other_requests = ComputerRequestStore(store)
            other_requests.append(
                other_group.group_id,
                {
                    "request_id": "req-owner-g2",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            other_authorities, other_start_claim = self._recording_security(
                home, other_requests, other_group.group_id, "req-owner-g2", "foreman"
            )
            other_issue = other_authorities.begin_recording(
                group_id=other_group.group_id,
                actor_id="foreman",
                resource_id=recording_id,
                request_id="req-owner-g2",
                start_claim=other_start_claim,
            )
            other_active = other_authorities.activate(other_issue.claim)
            lease.acquire(
                group_id=other_group.group_id,
                actor_id="foreman",
                run_id=recording_id,
                authority=other_active,
            )

            restarted = RecordingStore(home, store, requests, authorities, lease, Mock())
            self.assertEqual(first._read(group.group_id, recording_id)["status"], "exploring")
            self._recover_recordings(home, restarted)

            self.assertEqual(
                restarted._read(group.group_id, recording_id, actor_id="foreman")["status"],
                "suspended",
            )
            lease_value = lease.status()["lease"]
            self.assertEqual(lease_value["group_id"], other_group.group_id)
            self.assertEqual(lease_value["actor_id"], "foreman")
            self.assertEqual(lease_value["run_id"], recording_id)

    def test_recording_restart_finishes_revoked_terminating_crash_prefix(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-stop-recovery", topic="")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-stop-recovery",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-stop-recovery", "foreman"
            )
            lease = ComputerControlLease(home)
            first = RecordingStore(home, store, requests, authorities, lease, Mock())
            started = first.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-stop-recovery",
                start_claim=start_claim,
                name="stop recovery",
            )
            recording_id = started["recording_id"]
            active = authorities.validate_active_receipt(
                started["operation_receipt"],
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            value = first._read(group.group_id, recording_id, actor_id="foreman")
            value.update(
                {
                    "status": "terminating",
                    "abort_reason": "user_abort",
                    "updated_at": time.time(),
                }
            )
            first._write(group.group_id, value)
            first._active.clear()
            self.assertTrue(lease.release(run_id=recording_id, authority=active))
            terminating = authorities.begin_termination(active)
            authorities.revoke(terminating)

            restarted = RecordingStore(home, store, requests, authorities, lease, Mock())
            self._recover_recordings(home, restarted)

            recovered = restarted._read(group.group_id, recording_id, actor_id="foreman")
            self.assertEqual(recovered["status"], "aborted")
            self.assertEqual(recovered["abort_reason"], "user_abort")
            self.assertEqual(
                authorities.persisted_record(group.group_id, recording_id)["state"],
                "revoked",
            )
            self.assertFalse(lease.status()["active"])

    def test_recording_restart_ignores_payload_identity_that_disagrees_with_path(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            recordings_root = (
                home / "groups" / "g_path" / "state" / "computer-control" / "recordings"
            )
            recordings_root.mkdir(parents=True)
            traversal_group = f"../../recording_escape_{home.name}"
            outside_root = (
                home
                / "groups"
                / traversal_group
                / "state"
                / "computer-control"
                / "derived-authorities"
            ).resolve()
            (recordings_root / "rec_group.json").write_text(
                json.dumps(
                    {
                        "group_id": traversal_group,
                        "recording_id": "rec_group",
                        "actor_id": "foreman",
                        "status": "terminating",
                    }
                ),
                encoding="utf-8",
            )
            (recordings_root / "rec_path.json").write_text(
                json.dumps(
                    {
                        "group_id": "g_path",
                        "recording_id": "rec_other",
                        "actor_id": "foreman",
                        "status": "exploring",
                    }
                ),
                encoding="utf-8",
            )
            authorities = Mock()
            lease = Mock()
            workflows = WorkflowStore(home)
            requests = ComputerRequestStore(workflows)
            self.assertFalse(outside_root.exists())

            restarted = RecordingStore(home, workflows, requests, authorities, lease, Mock())
            self._recover_recordings(home, restarted)

            authorities.begin_stop.assert_not_called()
            authorities.suspend_after_restart.assert_not_called()
            lease.release_recording_for_stop.assert_not_called()
            self.assertFalse(outside_root.exists())

    def test_recording_abort_waits_for_inflight_before_releasing_old_lease_lineage(self):
        class BlockingSession:
            transport_restarts = 0

            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()

            def catalog_sync(self):
                return [{"name": "Snapshot"}]

            def call_tool_sync(self, name, arguments, *, timeout):
                self.entered.set()
                self.release.wait(5)
                return {"content": [{"type": "text", "text": "done"}]}

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
            home = Path(td)
            group = create_group(load_registry(), title="recording-abort")
            store = WorkflowStore(home)
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-abort",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                },
            )
            authorities, start_claim = self._recording_security(
                home, requests, group.group_id, "req-abort", "foreman"
            )
            lease = ComputerControlLease(home)
            session = BlockingSession()
            recordings = RecordingStore(home, store, requests, authorities, lease, session)
            started = recordings.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-abort",
                start_claim=start_claim,
                name="abort",
            )
            recording_id = started["recording_id"]
            receipt = started["operation_receipt"]
            authority = authorities.validate_active_receipt(
                receipt,
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            errors = []

            def invoke() -> None:
                try:
                    recordings.call(
                        group.group_id,
                        recording_id,
                        actor_id="foreman",
                        authority=authority,
                        tool="Snapshot",
                        arguments={},
                        record=False,
                    )
                except Exception as exc:
                    errors.append(exc)

            worker = threading.Thread(target=invoke)
            worker.start()
            self.assertTrue(session.entered.wait(2))
            stop_claim = authorities.validate_recording_stop_owner(
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            stopping = recordings.abort(
                group.group_id,
                recording_id,
                actor_id="foreman",
                stop_claim=stop_claim,
                reason="user_abort",
            )
            self.assertEqual(stopping["status"], "terminating")
            self.assertTrue(lease.status()["active"])
            with self.assertRaises(PermissionError):
                authorities.validate_active_receipt(
                    receipt,
                    expected_group_id=group.group_id,
                    expected_actor_id="foreman",
                    expected_resource_id=recording_id,
                    expected_kind="recording",
                )

            session.release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(errors)
            self.assertEqual(
                recordings._read(group.group_id, recording_id, actor_id="foreman")["status"],
                "aborted",
            )
            self.assertFalse(lease.status()["active"])
            with self.assertRaises(PermissionError):
                authorities.validate_active_receipt(
                    receipt,
                    expected_group_id=group.group_id,
                    expected_actor_id="foreman",
                    expected_resource_id=recording_id,
                    expected_kind="recording",
                )

    def test_computer_artifacts_are_exposed_as_mcp_images_with_path_containment(self):
        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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
            authorities, start_claim = self._recording_security(
                Path(td), requests, group.group_id, "req-record", "foreman"
            )
            session = FakeSession()
            recordings = RecordingStore(
                Path(td),
                store,
                requests,
                authorities,
                ComputerControlLease(Path(td)),
                session,
            )
            value = recordings.start(
                group.group_id,
                actor_id="foreman",
                request_id="req-record",
                start_claim=start_claim,
                name="recorded task",
                inputs={"message": {"type": "string", "default": "hello"}},
            )
            recording_id = value["recording_id"]
            operation_receipt = value["operation_receipt"]
            authority = authorities.validate_active_receipt(
                operation_receipt,
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            suspended = recordings.suspend(
                group.group_id,
                recording_id,
                actor_id="foreman",
                authority=authority,
            )
            self.assertEqual(suspended["status"], "suspended")
            suspended_authority = authorities.validate_suspended_receipt(
                operation_receipt,
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            resumed = recordings.resume(
                group.group_id,
                recording_id,
                actor_id="foreman",
                authority=suspended_authority,
            )
            authority = authorities.validate_active_receipt(
                operation_receipt,
                expected_group_id=group.group_id,
                expected_actor_id="foreman",
                expected_resource_id=recording_id,
                expected_kind="recording",
            )
            self.assertTrue(resumed["requires_snapshot_baseline"])
            with self.assertRaisesRegex(RuntimeError, "Snapshot"):
                recordings.call(group.group_id, recording_id, actor_id="foreman", authority=authority, tool="Type", arguments={"text": "early"})
            recordings.call(group.group_id, recording_id, actor_id="foreman", authority=authority, tool="Snapshot", arguments={}, record=False)
            self.assertEqual(len(recordings.get(group.group_id, recording_id, actor_id="foreman", authority=authority)["steps"]), 0)
            with self.assertRaisesRegex(ValueError, "do not resolve"):
                recordings.call(
                    group.group_id,
                    recording_id,
                    actor_id="foreman",
                    authority=authority,
                    tool="Type",
                    arguments={"text": "different"},
                    workflow_arguments={"text": "${inputs.message}"},
                )
            with self.assertRaisesRegex(RuntimeError, "failed"):
                recordings.call(group.group_id, recording_id, actor_id="foreman", authority=authority, tool="Type", arguments={"fail": True})
            recordings.call(
                group.group_id,
                recording_id,
                actor_id="foreman",
                authority=authority,
                tool="Type",
                arguments={"text": "hello"},
                workflow_arguments={"text": "${inputs.message}"},
            )
            committed = recordings.commit(
                group.group_id,
                recording_id,
                actor_id="foreman",
                authority=authority,
            )
            self.assertEqual(len(committed["recording"]["steps"]), 1)
            self.assertEqual(committed["workflow"]["definition"]["nodes"][1]["arguments"]["text"], "${inputs.message}")
            runner = WorkflowRunner(Path(td), store, ComputerControlLease(Path(td)), session)
            self._authorize_legacy_runner(Path(td), runner)
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
    def test_replay_awaits_verification_then_auto_publishes_and_trusts(self):
        class ReplaySession:
            async def call_tool(self, name, arguments, *, timeout):
                return {"sent": arguments["text"]}

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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
            self._authorize_legacy_runner(Path(td), runner)
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
    def test_transport_failure_does_not_enter_adaptive_recovery(self):
        from no1.computer_control.mcp import MCPUnavailable

        class BrokenSession:
            transport_restarts = 1

            async def call_tool(self, name, arguments, *, timeout):
                raise MCPUnavailable("transport lost")

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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
            self._authorize_legacy_runner(Path(td), runner)
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
    def test_tool_error_result_fails_replay_and_cannot_be_verified(self):
        class ToolErrorSession:
            async def call_tool(self, name, arguments, *, timeout):
                return {"isError": True, "content": [{"type": "text", "text": "Either loc or label must be provided."}]}

        with tempfile.TemporaryDirectory() as td, _canonical_home_env(td):
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
            self._authorize_legacy_runner(Path(td), runner)
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
            run_path = runner._run_path(group.group_id, run["run_id"])
            terminal_before = run_path.read_bytes()
            terminal_variants = []
            for changes in (
                {"status": "awaiting_verification"},
                {"group_id": "other"},
                {"origin": "manual_actor"},
            ):
                variant = json.loads(json.dumps(run))
                variant.update(changes)
                variant["metrics"] = {"replay_success": True}
                variant["error"] = None
                variant["events"][-1].update(
                    {"status": "completed", "result": {"isError": True}}
                )
                terminal_variants.append(variant)
            for variant in terminal_variants:
                with self.subTest(terminal_variant=variant["origin"], group=variant["group_id"]):
                    with self.assertRaises(AttributeError):
                        runner._write(group.group_id, variant)
                    self.assertEqual(run_path.read_bytes(), terminal_before)
            with self.assertRaises(PermissionError):
                runner.verify(group.group_id, run["run_id"], actor_id="a", passed=True, summary="", evidence_ids=[], fingerprint="fp")
            persisted = runner.get(group.group_id, run["run_id"])
            self.assertEqual(persisted["status"], "failed")
            self.assertEqual(persisted["events"][-1]["status"], "failed")

            active_definition = WorkflowDefinition.model_validate({
                "name": "active raw writer guard",
                "auto_verify": False,
                "nodes": [
                    {"id": "s", "type": "start"},
                    {"id": "w", "type": "wait", "duration_seconds": 30},
                    {"id": "e", "type": "end"},
                ],
                "edges": [{"source": "s", "target": "w"}, {"source": "w", "target": "e"}],
            })
            active_workflow = store.create(group.group_id, active_definition)
            active = runner.start_sync(
                group.group_id,
                active_workflow["manifest"]["workflow_id"],
                actor_id="a",
                version=1,
                inputs={},
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                active = runner.get(group.group_id, active["run_id"])
                if active.get("current_node_id") == "w":
                    break
                time.sleep(0.02)
            self.assertEqual(active.get("current_node_id"), "w")
            active_path = runner._run_path(group.group_id, active["run_id"])
            active_before = active_path.read_bytes()
            active_variant = json.loads(json.dumps(active))
            active_variant.update(
                {"origin": "manual_actor", "group_id": "other", "status": "completed"}
            )
            for write_group in (group.group_id, "other"):
                with self.subTest(active_write_group=write_group):
                    with self.assertRaises(AttributeError):
                        runner._write(write_group, active_variant)
                    self.assertEqual(active_path.read_bytes(), active_before)
            runner.cancel_sync(group.group_id, active["run_id"])

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
        process = Mock(returncode=9009, pid=1234, stdout=None, stderr=None)
        process.wait = AsyncMock(return_value=9009)
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
            setup._ensure_python_313 = AsyncMock(return_value=root / "python.exe")
            setup._run_command = AsyncMock(return_value="")

            with patch("no1.computer_control.mcp.os.name", "nt"):
                await setup.ensure(force=True)
                result = await setup.wait()

            self.assertEqual(result["phase"], "ready")
            self.assertEqual(result["version"], "1.2.3")
            command = setup._run_command.await_args.args[0]
            self.assertIn("windows-mcp", command)
            self.assertFalse(any("windows-mcp==" in item for item in command))
            self.assertIn("--default-index", command)
            self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", command)
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
            index_args = ["--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"]
            self.assertEqual(calls[0], ["python", "-m", "pip", "install", "--user", *index_args, "uv"])
            self.assertEqual(calls[1], ["python", "-m", "pip", "install", *index_args, "uv"])

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
            self.assertEqual(
                calls[1],
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "--user",
                    "--index-url",
                    "https://pypi.tuna.tsinghua.edu.cn/simple",
                    "uv",
                ],
            )
            self.assertEqual(calls[2], [str(installed), "--version"])

    def test_package_index_prefers_explicit_https_configuration(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "no1.computer_control.mcp.os.environ",
            {"ONECOLLEAGUE_PYPI_INDEX_URL": "https://packages.example.test/simple/"},
            clear=True,
        ):
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            self.assertEqual(setup._package_index(), ("https://packages.example.test/simple", False))

    def test_invalid_or_insecure_package_index_uses_default_mirror(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "no1.computer_control.mcp.os.environ",
            {"ONECOLLEAGUE_PYPI_INDEX_URL": "http://packages.example.test/simple"},
            clear=True,
        ):
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            self.assertEqual(
                setup._package_index(),
                ("https://pypi.tuna.tsinghua.edu.cn/simple", True),
            )

    async def test_default_package_index_falls_back_to_official_pypi_for_same_attempt(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "no1.computer_control.mcp.os.environ", {}, clear=True
        ):
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            setup._begin_attempt("install")
            setup._run_command = AsyncMock(side_effect=[MCPUnavailable("connection timed out"), "installed", "next"])

            result = await setup._run_package_command(["uv", "tool", "install"], ["windows-mcp"], env={}, timeout=1)
            await setup._run_package_command(["uv", "tool", "upgrade"], ["windows-mcp"], env={}, timeout=1)

            self.assertEqual(result, "installed")
            calls = [call.args[0] for call in setup._run_command.await_args_list]
            self.assertIn("https://pypi.tuna.tsinghua.edu.cn/simple", calls[0])
            self.assertIn("https://pypi.org/simple", calls[1])
            self.assertIn("https://pypi.org/simple", calls[2])
            self.assertTrue(setup.status()["package_index_fallback"])

    async def test_private_package_index_never_falls_back_implicitly(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "no1.computer_control.mcp.os.environ",
            {"ONECOLLEAGUE_PYPI_INDEX_URL": "https://user:password@packages.example.test/simple"},
            clear=True,
        ):
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            setup._begin_attempt("install")
            setup._run_command = AsyncMock(side_effect=MCPUnavailable("connection timed out"))

            with self.assertRaisesRegex(MCPUnavailable, "connection timed out"):
                await setup._run_package_command(["uv", "tool", "install"], ["windows-mcp"], env={}, timeout=1)

            self.assertEqual(setup._run_command.await_count, 1)
            self.assertNotIn("password", setup.status()["package_index"])
            self.assertNotIn("password", "\n".join(setup.status()["logs"]))

    async def test_python_runtime_download_does_not_use_pypi_index(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(
            "no1.computer_control.mcp.os.environ",
            {"ONECOLLEAGUE_UV_PYTHON_MIRROR": "https://python.example.test/downloads"},
            clear=True,
        ):
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            python = setup.python_dir / "cpython-3.13" / "python.exe"
            calls = []

            async def run(command, **_kwargs):
                calls.append(command)
                if "install" in command:
                    python.parent.mkdir(parents=True, exist_ok=True)
                    python.write_bytes(b"")
                    return "installed"
                return str(python) if python.is_file() else ""

            setup._run_command = AsyncMock(side_effect=run)
            result = await setup._ensure_python_313(Path("uv.exe"), env={})

            self.assertEqual(result, python)
            install = next(command for command in calls if "install" in command)
            self.assertIn("--mirror", install)
            self.assertIn("https://python.example.test/downloads", install)
            self.assertNotIn("--default-index", install)
            self.assertNotIn("--index-url", install)

    async def test_command_output_is_visible_before_process_exits(self):
        first_line = asyncio.Event()
        finish = asyncio.Event()

        class Stream:
            def __init__(self):
                self.sent = False

            async def readline(self):
                if not self.sent:
                    self.sent = True
                    first_line.set()
                    return b"downloading package\n"
                await finish.wait()
                return b""

        process = Mock(pid=321, returncode=None, stdout=Stream(), stderr=None)

        async def wait():
            await finish.wait()
            process.returncode = 0
            return 0

        process.wait = AsyncMock(side_effect=wait)
        with tempfile.TemporaryDirectory() as td:
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            with patch("no1.computer_control.mcp.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                task = asyncio.create_task(setup._run_command(["uv", "tool", "install"], timeout=2))
                await asyncio.wait_for(first_line.wait(), timeout=1)
                await asyncio.sleep(0)
                self.assertIn("downloading package", "\n".join(setup.status()["logs"]))
                finish.set()
                self.assertIn("downloading package", await task)

    async def test_cancel_marks_running_setup_as_cancelled(self):
        started = asyncio.Event()

        async def wait_for_cancel():
            started.set()
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as td:
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            setup._ensure_uv = AsyncMock(side_effect=wait_for_cancel)
            with patch("no1.computer_control.mcp.os.name", "nt"):
                await setup.ensure(force=True)
                await asyncio.wait_for(started.wait(), timeout=1)
                result = await setup.cancel()

            self.assertEqual(result["phase"], "cancelled")
            self.assertFalse(result["in_progress"])
            self.assertFalse(result["can_cancel"])

    def test_stale_transient_setup_is_marked_interrupted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "state" / "computer-control" / "setup.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({"phase": "downloading", "owner_pid": os.getpid(), "detail": "installing_windows_mcp"}),
                encoding="utf-8",
            )

            setup = WindowsMCPSetup(root, _SetupSession(), WorkflowStore(root))

            self.assertEqual(setup.status()["phase"], "failed")
            self.assertEqual(setup.status()["error"]["code"], "setup_interrupted")

    async def test_completed_task_cannot_leave_setup_in_transient_phase(self):
        with tempfile.TemporaryDirectory() as td:
            setup = WindowsMCPSetup(Path(td), _SetupSession(), WorkflowStore(Path(td)))
            setup._status = {"phase": "downloading", "detail": "installing_windows_mcp"}
            setup._task = asyncio.create_task(asyncio.sleep(0))
            await setup._task

            result = setup.status()

            self.assertEqual(result["phase"], "failed")
            self.assertEqual(result["error"]["code"], "setup_task_stopped")


if __name__ == "__main__":
    unittest.main()
