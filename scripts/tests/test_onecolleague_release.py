"""Release identity and distribution contract tests, using temporary repositories."""

import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "onecolleague_release.py"
SPEC = importlib.util.spec_from_file_location("onecolleague_release", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "docs").mkdir()
        (self.root / "onecolleague-release.json").write_text(json.dumps({
            "schemaVersion": 1, "version": "1.0.0", "tagPrefix": "onecolleague-v", "target": release.TARGET}))
        (self.root / "Cargo.toml").write_text('[workspace.package]\nversion = "0.4.45"\n')
        (self.root / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.88.0"\n')
        (self.root / "LICENSE").write_text("Apache-2.0")
        (self.root / "docs/onecolleague-distribution.md").write_text("OneColleague")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.binary = self.root / "cccc.exe"
        header = bytearray(64)
        header[:2] = b"MZ"
        struct.pack_into("<I", header, 60, 64)
        self.binary.write_bytes(header + b"PE\0\0\x64\x86")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def test_distribution_is_distinct_and_archive_is_flat_and_reproducible(self):
        first = release.package(self.root, self.binary, self.root / "out1", build_id="42", expected_commit=self.commit)
        second = release.package(self.root, self.binary, self.root / "out2")
        self.assertEqual(first["version"], "1.0.0")
        self.assertEqual(first["coreVersion"], "0.4.45")
        self.assertEqual(first["package"], second["package"])
        archive = self.root / "out1" / first["package"]["name"]
        self.assertEqual(archive.read_bytes(), (self.root / "out1" / release.ALIAS).read_bytes())
        with zipfile.ZipFile(archive) as handle:
            self.assertEqual(set(handle.namelist()), {"cccc.exe", "LICENSE", "README.md"})

    def test_upstream_tag_and_snapshot_mismatch_fail(self):
        with self.assertRaisesRegex(ValueError, "release tag"):
            release.identity(self.root, "v0.4.45")
        with self.assertRaisesRegex(ValueError, "checkout commit"):
            release.identity(self.root, expected_commit="0" * 40)

    def test_release_tag_must_match_distribution_and_checkout(self):
        self.git("tag", "onecolleague-v1.0.0")
        self.assertEqual(release.identity(self.root, "refs/tags/onecolleague-v1.0.0")["tag"], "onecolleague-v1.0.0")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "new")
        with self.assertRaisesRegex(ValueError, "HEAD"):
            release.identity(self.root, "onecolleague-v1.0.0")

    def test_non_windows_binary_fails_before_output(self):
        self.binary.write_bytes(b"not a PE")
        with self.assertRaisesRegex(ValueError, "Windows PE"):
            release.package(self.root, self.binary, self.root / "dist")
        self.assertFalse((self.root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
