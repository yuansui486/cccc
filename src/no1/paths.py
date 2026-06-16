from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

STUDIO_DIRNAME = "studio"
STUDIO_ASSTES_DIRNAME = "asstes"
STUDIO_DRAFTS_DIRNAME = "drafts"


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


def onecolleague_studio_dir(home: Optional[Path] = None) -> Path:
    base = Path(home).expanduser().resolve() if home is not None else onecolleague_home()
    return base / STUDIO_DIRNAME


def onecolleague_studio_asstes_dir(home: Optional[Path] = None) -> Path:
    return onecolleague_studio_dir(home) / STUDIO_ASSTES_DIRNAME


def onecolleague_studio_drafts_dir(home: Optional[Path] = None) -> Path:
    return onecolleague_studio_dir(home) / STUDIO_DRAFTS_DIRNAME


def ensure_studio_dirs(home: Optional[Path] = None) -> Dict[str, Path]:
    studio = onecolleague_studio_dir(home)
    asstes = onecolleague_studio_asstes_dir(home)
    drafts = onecolleague_studio_drafts_dir(home)
    studio.mkdir(parents=True, exist_ok=True)
    asstes.mkdir(parents=True, exist_ok=True)
    drafts.mkdir(parents=True, exist_ok=True)
    return {
        "home": studio.parent,
        "studio": studio,
        "asstes": asstes,
        "drafts": drafts,
    }
