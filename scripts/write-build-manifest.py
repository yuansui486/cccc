#!/usr/bin/env python3
"""Write deterministic metadata for a TeamCity build artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--build-id", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    artifact = args.artifact.resolve()
    if not artifact.is_file():
        raise SystemExit(f"artifact does not exist: {artifact}")

    manifest = {
        "schemaVersion": 1,
        "kind": args.kind,
        "version": args.version,
        "buildId": args.build_id,
        "commit": args.commit,
        "tag": args.tag,
        "artifact": {
            "name": artifact.name,
            "size": artifact.stat().st_size,
            "sha256": sha256(artifact),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
