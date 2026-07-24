"""Pure trigger validation helpers used by the workflow editor and scheduler."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .models import WorkflowTrigger


def validate_trigger(trigger: WorkflowTrigger, *, group_root: Optional[Path] = None) -> None:
    config = trigger.config
    if trigger.type == "cron":
        expression = str(config.get("expression") or config.get("cron") or "").strip()
        fields = expression.split()
        if len(fields) not in (5, 6) or any(not field or len(field) > 40 for field in fields):
            raise ValueError("cron trigger requires a five or six field expression")
        if config.get("timezone") and len(str(config["timezone"])) > 100:
            raise ValueError("invalid trigger timezone")
    elif trigger.type == "interval":
        seconds = int(config.get("seconds") or 0)
        if seconds < 10 or seconds > 31_536_000:
            raise ValueError("interval must be between 10 seconds and one year")
    elif trigger.type == "event":
        if not str(config.get("kind") or "").strip():
            raise ValueError("event trigger requires an event kind")
    elif trigger.type == "file":
        raw = str(config.get("path") or "").strip()
        if not raw or not group_root:
            raise ValueError("file trigger requires a group-authorized path")
        target = Path(raw).expanduser().resolve()
        root = group_root.resolve()
        if target != root and root not in target.parents:
            raise ValueError("file trigger path must be inside the authorized group root")
        if any(part in {".git", ".onecolleague", "state"} for part in target.parts):
            raise ValueError("file trigger path is not allowed")
    elif trigger.type == "element":
        locator = config.get("locator")
        if not isinstance(locator, dict) or not any(str(locator.get(key) or "").strip() for key in ("window_name", "control_type", "name", "text")):
            raise ValueError("element trigger requires a persistent locator")


def trigger_enabled_for_automation(trigger: WorkflowTrigger, *, published: bool, trusted: bool) -> bool:
    return bool(trigger.enabled and published and trusted)


def should_confirm_element(previous: bool, current: bool, consecutive_hits: int, *, required_hits: int = 2) -> tuple[bool, int]:
    """Return (fire, next_hit_count), requiring a false baseline before rearming."""
    if not current:
        return False, 0
    hits = consecutive_hits + 1 if previous else 1
    return hits >= max(1, required_hits), hits


def is_authorized_file_event(path: str, roots: Iterable[Path]) -> bool:
    try:
        candidate = Path(path).resolve()
    except Exception:
        return False
    return any(candidate == root.resolve() or root.resolve() in candidate.parents for root in roots)
