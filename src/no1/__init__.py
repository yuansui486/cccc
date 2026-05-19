from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _detect_version() -> str:
    for dist_name in ("no1",):
        try:
            return version(dist_name)
        except PackageNotFoundError:
            continue
    return "0.0.0"


__version__ = _detect_version()
