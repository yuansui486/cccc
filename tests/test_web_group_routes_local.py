import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestWebGroupRoutesLocal(unittest.TestCase):
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

    def _client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def _app(self):
        from cccc.ports.web.app import create_app

        return create_app()

    def _create_group(self) -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        return create_group(reg, title="group-local-read", topic="local topic").group_id

    def test_group_show_reads_local_projection_without_daemon(self) -> None:
        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            with patch("cccc.ports.web.app.call_daemon", side_effect=AssertionError("group_show should not call daemon")):
                with self._client() as client:
                    resp = client.get(f"/api/v1/groups/{group_id}")
                    self.assertEqual(resp.status_code, 200)
                    body = resp.json()
                    self.assertTrue(bool(body.get("ok")), body)
                    group = (body.get("result") or {}).get("group") or {}
                    self.assertEqual(str(group.get("group_id") or ""), group_id)
                    self.assertEqual(str(group.get("title") or ""), "group-local-read")
                    self.assertEqual(str(group.get("topic") or ""), "local topic")
        finally:
            cleanup()

    def test_legacy_codex_headless_routes_remain_available(self) -> None:
        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            app = self._app()
            route_paths = {getattr(route, "path", "") for route in app.routes}
            self.assertIn("/api/v1/groups/{group_id}/codex/stream", route_paths)

            with TestClient(app) as client:
                snapshot_resp = client.get(f"/api/v1/groups/{group_id}/codex/snapshot")
                self.assertEqual(snapshot_resp.status_code, 200)
                snapshot_body = snapshot_resp.json()
                self.assertTrue(bool(snapshot_body.get("ok")), snapshot_body)
                self.assertEqual(str((snapshot_body.get("result") or {}).get("group_id") or ""), group_id)
        finally:
            cleanup()
