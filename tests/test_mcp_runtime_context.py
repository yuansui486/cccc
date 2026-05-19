from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestMcpRuntimeContext(unittest.TestCase):
    def test_iter_ancestor_pids_uses_windows_parent_chain(self) -> None:
        from no1.ports.mcp.common import _iter_ancestor_pids

        chain = {900: 700, 700: 500, 500: 0}
        with patch("no1.ports.mcp.common.os.name", "nt"), patch(
            "no1.ports.mcp.common.os.getpid",
            return_value=900,
        ), patch(
            "no1.ports.mcp.common._proc_parent_pid_windows",
            side_effect=lambda pid: chain.get(pid, 0),
        ):
            self.assertEqual(_iter_ancestor_pids(), [900, 700, 500])

    def test_runtime_context_recovers_from_ancestor_env(self) -> None:
        from no1.ports.mcp.common import _runtime_context

        fake_home = Path("/tmp/onecolleague-runtime-home").resolve()

        def _fake_proc_environ(pid: int) -> dict[str, str]:
            if pid == 42:
                return {
                    "CCCC_HOME": str(fake_home),
                    "CCCC_GROUP_ID": "g_ancestor",
                    "CCCC_ACTOR_ID": "foreman-ancestor",
                }
            return {}

        with patch.dict(os.environ, {}, clear=True), patch(
            "no1.ports.mcp.common._iter_ancestor_pids",
            return_value=[100, 42, 1],
        ), patch(
            "no1.ports.mcp.common._proc_environ",
            side_effect=_fake_proc_environ,
        ), patch(
            "no1.ports.mcp.common.onecolleague_home",
            return_value=fake_home,
        ):
            ctx = _runtime_context()

        self.assertEqual(ctx.home, str(fake_home))
        self.assertEqual(ctx.group_id, "g_ancestor")
        self.assertEqual(ctx.actor_id, "foreman-ancestor")

    def test_runtime_context_explicit_empty_env_suppresses_ancestor_recovery(self) -> None:
        from no1.ports.mcp.common import _runtime_context

        fake_home = Path("/tmp/onecolleague-runtime-home").resolve()

        def _fake_proc_environ(pid: int) -> dict[str, str]:
            if pid == 42:
                return {
                    "CCCC_HOME": str(fake_home),
                    "CCCC_GROUP_ID": "g_ancestor",
                    "CCCC_ACTOR_ID": "foreman-ancestor",
                }
            return {}

        with patch.dict(os.environ, {"CCCC_HOME": "", "CCCC_GROUP_ID": "", "CCCC_ACTOR_ID": ""}, clear=True), patch(
            "no1.ports.mcp.common._iter_ancestor_pids",
            return_value=[100, 42, 1],
        ), patch(
            "no1.ports.mcp.common._proc_environ",
            side_effect=_fake_proc_environ,
        ), patch(
            "no1.ports.mcp.common.onecolleague_home",
            return_value=fake_home,
        ):
            ctx = _runtime_context()

        self.assertEqual(ctx.home, str(fake_home))
        self.assertEqual(ctx.group_id, "")
        self.assertEqual(ctx.actor_id, "")

    def test_runtime_context_falls_back_to_pty_state(self) -> None:
        from no1.ports.mcp.common import _runtime_context

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            state_path = home / "groups" / "g_state" / "state" / "runners" / "pty" / "管理员.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "v": 1,
                        "kind": "pty",
                        "group_id": "g_state",
                        "actor_id": "管理员",
                        "pid": 42,
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True), patch(
                "no1.ports.mcp.common._iter_ancestor_pids",
                return_value=[100, 42, 1],
            ), patch(
                "no1.ports.mcp.common._proc_environ",
                return_value={},
            ), patch(
                "no1.ports.mcp.common.onecolleague_home",
                return_value=home,
            ):
                ctx = _runtime_context()

        self.assertEqual(ctx.home, str(home))
        self.assertEqual(ctx.group_id, "g_state")
        self.assertEqual(ctx.actor_id, "管理员")

    def test_runtime_context_explicit_empty_env_suppresses_pty_state_recovery(self) -> None:
        from no1.ports.mcp.common import _runtime_context

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            state_path = home / "groups" / "g_state" / "state" / "runners" / "pty" / "管理员.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "v": 1,
                        "kind": "pty",
                        "group_id": "g_state",
                        "actor_id": "管理员",
                        "pid": 42,
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"CCCC_HOME": "", "CCCC_GROUP_ID": "", "CCCC_ACTOR_ID": ""}, clear=True), patch(
                "no1.ports.mcp.common._iter_ancestor_pids",
                return_value=[100, 42, 1],
            ), patch(
                "no1.ports.mcp.common._proc_environ",
                return_value={},
            ), patch(
                "no1.ports.mcp.common.onecolleague_home",
                return_value=home,
            ):
                ctx = _runtime_context()

        self.assertEqual(ctx.home, str(home))
        self.assertEqual(ctx.group_id, "")
        self.assertEqual(ctx.actor_id, "")

    def test_call_daemon_uses_recovered_home(self) -> None:
        from no1.ports.mcp.common import _RuntimeContext, _call_daemon_or_raise

        fake_home = Path("/tmp/onecolleague-daemon-home").resolve()
        captured: dict[str, object] = {}

        def _fake_call_daemon(req, **kwargs):
            captured["req"] = req
            captured["kwargs"] = kwargs
            return {"ok": True, "result": {"pong": True}}

        with patch(
            "no1.ports.mcp.common._runtime_context",
            return_value=_RuntimeContext(home=str(fake_home), group_id="g1", actor_id="a1"),
        ), patch(
            "no1.ports.mcp.common.call_daemon",
            side_effect=_fake_call_daemon,
        ):
            out = _call_daemon_or_raise({"op": "ping"})

        self.assertEqual(out.get("pong"), True)
        kwargs = captured.get("kwargs") if isinstance(captured.get("kwargs"), dict) else {}
        paths = kwargs.get("paths")
        self.assertEqual(str(getattr(paths, "home", "")), str(fake_home))

    def test_actor_add_uses_recovered_context_when_env_missing(self) -> None:
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import _RuntimeContext

        captured = {}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"ok": True}}

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "", "CCCC_ACTOR_ID": ""}, clear=False), patch(
            "no1.ports.mcp.common._runtime_context",
            return_value=_RuntimeContext(home="/tmp/onecolleague", group_id="g_runtime", actor_id="管理员"),
        ), patch.object(
            mcp_common,
            "call_daemon",
            side_effect=_fake_call_daemon,
        ):
            out = mcp_server.handle_tool_call(
                "onecolleague_actor",
                {
                    "action": "add",
                    "actor_id": "peer_new",
                    "runtime": "codex",
                    "runner": "pty",
                },
            )

        self.assertEqual(out.get("ok"), True)
        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(req.get("op"), "actor_add")
        self.assertEqual(args.get("group_id"), "g_runtime")
        self.assertEqual(args.get("by"), "管理员")

    def test_onecolleague_help_uses_recovered_actor_notes_when_env_missing(self) -> None:
        from no1.kernel.prompt_files import PromptFile
        from no1.ports.mcp.common import _RuntimeContext
        from no1.ports.mcp.server import handle_tool_call

        prompt = "## @actor: 管理员\n只有你知道的密码base1234\n"

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "", "CCCC_ACTOR_ID": ""}, clear=False), patch(
            "no1.ports.mcp.server._runtime_context",
            return_value=_RuntimeContext(home="/tmp/onecolleague", group_id="g_help", actor_id="管理员"),
        ), patch(
            "no1.ports.mcp.server.load_group",
            return_value=object(),
        ), patch(
            "no1.ports.mcp.server.get_effective_role",
            return_value="foreman",
        ), patch(
            "no1.ports.mcp.server.read_group_prompt_file",
            return_value=PromptFile(
                filename="ONECOLLEAGUE_HELP.md",
                path="/tmp/ONECOLLEAGUE_HELP.md",
                found=True,
                content=prompt,
            ),
        ), patch(
            "no1.ports.mcp.server._append_runtime_help_addenda",
            side_effect=lambda markdown, group_id, actor_id: markdown,
        ), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={},
        ):
            out = handle_tool_call("onecolleague_help", {})

        markdown = str(out.get("markdown") or "")
        self.assertIn("## Notes for you", markdown)
        self.assertIn("只有你知道的密码base1234", markdown)


if __name__ == "__main__":
    unittest.main()
