#!/usr/bin/env python3
"""Validate OneColleague identity or package a flat Windows core ZIP (Python 3.11+)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import time
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGET = "x86_64-pc-windows-msvc"
ALIAS = "onecolleague-windows-standalone.zip"
VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(root: Path, tag: str = "", expected_commit: str = "") -> dict:
    config = json.loads((root / "onecolleague-release.json").read_text(encoding="utf-8"))
    if config.get("schemaVersion") != 1 or not VERSION.fullmatch(config.get("version", "")):
        raise ValueError("invalid OneColleague release configuration")
    if config.get("target") != TARGET or config.get("tagPrefix") != "onecolleague-v":
        raise ValueError("unsupported target or release tag prefix")
    tag = tag.removeprefix("refs/tags/")
    if tag and tag != config["tagPrefix"] + config["version"]:
        raise ValueError("release tag must equal onecolleague-v + distribution version")
    with (root / "Cargo.toml").open("rb") as stream:
        core_version = tomllib.load(stream)["workspace"]["package"]["version"]
    with (root / "rust-toolchain.toml").open("rb") as stream:
        toolchain = tomllib.load(stream)["toolchain"]["channel"]
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if expected_commit and commit.lower() != expected_commit.lower():
        raise ValueError(f"checkout commit {commit} differs from expected snapshot {expected_commit}")
    if tag:
        tagged = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", f"refs/tags/{tag}^{{commit}}"], text=True
        ).strip()
        if tagged != commit:
            raise ValueError("release tag does not point at checkout HEAD")
    return {"schemaVersion": 1, "version": config["version"], "coreVersion": core_version,
            "commit": commit, "tag": tag, "target": TARGET, "toolchain": toolchain}


def verify_pe(binary: Path) -> None:
    with binary.open("rb") as stream:
        header = stream.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError("core binary is not a Windows PE executable")
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise ValueError("core binary is not Windows x64")


def package(root: Path, binary: Path, output: Path, *, tag: str = "", build_id: str = "",
            expected_commit: str = "") -> dict:
    manifest = identity(root, tag, expected_commit)
    verify_pe(binary)
    output.mkdir(parents=True, exist_ok=True)
    name = f"onecolleague-{manifest['version']}-windows-x64.zip"
    archive_path = output / name
    files = [("cccc.exe", binary), ("LICENSE", root / "LICENSE"),
             ("README.md", root / "docs" / "onecolleague-distribution.md")]
    epoch = max(315532800, int(os.environ.get("SOURCE_DATE_EPOCH", "315532800")))
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for entry_name, source in files:
            info = zipfile.ZipInfo(entry_name, date_time=time.gmtime(epoch)[:6])
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | (0o755 if entry_name.endswith(".exe") else 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with source.open("rb") as reader, archive.open(info, "w") as writer:
                shutil.copyfileobj(reader, writer)
    shutil.copyfile(archive_path, output / ALIAS)
    manifest.update({"buildId": build_id,
                     "package": {"name": name, "alias": ALIAS, "size": archive_path.stat().st_size,
                                 "sha256": sha256(archive_path)},
                     "executable": {"name": "cccc.exe", "size": binary.stat().st_size,
                                    "sha256": sha256(binary)}})
    (output / "core-release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "package"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tag", default=os.environ.get("RELEASE_TAG", ""))
    parser.add_argument("--expected-commit", default=os.environ.get("EXPECTED_COMMIT", ""))
    parser.add_argument("--build-id", default=os.environ.get("BUILD_ID", ""))
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        if args.command == "package":
            if args.binary is None:
                parser.error("package requires --binary")
            result = package(args.root.resolve(), args.binary.resolve(), args.output_dir.resolve(),
                             tag=args.tag, build_id=args.build_id, expected_commit=args.expected_commit)
        else:
            result = identity(args.root.resolve(), args.tag, args.expected_commit)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
