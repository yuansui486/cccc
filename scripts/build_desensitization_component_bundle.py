#!/usr/bin/env python3
"""Build the small source bundle used to bootstrap the desensitization Sidecar.

The runtime dependencies (OCR/NLP/model wheels) are deliberately absent.  The
bundle only carries the component project metadata and its Python/static source
so the main wheel remains lightweight while an installed application can still
materialize the Sidecar project at first use.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
import zipfile


ARCHIVE_NAME = "desensitization_component.zip"
_EXCLUDED_PARTS = {
    ".venv",
    ".pytest_cache",
    "__pycache__",
    "tmp-sidecar-home",
    ".git",
    "tests",
    "data",
}
_BUNDLE_PATH = "no1/resources/desensitization_component.zip"
_HEAVY_DISTRIBUTIONS = {
    "modelscope",
    "paddleocr",
    "paddlepaddle",
    "presidio-analyzer",
    "presidio-anonymizer",
    "pymupdf",
    "torch",
    "transformers",
}


def _normalized_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def _files(component: Path) -> list[tuple[Path, str]]:
    metadata = []
    for name in ("pyproject.toml", "uv.lock"):
        path = component / name
        if not path.is_file():
            raise FileNotFoundError(f"missing component metadata: {path}")
        metadata.append((path, name))

    source = component / "src" / "cccc_desensitization"
    if not source.is_dir():
        raise FileNotFoundError(f"missing component source directory: {source}")
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(component).parts
        if any(part in _EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative_parts):
            continue
        metadata.append((path, path.relative_to(component).as_posix()))
    if not any(name == "src/cccc_desensitization/sidecar.py" for _, name in metadata):
        raise FileNotFoundError("component source is missing src/cccc_desensitization/sidecar.py")
    return metadata


def _render(component: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, name in _files(component):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o644 << 16
                archive.writestr(info, _normalized_bytes(path))
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.replace(temporary, destination)
        return digest
    finally:
        temporary.unlink(missing_ok=True)


def _validate_bundle_bytes(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()

    required = {"pyproject.toml", "uv.lock", "src/cccc_desensitization/sidecar.py"}
    missing = required.difference(names)
    if missing:
        raise ValueError(f"component bundle is missing: {', '.join(sorted(missing))}")
    for name in names:
        path = Path(name.replace("\\", "/"))
        if name.startswith("/") or ".." in path.parts:
            raise ValueError(f"unsafe component bundle entry: {name}")
        if any(part in _EXCLUDED_PARTS or part.endswith(".egg-info") for part in path.parts):
            raise ValueError(f"excluded component bundle entry: {name}")
        if path.suffix.lower() in {".db", ".pyc", ".pyd", ".so", ".dll", ".bin"}:
            raise ValueError(f"runtime artifact found in component bundle: {name}")


def _bundle_entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _expected_entries(component: Path) -> dict[str, bytes]:
    return {name: _normalized_bytes(path) for path, name in _files(component)}


def _verify_wheel(wheel: Path, expected_bundle: bytes) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if _BUNDLE_PATH not in names:
            raise ValueError(f"wheel is missing {_BUNDLE_PATH}: {wheel}")
        bundled = archive.read(_BUNDLE_PATH)
        if bundled != expected_bundle:
            raise ValueError(f"wheel contains a stale component bundle: {wheel}")
        metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), "")
        metadata = archive.read(metadata_name).decode("utf-8") if metadata_name else ""

    _validate_bundle_bytes(bundled)
    forbidden = [
        name
        for name in names
        if name.startswith(("cccc/", "no1/desensitization/", "no1/desensitization_legacy/"))
    ]
    if forbidden:
        raise ValueError(f"wheel contains legacy application packages: {forbidden[:5]}")
    dependencies = set()
    for line in metadata.splitlines():
        if not line.lower().startswith("requires-dist:"):
            continue
        value = line.split(":", 1)[1].strip()
        match = re.match(r"[A-Za-z0-9_.-]+", value)
        if match:
            dependencies.add(match.group(0).lower().replace("_", "-"))
    heavy = dependencies.intersection(_HEAVY_DISTRIBUTIONS)
    if heavy:
        raise ValueError(f"main wheel declares Sidecar-only dependencies: {', '.join(sorted(heavy))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", type=Path, default=Path("components/desensitization"))
    parser.add_argument("--output", type=Path, default=Path("src/no1/resources") / ARCHIVE_NAME)
    parser.add_argument("--check", action="store_true", help="fail when the existing archive is stale or missing")
    parser.add_argument("--verify-wheel", action="append", default=[], type=Path, help="verify a built OneColleague wheel")
    args = parser.parse_args()

    component = args.component.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if args.check:
        if not output.is_file():
            raise SystemExit(f"component bundle is missing; run this script to refresh: {output}")
        _validate_bundle_bytes(output.read_bytes())
        actual = _bundle_entries(output.read_bytes())
        expected = _expected_entries(component)
        if actual != expected:
            raise SystemExit(f"component bundle is stale; run this script to refresh: {output}")
        print(f"OK: component bundle is current ({output})")
    else:
        digest = _render(component, output)
        print(f"OK: wrote {output} (sha256={digest})")

    bundle = output.read_bytes()
    _validate_bundle_bytes(bundle)
    for wheel in args.verify_wheel:
        resolved = wheel.expanduser().resolve()
        _verify_wheel(resolved, bundle)
        print(f"OK: wheel contains only the lightweight component bundle ({resolved})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
