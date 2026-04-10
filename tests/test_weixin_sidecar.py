from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest.mock import patch


class TestWeixinSidecarPath(unittest.TestCase):
    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[1]

    def _with_home(self) -> tuple[Path, Callable[[], None]]:
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = Path(td_ctx.__enter__()).resolve()
        os.environ["CCCC_HOME"] = str(td)

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def test_resolve_weixin_sidecar_script_path_materializes_packaged_bundle(self) -> None:
        from cccc.ports.im.weixin_sidecar import resolve_weixin_sidecar_script_path

        home, cleanup = self._with_home()
        try:
            with patch("cccc.ports.im.weixin_sidecar._repo_sidecar_path", return_value=home / "missing.mjs"):
                path = resolve_weixin_sidecar_script_path()

            self.assertEqual(path, home / "cache" / "sidecars" / "weixin_sidecar.mjs")
            self.assertTrue(path.exists())
            self.assertIn("weixin-agent-sdk", path.read_text(encoding="utf-8"))
            package_path = path.parent / "package.json"
            package_lock_path = path.parent / "package-lock.json"
            self.assertTrue(package_path.exists())
            self.assertTrue(package_lock_path.exists())
            package_json = json.loads(package_path.read_text(encoding="utf-8"))
            lock_json = json.loads(package_lock_path.read_text(encoding="utf-8"))
            self.assertEqual(package_json["dependencies"]["weixin-agent-sdk"], "0.4.0")
            self.assertEqual(lock_json["packages"][""]["dependencies"]["weixin-agent-sdk"], "0.4.0")
        finally:
            cleanup()

    def test_resolve_weixin_sidecar_script_path_falls_back_to_repo_script(self) -> None:
        from cccc.ports.im.weixin_sidecar import resolve_weixin_sidecar_script_path

        with patch("cccc.ports.im.weixin_sidecar._packaged_sidecar_bundle_bytes", return_value={}):
            path = resolve_weixin_sidecar_script_path()

        self.assertTrue(path.exists())
        self.assertEqual(path.name, "weixin_sidecar.mjs")

    def test_packaged_sidecar_bundle_matches_repo_bundle(self) -> None:
        repo_dir = self._repo_root() / "scripts" / "im"
        packaged_dir = self._repo_root() / "src" / "cccc" / "resources" / "im"

        for filename in ("weixin_sidecar.mjs", "package.json", "package-lock.json"):
            with self.subTest(filename=filename):
                repo_text = repo_dir.joinpath(filename).read_text(encoding="utf-8")
                packaged_text = packaged_dir.joinpath(filename).read_text(encoding="utf-8")
                if filename.endswith(".json"):
                    self.assertEqual(json.loads(repo_text), json.loads(packaged_text))
                else:
                    self.assertEqual(repo_text, packaged_text)


if __name__ == "__main__":
    unittest.main()
