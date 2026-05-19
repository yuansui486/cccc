from __future__ import annotations

import os
from pathlib import Path


def onecolleague_home() -> Path:
    env = os.environ.get("ONECOLLEAGUE_HOME", "").strip() or os.environ.get("CCCC_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".cccc").resolve()


def ensure_home() -> Path:
    home = onecolleague_home()
    home.mkdir(parents=True, exist_ok=True)
    return home
