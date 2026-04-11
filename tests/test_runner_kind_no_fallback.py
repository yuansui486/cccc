from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestRunnerKindNoFallback(unittest.TestCase):
    def test_effective_runner_kind_stays_pty_even_if_backend_flag_is_false(self) -> None:
        from cccc.daemon import server as daemon_server
        from cccc.daemon.actors import runner_ops

        with patch.object(daemon_server.pty_runner, "PTY_SUPPORTED", False, create=False), patch.object(
            runner_ops.pty_runner, "PTY_SUPPORTED", False, create=False
        ):
            self.assertEqual(daemon_server._effective_runner_kind("pty"), "pty")
            self.assertEqual(daemon_server._effective_runner_kind("headless"), "headless")
            self.assertEqual(runner_ops._effective_runner_kind("pty"), "pty")
            self.assertEqual(runner_ops._effective_runner_kind("headless"), "headless")

    def test_group_start_fails_instead_of_falling_back_to_headless(self) -> None:
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td
        try:
            from cccc.contracts.v1 import DaemonRequest
            from cccc.daemon.server import handle_request, pty_runner as server_pty_runner
            from cccc.kernel.actors import add_actor
            from cccc.kernel.group import load_group

            def call(op: str, args: dict):
                return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

            create, _ = call("group_create", {"title": "win-no-fallback", "topic": "", "by": "user"})
            self.assertTrue(create.ok, getattr(create, "error", None))
            group_id = str((create.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            attach, _ = call("attach", {"group_id": group_id, "path": ".", "by": "user"})
            self.assertTrue(attach.ok, getattr(attach, "error", None))

            group = load_group(group_id)
            assert group is not None
            add_actor(
                group,
                actor_id="peer1",
                title="Peer 1",
                command=["echo", "hello"],
                runner="pty",
                runtime="custom",
            )

            with patch.object(server_pty_runner, "PTY_SUPPORTED", False, create=False), patch(
                "cccc.daemon.server._ensure_mcp_installed", return_value=True
            ), patch(
                "cccc.daemon.group.group_lifecycle_ops.pty_runner.SUPERVISOR.start_actor",
                side_effect=RuntimeError("pty backend unavailable"),
            ):
                start, _ = call("group_start", {"group_id": group_id, "by": "user"})

            self.assertFalse(start.ok)
            self.assertEqual(getattr(start.error, "code", ""), "group_start_failed")
            self.assertIn("pty backend unavailable", getattr(start.error, "message", ""))
        finally:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_codex_pty_uses_pty_supervisor_for_running_and_stop(self) -> None:
        from cccc.daemon.actors import runner_ops

        fake_group = object()
        with patch.object(runner_ops, "load_group", return_value=fake_group), patch.object(
            runner_ops, "find_actor", return_value={"runtime": "codex", "runner": "pty"}
        ), patch.object(
            runner_ops.codex_app_supervisor, "actor_running", return_value=False
        ) as codex_running, patch.object(
            runner_ops.pty_runner.SUPERVISOR, "actor_running", return_value=True
        ) as pty_running:
            self.assertTrue(runner_ops.is_actor_running("g1", "a1", "pty"))
            codex_running.assert_not_called()
            pty_running.assert_called_once_with("g1", "a1")

        with patch.object(runner_ops, "load_group", return_value=fake_group), patch.object(
            runner_ops, "find_actor", return_value={"runtime": "codex", "runner": "pty"}
        ), patch.object(
            runner_ops.codex_app_supervisor, "stop_actor"
        ) as codex_stop, patch.object(
            runner_ops.pty_runner.SUPERVISOR, "stop_actor"
        ) as pty_stop:
            runner_ops.stop_actor("g1", "a1", "pty")
            codex_stop.assert_not_called()
            pty_stop.assert_called_once_with(group_id="g1", actor_id="a1")


if __name__ == "__main__":
    unittest.main()
