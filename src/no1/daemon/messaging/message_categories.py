from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from ...kernel.actors import get_effective_role
from ...ports.mcp.utils.help_markdown import parse_help_markdown
from ...util.fs import atomic_write_text, read_json
from ...util.time import utc_now_iso
from ...kernel.prompt_files import HELP_FILENAME, read_group_prompt_file


ROLE_PROMPT_DEFAULT_EVERY = 5
ROLE_PROMPT_MAX_EVERY = 1000
ROLE_PROMPT_STATE_VERSION = 1
_STATE_LOCK = threading.RLock()
_RECENT_EVENT_LIMIT = 2048


def category(tag: str, text: str = "") -> str:
    value = str(text or "").strip()
    prefix = f"[{str(tag or '').strip()}]"
    return f"{prefix} {value}" if value else prefix


def normalize_role_prompt_every(value: Any, *, default: int = ROLE_PROMPT_DEFAULT_EVERY) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)
    return max(0, min(ROLE_PROMPT_MAX_EVERY, number))


def actor_role_prompt(group: Any, actor_id: str) -> str:
    aid = str(actor_id or "").strip()
    if not aid:
        return ""
    try:
        pf = read_group_prompt_file(group, HELP_FILENAME)
        if not pf.found or not isinstance(pf.content, str) or not pf.content.strip():
            return ""
        note = str(parse_help_markdown(pf.content).get("actor_notes", {}).get(aid) or "").strip()
        if not note:
            return ""
        role = str(get_effective_role(group, aid) or "peer").strip() or "peer"
        group_name = str(getattr(group, "doc", {}).get("title") or getattr(group, "group_id", "") or "").strip()
        group_name = " ".join(group_name.split())
        if not group_name:
            group_name = str(getattr(group, "group_id", "") or "group").strip()
        return f"[role] You are {aid} ({role}) in group {group_name}.\n{note}"
    except Exception:
        return ""


@dataclass(frozen=True)
class RolePromptDecision:
    batch_id: str
    event_ids: tuple[str, ...]
    inject: bool
    role_block: str = ""


def _state_path(group: Any) -> Path:
    return Path(group.path) / "state" / "role_prompt_delivery.json"


def _load(group: Any) -> dict[str, Any]:
    try:
        raw = read_json(_state_path(group))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw.setdefault("v", ROLE_PROMPT_STATE_VERSION)
    raw.setdefault("message_count", 0)
    raw.setdefault("committed_event_ids", [])
    raw.setdefault("decisions", {})
    raw.setdefault("batches", {})
    try:
        raw["message_count"] = max(0, int(raw.get("message_count") or 0))
    except Exception:
        raw["message_count"] = 0
    raw["committed_event_ids"] = [str(x) for x in raw.get("committed_event_ids") or [] if str(x)][-_RECENT_EVENT_LIMIT:]
    if not isinstance(raw["decisions"], dict):
        raw["decisions"] = {}
    if not isinstance(raw["batches"], dict):
        raw["batches"] = {}
    return raw


def _save(group: Any, state: dict[str, Any]) -> None:
    path = _state_path(group)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _batch_id(group: Any, event_ids: Iterable[str]) -> str:
    import hashlib

    ids = [str(x or "").strip() for x in event_ids if str(x or "").strip()]
    raw = f"{str(getattr(group, 'group_id', '') or '')}|{','.join(ids)}"
    return "rolebatch:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def plan_role_prompt_delivery(group: Any, *, actor_id: str, event_ids: Iterable[str]) -> RolePromptDecision:
    ids = tuple(dict.fromkeys(str(x or "").strip() for x in event_ids if str(x or "").strip()))
    batch_id = _batch_id(group, ids)
    role_block = actor_role_prompt(group, actor_id)
    messaging = group.doc.get("messaging") if isinstance(group.doc.get("messaging"), dict) else {}
    every = normalize_role_prompt_every(messaging.get("role_prompt_every_messages"))
    with _STATE_LOCK:
        state = _load(group)
        committed = set(state["committed_event_ids"])
        decisions = state["decisions"]
        before = int(state.get("message_count") or 0)
        cursor = before
        inject = False
        for event_id in ids:
            if event_id in committed:
                continue
            existing = decisions.get(event_id)
            if existing is None:
                cursor += 1
                crossed = every > 0 and (cursor // every) > ((cursor - 1) // every)
                decisions[event_id] = bool(crossed)
            if bool(decisions.get(event_id)):
                inject = True
        batch = state["batches"].get(batch_id) if isinstance(state["batches"].get(batch_id), dict) else {}
        batch.update({"event_ids": list(ids), "inject": bool(inject), "created_at": batch.get("created_at") or utc_now_iso()})
        state["batches"][batch_id] = batch
        _save(group, state)
    return RolePromptDecision(batch_id=batch_id, event_ids=ids, inject=bool(inject and role_block), role_block=role_block if inject else "")


def commit_role_prompt_delivery(group: Any, *, decision: Optional[RolePromptDecision], actor_id: str) -> None:
    if decision is None or not decision.event_ids:
        return
    batch_id = str(decision.batch_id or "")
    aid = str(actor_id or "").strip()
    with _STATE_LOCK:
        state = _load(group)
        batch = state["batches"].get(batch_id) if isinstance(state["batches"].get(batch_id), dict) else {}
        accepted = {str(x) for x in batch.get("accepted_actor_ids") or [] if str(x)}
        if aid:
            accepted.add(aid)
        batch["accepted_actor_ids"] = sorted(accepted)
        state["batches"][batch_id] = batch
        committed = list(state["committed_event_ids"])
        seen = set(committed)
        added = 0
        for event_id in decision.event_ids:
            if event_id in seen:
                continue
            committed.append(event_id)
            seen.add(event_id)
            added += 1
        state["committed_event_ids"] = committed[-_RECENT_EVENT_LIMIT:]
        state["message_count"] = max(0, int(state.get("message_count") or 0)) + added
        _save(group, state)
