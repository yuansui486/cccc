import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestDiagnosticsOps(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home
            td_ctx.__exit__(None, None, None)

        return td, cleanup

    def _call(self, op: str, args: dict):
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def test_debug_ops_require_developer_mode(self) -> None:
        _, cleanup = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": False}})
            self.assertTrue(update.ok, getattr(update, "error", None))
            resp, _ = self._call("debug_snapshot", {"by": "user"})
            self.assertFalse(resp.ok)
            self.assertEqual(str(getattr(resp, "error", None).code), "developer_mode_required")
        finally:
            cleanup()

    def test_debug_tail_logs_invalid_component(self) -> None:
        _, cleanup = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": True}})
            self.assertTrue(update.ok, getattr(update, "error", None))

            tail, _ = self._call("debug_tail_logs", {"by": "user", "component": "unknown"})
            self.assertFalse(tail.ok)
            self.assertEqual(str(getattr(tail, "error", None).code), "invalid_component")
        finally:
            cleanup()

    def test_debug_tail_logs_reads_plain_log_files(self) -> None:
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        td, cleanup = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": True}})
            self.assertTrue(update.ok, getattr(update, "error", None))

            home = Path(td)
            daemon_dir = home / "daemon"
            daemon_dir.mkdir(parents=True, exist_ok=True)
            (daemon_dir / "onecolleagued.log").write_text("daemon-1\ndaemon-2\ndaemon-3\n", encoding="utf-8")
            (daemon_dir / "onecolleague-web.log").write_text("web-1\nweb-2\n", encoding="utf-8")

            reg = load_registry()
            group = create_group(reg, title="diag-logs")
            im_log = home / "groups" / group.group_id / "state" / "im_bridge.log"
            im_log.parent.mkdir(parents=True, exist_ok=True)
            im_log.write_text("im-1\nim-2\nim-3\n", encoding="utf-8")

            daemon_tail, _ = self._call("debug_tail_logs", {"by": "user", "component": "daemon", "lines": 2})
            self.assertTrue(daemon_tail.ok, getattr(daemon_tail, "error", None))
            self.assertEqual((daemon_tail.result or {}).get("lines"), ["daemon-2", "daemon-3"])

            web_tail, _ = self._call("debug_tail_logs", {"by": "user", "component": "web", "lines": 2})
            self.assertTrue(web_tail.ok, getattr(web_tail, "error", None))
            self.assertEqual((web_tail.result or {}).get("lines"), ["web-1", "web-2"])

            im_tail, _ = self._call(
                "debug_tail_logs",
                {"by": "user", "component": "im", "group_id": group.group_id, "lines": 2},
            )
            self.assertTrue(im_tail.ok, getattr(im_tail, "error", None))
            self.assertEqual((im_tail.result or {}).get("lines"), ["im-2", "im-3"])
        finally:
            cleanup()

    def test_debug_clear_logs_im_requires_group(self) -> None:
        _, cleanup = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": True}})
            self.assertTrue(update.ok, getattr(update, "error", None))

            clear, _ = self._call("debug_clear_logs", {"by": "user", "component": "im"})
            self.assertFalse(clear.ok)
            self.assertEqual(str(getattr(clear, "error", None).code), "missing_group_id")
        finally:
            cleanup()

    def test_try_handle_unknown_diagnostics_op_returns_none(self) -> None:
        from no1.daemon.ops.diagnostics_ops import try_handle_diagnostics_op

        resp = try_handle_diagnostics_op(
            "not_diagnostics",
            {},
            developer_mode_enabled=lambda: True,
            get_observability=lambda: {},
            effective_runner_kind=lambda runner: runner,
            throttle_debug_summary=lambda _group_id: {},
            can_read_terminal_transcript=lambda _group, _by, _target: False,
            pty_backlog_bytes=lambda: 1024,
        )
        self.assertIsNone(resp)

    def test_debug_snapshot_uses_app_supervisors_for_main_headless_runtimes(self) -> None:
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": True}})
            self.assertTrue(update.ok, getattr(update, "error", None))
            group = create_group(load_registry(), title="debug-headless-app-supervisors")
            add_actor(group, actor_id="codex-1", title="Codex", runtime="codex", runner="headless")
            add_actor(group, actor_id="claude-1", title="Claude", runtime="claude", runner="headless")
            add_actor(group, actor_id="gemini-1", title="Gemini", runtime="gemini", runner="headless")

            with (
                patch("no1.daemon.ops.diagnostics_ops.codex_app_supervisor.get_state", return_value={"thread_id": "thr"}),
                patch("no1.daemon.ops.diagnostics_ops.codex_app_supervisor.actor_running", return_value=True),
                patch("no1.daemon.ops.diagnostics_ops.claude_app_supervisor.get_state", return_value={"session_id": "sid"}),
                patch("no1.daemon.ops.diagnostics_ops.claude_app_supervisor.actor_running", return_value=True),
                patch(
                    "no1.daemon.ops.diagnostics_ops.headless_runner.SUPERVISOR.actor_running",
                    return_value=True,
                ) as generic_running,
            ):
                resp, _ = self._call("debug_snapshot", {"group_id": group.group_id, "by": "user"})

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            actors = {
                str(item.get("id") or ""): item
                for item in (resp.result or {}).get("actors", [])
                if isinstance(item, dict)
            }
            self.assertTrue(actors["codex-1"].get("running"))
            self.assertTrue(actors["claude-1"].get("running"))
            self.assertTrue(actors["gemini-1"].get("running"))
            generic_running.assert_called_once_with(group.group_id, "gemini-1")
        finally:
            cleanup()

    def test_debug_snapshot_includes_web_binding_runtime_evidence(self) -> None:
        from no1.ports.web.runtime_control import write_web_runtime_state

        td, cleanup = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": True}})
            self.assertTrue(update.ok, getattr(update, "error", None))

            cfg, _ = self._call(
                "remote_access_configure",
                {
                    "by": "user",
                    "provider": "manual",
                    "web_host": "0.0.0.0",
                    "web_port": 9001,
                },
            )
            self.assertTrue(cfg.ok, getattr(cfg, "error", None))

            write_web_runtime_state(
                home=Path(td),
                pid=os.getpid(),
                host="127.0.0.1",
                port=8848,
                mode="normal",
                supervisor_managed=True,
                supervisor_pid=os.getpid(),
                launcher_pid=os.getpid(),
                launch_source="test",
            )

            resp, _ = self._call("debug_snapshot", {"by": "user"})
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            web = (resp.result or {}).get("web") if isinstance(resp.result, dict) else {}
            configured = web.get("configured") if isinstance(web.get("configured"), dict) else {}
            runtime = web.get("runtime") if isinstance(web.get("runtime"), dict) else {}

            self.assertEqual(str(configured.get("host") or ""), "0.0.0.0")
            self.assertEqual(int(configured.get("port") or 0), 9001)
            self.assertEqual(str(configured.get("exposure_class") or ""), "private")
            self.assertEqual(str(runtime.get("host") or ""), "127.0.0.1")
            self.assertEqual(int(runtime.get("port") or 0), 8848)
            self.assertEqual(bool(runtime.get("pid_alive")), True)
            self.assertEqual(bool(web.get("runtime_matches_configured_binding")), False)
            self.assertIn("binding_apply_pending", web.get("issues") or [])
            self.assertTrue(str(web.get("log_path") or "").endswith("daemon/onecolleague-web.log"))
        finally:
            cleanup()

    def test_debug_snapshot_marks_stale_web_runtime_pid(self) -> None:
        from no1.ports.web.runtime_control import write_web_runtime_state

        td, cleanup = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": True}})
            self.assertTrue(update.ok, getattr(update, "error", None))

            write_web_runtime_state(
                home=Path(td),
                pid=2_147_483_647,
                host="127.0.0.1",
                port=8848,
                mode="normal",
                supervisor_managed=True,
                supervisor_pid=os.getpid(),
                launch_source="test",
            )

            resp, _ = self._call("debug_snapshot", {"by": "user"})
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            web = (resp.result or {}).get("web") if isinstance(resp.result, dict) else {}
            runtime = web.get("runtime") if isinstance(web.get("runtime"), dict) else {}

            self.assertEqual(int(runtime.get("pid") or 0), 2_147_483_647)
            self.assertEqual(bool(runtime.get("pid_alive")), False)
            self.assertIn("runtime_pid_stale", web.get("issues") or [])
        finally:
            cleanup()

    def test_terminal_tail_returns_stopped_actor_snapshot(self) -> None:
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            group = create_group(load_registry(), title="terminal-tail-stopped")
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="kimi", runner="pty")
            with patch(
                "no1.daemon.ops.diagnostics_ops.pty_runner.SUPERVISOR.tail_output",
                return_value=b"Error code: 401\ninvalid_authentication_error\n",
            ):
                resp, _ = self._call(
                    "terminal_tail",
                    {"group_id": group.group_id, "actor_id": "peer1", "strip_ansi": False},
                )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            self.assertEqual((resp.result or {}).get("text"), "Error code: 401\ninvalid_authentication_error\n")
        finally:
            cleanup()

    def test_terminal_history_forwards_cursor_limit_and_expired_state(self) -> None:
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            group = create_group(load_registry(), title="terminal-history-cursor")
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="pty")
            with patch(
                "no1.daemon.ops.diagnostics_ops.pty_runner.SUPERVISOR.history_page",
                return_value={
                    "data": b"",
                    "start_cursor": 20,
                    "end_cursor": 20,
                    "has_more": False,
                    "cursor_expired": True,
                },
            ) as history_page:
                resp, _ = self._call(
                    "terminal_history",
                    {
                        "group_id": group.group_id,
                        "actor_id": "peer1",
                        "before": 10,
                        "limit_bytes": 5,
                    },
                )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            self.assertTrue((resp.result or {}).get("cursor_expired"))
            self.assertEqual((resp.result or {}).get("start_cursor"), 20)
            history_page.assert_called_once_with(
                group_id=group.group_id,
                actor_id="peer1",
                before=10,
                limit_bytes=5,
            )
        finally:
            cleanup()

    def test_terminal_history_uses_transcript_permission_policy(self) -> None:
        from no1.daemon.ops.diagnostics_ops import handle_terminal_history
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            group = create_group(load_registry(), title="terminal-history-permission")
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="pty")
            resp = handle_terminal_history(
                {"group_id": group.group_id, "actor_id": "peer1", "by": "peer2"},
                can_read_terminal_transcript=lambda _group, _by, _target: False,
                pty_backlog_bytes=lambda: 4096,
            )

            self.assertFalse(resp.ok)
            self.assertEqual(resp.error.code, "permission_denied")
        finally:
            cleanup()

    def test_terminal_history_rejects_non_pty_actor(self) -> None:
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            group = create_group(load_registry(), title="terminal-history-runner")
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="headless")
            resp, _ = self._call(
                "terminal_history",
                {"group_id": group.group_id, "actor_id": "peer1"},
            )

            self.assertFalse(resp.ok)
            self.assertEqual(resp.error.code, "not_pty_actor")
        finally:
            cleanup()

    def test_terminal_tail_strips_codex_working_status_lines(self) -> None:
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            group = create_group(load_registry(), title="terminal-tail-codex-status")
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="pty")
            with patch(
                "no1.daemon.ops.diagnostics_ops.pty_runner.SUPERVISOR.tail_output",
                return_value="◦ Working  9m 41s • esc to interrupt)\nvisible\n".encode(),
            ):
                resp, _ = self._call(
                    "terminal_tail",
                    {"group_id": group.group_id, "actor_id": "peer1", "strip_ansi": False},
                )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            self.assertEqual((resp.result or {}).get("text"), "visible")
        finally:
            cleanup()

    def test_terminal_tail_does_not_include_shared_runtime_log(self) -> None:
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        td, cleanup = self._with_home()
        try:
            shared_log = Path(td) / ".kimi" / "logs" / "kimi.log"
            shared_log.parent.mkdir(parents=True, exist_ok=True)
            shared_log.write_text("historical authentication error\n", encoding="utf-8")
            group = create_group(load_registry(), title="terminal-tail-shared-log")
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="kimi", runner="pty")
            with patch(
                "no1.daemon.ops.diagnostics_ops.pty_runner.SUPERVISOR.tail_output",
                return_value=b"Process exited with code 1 before producing terminal output.\n",
            ):
                resp, _ = self._call(
                    "terminal_tail",
                    {"group_id": group.group_id, "actor_id": "peer1", "strip_ansi": False},
                )

            text = str((resp.result or {}).get("text") or "")
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            self.assertEqual(text, "Process exited with code 1 before producing terminal output.\n")
            self.assertNotIn("historical authentication error", text)
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
