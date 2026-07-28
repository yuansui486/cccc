from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from ...kernel.experience import read_experience
from ...kernel.group import Group
from ...util.conv import coerce_bool
from ...util.fs import atomic_write_text, read_json


EXPERIENCE_REMINDER_LINE = (
    "[onecolleague experience] Before handling this turn, read the project's shared experience with "
    '`onecolleague_experience(action="read")`; apply relevant verified lessons, and append only new reusable findings.'
)

_STATE_LOCK = threading.RLock()
_RECENT_EVENT_LIMIT = 512


@dataclass(frozen=True)
class ExperienceReminderDecision:
    group_id: str
    scope_key: str
    actor_id: str
    event_ids: tuple[str, ...]
    due: bool


def _config(group: Group) -> tuple[bool, int]:
    raw = group.doc.get("experience") if isinstance(group.doc.get("experience"), dict) else {}
    enabled = coerce_bool(raw.get("reminder_enabled"), default=True)
    try:
        every = int(raw.get("reminder_every_user_messages", 10))
    except Exception:
        every = 10
    return enabled, max(1, min(every, 1000))


def _state_path(group: Group):
    return group.path / "state" / "experience_reminders.json"


def _load_state(group: Group) -> dict[str, Any]:
    try:
        raw = read_json(_state_path(group))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if not isinstance(raw.get("scopes"), dict):
        raw["scopes"] = {}
    raw["v"] = 1
    return raw


def _actor_state(state: dict[str, Any], scope_key: str, actor_id: str) -> dict[str, Any]:
    scopes = state.setdefault("scopes", {})
    scope = scopes.setdefault(scope_key, {})
    actors = scope.setdefault("actors", {})
    actor = actors.setdefault(actor_id, {})
    if not isinstance(actor.get("recent_event_ids"), list):
        actor["recent_event_ids"] = []
    try:
        actor["count"] = max(0, int(actor.get("count") or 0))
    except Exception:
        actor["count"] = 0
    return actor


def plan_experience_reminder(
    group: Group,
    *,
    actor_id: str,
    messages: Iterable[Any],
    scope_key: str = "",
) -> ExperienceReminderDecision:
    gid = str(group.group_id or "").strip()
    aid = str(actor_id or "").strip()
    resolved_scope = str(scope_key or group.doc.get("active_scope_key") or "").strip() or "default"
    enabled, every = _config(group)
    candidates: list[str] = []
    for item in messages:
        if isinstance(item, dict):
            kind = str(item.get("kind") or "chat.message")
            by = str(item.get("by") or "")
            event_id = str(item.get("id") or item.get("event_id") or "").strip()
        else:
            kind = str(getattr(item, "kind", "chat.message") or "chat.message")
            by = str(getattr(item, "by", "") or "")
            event_id = str(getattr(item, "event_id", "") or "").strip()
        item_scope = str(item.get("scope_key") or "").strip() if isinstance(item, dict) else str(getattr(item, "scope_key", "") or "").strip()
        if item_scope and not scope_key:
            resolved_scope = item_scope
        if kind == "chat.message" and by == "user" and event_id and event_id not in candidates:
            candidates.append(event_id)
    if not enabled or not gid or not aid or not candidates:
        return ExperienceReminderDecision(gid, resolved_scope, aid, tuple(), False)

    with _STATE_LOCK:
        state = _load_state(group)
        actor = _actor_state(state, resolved_scope, aid)
        recent = {str(item) for item in actor.get("recent_event_ids") or []}
        unseen = tuple(event_id for event_id in candidates if event_id not in recent)
        before = int(actor.get("count") or 0)
        due = bool(unseen) and (before + len(unseen)) // every > before // every
    if due:
        try:
            read_experience(group, ensure=True)
        except Exception:
            due = False
    return ExperienceReminderDecision(gid, resolved_scope, aid, unseen, due)


def commit_experience_reminder(group: Group, decision: ExperienceReminderDecision) -> None:
    if not decision.event_ids:
        return
    with _STATE_LOCK:
        state = _load_state(group)
        actor = _actor_state(state, decision.scope_key, decision.actor_id)
        recent_list = [str(item) for item in actor.get("recent_event_ids") or [] if str(item)]
        recent = set(recent_list)
        added = [event_id for event_id in decision.event_ids if event_id not in recent]
        if not added:
            return
        actor["count"] = int(actor.get("count") or 0) + len(added)
        actor["recent_event_ids"] = (recent_list + added)[-_RECENT_EVENT_LIMIT:]
        path = _state_path(group)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_experience_reminder(text: str, decision: ExperienceReminderDecision) -> str:
    out = str(text or "").rstrip("\n")
    if not decision.due or EXPERIENCE_REMINDER_LINE in out:
        return out
    return f"{out}\n\n{EXPERIENCE_REMINDER_LINE}" if out else EXPERIENCE_REMINDER_LINE
