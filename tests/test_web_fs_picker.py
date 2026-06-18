import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestWebFsPicker(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("ONECOLLEAGUE_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["ONECOLLEAGUE_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("ONECOLLEAGUE_HOME", None)
            else:
                os.environ["ONECOLLEAGUE_HOME"] = old_home

        return td, cleanup

    def _client(self) -> TestClient:
        from no1.ports.web.app import create_app

        return TestClient(create_app())

    def test_pick_directory_route_returns_selected_path(self) -> None:
        _, cleanup = self._with_home()
        try:
            with patch(
                "no1.ports.web.routes.base._pick_directory_with_windows_dialog",
                return_value={"ok": True, "result": {"path": r"D:\Works", "cancelled": False}},
            ) as mock_pick:
                with self._client() as client:
                    resp = client.post(
                        "/api/v1/fs/pick-directory",
                        json={"initial_path": r"D:\Missing\Child", "title": "选择工作目录"},
                    )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")), body)
            self.assertEqual((body.get("result") or {}).get("path"), r"D:\Works")
            mock_pick.assert_called_once_with(initial_path=r"D:\Missing\Child", title="选择工作目录")
        finally:
            cleanup()

    def test_pick_directory_reports_unavailable_off_windows(self) -> None:
        from no1.ports.web.routes import base as base_routes

        with patch.object(base_routes.os, "name", "posix"):
            result = base_routes._pick_directory_with_windows_dialog(initial_path="~", title="Select folder")

        self.assertFalse(bool(result.get("ok")), result)
        error = result.get("error") or {}
        self.assertEqual(error.get("code"), "NATIVE_PICKER_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
