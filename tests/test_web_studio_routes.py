from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class TestWebStudioRoutes(unittest.TestCase):
    def _with_home(self):
        old_new = os.environ.get("ONECOLLEAGUE_HOME")
        old_legacy = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["ONECOLLEAGUE_HOME"] = td
        os.environ.pop("CCCC_HOME", None)

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_new is None:
                os.environ.pop("ONECOLLEAGUE_HOME", None)
            else:
                os.environ["ONECOLLEAGUE_HOME"] = old_new
            if old_legacy is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_legacy

        return td, cleanup

    def _client(self) -> TestClient:
        from no1.ports.web.app import create_app

        return TestClient(create_app())

    def test_studio_paths_returns_resolved_home_and_creates_dirs(self) -> None:
        td, cleanup = self._with_home()
        try:
            with self._client() as client:
                resp = client.get("/api/v1/studio/paths")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")))
            result = body.get("result") or {}
            home = Path(td).resolve()
            self.assertEqual(result.get("home_path"), str(home))
            self.assertEqual(result.get("studio_path"), str(home / "studio"))
            self.assertEqual(result.get("assets_path"), str(home / "studio" / "assets"))
            self.assertEqual(result.get("drafts_path"), str(home / "studio" / "drafts"))
            self.assertTrue((home / "studio" / "assets").is_dir())
            self.assertTrue((home / "studio" / "drafts").is_dir())
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
