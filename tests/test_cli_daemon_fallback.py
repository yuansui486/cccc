from __future__ import annotations

import os
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch


class TestCliDaemonFallback(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def _create_group(self) -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        group = create_group(reg, title="cli-daemon-fallback", topic="")
        return group.group_id

    def test_send_does_not_fallback_after_daemon_rejection(self) -> None:
        from cccc import cli

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            resp = {"ok": False, "error": {"code": "no_enabled_recipients", "message": "blocked by daemon"}}
            args = Namespace(
                group=group_id,
                text="hello",
                by="user",
                path="",
                to=[],
                priority="normal",
                reply_required=False,
            )

            with patch.object(cli, "_ensure_daemon_running", return_value=True), \
                 patch.object(cli, "call_daemon", return_value=resp), \
                 patch.object(cli, "append_event", side_effect=AssertionError("local append should not run")) as append_event, \
                 patch.object(cli, "_print_json") as print_json:
                code = cli.cmd_send(args)

            self.assertEqual(code, 2)
            append_event.assert_not_called()
            print_json.assert_called_once_with(resp)
        finally:
            cleanup()

    def test_send_keeps_local_fallback_for_daemon_unavailable(self) -> None:
        from cccc import cli

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            resp = {"ok": False, "error": {"code": "daemon_unavailable", "message": "daemon unavailable"}}
            args = Namespace(
                group=group_id,
                text="offline fallback",
                by="user",
                path="",
                to=[],
                priority="normal",
                reply_required=False,
            )

            with patch.object(cli, "_ensure_daemon_running", return_value=True), \
                 patch.object(cli, "call_daemon", return_value=resp), \
                 patch.object(cli, "_print_json") as print_json:
                code = cli.cmd_send(args)

            self.assertEqual(code, 0)
            printed = print_json.call_args.args[0]
            self.assertTrue(bool(printed.get("ok")))
            self.assertEqual(str(((printed.get("result") or {}).get("event") or {}).get("kind") or ""), "chat.message")
        finally:
            cleanup()

    def test_actor_add_does_not_fallback_after_daemon_rejection(self) -> None:
        from cccc import cli

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            resp = {"ok": False, "error": {"code": "permission_denied", "message": "blocked by daemon"}}
            args = Namespace(
                group=group_id,
                actor_id="peer1",
                title="Peer 1",
                by="user",
                submit="enter",
                runner="pty",
                runtime="codex",
                command="",
                env=[],
                scope="",
            )

            with patch.object(cli, "_ensure_daemon_running", return_value=True), \
                 patch.object(cli, "call_daemon", return_value=resp), \
                 patch.object(cli, "append_event", side_effect=AssertionError("local append should not run")) as append_event, \
                 patch.object(cli, "_print_json") as print_json:
                code = cli.cmd_actor_add(args)

            self.assertEqual(code, 2)
            append_event.assert_not_called()
            print_json.assert_called_once_with(resp)
        finally:
            cleanup()

    def test_group_update_does_not_fallback_after_daemon_rejection(self) -> None:
        from cccc import cli

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            resp = {"ok": False, "error": {"code": "permission_denied", "message": "blocked by daemon"}}
            args = Namespace(group=group_id, by="user", title="new-title", topic=None)

            with patch.object(cli, "_ensure_daemon_running", return_value=True), \
                 patch.object(cli, "call_daemon", return_value=resp), \
                 patch.object(cli, "append_event", side_effect=AssertionError("local append should not run")) as append_event, \
                 patch.object(cli, "_print_json") as print_json:
                code = cli.cmd_group_update(args)

            self.assertEqual(code, 2)
            append_event.assert_not_called()
            print_json.assert_called_once_with(resp)
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
