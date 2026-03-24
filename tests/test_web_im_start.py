import os
import hashlib
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


class _DummyLockFile:
    def close(self) -> None:
        return None


class _FakeBridge:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def start(self) -> bool:
        return True

    def run_forever(self) -> None:
        return None


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


    def test_im_start_wecom_injects_credentials_into_env(self) -> None:
        from cccc.ports.web.app import create_app

        home, cleanup = self._with_home()
        try:
            gid = self._create_group("im-start-wecom")
            with TestClient(create_app()) as client:
                set_resp = client.post(
                    "/api/im/set",
                    json={
                        "group_id": gid,
                        "platform": "wecom",
                        "wecom_bot_id": "corp123",
                        "wecom_secret": "sec456",
                    },
                )
                self.assertEqual(set_resp.status_code, 200)
                self.assertTrue(bool(set_resp.json().get("ok")))

                with patch("subprocess.Popen", return_value=_AliveProc()) as popen:
                    start_resp = client.post("/api/im/start", json={"group_id": gid})

                self.assertEqual(start_resp.status_code, 200)
                self.assertTrue(bool(start_resp.json().get("ok")))

            self.assertIsNotNone(popen.call_args)
            kwargs = dict(popen.call_args.kwargs)
            child_env = kwargs.get("env") or {}
            self.assertEqual(child_env.get("WECOM_BOT_ID"), "corp123")
            self.assertEqual(child_env.get("WECOM_SECRET"), "sec456")

            argv = list(popen.call_args.args[0])
            self.assertEqual(argv, [sys.executable, "-m", "cccc.ports.im", gid, "wecom"])
        finally:
            cleanup()

    def test_start_bridge_wecom_lock_uses_bot_id_identity(self) -> None:
        from cccc.ports.im.bridge import start_bridge
        from cccc.kernel.group import load_group

        _, cleanup = self._with_home()
        try:
            gid = self._create_group("bridge-lock-wecom")
            group = load_group(gid)
            assert group is not None
            group.doc["im"] = {
                "platform": "wecom",
                "wecom_bot_id": "corp123",
                "wecom_secret": "sec456",
            }
            group.save()

            lock_paths: list[str] = []

            def fake_acquire(lock_path):
                lock_paths.append(str(lock_path))
                return _DummyLockFile()

            with patch("cccc.ports.im.bridge._acquire_singleton_lock", side_effect=fake_acquire):
                with patch("cccc.ports.im.bridge.IMBridge", _FakeBridge):
                    start_bridge(gid, "wecom")

            self.assertGreaterEqual(len(lock_paths), 2)
            token_fingerprint = hashlib.sha256("wecom|bot_id=corp123".encode("utf-8")).hexdigest()[:12]
            self.assertTrue(lock_paths[0].endswith(f"im_bridge_wecom_{token_fingerprint}.lock"))
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
