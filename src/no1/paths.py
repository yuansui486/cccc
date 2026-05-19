from __future__ import annotations

import os
from pathlib import Path


def _env_path(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _default_onecolleague_home() -> Path:
    return (Path.home() / ".onecolleague").resolve()


def onecolleague_home() -> Path:
    env = _env_path("ONECOLLEAGUE_HOME") or _env_path("CCCC_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return _default_onecolleague_home()


def ensure_home() -> Path:
    home = onecolleague_home()
    home.mkdir(parents=True, exist_ok=True)
    return home
