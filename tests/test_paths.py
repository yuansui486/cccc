from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestPaths(unittest.TestCase):
    def _clean_home_env(self):
        old_new = os.environ.get("ONECOLLEAGUE_HOME")
        old_legacy = os.environ.get("CCCC_HOME")
        os.environ.pop("ONECOLLEAGUE_HOME", None)
        os.environ.pop("CCCC_HOME", None)

        def cleanup() -> None:
            if old_new is None:
                os.environ.pop("ONECOLLEAGUE_HOME", None)
            else:
                os.environ["ONECOLLEAGUE_HOME"] = old_new
            if old_legacy is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_legacy

        return cleanup

    def test_default_home_uses_onecolleague_directory_without_migration(self) -> None:
        from no1.paths import onecolleague_home

        cleanup = self._clean_home_env()
        try:
            with tempfile.TemporaryDirectory() as td:
                fake_user_home = Path(td)
                legacy = fake_user_home / ".cccc"
                legacy.mkdir()
                with patch("no1.paths.Path.home", return_value=fake_user_home):
                    self.assertEqual(onecolleague_home(), (fake_user_home / ".onecolleague").resolve())
                    self.assertTrue(legacy.exists())
        finally:
            cleanup()

    def test_onecolleague_home_env_takes_precedence(self) -> None:
        from no1.paths import onecolleague_home

        cleanup = self._clean_home_env()
        try:
            os.environ["CCCC_HOME"] = "/tmp/legacy-home"
            os.environ["ONECOLLEAGUE_HOME"] = "/tmp/onecolleague-home"
            self.assertEqual(onecolleague_home(), Path("/tmp/onecolleague-home").resolve())
        finally:
            cleanup()

    def test_legacy_home_env_still_works_when_explicit(self) -> None:
        from no1.paths import onecolleague_home

        cleanup = self._clean_home_env()
        try:
            os.environ["CCCC_HOME"] = "/tmp/legacy-home"
            self.assertEqual(onecolleague_home(), Path("/tmp/legacy-home").resolve())
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
