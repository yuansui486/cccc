from __future__ import annotations

import importlib
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

from .compat import load_group, ensure_home

_ENGINE_LOCK = threading.RLock()
_ENGINE: ModuleType | None = None


def model_root() -> Path:
    root = ensure_home() / "state" / "desensitization" / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def group_engine_root(group_id: str) -> Path:
    group = load_group(group_id)
    if group is None:
        raise ValueError("group not found")
    root = group.path / "state" / "desensitization" / "engine"
    root.mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    return root


def _load_engine() -> ModuleType:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    bootstrap = ensure_home() / "state" / "desensitization" / "bootstrap"
    bootstrap.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LOCAL_DESENSITIZATION_DATA_DIR", str(bootstrap))
    os.environ.setdefault("LOCAL_DESENSITIZATION_MODEL_DIR", str(model_root()))
    os.environ.setdefault("PRESIDIO_DEVICE", "cpu")
    _ENGINE = importlib.import_module("cccc_desensitization.main")
    return _ENGINE


@contextmanager
def engine_context(group_id: str) -> Iterator[ModuleType]:
    """Bind the migrated engine's legacy globals to one group for one call.

    The original application kept process-wide DATA/DB globals. The integrated
    workbench serializes engine calls and points those globals at a group-owned
    directory, while model instances remain shared to avoid loading OCR twice.
    """

    with _ENGINE_LOCK:
        engine = _load_engine()
        root = group_engine_root(group_id)
        engine.DATA = root
        engine.TASKS = root / "tasks"
        engine.MODELS = model_root()
        engine.DB = root / "engine.sqlite3"
        # Uploads are read by the migrated engine in bounded chunks.  The
        # service layer performs configured size and resource checks before
        # entering the engine; zero means no fixed business byte limit.
        engine.MAX_UPLOAD = 0
        engine.policy_store = {
            key: dict(value) for key, value in engine.DEFAULT_POLICIES.items()
        }
        engine._POLICY_STORE_LOADED = False
        engine._load_policy_store()
        yield engine


def engine_call(group_id: str, method: str, *args: Any, **kwargs: Any) -> Any:
    with engine_context(group_id) as engine:
        target = getattr(engine, method)
        return target(*args, **kwargs)


def model_call(method: str, *args: Any, **kwargs: Any) -> Any:
    with _ENGINE_LOCK:
        engine = _load_engine()
        return getattr(engine, method)(*args, **kwargs)


def model_capabilities() -> dict[str, Any]:
    engine = _load_engine()
    status = dict(engine.model_status())
    status["model_dir"] = str(model_root())
    return status
