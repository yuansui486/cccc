"""Durable background startup coordination for managed OpenClaw actors."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from queue import Queue
import threading
from typing import Any, Callable, Dict, Optional
import uuid

from ..kernel.actors import find_actor
from ..kernel.events import publish_event
from ..kernel.group import load_group
from ..util.conv import coerce_bool
from ..util.fs import atomic_write_json, read_json
from ..util.time import utc_now_iso


LOGGER = logging.getLogger(__name__)
_STATE_FILE = "actor_startups.json"
_WORKER_COUNT = 2
_ACTIVE_STARTUP_STATES = frozenset({"queued", "initializing", "running"})
_LOCK = threading.RLock()
_GROUP_LOCKS: Dict[str, threading.RLock] = {}
_ACTOR_EXECUTION_LOCKS: Dict[tuple[str, str], threading.Lock] = {}


def _group_lock(group_id: str) -> threading.RLock:
    gid = str(group_id or "").strip()
    with _LOCK:
        return _GROUP_LOCKS.setdefault(gid, threading.RLock())


def _actor_execution_lock(group_id: str, actor_id: str) -> threading.Lock:
    key = (str(group_id or "").strip(), str(actor_id or "").strip())
    with _LOCK:
        return _ACTOR_EXECUTION_LOCKS.setdefault(key, threading.Lock())


def _state_path(group_id: str):
    group = load_group(group_id)
    group_path = getattr(group, "path", None) if group is not None else None
    return group_path / "state" / _STATE_FILE if group_path is not None else None


def _load_rows(group_id: str) -> Dict[str, Dict[str, Any]]:
    path = _state_path(group_id)
    if path is None:
        return {}
    raw = read_json(path)
    rows = raw.get("actors") if isinstance(raw.get("actors"), dict) else {}
    return {
        str(actor_id): dict(row)
        for actor_id, row in rows.items()
        if str(actor_id).strip() and isinstance(row, dict)
    }


def _write_rows(group_id: str, rows: Dict[str, Dict[str, Any]]) -> None:
    path = _state_path(group_id)
    if path is None:
        return
    atomic_write_json(path, {"v": 1, "actors": rows}, indent=2)


def read_openclaw_startup(group_id: str, actor_id: str) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    if not gid or not aid:
        return {}
    with _group_lock(gid):
        return dict(_load_rows(gid).get(aid) or {})


def project_openclaw_startup(group_id: str, actor_id: str) -> Optional[Dict[str, Any]]:
    row = read_openclaw_startup(group_id, actor_id)
    if not row:
        return None
    return {
        key: row.get(key)
        for key in (
            "state",
            "phase",
            "attempt_id",
            "requested_at",
            "started_at",
            "finished_at",
            "updated_at",
            "error",
        )
        if row.get(key) not in (None, "")
    }


def _actor_fingerprint(actor: Dict[str, Any]) -> str:
    payload = {
        key: actor.get(key)
        for key in (
            "command",
            "env",
            "runner",
            "runtime",
            "runtime_options",
            "default_scope_key",
            "profile_id",
            "profile_revision_applied",
            "capability_autoload",
            "capability_hidden",
        )
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _update_current(
    group_id: str,
    actor_id: str,
    *,
    attempt_id: str,
    generation: int,
    patch: Dict[str, Any],
) -> bool:
    with _group_lock(group_id):
        rows = _load_rows(group_id)
        current = rows.get(actor_id)
        if not isinstance(current, dict):
            return False
        if str(current.get("attempt_id") or "") != attempt_id or int(current.get("generation") or 0) != generation:
            return False
        current.update(patch)
        current["updated_at"] = utc_now_iso()
        rows[actor_id] = current
        _write_rows(group_id, rows)
    return True


def _attempt_is_current(group_id: str, actor_id: str, attempt_id: str, generation: int) -> bool:
    current = read_openclaw_startup(group_id, actor_id)
    if str(current.get("attempt_id") or "") != attempt_id or int(current.get("generation") or 0) != generation:
        return False
    group = load_group(group_id)
    actor = find_actor(group, actor_id) if group is not None else None
    return bool(
        isinstance(actor, dict)
        and str(actor.get("runtime") or "").strip().lower() == "openclaw"
        and coerce_bool(actor.get("enabled"), default=True)
    )


@dataclass(frozen=True)
class _StartupTask:
    group_id: str
    actor_id: str
    by: str
    caller_id: str
    is_admin: bool
    attempt_id: str
    generation: int
    start_actor_process: Callable[..., Dict[str, Any]]


@dataclass(frozen=True)
class OpenClawStartupReservation:
    task: _StartupTask
    startup: Dict[str, Any]


def _claim_startup_task(task: _StartupTask) -> bool:
    """Atomically claim one committed queued attempt for worker execution."""
    with _group_lock(task.group_id):
        rows = _load_rows(task.group_id)
        current = rows.get(task.actor_id)
        if not isinstance(current, dict):
            return False
        if (
            str(current.get("attempt_id") or "") != task.attempt_id
            or int(current.get("generation") or 0) != task.generation
            or str(current.get("state") or "").strip().lower() != "queued"
            or not bool(current.get("committed"))
        ):
            return False
        now = utc_now_iso()
        current.update(
            {
                "state": "initializing",
                "phase": "resolving",
                "started_at": now,
                "error": "",
                "updated_at": now,
            }
        )
        rows[task.actor_id] = current
        _write_rows(task.group_id, rows)
        return True


class _OpenClawStartupCoordinator:
    def __init__(self) -> None:
        self._queue: Queue[Optional[_StartupTask]] = Queue()
        self._lock = threading.RLock()
        self._workers: list[threading.Thread] = []
        self._stopping = False
        self._enabled = False

    def _ensure_workers(self) -> None:
        with self._lock:
            if self._workers and any(worker.is_alive() for worker in self._workers):
                return
            self._stopping = False
            self._workers = []
            for index in range(_WORKER_COUNT):
                worker = threading.Thread(
                    target=self._run,
                    name=f"onecolleague-openclaw-start-{index + 1}",
                    daemon=True,
                )
                worker.start()
                self._workers.append(worker)

    def submit(self, task: _StartupTask) -> bool:
        with self._lock:
            if not self._enabled or self._stopping:
                return False
            self._ensure_workers()
            if not self._enabled or self._stopping:
                return False
            self._queue.put(task)
        return True

    def start(self) -> None:
        with self._lock:
            self._enabled = True
        self._ensure_workers()

    def shutdown(self) -> None:
        with self._lock:
            workers = list(self._workers)
            self._workers = []
            self._stopping = True
            self._enabled = False
        for _ in workers:
            self._queue.put(None)
        for worker in workers:
            worker.join(timeout=1.0)

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                with _actor_execution_lock(task.group_id, task.actor_id):
                    self._execute(task)
            finally:
                self._queue.task_done()

    def _execute(self, task: _StartupTask) -> None:
        with self._lock:
            active = self._enabled and not self._stopping
        if not active:
            now = utc_now_iso()
            _update_current(
                task.group_id,
                task.actor_id,
                attempt_id=task.attempt_id,
                generation=task.generation,
                patch={
                    "state": "stopped",
                    "phase": "stopped",
                    "finished_at": now,
                    "error": "OpenClaw startup coordinator stopped before execution",
                },
            )
            return
        if not _attempt_is_current(task.group_id, task.actor_id, task.attempt_id, task.generation):
            return
        if not _claim_startup_task(task):
            return

        def guard() -> bool:
            with self._lock:
                running = self._enabled and not self._stopping
            return running and _attempt_is_current(
                task.group_id,
                task.actor_id,
                task.attempt_id,
                task.generation,
            )

        def phase(value: str) -> None:
            if _update_current(
                task.group_id,
                task.actor_id,
                attempt_id=task.attempt_id,
                generation=task.generation,
                patch={"state": "initializing", "phase": str(value or "initializing")},
            ):
                publish_event(
                    "actor.starting",
                    {
                        "group_id": task.group_id,
                        "actor_id": task.actor_id,
                        "attempt_id": task.attempt_id,
                        "phase": str(value or "initializing"),
                    },
                )

        phase("resolving")

        group = load_group(task.group_id)
        actor = find_actor(group, task.actor_id) if group is not None else None
        if group is None or not isinstance(actor, dict):
            _update_current(
                task.group_id,
                task.actor_id,
                attempt_id=task.attempt_id,
                generation=task.generation,
                patch={
                    "state": "failed",
                    "phase": "failed",
                    "finished_at": utc_now_iso(),
                    "error": "OpenClaw actor disappeared during startup",
                },
            )
            return
        if not guard():
            _update_current(
                task.group_id,
                task.actor_id,
                attempt_id=task.attempt_id,
                generation=task.generation,
                patch={
                    "state": "stopped",
                    "phase": "stopped",
                    "finished_at": utc_now_iso(),
                    "error": "OpenClaw startup stopped before launch",
                },
            )
            return
        try:
            result = task.start_actor_process(
                group,
                task.actor_id,
                command=list(actor.get("command") or []) if isinstance(actor.get("command"), list) else [],
                env=dict(actor.get("env") or {}) if isinstance(actor.get("env"), dict) else {},
                runner=str(actor.get("runner") or "pty"),
                runtime="openclaw",
                by=task.by,
                caller_id=task.caller_id,
                is_admin=task.is_admin,
                start_guard=guard,
                start_phase=phase,
            )
        except Exception as exc:
            result = {"success": False, "error": str(exc)}

        if not guard():
            _update_current(
                task.group_id,
                task.actor_id,
                attempt_id=task.attempt_id,
                generation=task.generation,
                patch={
                    "state": "stopped",
                    "phase": "stopped",
                    "finished_at": utc_now_iso(),
                    "error": "OpenClaw startup stopped before completion",
                },
            )
            return
        finished = utc_now_iso()
        if bool(result.get("success")):
            _update_current(
                task.group_id,
                task.actor_id,
                attempt_id=task.attempt_id,
                generation=task.generation,
                patch={"state": "running", "phase": "running", "finished_at": finished, "error": ""},
            )
            return

        error = str(result.get("error") or "unknown OpenClaw startup error").strip()
        if _update_current(
            task.group_id,
            task.actor_id,
            attempt_id=task.attempt_id,
            generation=task.generation,
            patch={"state": "failed", "phase": "failed", "finished_at": finished, "error": error},
        ):
            publish_event(
                "actor.start_failed",
                {
                    "group_id": task.group_id,
                    "actor_id": task.actor_id,
                    "attempt_id": task.attempt_id,
                    "error": error,
                },
            )
            LOGGER.warning("OpenClaw startup failed group=%s actor=%s: %s", task.group_id, task.actor_id, error)


_COORDINATOR = _OpenClawStartupCoordinator()


def reserve_openclaw_actor_start(
    group_id: str,
    actor_id: str,
    *,
    by: str,
    caller_id: str,
    is_admin: bool,
    start_actor_process: Callable[..., Dict[str, Any]],
) -> OpenClawStartupReservation:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    group = load_group(gid)
    actor = find_actor(group, aid) if group is not None else None
    if group is None or not isinstance(actor, dict):
        raise ValueError(f"actor not found: {aid}")
    if str(actor.get("runtime") or "").strip().lower() != "openclaw":
        raise ValueError("actor is not configured for OpenClaw")

    with _group_lock(gid):
        rows = _load_rows(gid)
        previous = rows.get(aid) if isinstance(rows.get(aid), dict) else {}
        generation = int(previous.get("generation") or 0) + 1
        attempt_id = uuid.uuid4().hex
        now = utc_now_iso()
        row = {
            "v": 1,
            "state": "queued",
            "phase": "queued",
            "attempt_id": attempt_id,
            "generation": generation,
            "requested_at": now,
            "started_at": "",
            "finished_at": "",
            "updated_at": now,
            "error": "",
            "committed": False,
            "desired_fingerprint": _actor_fingerprint(actor),
        }
        rows[aid] = row
        _write_rows(gid, rows)

    startup = project_openclaw_startup(gid, aid) or row
    return OpenClawStartupReservation(
        task=_StartupTask(
            group_id=gid,
            actor_id=aid,
            by=str(by or "user").strip() or "user",
            caller_id=str(caller_id or "").strip(),
            is_admin=bool(is_admin),
            attempt_id=attempt_id,
            generation=generation,
            start_actor_process=start_actor_process,
        ),
        startup=dict(startup),
    )


def fail_openclaw_actor_start_reservation(
    reservation: OpenClawStartupReservation,
    *,
    error: str,
) -> bool:
    task = reservation.task
    with _group_lock(task.group_id):
        rows = _load_rows(task.group_id)
        current = rows.get(task.actor_id)
        if not isinstance(current, dict):
            return False
        if (
            str(current.get("attempt_id") or "") != task.attempt_id
            or int(current.get("generation") or 0) != task.generation
            or str(current.get("state") or "").strip().lower() != "queued"
            or bool(current.get("committed"))
        ):
            return False
        now = utc_now_iso()
        current.update(
            {
                "state": "failed",
                "phase": "failed",
                "finished_at": now,
                "updated_at": now,
                "error": str(error or "OpenClaw startup was not submitted").strip(),
            }
        )
        rows[task.actor_id] = current
        _write_rows(task.group_id, rows)
        return True


def commit_openclaw_actor_start(
    reservation: OpenClawStartupReservation,
) -> Dict[str, Any]:
    task = reservation.task
    with _group_lock(task.group_id):
        rows = _load_rows(task.group_id)
        current = rows.get(task.actor_id)
        if (
            not isinstance(current, dict)
            or str(current.get("state") or "").strip().lower() != "queued"
            or str(current.get("attempt_id") or "") != task.attempt_id
            or int(current.get("generation") or 0) != task.generation
            or bool(current.get("committed"))
            or not _attempt_is_current(task.group_id, task.actor_id, task.attempt_id, task.generation)
        ):
            raise RuntimeError("OpenClaw startup attempt was superseded before submission")
        current["committed"] = True
        current["updated_at"] = utc_now_iso()
        rows[task.actor_id] = current
        _write_rows(task.group_id, rows)
    try:
        submitted = _COORDINATOR.submit(task)
    except Exception as exc:
        _update_current(
            task.group_id,
            task.actor_id,
            attempt_id=task.attempt_id,
            generation=task.generation,
            patch={
                "state": "failed",
                "phase": "failed",
                "finished_at": utc_now_iso(),
                "error": str(exc),
            },
        )
        raise
    if not submitted:
        error = "OpenClaw startup coordinator is not running"
        _update_current(
            task.group_id,
            task.actor_id,
            attempt_id=task.attempt_id,
            generation=task.generation,
            patch={
                "state": "failed",
                "phase": "failed",
                "finished_at": utc_now_iso(),
                "error": error,
            },
        )
        raise RuntimeError(error)
    try:
        publish_event(
            "actor.starting",
            {
                "group_id": task.group_id,
                "actor_id": task.actor_id,
                "attempt_id": task.attempt_id,
                "phase": "queued",
            },
        )
    except Exception:
        LOGGER.exception(
            "Failed to publish queued OpenClaw startup group=%s actor=%s attempt=%s",
            task.group_id,
            task.actor_id,
            task.attempt_id,
        )
    return dict(reservation.startup)


def queue_openclaw_actor_start(
    group_id: str,
    actor_id: str,
    *,
    by: str,
    caller_id: str,
    is_admin: bool,
    start_actor_process: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    reservation = reserve_openclaw_actor_start(
        group_id,
        actor_id,
        by=by,
        caller_id=caller_id,
        is_admin=is_admin,
        start_actor_process=start_actor_process,
    )
    return commit_openclaw_actor_start(reservation)


def cancel_openclaw_actor_start(group_id: str, actor_id: str, *, remove: bool = False) -> None:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    if not gid or not aid:
        return
    with _group_lock(gid):
        rows = _load_rows(gid)
        previous = rows.get(aid) if isinstance(rows.get(aid), dict) else {}
        if remove:
            rows.pop(aid, None)
        else:
            now = utc_now_iso()
            rows[aid] = {
                **previous,
                "v": 1,
                "state": "stopped",
                "phase": "stopped",
                "generation": int(previous.get("generation") or 0) + 1,
                "attempt_id": uuid.uuid4().hex,
                "finished_at": now,
                "updated_at": now,
                "error": "",
            }
        _write_rows(gid, rows)


def cancel_all_openclaw_actor_starts(group_id: str) -> int:
    """Cancel every durable startup attempt for one group.

    This is used before daemon services become reachable. Bumping each active
    generation also makes queued in-memory work from a prior coordinator run
    fail its current-attempt guard.
    """
    gid = str(group_id or "").strip()
    if not gid:
        return 0
    with _group_lock(gid):
        rows = _load_rows(gid)
        active_ids = [
            actor_id
            for actor_id, row in rows.items()
            if str(row.get("state") or "").strip().lower() in _ACTIVE_STARTUP_STATES
        ]
        if not active_ids:
            return 0
        now = utc_now_iso()
        for actor_id in active_ids:
            previous = rows[actor_id]
            rows[actor_id] = {
                **previous,
                "v": 1,
                "state": "stopped",
                "phase": "stopped",
                "generation": int(previous.get("generation") or 0) + 1,
                "attempt_id": uuid.uuid4().hex,
                "finished_at": now,
                "updated_at": now,
                "error": "",
            }
        _write_rows(gid, rows)
        return len(active_ids)


def shutdown_openclaw_startup_workers() -> None:
    _COORDINATOR.shutdown()


def start_openclaw_startup_workers() -> None:
    _COORDINATOR.start()
