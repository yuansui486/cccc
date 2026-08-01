from __future__ import annotations

import os
import threading
import time
import uuid
import weakref
from pathlib import Path
from typing import Any, Dict

from .lease import ComputerControlLease
from .derived_authority import DerivedAuthorityStore
from .run_authority import RunAuthorityStore
from .mcp import WindowsMCPSetup, WindowsMCPSession
from .runtime import WorkflowRunner
from .recording import RecordingStore
from .requests import ComputerRequestStore
from .scheduler import ComputerControlScheduler
from .storage import WorkflowStore
from .picker import ElementPickerManager
from .observation import ElementObservationService
from ..util.file_lock import is_lockfile_handle
from ..daemon.messaging.turn_provenance import (
    get_actor_turn_generation,
    get_daemon_turn_issuer_epoch,
)


_DAEMON_OWNER_SEAL = object()
_DAEMON_EXECUTION_SEAL = object()
_SERVICE_PROCESS_EPOCH = "computer_control_" + uuid.uuid4().hex
_owner_registry_lock = threading.Lock()
_owner_registry: Dict[int, tuple[weakref.ReferenceType[Any], Dict[str, Any]]] = {}
_generation_registry_lock = threading.Lock()
_generation_registry: Dict[
    tuple[str, str, int],
    tuple[weakref.ReferenceType[Any], "_DaemonGenerationAdmission"],
] = {}
_execution_registry_lock = threading.Lock()
_execution_registry: Dict[
    int,
    tuple[
        weakref.ReferenceType[Any],
        weakref.ReferenceType[Any],
        weakref.ReferenceType[Any],
        Dict[str, Any],
    ],
] = {}


class _DaemonGenerationClaim:
    __slots__ = ("_admission", "subject", "owner_key", "_released")

    def __init__(self, admission: "_DaemonGenerationAdmission", subject: str):
        self._admission = admission
        self.subject = subject
        self.owner_key = admission.owner_key
        self._released = False

    def release(self) -> None:
        self._admission.release(self)

    def admitted(self) -> bool:
        return self._admission.admits(self)


class _DaemonGenerationAdmission:
    """Irreversible admission and child-owner accounting for one daemon lock."""

    def __init__(self, *, owner_key: tuple[int, str, int]) -> None:
        self._lock = threading.Lock()
        self._open = True
        self._active: Dict[int, _DaemonGenerationClaim] = {}
        self.owner_key = owner_key

    def claim(self, subject: str) -> _DaemonGenerationClaim:
        with self._lock:
            if not self._open:
                raise RuntimeError("computer-control daemon shutdown admission is closed")
            claim = _DaemonGenerationClaim(self, subject)
            self._active[id(claim)] = claim
            return claim

    def release(self, claim: _DaemonGenerationClaim) -> None:
        with self._lock:
            if claim._released:
                return
            current = self._active.get(id(claim))
            if current is claim:
                self._active.pop(id(claim), None)
            claim._released = True

    def admits(self, claim: _DaemonGenerationClaim) -> bool:
        with self._lock:
            return self._open and self._active.get(id(claim)) is claim

    def close(self) -> None:
        with self._lock:
            self._open = False

    def shutdown_complete(self) -> bool:
        with self._lock:
            return not self._open and not self._active


def _generation_admission_for_lock(
    *,
    home: Path,
    process_epoch: str,
    lock_handle: Any,
) -> _DaemonGenerationAdmission:
    key = (str(home), process_epoch, id(lock_handle))
    with _generation_registry_lock:
        registered = _generation_registry.get(key)
        if registered is not None and registered[0]() is lock_handle:
            return registered[1]

        def discard(reference: weakref.ReferenceType[Any]) -> None:
            with _generation_registry_lock:
                current = _generation_registry.get(key)
                if current is not None and current[0] is reference:
                    _generation_registry.pop(key, None)

        reference = weakref.ref(lock_handle, discard)
        admission = _DaemonGenerationAdmission(
            owner_key=(os.getpid(), process_epoch, id(lock_handle)),
        )
        _generation_registry[key] = (reference, admission)
        return admission


class DaemonComputerControlOwner:
    """Process-local proof that the daemon lifecycle lock is still held."""

    __slots__ = (
        "authority_home",
        "role",
        "pid",
        "process_epoch",
        "lock_handle_id",
        "__weakref__",
    )

    def __init__(self, *, _seal: object, state: Dict[str, Any]):
        if _seal is not _DAEMON_OWNER_SEAL:
            raise TypeError("daemon computer-control owners can only be issued after daemon lock acquisition")
        self.authority_home = str(state["authority_home"])
        self.role = "daemon"
        self.pid = int(state["pid"])
        self.process_epoch = str(state["process_epoch"])
        self.lock_handle_id = int(state["lock_handle_id"])

    def __reduce__(self):
        raise TypeError("daemon computer-control owners are process-local")


class _DaemonComputerControlExecutionClaim:
    """Process-local capability for legacy execution under one READY daemon."""

    __slots__ = (
        "authority_home",
        "pid",
        "process_epoch",
        "lock_handle_id",
        "service_id",
        "__weakref__",
    )

    def __init__(self, *, _seal: object, state: Dict[str, Any]):
        if _seal is not _DAEMON_EXECUTION_SEAL:
            raise TypeError("daemon execution claims can only be issued by a lifecycle owner")
        self.authority_home = str(state["authority_home"])
        self.pid = int(state["pid"])
        self.process_epoch = str(state["process_epoch"])
        self.lock_handle_id = int(state["lock_handle_id"])
        self.service_id = int(state["service_id"])

    def __reduce__(self):
        raise TypeError("daemon execution claims are process-local")


def _bind_daemon_owner(owner: DaemonComputerControlOwner, state: Dict[str, Any]) -> None:
    identity = id(owner)

    def discard(reference: weakref.ReferenceType[Any]) -> None:
        with _owner_registry_lock:
            current = _owner_registry.get(identity)
            if current is not None and current[0] is reference:
                _owner_registry.pop(identity, None)

    reference = weakref.ref(owner, discard)
    with _owner_registry_lock:
        _owner_registry[identity] = (reference, state)


def _daemon_owner_state(owner: Any, *, home: Path) -> Dict[str, Any]:
    if not isinstance(owner, DaemonComputerControlOwner):
        raise PermissionError("daemon computer-control owner is required")
    with _owner_registry_lock:
        registered = _owner_registry.get(id(owner))
        state = registered[1] if registered is not None and registered[0]() is owner else None
    canonical_home = home.resolve()
    if (
        state is None
        or int(state["pid"]) != os.getpid()
        or str(state["process_epoch"]) != _SERVICE_PROCESS_EPOCH
        or str(state["role"]) != "daemon"
        or Path(str(state["authority_home"])) != canonical_home
        or id(state["lock_handle"]) != int(state["lock_handle_id"])
        or not is_lockfile_handle(state["lock_handle"])
    ):
        raise PermissionError("daemon computer-control owner is not current for this home")
    return dict(state)


def _claim_daemon_generation(
    owner: Any,
    *,
    home: Path,
    subject: str,
) -> _DaemonGenerationClaim:
    state = _daemon_owner_state(owner, home=home)
    admission = state.get("generation_admission")
    if not isinstance(admission, _DaemonGenerationAdmission):
        raise PermissionError("daemon computer-control generation is required")
    return admission.claim(subject)


def _release_daemon_generation(claim: Any) -> None:
    if isinstance(claim, _DaemonGenerationClaim):
        claim.release()


def _bind_daemon_execution_claim(
    claim: _DaemonComputerControlExecutionClaim,
    *,
    service: "ComputerControlServices",
    owner: DaemonComputerControlOwner,
    state: Dict[str, Any],
) -> None:
    identity = id(claim)

    def discard(reference: weakref.ReferenceType[Any]) -> None:
        with _execution_registry_lock:
            current = _execution_registry.get(identity)
            if current is not None and current[0] is reference:
                _execution_registry.pop(identity, None)

    reference = weakref.ref(claim, discard)
    with _execution_registry_lock:
        _execution_registry[identity] = (
            reference,
            weakref.ref(service),
            weakref.ref(owner),
            dict(state),
        )


def _issue_daemon_execution_claim(
    service: "ComputerControlServices",
    owner: DaemonComputerControlOwner,
) -> _DaemonComputerControlExecutionClaim:
    state = _daemon_owner_state(owner, home=service.home)
    private_state = {
        "authority_home": str(service.home),
        "pid": int(state["pid"]),
        "process_epoch": str(state["process_epoch"]),
        "lock_handle_id": int(state["lock_handle_id"]),
        "service_id": id(service),
    }
    claim = _DaemonComputerControlExecutionClaim(
        _seal=_DAEMON_EXECUTION_SEAL,
        state=private_state,
    )
    _bind_daemon_execution_claim(
        claim,
        service=service,
        owner=owner,
        state=private_state,
    )
    return claim


def _validate_daemon_execution_claim(claim: Any, *, home: Path) -> Dict[str, Any]:
    if not isinstance(claim, _DaemonComputerControlExecutionClaim):
        raise PermissionError("current READY daemon execution claim is required")
    with _execution_registry_lock:
        registered = _execution_registry.get(id(claim))
        if registered is None or registered[0]() is not claim:
            registered = None
        service = registered[1]() if registered is not None else None
        owner = registered[2]() if registered is not None else None
        private_state = dict(registered[3]) if registered is not None else None
    public_state = {
        "authority_home": claim.authority_home,
        "pid": claim.pid,
        "process_epoch": claim.process_epoch,
        "lock_handle_id": claim.lock_handle_id,
        "service_id": claim.service_id,
    }
    canonical_home = home.resolve()
    if (
        private_state is None
        or public_state != private_state
        or Path(claim.authority_home) != canonical_home
        or service is None
        or owner is None
        or service.home != canonical_home
        or id(service) != claim.service_id
    ):
        raise PermissionError("current READY daemon execution claim is required")
    owner_state = _daemon_owner_state(owner, home=canonical_home)
    if (
        claim.pid != os.getpid()
        or claim.pid != int(owner_state["pid"])
        or claim.process_epoch != str(owner_state["process_epoch"])
        or claim.lock_handle_id != int(owner_state["lock_handle_id"])
    ):
        raise PermissionError("current READY daemon execution claim is required")
    service._require_current_daemon_execution_claim(owner, claim)
    return {**private_state, "service": service, "owner": owner}


def issue_daemon_computer_control_owner(
    home: Path,
    *,
    lock_handle: Any,
) -> DaemonComputerControlOwner:
    canonical_home = home.resolve()
    expected_lock = (canonical_home / "daemon" / "onecolleagued.lock").resolve()
    try:
        actual_lock = Path(str(lock_handle.name)).resolve()
    except Exception as exc:
        raise PermissionError("daemon lifecycle lock handle is required") from exc
    if actual_lock != expected_lock or not is_lockfile_handle(lock_handle):
        raise PermissionError("daemon lifecycle lock handle is required")
    admission = _generation_admission_for_lock(
        home=canonical_home,
        process_epoch=_SERVICE_PROCESS_EPOCH,
        lock_handle=lock_handle,
    )
    state = {
        "authority_home": str(canonical_home),
        "role": "daemon",
        "pid": os.getpid(),
        "process_epoch": _SERVICE_PROCESS_EPOCH,
        "lock_handle_id": id(lock_handle),
        "lock_handle": lock_handle,
        "generation_admission": admission,
    }
    owner = DaemonComputerControlOwner(_seal=_DAEMON_OWNER_SEAL, state=state)
    _bind_daemon_owner(owner, state)
    return owner


class ComputerControlServices:
    DRAIN_TIMEOUT_SECONDS = 5.0

    def __init__(self, home: Path, *, role: str = "passive"):
        self.home = home.resolve()
        self.role = str(role or "").strip().lower()
        if self.role not in {"passive", "daemon"}:
            raise ValueError("computer-control service role is invalid")
        self.store = WorkflowStore(self.home)
        self.requests = ComputerRequestStore(self.store)
        self.authorities = DerivedAuthorityStore(
            self.home,
            issuer_epoch_provider=get_daemon_turn_issuer_epoch,
            generation_provider=get_actor_turn_generation,
        )
        self.run_authorities = RunAuthorityStore(
            self.home,
            issuer_epoch_provider=get_daemon_turn_issuer_epoch,
            generation_provider=get_actor_turn_generation,
        )
        self.lease = ComputerControlLease(self.home)
        self.session = WindowsMCPSession()
        self.setup = WindowsMCPSetup(self.home, self.session, self.store, self.lease)
        self.observation = ElementObservationService()
        self.picker = ElementPickerManager(
            self.home,
            self.lease,
            snapshot_provider=self._snapshot_for_picker,
        )
        self.recordings = RecordingStore(
            self.home,
            self.store,
            self.requests,
            self.authorities,
            self.lease,
            self.session,
            fingerprint_provider=lambda: str(self.setup.status().get("fingerprint") or ""),
            observation_provider=self.observation,
        )
        self.runner = WorkflowRunner(
            self.home,
            self.store,
            self.lease,
            self.session,
            run_authorities=self.run_authorities,
            requests=self.requests,
            fingerprint_provider=lambda: str(self.setup.status().get("fingerprint") or ""),
            observation_provider=self.observation,
        )
        self.scheduler = ComputerControlScheduler(self)
        self._daemon_start = threading.Condition()
        self._daemon_start_state = "new"
        self._daemon_start_epoch = ""
        self._daemon_start_lock_handle_id = 0
        self._daemon_execution_claim: Any = None
        self._daemon_shutdown_requested = False
        self._daemon_generation_admission: Any = None

    def start_daemon(self, owner: DaemonComputerControlOwner) -> "ComputerControlServices":
        state = _daemon_owner_state(owner, home=self.home)
        if self.role != "daemon":
            raise PermissionError("daemon owner cannot start a passive computer-control service")
        epoch = str(state["process_epoch"])
        lock_handle_id = int(state["lock_handle_id"])
        admission = state.get("generation_admission")
        if not isinstance(admission, _DaemonGenerationAdmission):
            raise PermissionError("daemon computer-control generation is required")
        with self._daemon_start:
            if self._daemon_generation_admission is None:
                self._daemon_generation_admission = admission
            elif self._daemon_generation_admission is not admission:
                raise PermissionError("computer-control service belongs to another daemon generation")
            if self._daemon_shutdown_requested:
                raise RuntimeError("computer-control daemon shutdown admission is closed")
            while self._daemon_start_state == "starting":
                self._daemon_start.wait()
            if self._daemon_shutdown_requested:
                raise RuntimeError("computer-control daemon shutdown admission is closed")
            if self._daemon_start_state == "stopping":
                raise RuntimeError("computer-control daemon service is stopping")
            if self._daemon_start_state == "ready":
                if (
                    self._daemon_start_epoch != epoch
                    or self._daemon_start_lock_handle_id != lock_handle_id
                ):
                    raise PermissionError("computer-control service belongs to another daemon owner")
                # A worker crash must not leave a READY service with unattended
                # execution silently disabled.  start_daemon is idempotent for
                # a live worker and recreates it when the prior thread exited.
                start_session = getattr(self.session, "_start_daemon", None)
                if callable(start_session):
                    start_session(owner, home=self.home)
                self.setup._start_daemon(owner)
                self.picker._start_daemon(owner)
                self.scheduler.start_daemon(owner)
                return self
            self._daemon_start_state = "starting"
            self._daemon_start_epoch = epoch
            self._daemon_start_lock_handle_id = lock_handle_id
        try:
            start_session = getattr(self.session, "_start_daemon", None)
            if callable(start_session):
                start_session(owner, home=self.home)
            self.recordings._recover_after_restart(owner)
            self.runner._recover_manual_runs_after_restart(owner)
            self.setup._start_daemon(owner)
            self.picker._start_daemon(owner)
            self.recordings._start_watchdog(owner)
            execution_claim = _issue_daemon_execution_claim(self, owner)
            self.runner._bind_daemon_execution_claim(execution_claim)
            self._daemon_execution_claim = execution_claim
            self.scheduler.start_daemon(owner)
        except Exception:
            self.recordings._request_watchdog_stop()
            self.picker._request_daemon_stop()
            self.setup._request_daemon_stop()
            request_session_stop = getattr(self.session, "_request_daemon_stop", None)
            if callable(request_session_stop):
                request_session_stop()
            watchdog_stopped = self.recordings._drain_watchdog(
                timeout=self.DRAIN_TIMEOUT_SECONDS
            )
            picker_stopped = self.picker._drain_daemon(
                timeout=self.DRAIN_TIMEOUT_SECONDS
            )
            setup_stopped = self.setup._drain_daemon_sync(
                timeout=self.DRAIN_TIMEOUT_SECONDS
            )
            session_stopped = setup_stopped
            drain_session = getattr(self.session, "_drain_daemon_sync", None)
            if setup_stopped and callable(drain_session):
                session_stopped = drain_session(timeout=self.DRAIN_TIMEOUT_SECONDS)
            self.runner._clear_daemon_execution_claim(self._daemon_execution_claim)
            self._daemon_execution_claim = None
            with self._daemon_start:
                stopped = (
                    watchdog_stopped
                    and picker_stopped
                    and setup_stopped
                    and session_stopped
                )
                self._daemon_start_state = "new" if stopped else "stopping"
                if stopped:
                    self._daemon_start_lock_handle_id = 0
                self._daemon_start.notify_all()
            raise
        shutdown_requested = False
        with self._daemon_start:
            shutdown_requested = self._daemon_shutdown_requested
            self._daemon_start_state = "stopping" if shutdown_requested else "ready"
            self._daemon_start.notify_all()
        if shutdown_requested:
            raise RuntimeError("computer-control daemon shutdown admission is closed")
        return self

    def begin_daemon_shutdown(self) -> None:
        if self.role != "daemon":
            raise PermissionError("daemon shutdown requires a daemon service")
        with self._daemon_start:
            self._daemon_shutdown_requested = True
            admission = self._daemon_generation_admission
            if isinstance(admission, _DaemonGenerationAdmission):
                admission.close()
            while self._daemon_start_state == "starting":
                self._daemon_start.wait()
            if self._daemon_start_state == "ready":
                self._daemon_start_state = "stopping"
            self._daemon_start.notify_all()

    def daemon_shutdown_complete(self) -> bool:
        with self._daemon_start:
            admission = self._daemon_generation_admission
            service_complete = bool(
                self._daemon_shutdown_requested
                and self._daemon_start_state == "new"
                and self._daemon_execution_claim is None
                and self.runner._daemon_execution_claim is None
            )
        return bool(
            service_complete
            and isinstance(admission, _DaemonGenerationAdmission)
            and admission.shutdown_complete()
        )

    def stop_daemon(self) -> bool:
        deadline = time.monotonic() + self.DRAIN_TIMEOUT_SECONDS
        with self._daemon_start:
            while self._daemon_start_state == "starting":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._daemon_start.wait(remaining)
            self._daemon_start_state = "stopping"
            self._daemon_start.notify_all()

        self.runner.request_daemon_stop()
        self.recordings._request_watchdog_stop()
        self.picker._request_daemon_stop()
        self.setup._request_daemon_stop()
        request_session_stop = getattr(self.session, "_request_daemon_stop", None)
        if callable(request_session_stop):
            request_session_stop()
        scheduler_stopped = self.scheduler.stop_daemon(
            timeout=max(0.0, deadline - time.monotonic())
        )
        setup_stopped = self.setup._drain_daemon_sync(
            timeout=max(0.0, deadline - time.monotonic())
        )
        transport_stopped = setup_stopped
        if setup_stopped:
            stop_session = getattr(self.session, "stop_sync", None)
            if callable(stop_session):
                try:
                    stop_session(timeout=max(0.0, deadline - time.monotonic()))
                except BaseException:
                    transport_stopped = False
        runner_stopped = self.runner.drain_daemon(
            timeout=max(0.0, deadline - time.monotonic())
        )
        watchdog_stopped = self.recordings._drain_watchdog(
            timeout=max(0.0, deadline - time.monotonic())
        )
        picker_stopped = self.picker._drain_daemon(
            timeout=max(0.0, deadline - time.monotonic())
        )
        session_stopped = transport_stopped and runner_stopped and setup_stopped
        drain_session = getattr(self.session, "_drain_daemon_sync", None)
        if session_stopped and callable(drain_session):
            session_stopped = drain_session(
                timeout=max(0.0, deadline - time.monotonic())
            )
        if not (
            scheduler_stopped
            and transport_stopped
            and runner_stopped
            and watchdog_stopped
            and picker_stopped
            and setup_stopped
            and session_stopped
        ):
            return False

        with self._daemon_start:
            claim = self._daemon_execution_claim
            self._daemon_execution_claim = None
            self._daemon_start_state = "new"
            self._daemon_start_lock_handle_id = 0
            self._daemon_start.notify_all()
        self.runner._clear_daemon_execution_claim(claim)
        return True

    def _require_current_daemon_execution_claim(
        self,
        owner: DaemonComputerControlOwner,
        claim: _DaemonComputerControlExecutionClaim,
    ) -> "ComputerControlServices":
        state = _daemon_owner_state(owner, home=self.home)
        with self._daemon_start:
            if (
                self.role != "daemon"
                or self._daemon_start_state != "ready"
                or self._daemon_start_epoch != str(state["process_epoch"])
                or self._daemon_start_lock_handle_id != int(state["lock_handle_id"])
                or self._daemon_execution_claim is not claim
            ):
                raise PermissionError(
                    "current READY daemon execution claim is required"
                )
        return self

    def require_daemon_ready(self, owner: DaemonComputerControlOwner) -> "ComputerControlServices":
        state = _daemon_owner_state(owner, home=self.home)
        with self._daemon_start:
            if (
                self.role != "daemon"
                or self._daemon_start_state != "ready"
                or self._daemon_start_epoch != str(state["process_epoch"])
                or self._daemon_start_lock_handle_id != int(state["lock_handle_id"])
            ):
                raise RuntimeError("computer-control daemon service is not ready")
        return self

    def _snapshot_for_picker(self) -> Any:
        """Return one MCP snapshot for picker fallback integrations.

        Native UIA is preferred by :class:`ElementPickerManager`; keeping this
        callback here allows a future helper or a browser DOM adapter to use
        the existing supervised MCP session without importing MCP code into
        the picker module.
        """
        tools = self.session.catalog_sync()
        name = next((str(item.get("name") or "") for item in tools if str(item.get("name") or "").casefold() == "snapshot"), "")
        if not name:
            raise RuntimeError("Windows-MCP Snapshot tool is unavailable")
        return self.session.call_tool_sync(name, {})


_services: Dict[tuple[str, str, str, int], ComputerControlServices] = {}
_daemon_owners: Dict[str, DaemonComputerControlOwner] = {}
_lock = threading.Lock()


def _service_key(
    home: Path,
    role: str,
    process_epoch: str = "",
    lock_handle_id: int = 0,
) -> tuple[str, str, str, int]:
    return (
        str(home.resolve()),
        role,
        process_epoch if role == "daemon" else "",
        lock_handle_id if role == "daemon" else 0,
    )


def start_daemon_services(home: Path, *, lock_handle: Any) -> ComputerControlServices:
    canonical_home = home.resolve()
    owner = issue_daemon_computer_control_owner(canonical_home, lock_handle=lock_handle)
    state = _daemon_owner_state(owner, home=canonical_home)
    key = _service_key(
        canonical_home,
        "daemon",
        str(state["process_epoch"]),
        int(state["lock_handle_id"]),
    )
    with _lock:
        _daemon_owners[str(canonical_home)] = owner
        service = _services.get(key)
        if service is None:
            service = ComputerControlServices(canonical_home, role="daemon")
            _services[key] = service
    return service.start_daemon(owner)


def get_services(home: Path, *, role: str = "passive") -> ComputerControlServices:
    canonical_home = home.resolve()
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in {"passive", "daemon"}:
        raise ValueError("computer-control service role is invalid")
    if normalized_role == "daemon":
        with _lock:
            owner = _daemon_owners.get(str(canonical_home))
        state = _daemon_owner_state(owner, home=canonical_home)
        key = _service_key(
            canonical_home,
            normalized_role,
            str(state["process_epoch"]),
            int(state["lock_handle_id"]),
        )
        with _lock:
            service = _services.get(key)
            if service is None:
                service = ComputerControlServices(canonical_home, role="daemon")
                _services[key] = service
        return service.start_daemon(owner).require_daemon_ready(owner)
    key = _service_key(canonical_home, normalized_role)
    with _lock:
        service = _services.get(key)
        if service is None:
            service = ComputerControlServices(canonical_home, role=normalized_role)
            _services[key] = service
        return service
