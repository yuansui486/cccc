from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set

from ...paths import ensure_home
from ...util.fs import atomic_write_json, read_json

LOGGER = logging.getLogger(__name__)

JOB_KIND_PET_REVIEW = "pet_review"
JOB_KIND_PET_PROFILE_REFRESH = "pet_profile_refresh"

TRIGGER_STARTUP_RESUME = "startup_resume"
TRIGGER_EVENT = "event"
TRIGGER_MANUAL = "manual"
TRIGGER_TIMER = "timer"

_PERSIST_SCHEMA = 1
_PERSIST_FILENAME = "assistive_jobs.json"
_VALID_JOB_KINDS = {JOB_KIND_PET_REVIEW, JOB_KIND_PET_PROFILE_REFRESH}
_VALID_TRIGGER_CLASSES = {TRIGGER_STARTUP_RESUME, TRIGGER_EVENT, TRIGGER_MANUAL, TRIGGER_TIMER}


@dataclass
class _AssistiveJobState:
    pending: bool = False
    in_flight: bool = False
    rerun_pending: bool = False
    dirty_since: float = 0.0
    due_at: float = 0.0
    last_started_at: float = 0.0
    last_finished_at: float = 0.0
    last_trigger_class: str = ""
    reasons: Set[str] = field(default_factory=set)
    source_event_id: str = ""
    suppressed_reason: str = ""
    timer: Optional[threading.Timer] = None


_LOCK = threading.Lock()
_STATE_BY_GROUP: Dict[str, Dict[str, _AssistiveJobState]] = {}


def _normalize_job_kind(value: str) -> str:
    job_kind = str(value or "").strip().lower()
    if job_kind not in _VALID_JOB_KINDS:
        raise ValueError(f"unsupported assistive job kind: {value}")
    return job_kind


def _normalize_trigger_class(value: str) -> str:
    trigger_class = str(value or "").strip().lower()
    if trigger_class not in _VALID_TRIGGER_CLASSES:
        return TRIGGER_EVENT
    return trigger_class


def _normalize_reason(value: str) -> str:
    return str(value or "").strip().lower()


def _trigger_rank(trigger_class: str) -> int:
    priority = {
        TRIGGER_MANUAL: 0,
        TRIGGER_STARTUP_RESUME: 1,
        TRIGGER_EVENT: 2,
        TRIGGER_TIMER: 3,
    }
    return priority.get(_normalize_trigger_class(trigger_class), 10)


def _state_path(group_id: str) -> Path:
    gid = str(group_id or "").strip()
    if not gid:
        return ensure_home() / "groups" / "_invalid" / "state" / _PERSIST_FILENAME
    return ensure_home() / "groups" / gid / "state" / _PERSIST_FILENAME


def _wall_to_monotonic(ts: float, *, now_wall: float, now_mono: float) -> float:
    if ts <= 0.0:
        return 0.0
    return max(0.0, now_mono - max(0.0, now_wall - ts))


def _monotonic_to_wall(ts: float, *, now_wall: float, now_mono: float) -> float:
    if ts <= 0.0:
        return 0.0
    return max(0.0, now_wall + (ts - now_mono))


def _normalize_job_state(raw: Dict[str, Any]) -> _AssistiveJobState:
    state = _AssistiveJobState()
    state.pending = bool(raw.get("pending"))
    state.in_flight = bool(raw.get("in_flight"))
    state.rerun_pending = bool(raw.get("rerun_pending"))
    now_wall = time.time()
    now_mono = time.monotonic()
    state.dirty_since = _wall_to_monotonic(float(raw.get("dirty_since_wall") or 0.0), now_wall=now_wall, now_mono=now_mono)
    state.due_at = _wall_to_monotonic(float(raw.get("due_at_wall") or 0.0), now_wall=now_wall, now_mono=now_mono)
    state.last_started_at = _wall_to_monotonic(float(raw.get("last_started_wall") or 0.0), now_wall=now_wall, now_mono=now_mono)
    state.last_finished_at = _wall_to_monotonic(float(raw.get("last_finished_wall") or 0.0), now_wall=now_wall, now_mono=now_mono)
    state.last_trigger_class = _normalize_trigger_class(raw.get("last_trigger_class") or "")
    state.reasons = {_normalize_reason(item) for item in (raw.get("reasons") if isinstance(raw.get("reasons"), list) else []) if str(item or "").strip()}
    state.source_event_id = str(raw.get("source_event_id") or "").strip()
    state.suppressed_reason = str(raw.get("suppressed_reason") or "").strip()
    return state


def _job_state_to_payload(state: _AssistiveJobState) -> Dict[str, Any]:
    now_wall = time.time()
    now_mono = time.monotonic()
    return {
        "pending": bool(state.pending),
        "in_flight": bool(state.in_flight),
        "rerun_pending": bool(state.rerun_pending),
        "dirty_since_wall": _monotonic_to_wall(state.dirty_since, now_wall=now_wall, now_mono=now_mono),
        "due_at_wall": _monotonic_to_wall(state.due_at, now_wall=now_wall, now_mono=now_mono),
        "last_started_wall": _monotonic_to_wall(state.last_started_at, now_wall=now_wall, now_mono=now_mono),
        "last_finished_wall": _monotonic_to_wall(state.last_finished_at, now_wall=now_wall, now_mono=now_mono),
        "last_trigger_class": str(state.last_trigger_class or "").strip(),
        "reasons": sorted({item for item in state.reasons if item}),
        "source_event_id": str(state.source_event_id or "").strip(),
        "suppressed_reason": str(state.suppressed_reason or "").strip(),
    }


def _job_state_is_empty(state: _AssistiveJobState) -> bool:
    return not any(
        [
            state.pending,
            state.in_flight,
            state.rerun_pending,
            state.dirty_since > 0.0,
            state.due_at > 0.0,
            state.last_trigger_class,
            state.reasons,
            state.source_event_id,
            state.suppressed_reason,
        ]
    )


def _load_group_states_locked(group_id: str) -> Dict[str, _AssistiveJobState]:
    gid = str(group_id or "").strip()
    states = _STATE_BY_GROUP.setdefault(gid, {})
    if states:
        return states
    payload = read_json(_state_path(gid))
    jobs_raw = payload.get("jobs") if isinstance(payload, dict) else {}
    if isinstance(jobs_raw, dict):
        for job_kind, raw in jobs_raw.items():
            normalized_kind = str(job_kind or "").strip().lower()
            if normalized_kind not in _VALID_JOB_KINDS or not isinstance(raw, dict):
                continue
            states[normalized_kind] = _normalize_job_state(raw)
    return states


def _persist_group_states_locked(group_id: str) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    states = _STATE_BY_GROUP.get(gid, {})
    jobs_payload = {
        job_kind: _job_state_to_payload(state)
        for job_kind, state in states.items()
        if not _job_state_is_empty(state)
    }
    path = _state_path(gid)
    if not jobs_payload:
        _STATE_BY_GROUP.pop(gid, None)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            "schema": _PERSIST_SCHEMA,
            "group_id": gid,
            "jobs": jobs_payload,
        },
        indent=2,
    )


def _cancel_timer(state: _AssistiveJobState) -> None:
    timer = state.timer
    state.timer = None
    if timer is not None:
        timer.cancel()


def _job_debounce_seconds(job_kind: str) -> float:
    normalized = _normalize_job_kind(job_kind)
    if normalized == JOB_KIND_PET_REVIEW:
        from . import review_scheduler

        return float(review_scheduler.PET_REVIEW_DEBOUNCE_SECONDS)
    return 0.0


def _job_min_interval_seconds(job_kind: str) -> float:
    normalized = _normalize_job_kind(job_kind)
    if normalized == JOB_KIND_PET_REVIEW:
        from . import review_scheduler

        return float(review_scheduler.PET_REVIEW_MIN_INTERVAL_SECONDS)
    return 0.0


def _job_max_delay_seconds(job_kind: str) -> float:
    normalized = _normalize_job_kind(job_kind)
    if normalized == JOB_KIND_PET_REVIEW:
        from . import review_scheduler

        return float(review_scheduler.PET_REVIEW_MAX_DELAY_SECONDS)
    return 0.0


def _job_lease_seconds(job_kind: str) -> float:
    normalized = _normalize_job_kind(job_kind)
    if normalized == JOB_KIND_PET_REVIEW:
        from . import review_scheduler

        return float(getattr(review_scheduler, "PET_REVIEW_LEASE_SECONDS", 90.0))
    from . import profile_refresh

    return float(getattr(profile_refresh, "PET_PROFILE_REFRESH_LEASE_SECONDS", 300.0))


def _job_can_run(job_kind: str, group_id: str) -> bool:
    normalized = _normalize_job_kind(job_kind)
    if normalized == JOB_KIND_PET_REVIEW:
        from . import review_scheduler

        return bool(review_scheduler._can_review_now(group_id))
    from . import profile_refresh

    return bool(profile_refresh._can_refresh_now(group_id))


def _job_unavailable_reason(job_kind: str, group_id: str) -> str:
    normalized = _normalize_job_kind(job_kind)
    if normalized == JOB_KIND_PET_REVIEW:
        from . import review_scheduler

        return str(review_scheduler._review_unavailable_reason(group_id) or "job_unavailable")
    from . import profile_refresh

    return str(profile_refresh._profile_refresh_unavailable_reason(group_id) or "job_unavailable")


def _job_dispatch(job_kind: str, group_id: str, reasons: Set[str], source_event_id: str, trigger_class: str) -> bool:
    normalized = _normalize_job_kind(job_kind)
    if normalized == JOB_KIND_PET_REVIEW:
        from . import review_scheduler

        review_scheduler._emit_pet_review(group_id, reasons, source_event_id)
        return True
    from . import profile_refresh

    return bool(
        profile_refresh._dispatch_profile_refresh(
            group_id,
            reasons=reasons,
            source_event_id=source_event_id,
            trigger_class=trigger_class,
        )
    )


def _compute_due_at(state: _AssistiveJobState, *, job_kind: str, immediate: bool, now: float) -> float:
    base = max(state.last_started_at + _job_min_interval_seconds(job_kind), now if immediate else state.dirty_since + _job_debounce_seconds(job_kind))
    max_delay = _job_max_delay_seconds(job_kind)
    if max_delay > 0.0 and state.dirty_since > 0.0:
        base = min(base, state.dirty_since + max_delay)
    return base


def _maybe_reclaim_stale_in_flight_locked(job_kind: str, state: _AssistiveJobState, *, now: float) -> None:
    if not state.in_flight:
        return
    lease_seconds = _job_lease_seconds(job_kind)
    if lease_seconds <= 0.0:
        return
    if state.last_started_at <= 0.0 or (now - state.last_started_at) < lease_seconds:
        return
    state.in_flight = False
    state.pending = True
    state.rerun_pending = False
    if state.dirty_since <= 0.0:
        state.dirty_since = state.last_started_at or now
    state.due_at = now
    state.last_finished_at = now
    state.suppressed_reason = "lease_expired"


def _reschedule_timer_locked(group_id: str, job_kind: str, state: _AssistiveJobState, *, now: float) -> bool:
    _cancel_timer(state)
    if state.in_flight or not state.pending or state.due_at <= 0.0:
        return False
    if state.due_at <= now:
        return True
    delay = max(0.0, state.due_at - now)
    timer = threading.Timer(delay, _pump_job, args=(group_id, job_kind))
    timer.daemon = True
    state.timer = timer
    timer.start()
    return False


def _pump_job(group_id: str, job_kind: str) -> None:
    normalized_kind = _normalize_job_kind(job_kind)
    dispatch_reasons: Set[str] = set()
    dispatch_source_event_id = ""
    dispatch_trigger_class = TRIGGER_EVENT
    should_dispatch = False
    try:
        with _LOCK:
            states = _load_group_states_locked(group_id)
            state = states.get(normalized_kind)
            if state is None:
                return
            state.timer = None
            now = time.monotonic()
            _maybe_reclaim_stale_in_flight_locked(normalized_kind, state, now=now)
            if state.in_flight:
                _persist_group_states_locked(group_id)
                return
            if not state.pending:
                if _job_state_is_empty(state):
                    states.pop(normalized_kind, None)
                _persist_group_states_locked(group_id)
                return
            if not _job_can_run(normalized_kind, group_id):
                state.suppressed_reason = _job_unavailable_reason(normalized_kind, group_id)
                _persist_group_states_locked(group_id)
                return
            if state.due_at > now:
                flush_now = _reschedule_timer_locked(group_id, normalized_kind, state, now=now)
                _persist_group_states_locked(group_id)
                if flush_now:
                    should_dispatch = True
                    dispatch_reasons = set(state.reasons)
                    dispatch_source_event_id = state.source_event_id
                    dispatch_trigger_class = state.last_trigger_class or TRIGGER_TIMER
                    state.in_flight = True
                    state.pending = False
                    state.rerun_pending = False
                    state.dirty_since = 0.0
                    state.due_at = 0.0
                    state.last_started_at = now
                    state.suppressed_reason = ""
                    _persist_group_states_locked(group_id)
                return
            should_dispatch = True
            dispatch_reasons = set(state.reasons)
            dispatch_source_event_id = state.source_event_id
            dispatch_trigger_class = state.last_trigger_class or TRIGGER_TIMER
            state.in_flight = True
            state.pending = False
            state.rerun_pending = False
            state.dirty_since = 0.0
            state.due_at = 0.0
            state.last_started_at = now
            state.suppressed_reason = ""
            _persist_group_states_locked(group_id)
    except Exception:
        LOGGER.exception("assistive_job_pump_failed group_id=%s job_kind=%s", group_id, normalized_kind)
        return

    if not should_dispatch:
        return

    dispatched = False
    try:
        dispatched = bool(_job_dispatch(normalized_kind, group_id, dispatch_reasons, dispatch_source_event_id, dispatch_trigger_class))
    except Exception:
        LOGGER.exception("assistive_job_dispatch_failed group_id=%s job_kind=%s", group_id, normalized_kind)
        dispatched = False

    if dispatched:
        return

    with _LOCK:
        states = _load_group_states_locked(group_id)
        state = states.get(normalized_kind)
        if state is None:
            return
        if state.in_flight:
            state.in_flight = False
            state.last_finished_at = time.monotonic()
            state.suppressed_reason = "dispatch_failed"
        if _job_state_is_empty(state):
            states.pop(normalized_kind, None)
        _persist_group_states_locked(group_id)


def request_job(
    group_id: str,
    *,
    job_kind: str,
    trigger_class: str,
    reason: str = "",
    source_event_id: str = "",
    immediate: bool = False,
) -> bool:
    gid = str(group_id or "").strip()
    if not gid:
        return False
    normalized_kind = _normalize_job_kind(job_kind)
    normalized_trigger = _normalize_trigger_class(trigger_class)
    normalized_reason = _normalize_reason(reason)
    flush_now = False
    with _LOCK:
        states = _load_group_states_locked(gid)
        state = states.get(normalized_kind)
        can_run = _job_can_run(normalized_kind, gid)
        if state is None and not can_run:
            return False
        if state is None:
            state = _AssistiveJobState()
            states[normalized_kind] = state
        now = time.monotonic()
        _maybe_reclaim_stale_in_flight_locked(normalized_kind, state, now=now)
        if not can_run and not (state.pending or state.in_flight):
            if _job_state_is_empty(state):
                states.pop(normalized_kind, None)
            _persist_group_states_locked(gid)
            return False
        state.pending = True
        if state.dirty_since <= 0.0:
            state.dirty_since = now
        if normalized_reason:
            state.reasons.add(normalized_reason)
        if source_event_id:
            state.source_event_id = str(source_event_id).strip()
        if (not state.last_trigger_class) or _trigger_rank(normalized_trigger) < _trigger_rank(state.last_trigger_class):
            state.last_trigger_class = normalized_trigger
        state.suppressed_reason = "" if can_run else _job_unavailable_reason(normalized_kind, gid)
        state.due_at = _compute_due_at(state, job_kind=normalized_kind, immediate=bool(immediate or normalized_trigger == TRIGGER_MANUAL), now=now)
        if state.in_flight:
            state.rerun_pending = True
            _persist_group_states_locked(gid)
            return True
        flush_now = can_run and _reschedule_timer_locked(gid, normalized_kind, state, now=now)
        _persist_group_states_locked(gid)
    if flush_now:
        _pump_job(gid, normalized_kind)
    return True


def cancel_job(group_id: str, job_kind: str) -> None:
    gid = str(group_id or "").strip()
    if not gid:
        return
    normalized_kind = _normalize_job_kind(job_kind)
    with _LOCK:
        states = _load_group_states_locked(gid)
        state = states.pop(normalized_kind, None)
        if state is not None:
            _cancel_timer(state)
        _persist_group_states_locked(gid)


def mark_job_completed(group_id: str, job_kind: str) -> bool:
    gid = str(group_id or "").strip()
    if not gid:
        return False
    normalized_kind = _normalize_job_kind(job_kind)
    flush_now = False
    with _LOCK:
        states = _load_group_states_locked(gid)
        state = states.get(normalized_kind)
        if state is None or not state.in_flight:
            return False
        now = time.monotonic()
        state.in_flight = False
        state.last_finished_at = now
        state.suppressed_reason = ""
        if state.pending or state.rerun_pending:
            state.rerun_pending = False
            if state.dirty_since <= 0.0:
                state.dirty_since = now
            if state.due_at <= 0.0:
                state.due_at = _compute_due_at(state, job_kind=normalized_kind, immediate=False, now=now)
            flush_now = _job_can_run(normalized_kind, gid) and _reschedule_timer_locked(gid, normalized_kind, state, now=now)
        else:
            state.pending = False
            state.rerun_pending = False
            state.dirty_since = 0.0
            state.due_at = 0.0
            state.last_trigger_class = ""
            state.reasons.clear()
            state.source_event_id = ""
        if _job_state_is_empty(state):
            states.pop(normalized_kind, None)
        _persist_group_states_locked(gid)
    if flush_now:
        _pump_job(gid, normalized_kind)
    return True


def recover_jobs(*, job_kinds: Optional[Iterable[str]] = None) -> None:
    wanted = {_normalize_job_kind(item) for item in (job_kinds or _VALID_JOB_KINDS)}
    groups_root = ensure_home() / "groups"
    if not groups_root.exists():
        return
    for path in groups_root.glob(f"*/state/{_PERSIST_FILENAME}"):
        gid = str(path.parent.parent.name or "").strip()
        if not gid or gid.startswith("_"):
            continue
        flush_pairs: list[tuple[str, str]] = []
        try:
            with _LOCK:
                states = _load_group_states_locked(gid)
                now = time.monotonic()
                for normalized_kind in sorted(wanted):
                    state = states.get(normalized_kind)
                    if state is None:
                        continue
                    _maybe_reclaim_stale_in_flight_locked(normalized_kind, state, now=now)
                    if state.in_flight:
                        continue
                    if state.pending:
                        if state.dirty_since <= 0.0:
                            state.dirty_since = now
                        if state.due_at <= 0.0:
                            state.due_at = now
                        if _job_can_run(normalized_kind, gid):
                            flush_now = _reschedule_timer_locked(gid, normalized_kind, state, now=now)
                            if flush_now:
                                flush_pairs.append((gid, normalized_kind))
                        else:
                            state.suppressed_reason = _job_unavailable_reason(normalized_kind, gid)
                    elif _job_state_is_empty(state):
                        states.pop(normalized_kind, None)
                _persist_group_states_locked(gid)
        except Exception:
            LOGGER.exception("assistive_job_recover_failed group_id=%s", gid)
            continue
        for pair_gid, pair_kind in flush_pairs:
            _pump_job(pair_gid, pair_kind)
