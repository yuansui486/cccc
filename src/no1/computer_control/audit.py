from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from ..util.file_lock import acquire_lockfile, release_lockfile


def audit(home: Path, kind: str, *, group_id: str = "", actor_id: str = "", details: Dict[str, Any] | None = None) -> None:
    """Append a redacted computer-control audit entry without affecting the request."""
    payload = {
        "id": uuid.uuid4().hex,
        "ts": time.time(),
        "kind": str(kind),
        "group_id": str(group_id),
        "actor_id": str(actor_id),
        "details": details if isinstance(details, dict) else {},
    }
    path = home / "state" / "computer-control" / "audit.jsonl"
    lock_path = path.with_suffix(".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = acquire_lockfile(lock_path, blocking=True)
        try:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        finally:
            release_lockfile(handle)
    except Exception:
        return
