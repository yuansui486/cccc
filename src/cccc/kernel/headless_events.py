from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict

from ..util.file_lock import acquire_lockfile, release_lockfile
from ..util.time import utc_now_iso


def headless_events_path(group_dir: Path) -> Path:
    return group_dir / "state" / "headless" / "events.jsonl"


def headless_events_lock_path(group_dir: Path) -> Path:
    return group_dir / "state" / "headless" / "events.lock"


def append_headless_event(group_dir: Path, *, group_id: str, actor_id: str, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "id": uuid.uuid4().hex,
        "ts": utc_now_iso(),
        "group_id": str(group_id or "").strip(),
        "actor_id": str(actor_id or "").strip(),
        "type": str(event_type or "").strip(),
        "data": data if isinstance(data, dict) else {},
    }
    if not payload["group_id"] or not payload["actor_id"] or not payload["type"]:
        raise ValueError("missing headless event fields")

    path = headless_events_path(group_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    lock = acquire_lockfile(headless_events_lock_path(group_dir), blocking=True)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    finally:
        release_lockfile(lock)
    return payload
