from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from ..util.time import parse_utc_iso, utc_now_iso
from .pet_task_triage import enum_text, trim_task_text

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}", re.IGNORECASE)


def _agent_hot(agent: Any) -> Any:
    return getattr(agent, "hot", None)


def _string(value: Any) -> str:
    return str(value or "").strip()


def _task_payload(task: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": _string(getattr(task, "id", "")),
        "title": trim_task_text(getattr(task, "title", ""), max_len=96),
        "status": enum_text(getattr(task, "status", "")),
    }
    assignee = _string(getattr(task, "assignee", ""))
    if assignee:
        payload["assignee"] = assignee
    waiting_on = enum_text(getattr(task, "waiting_on", ""))
    if waiting_on and waiting_on != "none":
        payload["waiting_on"] = waiting_on
    blocked_by = [_string(item) for item in list(getattr(task, "blocked_by", []) or []) if _string(item)]
    if blocked_by:
        payload["blocked_by"] = blocked_by[:3]
    handoff_to = _string(getattr(task, "handoff_to", ""))
    if handoff_to:
        payload["handoff_to"] = handoff_to
    updated_at = _string(getattr(task, "updated_at", ""))
    if updated_at:
        payload["updated_at"] = updated_at
    return payload


def _actor_payload(agent: Any) -> Dict[str, Any]:
    hot = _agent_hot(agent)
    payload: Dict[str, Any] = {
        "id": _string(getattr(agent, "id", "")),
    }
    active_task_id = _string(getattr(hot, "active_task_id", ""))
    if active_task_id:
        payload["active_task_id"] = active_task_id
    focus = _string(getattr(hot, "focus", ""))
    if focus:
        payload["focus"] = trim_task_text(focus, max_len=120)
    next_action = _string(getattr(hot, "next_action", ""))
    if next_action:
        payload["next_action"] = trim_task_text(next_action, max_len=120)
    blockers = [_string(item) for item in list(getattr(hot, "blockers", []) or []) if _string(item)]
    if blockers:
        payload["blockers"] = blockers[:3]
    return payload


def _minutes_since(raw_ts: Any, *, now_iso: str) -> int:
    dt = parse_utc_iso(_string(raw_ts))
    now_dt = parse_utc_iso(now_iso)
    if dt is None or now_dt is None:
        return 0
    return max(0, int((now_dt - dt).total_seconds() // 60))


def _looks_like_waiting_user(text: str) -> bool:
    normalized = _string(text).lower()
    if not normalized:
        return False
    return bool(re.search(r"waiting[_\s-]?user|need user|await user|user input|user reply|clarify with user|approval", normalized))


def _keywords(value: str) -> set[str]:
    return {match.group(0).lower() for match in _WORD_RE.finditer(_string(value))}


def _same_workstream_hint(task: Any, current_active_task: Any, actor: Dict[str, Any]) -> bool:
    if current_active_task is None:
        return False
    task_title = _string(getattr(task, "title", ""))
    active_title = _string(getattr(current_active_task, "title", ""))
    if not task_title or not active_title:
        return False
    shared = _keywords(task_title) & _keywords(active_title)
    if shared:
        return True
    focus = _string(actor.get("focus"))
    next_action = _string(actor.get("next_action"))
    haystack = f"{focus} {next_action}".lower()
    return bool(task_title and task_title.lower() in haystack) or bool(active_title and active_title.lower() in haystack)


def _candidate(
    *,
    kind: str,
    priority: int,
    hypothesis: str,
    actor: Dict[str, Any],
    task: Any,
    current_active_task: Any = None,
    task_stale_minutes: int = 0,
    extras: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "kind": kind,
        "priority": int(priority),
        "hypothesis": hypothesis,
        "actor": actor,
        "task": _task_payload(task),
        "signals": {
            "task_stale_minutes": max(0, int(task_stale_minutes or 0)),
        },
    }
    if current_active_task is not None:
        payload["current_active_task"] = _task_payload(current_active_task)
        payload["signals"]["same_workstream_hint"] = _same_workstream_hint(task, current_active_task, actor)
    if extras:
        payload["signals"].update({key: value for key, value in extras.items() if value not in ("", None, [], {})})
    return payload


def build_pet_task_evidence(tasks: Iterable[Any], agents: Iterable[Any], *, limit: int = 5) -> List[Dict[str, Any]]:
    task_list = list(tasks or [])
    agent_list = list(agents or [])
    now_iso = utc_now_iso()
    tasks_by_id = {_string(getattr(task, "id", "")): task for task in task_list if _string(getattr(task, "id", ""))}
    agents_by_id = {_string(getattr(agent, "id", "")): agent for agent in agent_list if _string(getattr(agent, "id", ""))}
    candidates: List[Dict[str, Any]] = []

    for agent in agent_list:
        actor = _actor_payload(agent)
        actor_id = _string(actor.get("id"))
        active_task_id = _string(actor.get("active_task_id"))
        if not actor_id or not active_task_id:
            continue
        task = tasks_by_id.get(active_task_id)
        if task is None:
            continue
        status = enum_text(getattr(task, "status", ""))
        task_stale_minutes = _minutes_since(getattr(task, "updated_at", ""), now_iso=now_iso)
        blockers = list(actor.get("blockers") or [])
        waiting_on = enum_text(getattr(task, "waiting_on", ""))
        blocked_by = [_string(item) for item in list(getattr(task, "blocked_by", []) or []) if _string(item)]

        if status not in {"active", "done", "completed", "archived"}:
            candidates.append(
                _candidate(
                    kind="mounted_task_status_mismatch",
                    priority=82,
                    hypothesis="The mounted task still looks non-active on the board and may need a status sync.",
                    actor=actor,
                    task=task,
                    task_stale_minutes=task_stale_minutes,
                )
            )

        if status == "active" and not _string(getattr(task, "assignee", "")):
            candidates.append(
                _candidate(
                    kind="active_task_missing_owner",
                    priority=84,
                    hypothesis="The mounted active task has no owner and may need explicit assignment.",
                    actor=actor,
                    task=task,
                    task_stale_minutes=task_stale_minutes,
                )
            )

        if (_looks_like_waiting_user(_string(actor.get("focus"))) or _looks_like_waiting_user(_string(actor.get("next_action")))) and waiting_on != "user":
            candidates.append(
                _candidate(
                    kind="waiting_user_unsynced",
                    priority=88,
                    hypothesis="The actor focus implies a user dependency, but the task metadata does not say waiting_on=user.",
                    actor=actor,
                    task=task,
                    task_stale_minutes=task_stale_minutes,
                )
            )

        if blockers and waiting_on == "" and not blocked_by:
            candidates.append(
                _candidate(
                    kind="blockers_unsynced",
                    priority=86,
                    hypothesis="The actor reports blockers, but the task metadata does not yet reflect a blocked state.",
                    actor=actor,
                    task=task,
                    task_stale_minutes=task_stale_minutes,
                    extras={"blocker_count": len(blockers)},
                )
            )

    for task in task_list:
        task_id = _string(getattr(task, "id", ""))
        assignee = _string(getattr(task, "assignee", ""))
        status = enum_text(getattr(task, "status", ""))
        if not task_id or not assignee or status != "active":
            continue
        agent = agents_by_id.get(assignee)
        if agent is None:
            continue
        actor = _actor_payload(agent)
        active_task_id = _string(actor.get("active_task_id"))
        if not active_task_id or active_task_id == task_id:
            continue
        current_active_task = tasks_by_id.get(active_task_id)
        task_stale_minutes = _minutes_since(getattr(task, "updated_at", ""), now_iso=now_iso)
        if task_stale_minutes < 10:
            continue
        candidates.append(
            _candidate(
                kind="ownership_drift",
                priority=78,
                hypothesis="The task is still assigned to this actor, but the actor's mounted task has shifted elsewhere.",
                actor=actor,
                task=task,
                current_active_task=current_active_task,
                task_stale_minutes=task_stale_minutes,
            )
        )

    candidates.sort(
        key=lambda item: (
            -int(item.get("priority") or 0),
            _string(item.get("kind")),
            _string(((item.get("task") or {}).get("id"))),
        )
    )
    return candidates[: max(0, int(limit or 0))]
