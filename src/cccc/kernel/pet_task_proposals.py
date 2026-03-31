from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..util.time import parse_utc_iso, utc_now_iso

from .pet_task_triage import enum_text, task_brief, trim_task_text


_TASK_PROPOSAL_ECHO_SUPPRESS_SECONDS = 10 * 60
_RECENT_USER_MESSAGE_SCAN_LIMIT = 12


def _proposal(
    *,
    priority: int,
    reason: str,
    summary: str,
    operation: str,
    task: Any,
    status: str = "",
    assignee: str = "",
) -> Dict[str, Any]:
    task_id = str(getattr(task, "id", "") or "").strip()
    title = trim_task_text(getattr(task, "title", "") or "", max_len=120)
    return {
        "priority": int(priority),
        "reason": reason,
        "summary": summary,
        "action": {
            "type": "task_proposal",
            "operation": operation,
            "task_id": task_id,
            "title": title,
            "status": status,
            "assignee": assignee or str(getattr(task, "assignee", "") or "").strip(),
        },
    }


def _matches_focus(reason: str, *, focus: str) -> bool:
    normalized_reason = str(reason or "").strip().lower()
    normalized_focus = str(focus or "").strip().lower()
    if normalized_focus in {"", "none", "task_pressure"}:
        return True
    if normalized_focus == "waiting_user":
        return normalized_reason == "waiting_user"
    if normalized_focus == "handoff":
        return normalized_reason == "handoff"
    if normalized_focus == "blocked":
        return normalized_reason == "blocked"
    return False


def _normalize_compare_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _quoted_task_title(value: Any) -> str:
    escaped = str(value or "").replace('"', '\\"')
    return f'"{escaped}"'


def _build_task_proposal_message(action: Dict[str, Any]) -> str:
    explicit = str(action.get("text") or "").strip()
    if explicit:
        return explicit

    refs: list[str] = []
    task_id = str(action.get("task_id") or "").strip()
    title = str(action.get("title") or "").strip()
    status = str(action.get("status") or "").strip()
    assignee = str(action.get("assignee") or "").strip()
    if task_id:
        refs.append(f"task_id={task_id}")
    if title:
        refs.append(f"title={_quoted_task_title(title)}")
    if status:
        refs.append(f"status={status}")
    if assignee:
        refs.append(f"assignee={assignee}")

    op = str(action.get("operation") or "").strip().lower() or "update"
    op_text = op if op in {"create", "update", "move", "handoff", "archive"} else "update"
    suffix = f" ({', '.join(refs)})" if refs else ""
    return f"Pet task proposal: please use cccc_task to {op_text} this task{suffix}."


def _event_targets_foreman(event: Dict[str, Any]) -> bool:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    raw_to = data.get("to")
    if not isinstance(raw_to, list):
        return False
    for item in raw_to:
        normalized = _normalize_compare_text(item)
        if normalized in {"@foreman", "foreman"}:
            return True
    return False


def _event_mentions_task(action: Dict[str, Any], text: Any) -> bool:
    normalized_text = _normalize_compare_text(text)
    if not normalized_text:
        return False
    expected = _normalize_compare_text(_build_task_proposal_message(action))
    return bool(expected) and normalized_text == expected


def _is_user_message_to_foreman(event: Dict[str, Any]) -> bool:
    if str(event.get("kind") or "").strip() != "chat.message":
        return False
    if str(event.get("by") or "").strip() != "user":
        return False
    return _event_targets_foreman(event)


def _is_recent_user_echo_for_task(
    action: Dict[str, Any],
    recent_chat_events: Iterable[Dict[str, Any]],
) -> bool:
    now_dt = parse_utc_iso(utc_now_iso())
    if now_dt is None:
        return False

    scanned = 0
    for event in recent_chat_events:
        if not isinstance(event, dict):
            continue
        if not _is_user_message_to_foreman(event):
            continue
        scanned += 1
        if scanned > _RECENT_USER_MESSAGE_SCAN_LIMIT:
            break
        event_dt = parse_utc_iso(str(event.get("ts") or "").strip())
        if event_dt is None:
            continue
        age_seconds = (now_dt - event_dt).total_seconds()
        if age_seconds < 0 or age_seconds > _TASK_PROPOSAL_ECHO_SUPPRESS_SECONDS:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if _event_mentions_task(action, data.get("text")):
            return True
    return False


def build_task_proposal_candidates(
    tasks: Iterable[Any],
    *,
    signal_payload: Optional[Dict[str, Any]] = None,
    recent_chat_events: Optional[Iterable[Dict[str, Any]]] = None,
    limit: int = 1,
) -> List[Dict[str, Any]]:
    proposals: List[Dict[str, Any]] = []
    proposal_ready = (
        signal_payload.get("proposal_ready")
        if isinstance(signal_payload, dict) and isinstance(signal_payload.get("proposal_ready"), dict)
        else {}
    )
    focus = str(proposal_ready.get("focus") or "none").strip().lower()
    ready = bool(proposal_ready.get("ready")) if proposal_ready else False
    if ready and focus in {"reply_pressure", "coordination_rhythm"}:
        return []
    for task in tasks:
        status = enum_text(getattr(task, "status", ""))
        waiting_on = enum_text(getattr(task, "waiting_on", ""))
        blocked_by = list(getattr(task, "blocked_by", []) or [])
        handoff_to = str(getattr(task, "handoff_to", "") or "").strip()
        assignee = str(getattr(task, "assignee", "") or "").strip()
        title = trim_task_text(getattr(task, "title", "") or "", max_len=72)
        brief = task_brief(task)

        if status in {"done", "completed", "archived"}:
            continue

        if waiting_on == "user":
            next_status = "active" if status == "planned" else status or "active"
            proposals.append(
                _proposal(
                    priority=100,
                    reason="waiting_user",
                    summary=f"{brief} is waiting on the user; foreman should close the dependency or push it forward.",
                    operation="move" if next_status else "update",
                    task=task,
                    status=next_status,
                    assignee=assignee,
                )
            )
            continue

        if handoff_to:
            proposals.append(
                _proposal(
                    priority=90,
                    reason="handoff",
                    summary=f"{brief} was handed off to {handoff_to}; foreman should confirm ownership and next step.",
                    operation="handoff",
                    task=task,
                    assignee=handoff_to,
                )
            )
            continue

        if blocked_by or waiting_on in {"actor", "external"}:
            blocker_text = ""
            if blocked_by:
                blocker_text = f"（blocked_by={', '.join(str(item) for item in blocked_by[:3])}）"
            proposals.append(
                _proposal(
                    priority=80,
                    reason="blocked",
                    summary=f"{brief} is blocked{blocker_text}; foreman should coordinate the unblock path.",
                    operation="update",
                    task=task,
                    assignee=assignee,
                )
            )
            continue

        if status == "planned" and not assignee:
            proposals.append(
                _proposal(
                    priority=70,
                    reason="planned_backlog",
                    summary=f"{brief} is still planned with no owner; foreman should decide whether to start or prune it.",
                    operation="update",
                    task=task,
                )
            )

    if recent_chat_events is not None:
        proposals = [
            item
            for item in proposals
            if not _is_recent_user_echo_for_task(
                item.get("action") if isinstance(item.get("action"), dict) else {},
                recent_chat_events,
            )
        ]

    if ready and focus not in {"", "none", "task_pressure"}:
        proposals = [
            item for item in proposals
            if _matches_focus(str(item.get("reason") or ""), focus=focus)
        ]

    proposals.sort(key=lambda item: (-int(item.get("priority") or 0), str(item.get("reason") or ""), str(((item.get("action") or {}).get("task_id") or ""))))
    return proposals[: max(1, int(limit or 1))]


def build_task_proposal_summary_lines(
    tasks: Iterable[Any],
    *,
    signal_payload: Optional[Dict[str, Any]] = None,
    recent_chat_events: Optional[Iterable[Dict[str, Any]]] = None,
    limit: int = 1,
) -> List[str]:
    lines: List[str] = []
    for item in build_task_proposal_candidates(
        tasks,
        signal_payload=signal_payload,
        recent_chat_events=recent_chat_events,
        limit=limit,
    ):
        summary = str(item.get("summary") or "").strip()
        if summary:
            lines.append(summary)
    return lines
