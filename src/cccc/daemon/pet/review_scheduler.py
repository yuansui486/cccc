from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Set

from ...contracts.v1.notify import SystemNotifyData
from ...daemon.messaging.delivery import emit_system_notify
from ...kernel.group import get_group_state, load_group
from ...kernel.pet_actor import PET_ACTOR_ID, get_pet_actor, is_desktop_pet_enabled
from ...paths import ensure_home
from ...util.fs import atomic_write_json, read_json

PET_REVIEW_DEBOUNCE_SECONDS = 1.2
PET_REVIEW_MIN_INTERVAL_SECONDS = 3.0
PET_REVIEW_MAX_DELAY_SECONDS = 10.0
_PERSIST_SCHEMA = 1
_PERSIST_FILENAME = "pet_review_pending.json"
LOGGER = logging.getLogger(__name__)


@dataclass
class _PetReviewState:
    dirty_since: float = 0.0
    last_dispatched_at: float = 0.0
    due_at: float = 0.0
    reasons: Set[str] = field(default_factory=set)
    source_event_id: str = ""
    timer: Optional[threading.Timer] = None


_LOCK = threading.Lock()
_STATE_BY_GROUP: Dict[str, _PetReviewState] = {}


def _normalize_reason(value: str) -> str:
    return str(value or "").strip().lower() or "state_changed"


def _pending_review_path(group_id: str) -> Path:
    gid = str(group_id or "").strip()
    if not gid:
        return ensure_home() / "groups" / "_invalid" / "state" / _PERSIST_FILENAME
    return ensure_home() / "groups" / gid / "state" / _PERSIST_FILENAME


def _clear_pending_review_file(group_id: str) -> None:
    try:
        _pending_review_path(group_id).unlink(missing_ok=True)
    except Exception:
        pass


def _monotonic_to_wall(ts: float, *, now_wall: float, now_mono: float) -> float:
    if ts <= 0.0:
        return 0.0
    return max(0.0, now_wall + (ts - now_mono))


def _persist_pending_review(group_id: str, state: _PetReviewState) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    path = _pending_review_path(gid)
    path.parent.mkdir(parents=True, exist_ok=True)
    now_wall = time.time()
    now_mono = time.monotonic()
    last_dispatched_wall = _monotonic_to_wall(state.last_dispatched_at, now_wall=now_wall, now_mono=now_mono)
    dirty_since_wall = _monotonic_to_wall(state.dirty_since, now_wall=now_wall, now_mono=now_mono)
    due_at_wall = _monotonic_to_wall(state.due_at, now_wall=now_wall, now_mono=now_mono)
    atomic_write_json(
        path,
        {
            "schema": _PERSIST_SCHEMA,
            "group_id": gid,
            "dirty_since_wall": dirty_since_wall,
            "last_dispatched_wall": last_dispatched_wall,
            "due_at_wall": due_at_wall,
            "reasons": sorted({item for item in state.reasons if item}),
            "source_event_id": str(state.source_event_id or "").strip(),
        },
        indent=2,
    )


def _cancel_timer(state: _PetReviewState) -> None:
    timer = state.timer
    state.timer = None
    if timer is not None:
        timer.cancel()


def _can_review_now(group_id: str) -> bool:
    group = load_group(group_id)
    if group is None:
        return False
    if not is_desktop_pet_enabled(group):
        return False
    if get_group_state(group) != "active":
        return False
    actor = get_pet_actor(group)
    if not isinstance(actor, dict):
        return False
    if not bool(actor.get("enabled", True)):
        return False
    return True


def _pet_review_notify(reasons: Set[str], source_event_id: str) -> SystemNotifyData:
    return SystemNotifyData(
        kind="info",
        priority="normal",
        title="Pet review requested",
        message="Re-evaluate current coordination state and refresh pet decisions.",
        target_actor_id=PET_ACTOR_ID,
        requires_ack=False,
        related_event_id=source_event_id or None,
        context={
            "kind": "pet_review",
            "reasons": sorted({item for item in reasons if item}),
            "source_event_id": source_event_id or None,
        },
    )


def _emit_pet_review(group_id: str, reasons: Set[str], source_event_id: str) -> None:
    group = load_group(group_id)
    if group is None:
        return
    if not _can_review_now(group_id):
        return
    emit_system_notify(group, by="system", notify=_pet_review_notify(reasons, source_event_id))


def request_manual_pet_review(
    group_id: str,
    *,
    reason: str,
    source_event_id: str = "",
) -> bool:
    gid = str(group_id or "").strip()
    if not gid:
        return False
    group = load_group(gid)
    if group is None:
        return False
    if not is_desktop_pet_enabled(group):
        return False
    if get_group_state(group) not in {"active", "idle"}:
        return False
    actor = get_pet_actor(group)
    if not isinstance(actor, dict):
        return False
    if not bool(actor.get("enabled", True)):
        return False
    emit_system_notify(
        group,
        by="system",
        notify=_pet_review_notify({_normalize_reason(reason)}, source_event_id),
    )
    return True


def _flush_due_review(group_id: str) -> None:
    with _LOCK:
        state = _STATE_BY_GROUP.get(group_id)
        if state is None:
            return
        state.timer = None
        if not _can_review_now(group_id):
            state.due_at = 0.0
            _persist_pending_review(group_id, state)
            return
        reasons = set(state.reasons)
        source_event_id = state.source_event_id
        state.reasons.clear()
        state.source_event_id = ""
        state.dirty_since = 0.0
        state.due_at = 0.0
        state.last_dispatched_at = time.monotonic()
        _STATE_BY_GROUP.pop(group_id, None)
    _clear_pending_review_file(group_id)
    _emit_pet_review(group_id, reasons, source_event_id)


def _schedule_locked(group_id: str, state: _PetReviewState, *, immediate: bool) -> None:
    now = time.monotonic()
    if state.dirty_since <= 0.0:
        state.dirty_since = now

    due_at = max(
        state.last_dispatched_at + PET_REVIEW_MIN_INTERVAL_SECONDS,
        now if immediate else state.dirty_since + PET_REVIEW_DEBOUNCE_SECONDS,
    )
    max_due_at = state.dirty_since + PET_REVIEW_MAX_DELAY_SECONDS
    if due_at > max_due_at:
        due_at = max_due_at
    state.due_at = due_at
    if due_at <= now:
        _cancel_timer(state)
        timer = threading.Timer(0.0, _flush_due_review, args=(group_id,))
        timer.daemon = True
        state.timer = timer
        _persist_pending_review(group_id, state)
        timer.start()
        return

    delay = max(0.0, due_at - now)
    _cancel_timer(state)
    timer = threading.Timer(delay, _flush_due_review, args=(group_id,))
    timer.daemon = True
    state.timer = timer
    _persist_pending_review(group_id, state)
    timer.start()


def request_pet_review(
    group_id: str,
    *,
    reason: str,
    source_event_id: str = "",
    immediate: bool = False,
) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    if not _can_review_now(gid):
        return
    persisted_reasons: Set[str] = set()
    persisted_source_event_id = ""
    pending_path = _pending_review_path(gid)
    payload = read_json(pending_path)
    if isinstance(payload, dict) and not _STATE_BY_GROUP.get(gid):
        persisted_reasons = {
            _normalize_reason(item)
            for item in (payload.get("reasons") if isinstance(payload.get("reasons"), list) else [])
            if str(item or "").strip()
        }
        persisted_source_event_id = str(payload.get("source_event_id") or "").strip()
    normalized_reason = _normalize_reason(reason)
    with _LOCK:
        state = _STATE_BY_GROUP.setdefault(gid, _PetReviewState())
        state.reasons.update(persisted_reasons)
        if persisted_source_event_id and not state.source_event_id:
            state.source_event_id = persisted_source_event_id
        state.reasons.add(normalized_reason)
        if source_event_id:
            state.source_event_id = str(source_event_id).strip()
        _schedule_locked(gid, state, immediate=immediate)


def cancel_pet_review(group_id: str) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    with _LOCK:
        state = _STATE_BY_GROUP.pop(gid, None)
    if state is not None:
        _cancel_timer(state)
    _clear_pending_review_file(gid)


def recover_pending_pet_reviews() -> None:
    groups_root = ensure_home() / "groups"
    if not groups_root.exists():
        return
    now_wall = time.time()
    now_mono = time.monotonic()
    for path in groups_root.glob(f"*/state/{_PERSIST_FILENAME}"):
        try:
            payload = read_json(path)
            if not isinstance(payload, dict):
                path.unlink(missing_ok=True)
                continue
            group_id = str(payload.get("group_id") or path.parent.parent.name or "").strip()
            if not group_id:
                path.unlink(missing_ok=True)
                continue
            if not _can_review_now(group_id):
                continue
            reasons_raw = payload.get("reasons")
            reasons = {
                _normalize_reason(item)
                for item in (reasons_raw if isinstance(reasons_raw, list) else [])
                if str(item or "").strip()
            }
            if not reasons:
                path.unlink(missing_ok=True)
                continue
            due_at_wall = float(payload.get("due_at_wall") or 0.0)
            dirty_since_wall = float(payload.get("dirty_since_wall") or 0.0)
            last_dispatched_wall = float(payload.get("last_dispatched_wall") or 0.0)
            with _LOCK:
                state = _STATE_BY_GROUP.setdefault(group_id, _PetReviewState())
                state.reasons = set(reasons)
                state.source_event_id = str(payload.get("source_event_id") or "").strip()
                state.dirty_since = now_mono - max(0.0, now_wall - dirty_since_wall) if dirty_since_wall > 0.0 else 0.0
                state.last_dispatched_at = (
                    now_mono - max(0.0, now_wall - last_dispatched_wall) if last_dispatched_wall > 0.0 else 0.0
                )
                if due_at_wall > 0.0:
                    state.due_at = now_mono + max(0.0, due_at_wall - now_wall)
                else:
                    state.due_at = now_mono
                _schedule_locked(group_id, state, immediate=False)
        except Exception as exc:
            LOGGER.warning("recover_pending_pet_reviews_failed path=%s err=%s", path, exc)
