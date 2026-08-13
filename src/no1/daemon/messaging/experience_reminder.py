from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from ...kernel.actors import find_foreman, list_visible_actors
from ...kernel.experience import read_experience
from ...kernel.group import Group
from ...util.conv import coerce_bool
from ...util.fs import atomic_write_text, read_json
from ...util.time import utc_now_iso


EXPERIENCE_REMINDER_LINE = (
    "[onecolleague experience] The foreman owns this review. Read the shared experience with "
    '`onecolleague_experience(action="read")`, apply verified lessons and confirmed user preferences, then append '
    "only new reusable findings or clearly evidenced stable preferences, "
    "then finish the cycle with review_complete; use result=no_change without writing when there is nothing new."
)

_STATE_LOCK = threading.RLock()
_RECENT_EVENT_LIMIT = 512
_HISTORY_LIMIT = 20
_CANDIDATE_LIMIT = 4000
_PEER_TIMEOUT_SECONDS = 300
_FOREMAN_RETRY_SECONDS = 300


@dataclass(frozen=True)
class ExperienceReminderDecision:
    group_id: str
    scope_key: str
    actor_id: str
    event_ids: tuple[str, ...]
    due: bool


def _config(group: Group) -> tuple[bool, int, int]:
    raw = group.doc.get("experience") if isinstance(group.doc.get("experience"), dict) else {}
    enabled = coerce_bool(raw.get("reminder_enabled"), default=True)
    try:
        every = int(raw.get("reminder_every_user_messages", 10))
    except Exception:
        every = 10
    try:
        force_after = int(raw.get("force_review_after_unwritten_reminders", 5))
    except Exception:
        force_after = 5
    return enabled, max(1, min(every, 1000)), max(1, min(force_after, 100))


def _state_path(group: Group):
    return group.path / "state" / "experience_reminders.json"


def _new_scope() -> dict[str, Any]:
    return {
        "message_count": 0,
        "recent_event_ids": [],
        "reminder_count": 0,
        "consecutive_unwritten": 0,
        "active_cycle": {},
        "last_result": {},
        "history": [],
    }


def _normalize_scope(raw: Any) -> dict[str, Any]:
    scope = raw if isinstance(raw, dict) else {}
    if isinstance(scope.get("actors"), dict):
        merged: list[str] = []
        for actor in scope.get("actors", {}).values():
            if not isinstance(actor, dict):
                continue
            for event_id in actor.get("recent_event_ids") or []:
                value = str(event_id or "").strip()
                if value and value not in merged:
                    merged.append(value)
        scope = {"recent_event_ids": merged, "message_count": len(merged)}
    defaults = _new_scope()
    for key, value in defaults.items():
        scope.setdefault(key, value)
    scope["recent_event_ids"] = [str(item) for item in scope.get("recent_event_ids") or [] if str(item)][
        -_RECENT_EVENT_LIMIT:
    ]
    for key in ("message_count", "reminder_count", "consecutive_unwritten"):
        try:
            scope[key] = max(0, int(scope.get(key) or 0))
        except Exception:
            scope[key] = 0
    if not isinstance(scope.get("active_cycle"), dict):
        scope["active_cycle"] = {}
    if not isinstance(scope.get("last_result"), dict):
        scope["last_result"] = {}
    if not isinstance(scope.get("history"), list):
        scope["history"] = []
    scope["history"] = [item for item in scope["history"] if isinstance(item, dict)][-_HISTORY_LIMIT:]
    scope.pop("actors", None)
    return scope


def _load_state(group: Group) -> dict[str, Any]:
    try:
        raw = read_json(_state_path(group))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    scopes = raw.get("scopes") if isinstance(raw.get("scopes"), dict) else {}
    raw["scopes"] = {str(key): _normalize_scope(value) for key, value in scopes.items()}
    raw["v"] = 2
    return raw


def _scope_state(state: dict[str, Any], scope_key: str) -> dict[str, Any]:
    scopes = state.setdefault("scopes", {})
    scope = _normalize_scope(scopes.get(scope_key))
    scopes[scope_key] = scope
    return scope


def _save_state(group: Group, state: dict[str, Any]) -> None:
    path = _state_path(group)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _record_result(scope: dict[str, Any], cycle: dict[str, Any], *, result: str, summary: str = "") -> None:
    now = utc_now_iso()
    item = {
        "cycle_id": str(cycle.get("cycle_id") or ""),
        "result": result,
        "forced": bool(cycle.get("forced")),
        "at": now,
        "foreman_id": str(cycle.get("foreman_id") or ""),
    }
    if summary:
        item["summary"] = str(summary)[:500]
    scope["last_result"] = item
    scope["history"] = (list(scope.get("history") or []) + [item])[-_HISTORY_LIMIT:]
    scope["active_cycle"] = {}
    if result == "written" or (bool(cycle.get("forced")) and result == "no_change"):
        scope["consecutive_unwritten"] = 0


def _enabled_peers(group: Group, foreman_id: str) -> list[str]:
    out: list[str] = []
    for actor in list_visible_actors(group):
        aid = str(actor.get("id") or "").strip()
        if aid and aid != foreman_id and coerce_bool(actor.get("enabled"), default=True):
            out.append(aid)
    return out


def _new_cycle(group: Group, scope_key: str, scope: dict[str, Any], force_after: int) -> dict[str, Any]:
    foreman = find_foreman(group) or {}
    foreman_id = str(foreman.get("id") or "").strip()
    forced = int(scope.get("consecutive_unwritten") or 0) + 1 >= force_after
    peers = _enabled_peers(group, foreman_id) if forced else []
    now = datetime.now(timezone.utc)
    revision = read_experience(group, ensure=True).revision
    cycle = {
        "cycle_id": f"exp_{uuid.uuid4().hex}",
        "scope_key": scope_key,
        "foreman_id": foreman_id,
        "forced": forced,
        "state": "collecting" if forced and peers else "awaiting_foreman",
        "starting_revision": revision,
        "requested_peer_ids": peers,
        "received_peer_ids": [],
        "candidates": [],
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    if forced and peers:
        cycle["collection_deadline_at"] = (now + timedelta(seconds=_PEER_TIMEOUT_SECONDS)).isoformat()
    scope["reminder_count"] = int(scope.get("reminder_count") or 0) + 1
    scope["consecutive_unwritten"] = int(scope.get("consecutive_unwritten") or 0) + 1
    if not foreman_id:
        cycle["state"] = "failed"
        _record_result(scope, cycle, result="failed", summary="No foreman is configured for this workgroup.")
        # Infrastructure failure must not consume the forced-review threshold.
        scope["consecutive_unwritten"] = max(force_after, int(scope.get("consecutive_unwritten") or 0))
        return {}
    scope["active_cycle"] = cycle
    return cycle


def _notify(group: Group, actor_id: str, title: str, message: str) -> None:
    if not actor_id:
        return
    try:
        from ...contracts.v1 import SystemNotifyData
        from .delivery import emit_system_notify

        emit_system_notify(
            group,
            by="experience",
            notify=SystemNotifyData(
                kind="info",
                priority="normal",
                title=title,
                message=message,
                target_actor_id=actor_id,
            ),
        )
    except Exception:
        return


def _dispatch_cycle(group: Group, cycle: dict[str, Any]) -> None:
    cycle_id = str(cycle.get("cycle_id") or "")
    if cycle.get("state") == "collecting":
        message = (
            f"经验强制复核周期 {cycle_id} 正在收集意见。请阅读当前 EXPERIENCE.md 和近期对话，"
            "只提交新的、已验证且可复用的候选经验；没有候选也要提交空结论。调用 "
            f'onecolleague_experience(action="candidate_submit", cycle_id="{cycle_id}", content="...")；'
            "没有新增时 content 传空字符串。"
        )
        for peer_id in cycle.get("requested_peer_ids") or []:
            _notify(group, str(peer_id), "经验候选收集", message)
        return
    foreman_id = str(cycle.get("foreman_id") or "")
    forced_note = "这是强制复核，先综合各成员候选。" if cycle.get("forced") else ""
    _notify(
        group,
        foreman_id,
        "沉淀经验复核",
        f"{forced_note}复核周期 {cycle_id}：先调用 review_status 读取成员候选，再读取共享经验与近期对话。"
        "若有新增，仅由你 append/replace；"
        f'完成后调用 onecolleague_experience(action="review_complete", cycle_id="{cycle_id}", '
        'result="written")。若无新增，不要修改文件，使用 result="no_change"。',
    )


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
    enabled, every, _force_after = _config(group)
    candidates: list[str] = []
    for item in messages:
        if isinstance(item, dict):
            kind = str(item.get("kind") or "chat.message")
            by = str(item.get("by") or "")
            event_id = str(item.get("id") or item.get("event_id") or "").strip()
            item_scope = str(item.get("scope_key") or "").strip()
        else:
            kind = str(getattr(item, "kind", "chat.message") or "chat.message")
            by = str(getattr(item, "by", "") or "")
            event_id = str(getattr(item, "event_id", "") or "").strip()
            item_scope = str(getattr(item, "scope_key", "") or "").strip()
        if item_scope and not scope_key:
            resolved_scope = item_scope
        if kind == "chat.message" and by == "user" and event_id and event_id not in candidates:
            candidates.append(event_id)
    if not enabled or not gid or not aid or not candidates:
        return ExperienceReminderDecision(gid, resolved_scope, aid, tuple(), False)

    with _STATE_LOCK:
        state = _load_state(group)
        scope = _scope_state(state, resolved_scope)
        recent = {str(item) for item in scope.get("recent_event_ids") or []}
        unseen = tuple(event_id for event_id in candidates if event_id not in recent)
        before = int(scope.get("message_count") or 0)
        due = bool(unseen) and (before + len(unseen)) // every > before // every
    return ExperienceReminderDecision(gid, resolved_scope, aid, unseen, due)


def commit_experience_reminder(group: Group, decision: ExperienceReminderDecision) -> None:
    if not decision.event_ids:
        return
    cycle_to_dispatch: dict[str, Any] = {}
    with _STATE_LOCK:
        state = _load_state(group)
        scope = _scope_state(state, decision.scope_key)
        recent_list = [str(item) for item in scope.get("recent_event_ids") or [] if str(item)]
        recent = set(recent_list)
        added = [event_id for event_id in decision.event_ids if event_id not in recent]
        if not added:
            return
        before = int(scope.get("message_count") or 0)
        scope["message_count"] = before + len(added)
        scope["recent_event_ids"] = (recent_list + added)[-_RECENT_EVENT_LIMIT:]
        enabled, every, force_after = _config(group)
        due = enabled and scope["message_count"] // every > before // every
        if due:
            active = scope.get("active_cycle") if isinstance(scope.get("active_cycle"), dict) else {}
            if not active:
                cycle_to_dispatch = dict(_new_cycle(group, decision.scope_key, scope, force_after))
            elif not bool(active.get("forced")):
                scope["reminder_count"] = int(scope.get("reminder_count") or 0) + 1
                scope["consecutive_unwritten"] = int(scope.get("consecutive_unwritten") or 0) + 1
                if int(scope["consecutive_unwritten"]) >= force_after:
                    peers = _enabled_peers(group, str(active.get("foreman_id") or ""))
                    active["forced"] = True
                    active["requested_peer_ids"] = peers
                    active["received_peer_ids"] = []
                    active["candidates"] = []
                    active["updated_at"] = utc_now_iso()
                    active["state"] = "collecting" if peers else "awaiting_foreman"
                    if peers:
                        active["collection_deadline_at"] = (
                            datetime.now(timezone.utc) + timedelta(seconds=_PEER_TIMEOUT_SECONDS)
                        ).isoformat()
                    cycle_to_dispatch = dict(active)
        _save_state(group, state)
    if cycle_to_dispatch:
        _dispatch_cycle(group, cycle_to_dispatch)


def append_experience_reminder(text: str, decision: ExperienceReminderDecision) -> str:
    out = str(text or "").rstrip("\n")
    # Review work is dispatched once by the daemon to the stable foreman.
    return out


def get_distillation_status(group: Group, *, scope_key: str = "") -> dict[str, Any]:
    resolved_scope = str(scope_key or group.doc.get("active_scope_key") or "").strip() or "default"
    enabled, every, force_after = _config(group)
    with _STATE_LOCK:
        state = _load_state(group)
        scope = _scope_state(state, resolved_scope)
        count = int(scope.get("message_count") or 0)
        active = dict(scope.get("active_cycle") or {})
        if active:
            active = {
                "cycle_id": str(active.get("cycle_id") or ""),
                "state": str(active.get("state") or "pending"),
                "forced": bool(active.get("forced")),
                "foreman_id": str(active.get("foreman_id") or ""),
                "requested_peers": len(active.get("requested_peer_ids") or []),
                "received_peers": len(active.get("received_peer_ids") or []),
                "deadline_at": str(active.get("collection_deadline_at") or ""),
            }
        return {
            "enabled": enabled,
            "reminder_every_user_messages": every,
            "force_review_after_unwritten_reminders": force_after,
            "messages_since_reminder": count % every,
            "messages_until_reminder": every - (count % every),
            "consecutive_unwritten": int(scope.get("consecutive_unwritten") or 0),
            "active_cycle": active or None,
            "last_result": dict(scope.get("last_result") or {}) or None,
            "history": list(reversed(scope.get("history") or [])),
        }


def review_status(group: Group, *, cycle_id: str = "") -> dict[str, Any]:
    scope_key = str(group.doc.get("active_scope_key") or "").strip() or "default"
    with _STATE_LOCK:
        scope = _scope_state(_load_state(group), scope_key)
        cycle = dict(scope.get("active_cycle") or {})
        if cycle_id and str(cycle.get("cycle_id") or "") != cycle_id:
            return {"found": False, "cycle_id": cycle_id}
        return {"found": bool(cycle), "cycle": cycle or None, "status": get_distillation_status(group, scope_key=scope_key)}


def submit_candidate(group: Group, *, cycle_id: str, actor_id: str, content: str) -> dict[str, Any]:
    notify_cycle: dict[str, Any] = {}
    scope_key = str(group.doc.get("active_scope_key") or "").strip() or "default"
    with _STATE_LOCK:
        state = _load_state(group)
        scope = _scope_state(state, scope_key)
        cycle = scope.get("active_cycle") or {}
        if str(cycle.get("cycle_id") or "") != cycle_id or cycle.get("state") != "collecting":
            raise ValueError("review cycle is not collecting candidates")
        requested = [str(item) for item in cycle.get("requested_peer_ids") or []]
        if actor_id not in requested:
            raise PermissionError("actor is not a requested peer")
        received = [str(item) for item in cycle.get("received_peer_ids") or []]
        if actor_id not in received:
            received.append(actor_id)
            cycle["received_peer_ids"] = received
            candidate = str(content or "").strip()
            if candidate:
                cycle.setdefault("candidates", []).append({"actor_id": actor_id, "summary": candidate[:_CANDIDATE_LIMIT]})
        cycle["updated_at"] = utc_now_iso()
        if set(received) >= set(requested):
            cycle["state"] = "awaiting_foreman"
            notify_cycle = dict(cycle)
        _save_state(group, state)
        result = dict(cycle)
    if notify_cycle:
        _dispatch_cycle(group, notify_cycle)
    return result


def complete_review(group: Group, *, cycle_id: str, actor_id: str, result: str, summary: str = "") -> dict[str, Any]:
    scope_key = str(group.doc.get("active_scope_key") or "").strip() or "default"
    with _STATE_LOCK:
        state = _load_state(group)
        scope = _scope_state(state, scope_key)
        cycle = scope.get("active_cycle") or {}
        if str(cycle.get("cycle_id") or "") != cycle_id:
            raise ValueError("review cycle not found")
        if actor_id != str(cycle.get("foreman_id") or ""):
            raise PermissionError("only the foreman can complete an experience review")
        if result not in {"written", "no_change", "failed"}:
            raise ValueError("result must be written, no_change, or failed")
        changed = read_experience(group, ensure=True).revision != str(cycle.get("starting_revision") or "")
        if result == "written" and not changed:
            raise ValueError("EXPERIENCE.md revision did not change")
        if result == "no_change" and changed:
            raise ValueError("EXPERIENCE.md changed; complete the review as written")
        _record_result(scope, cycle, result=result, summary=summary)
        _save_state(group, state)
        return dict(scope.get("last_result") or {})


def advance_experience_reviews(group: Group) -> None:
    notify_cycle: dict[str, Any] = {}
    scope_key = str(group.doc.get("active_scope_key") or "").strip() or "default"
    with _STATE_LOCK:
        state = _load_state(group)
        scope = _scope_state(state, scope_key)
        cycle = scope.get("active_cycle") or {}
        now = datetime.now(timezone.utc)
        if cycle.get("state") == "collecting":
            deadline = _parse_time(cycle.get("collection_deadline_at"))
            if deadline is None or now < deadline:
                return
            cycle["state"] = "awaiting_foreman"
            cycle["updated_at"] = now.isoformat()
            notify_cycle = dict(cycle)
            _save_state(group, state)
        elif cycle.get("state") == "awaiting_foreman":
            last_update = _parse_time(cycle.get("updated_at")) or _parse_time(cycle.get("created_at"))
            if last_update is None or now < last_update + timedelta(seconds=_FOREMAN_RETRY_SECONDS):
                return
            cycle["updated_at"] = now.isoformat()
            notify_cycle = dict(cycle)
            _save_state(group, state)
        else:
            return
    if notify_cycle:
        _dispatch_cycle(group, notify_cycle)
