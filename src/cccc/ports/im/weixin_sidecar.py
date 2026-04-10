from __future__ import annotations

from pathlib import Path

from ...paths import ensure_home
from ...util.fs import atomic_write_bytes


_SIDECAR_CACHE_DIR = ("cache", "sidecars")
_SIDECAR_FILENAME = "weixin_sidecar.mjs"
_SIDECAR_SUPPORT_FILENAMES = ("package.json", "package-lock.json")
_SIDECAR_BUNDLE_FILENAMES = (_SIDECAR_FILENAME, *_SIDECAR_SUPPORT_FILENAMES)


def _repo_sidecar_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "scripts" / "im"


def _repo_sidecar_path() -> Path:
    return _repo_sidecar_dir() / _SIDECAR_FILENAME


def _packaged_sidecar_bundle_bytes() -> dict[str, bytes]:
    try:
        import importlib.resources

        files = importlib.resources.files("cccc.resources").joinpath("im")
    except Exception:
        return {}

    bundle: dict[str, bytes] = {}
    for filename in _SIDECAR_BUNDLE_FILENAMES:
        try:
            payload = files.joinpath(filename).read_bytes()
        except Exception:
            payload = b""
        if payload:
            bundle[filename] = payload
    return bundle


def resolve_weixin_sidecar_script_path() -> Path:
    """Resolve a stable filesystem path for the bundled weixin sidecar script."""
    repo_script = _repo_sidecar_path()
    if repo_script.exists():
        return repo_script

    packaged_bundle = _packaged_sidecar_bundle_bytes()
    packaged_script = packaged_bundle.get(_SIDECAR_FILENAME, b"")
    if packaged_script:
        cache_dir = ensure_home().joinpath(*_SIDECAR_CACHE_DIR)
        for filename in _SIDECAR_BUNDLE_FILENAMES:
            cache_path = cache_dir / filename
            packaged = packaged_bundle.get(filename, b"")
            if not packaged:
                cache_path.unlink(missing_ok=True)
                continue
            try:
                current = cache_path.read_bytes() if cache_path.exists() else b""
            except Exception:
                current = b""
            if current != packaged:
                atomic_write_bytes(cache_path, packaged)
        return cache_dir / _SIDECAR_FILENAME

    return repo_script
