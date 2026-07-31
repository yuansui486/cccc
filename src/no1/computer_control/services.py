from __future__ import annotations

import os
import threading
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
_SERVICE_PROCESS_EPOCH = "computer_control_" + uuid.uuid4().hex
_owner_registry_lock = threading.Lock()
_owner_registry: Dict[int, tuple[weakref.ReferenceType[Any], Dict[str, Any]]] = {}


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
    state = {
        "authority_home": str(canonical_home),
        "role": "daemon",
        "pid": os.getpid(),
        "process_epoch": _SERVICE_PROCESS_EPOCH,
        "lock_handle_id": id(lock_handle),
        "lock_handle": lock_handle,
    }
    owner = DaemonComputerControlOwner(_seal=_DAEMON_OWNER_SEAL, state=state)
    _bind_daemon_owner(owner, state)
    return owner


class ComputerControlServices:
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

    def start_daemon(self, owner: DaemonComputerControlOwner) -> "ComputerControlServices":
        state = _daemon_owner_state(owner, home=self.home)
        if self.role != "daemon":
            raise PermissionError("daemon owner cannot start a passive computer-control service")
        epoch = str(state["process_epoch"])
        lock_handle_id = int(state["lock_handle_id"])
        with self._daemon_start:
            while self._daemon_start_state == "starting":
                self._daemon_start.wait()
            if self._daemon_start_state == "ready":
                if (
                    self._daemon_start_epoch != epoch
                    or self._daemon_start_lock_handle_id != lock_handle_id
                ):
                    raise PermissionError("computer-control service belongs to another daemon owner")
                # A worker crash must not leave a READY service with unattended
                # execution silently disabled.  start_daemon is idempotent for
                # a live worker and recreates it when the prior thread exited.
                self.scheduler.start_daemon(owner)
                return self
            self._daemon_start_state = "starting"
            self._daemon_start_epoch = epoch
            self._daemon_start_lock_handle_id = lock_handle_id
        try:
            self.recordings._recover_after_restart(owner)
            self.runner._recover_manual_runs_after_restart(owner)
            self.recordings._start_watchdog(owner)
            self.scheduler.start_daemon(owner)
        except Exception:
            with self._daemon_start:
                self._daemon_start_state = "new"
                self._daemon_start_lock_handle_id = 0
                self._daemon_start.notify_all()
            raise
        with self._daemon_start:
            self._daemon_start_state = "ready"
            self._daemon_start.notify_all()
        return self

    def stop_daemon(self) -> None:
        self.scheduler.stop_daemon()

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
