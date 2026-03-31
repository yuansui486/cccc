from __future__ import annotations

from typing import Any, Dict, Iterable, List


def enum_text(value: Any) -> str:
    if hasattr(value, "value"):
        try:
            return str(value.value or "").strip().lower()
        except Exception:
            return str(value).strip().lower()
    return str(value or "").strip().lower()


def trim_task_text(value: Any, *, max_len: int = 72) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= max_len:
        return text
    return text[: max(1, max_len - 1)].rstrip() + "…"


def task_brief(task: Any) -> str:
    task_id = str(getattr(task, "id", "") or "").strip()
    title = trim_task_text(getattr(task, "title", "") or task_id, max_len=64)
    assignee = str(getattr(task, "assignee", "") or "").strip()
    if assignee:
        return f"{task_id}:{title} @{assignee}" if task_id else f"{title} @{assignee}"
    return f"{task_id}:{title}" if task_id else title


def join_task_briefs(tasks: Iterable[Any], *, limit: int = 3) -> str:
    out: List[str] = []
    for task in list(tasks)[:limit]:
        brief = task_brief(task)
        if brief:
            out.append(brief)
    return " ; ".join(out)


def build_task_triage_payload(tasks: Iterable[Any], *, limit: int = 3) -> Dict[str, List[Any]]:
    blocked_tasks: List[Any] = []
    waiting_user_tasks: List[Any] = []
    handoff_tasks: List[Any] = []
    planned_backlog_tasks: List[Any] = []

    for task in tasks:
        status = enum_text(getattr(task, "status", ""))
        waiting_on = enum_text(getattr(task, "waiting_on", ""))
        blocked_by = list(getattr(task, "blocked_by", []) or [])
        handoff_to = str(getattr(task, "handoff_to", "") or "").strip()

        if status in {"done", "completed", "archived"}:
            continue
        if blocked_by or waiting_on in {"actor", "external"}:
            blocked_tasks.append(task)
        if waiting_on == "user":
            waiting_user_tasks.append(task)
        if handoff_to:
            handoff_tasks.append(task)
        if status == "planned":
            planned_backlog_tasks.append(task)

    return {
        "blocked_tasks": blocked_tasks[:limit],
        "waiting_user_tasks": waiting_user_tasks[:limit],
        "handoff_tasks": handoff_tasks[:limit],
        "planned_backlog_tasks": planned_backlog_tasks[:limit],
    }
