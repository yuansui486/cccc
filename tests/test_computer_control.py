import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from no1.computer_control.lease import ComputerControlLease, LeaseConflict
from no1.computer_control.mcp import WindowsMCPSetup
from no1.computer_control.models import WorkflowDefinition, WorkflowTrigger
from no1.computer_control.requests import ComputerRequestStore
from no1.computer_control.risk import annotate_catalog, workflow_risk
from no1.computer_control.storage import RevisionConflict, WorkflowStore
from no1.computer_control.triggers import should_confirm_element, validate_trigger
from no1.daemon.messaging.actor_turn_rendering import build_actor_delivery_text
from no1.kernel.group import create_group
from no1.kernel.registry import load_registry


class TestComputerControl(unittest.TestCase):
    def test_workflow_graph_and_secret_constraints(self):
        with self.assertRaises(ValueError):
            WorkflowDefinition.model_validate({"name": "bad", "nodes": [{"id": "s", "type": "start"}, {"id": "a", "type": "action", "tool": "x", "arguments": {"password": "plain"}}, {"id": "e", "type": "end"}], "edges": [{"source": "s", "target": "a"}, {"source": "a", "target": "e"}]})
        with self.assertRaises(ValueError):
            WorkflowDefinition.model_validate({"name": "loop", "nodes": [{"id": "s", "type": "start"}, {"id": "l", "type": "loop"}, {"id": "e", "type": "end"}], "edges": [{"source": "s", "target": "l"}, {"source": "l", "target": "e"}]})

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
        self.assertIn("onecolleague_computer_workflow", rendered)


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
            setup._find_uv = Mock(side_effect=[None, installed])
            setup._python_commands = Mock(return_value=[["python"]])
            setup._run_command = AsyncMock(side_effect=[RuntimeError("user site disabled"), ""])

            result = await setup._ensure_uv()

            self.assertEqual(result, installed)
            calls = [call.args[0] for call in setup._run_command.await_args_list]
            self.assertEqual(calls[0], ["python", "-m", "pip", "install", "--user", "uv"])
            self.assertEqual(calls[1], ["python", "-m", "pip", "install", "uv"])


if __name__ == "__main__":
    unittest.main()
