import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class _AliveProc:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return 0


class TestWebImStart(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = os.path.realpath(td_ctx.__enter__())
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def _create_group(self, title: str = "im-start") -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        group = create_group(reg, title=title, topic="")
        return group.group_id

    def test_im_start_uses_detached_child_process_defaults(self) -> None:
        from cccc.ports.web.app import create_app

        home, cleanup = self._with_home()
        try:
            gid = self._create_group("im-start-route")
            with TestClient(create_app()) as client:
                set_resp = client.post(
                    "/api/im/set",
                    json={
                        "group_id": gid,
                        "platform": "telegram",
                        "token_env": "TELEGRAM_BOT_TOKEN",
                    },
                )
                self.assertEqual(set_resp.status_code, 200)
                self.assertTrue(bool(set_resp.json().get("ok")))

                with patch("subprocess.Popen", return_value=_AliveProc()) as popen:
                    start_resp = client.post("/api/im/start", json={"group_id": gid})

                self.assertEqual(start_resp.status_code, 200)
                payload = start_resp.json()
                self.assertTrue(bool(payload.get("ok")))

            self.assertIsNotNone(popen.call_args)
            argv = list(popen.call_args.args[0])
            kwargs = dict(popen.call_args.kwargs)

            self.assertEqual(argv, [sys.executable, "-m", "cccc.ports.im", gid, "telegram"])
            self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)
            self.assertTrue(bool(kwargs.get("close_fds")))
            self.assertEqual(str(kwargs.get("cwd") or ""), home)
            self.assertIs(kwargs.get("stdout"), kwargs.get("stderr"))
            if os.name == "nt":
                self.assertIn("creationflags", kwargs)
                self.assertNotIn("start_new_session", kwargs)
            else:
                self.assertTrue(bool(kwargs.get("start_new_session")))

            pid_path = os.path.join(home, "groups", gid, "state", "im_bridge.pid")
            self.assertTrue(os.path.exists(pid_path))
            with open(pid_path, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), "4321")
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
