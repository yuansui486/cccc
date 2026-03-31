import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestWebGroupsLocalProjection(unittest.TestCase):
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

        return cleanup

    def _client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def test_groups_route_reads_local_projection_without_daemon(self) -> None:
        cleanup = self._with_home()
        try:
            with patch(
                "cccc.ports.web.routes.groups._read_groups_local",
                return_value={"ok": True, "result": {"groups": [{"group_id": "g1", "title": "T"}], "registry_health": {}}},
            ), patch("cccc.ports.web.app.call_daemon", side_effect=AssertionError("daemon should not be called")):
                with self._client() as client:
                    resp = client.get("/api/v1/groups")
                    self.assertEqual(resp.status_code, 200)
                    data = resp.json()
                    self.assertEqual(data["result"]["groups"][0]["group_id"], "g1")
        finally:
            cleanup()
