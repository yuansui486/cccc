from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import re
import secrets
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterator, Mapping

from ..util.file_lock import acquire_lockfile, release_lockfile
from ..util.fs import atomic_write_text
from .authorization import RunStartClaim
from .models import computer_control_permissions


_OPERATION_SEAL = object()
_READ_SEAL = object()
_EXECUTION_SEED_SEAL = object()
_EXECUTION_SEAL = object()
_TERMINATION_SEAL = object()
_STOP_OWNER_SEAL = object()
_RECOVERY_SEAL = object()
_TERMINAL_RECONCILIATION_SEAL = object()
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: Dict[str, threading.RLock] = {}
_RECORD_INTEGRITIES_GUARD = threading.Lock()
_RECORD_INTEGRITIES: Dict[tuple[str, str], str] = {}
_CLAIM_OWNERS_GUARD = threading.Lock()
_CLAIM_OWNERS: Dict[int, tuple[weakref.ReferenceType[Any], str]] = {}
_CONTROL_STATES = frozenset({"pending", "active", "terminating", "revoked"})
_EXECUTION_STATES = frozenset(
    {
        "prepared",
        "accepted",
        "running",
        "waiting_recovery",
        "waiting_approval",
        "awaiting_verification",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
        "start_failed",
    }
)
_EXECUTION_CONSUMABLE_STATES = frozenset(
    {
        "accepted",
        "running",
        "waiting_recovery",
        "waiting_approval",
        "awaiting_verification",
    }
)
_ACTIVE_RECOVERY_RUN_STATES = frozenset(
    {
        "initializing",
        "running",
        "recovering",
        "waiting_approval",
        "awaiting_verification",
    }
)
_TERMINAL_EXECUTION_STATES = frozenset(
    {"completed", "failed", "cancelled", "interrupted", "start_failed"}
)
_EXECUTION_SCOPE_FIELDS = (
    "issuer_epoch",
    "origin_issuer_epoch",
    "authority_id",
    "execution_id",
    "group_id",
    "actor_id",
    "resource_id",
    "request_id",
    "root_authority_id",
    "root_attempt_id",
    "root_generation",
    "workflow_id",
    "version",
    "definition_digest",
    "inputs_digest",
    "scope_digest",
    "operation_expires_at_epoch",
)
_RECORD_FIELDS = frozenset(
    {
        "v",
        "issuer_epoch",
        "origin_issuer_epoch",
        "kind",
        "authority_id",
        "execution_id",
        "root_authority_id",
        "root_attempt_id",
        "root_generation",
        "group_id",
        "actor_id",
        "resource_id",
        "request_id",
        "workflow_id",
        "version",
        "definition_digest",
        "inputs_digest",
        "permission_snapshot",
        "scope_digest",
        "control_state",
        "control_revision",
        "execution_state",
        "execution_state_revision",
        "created_at_epoch",
        "updated_at_epoch",
        "operation_expires_at_epoch",
        "secret_digest",
        "integrity_digest",
    }
)
_RUN_ANCHOR_SCOPE_FIELDS = tuple(
    field for field in _EXECUTION_SCOPE_FIELDS if field != "issuer_epoch"
)
_RUN_ANCHOR_FIELDS = frozenset({*_RUN_ANCHOR_SCOPE_FIELDS, "permission_snapshot"})
_HEX_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


def _build_record_integrity_digest():
    key = secrets.token_bytes(32)

    def digest(record: Dict[str, Any]) -> str:
        payload = {key: value for key, value in record.items() if key != "integrity_digest"}
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hmac.new(key, encoded.encode("utf-8"), hashlib.sha256).hexdigest()

    return digest


_record_integrity_digest = _build_record_integrity_digest()
del _build_record_integrity_digest


def _sign_record_integrity(record: Dict[str, Any]) -> Dict[str, Any]:
    sealed = dict(record)
    sealed["integrity_digest"] = _record_integrity_digest(sealed)
    return sealed


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _discard_run_claim_owner(
    identity: int,
    reference: weakref.ReferenceType[Any],
) -> None:
    with _CLAIM_OWNERS_GUARD:
        current = _CLAIM_OWNERS.get(identity)
        if current is not None and current[0] is reference:
            _CLAIM_OWNERS.pop(identity, None)


def _bind_run_claim_owner(claim: Any, authority_home: str) -> None:
    owner = str(authority_home or "")
    if not owner:
        raise TypeError("run claim authority home is required")
    identity = id(claim)
    reference = weakref.ref(
        claim,
        lambda released, key=identity: _discard_run_claim_owner(key, released),
    )
    with _CLAIM_OWNERS_GUARD:
        _CLAIM_OWNERS[identity] = (reference, owner)


def _require_run_claim_owner(claim: Any, authority_home: str) -> None:
    identity = id(claim)
    with _CLAIM_OWNERS_GUARD:
        current = _CLAIM_OWNERS.get(identity)
        valid = bool(
            current is not None
            and current[0]() is claim
            and current[1] == authority_home
        )
    if not valid:
        raise PermissionError("run authority claim belongs to another home")


@dataclass(frozen=True, init=False)
class RunOperationClaim:
    issuer_epoch: str
    origin_issuer_epoch: str
    authority_id: str
    execution_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    workflow_id: str
    version: int
    definition_digest: str
    inputs_digest: str
    scope_digest: str
    operation_expires_at_epoch: float
    control_state: str
    control_revision: int
    permission_snapshot: Mapping[str, bool]
    _seal: object
    _validator: Callable[[Any], Any]

    def __init__(
        self,
        *,
        _seal: object,
        _authority_home: str,
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _OPERATION_SEAL:
            raise TypeError("run operation claims can only be created by validation")
        _bind_run_claim_owner(self, _authority_home)
        for field in (
            "issuer_epoch",
            "origin_issuer_epoch",
            "authority_id",
            "execution_id",
            "group_id",
            "actor_id",
            "resource_id",
            "request_id",
            "root_authority_id",
            "root_attempt_id",
            "workflow_id",
            "definition_digest",
            "inputs_digest",
            "scope_digest",
            "control_state",
        ):
            object.__setattr__(self, field, str(record.get(field) or ""))
        object.__setattr__(self, "root_generation", int(record.get("root_generation") or 0))
        object.__setattr__(self, "version", int(record.get("version") or 0))
        object.__setattr__(self, "control_revision", int(record.get("control_revision") or 0))
        object.__setattr__(
            self,
            "operation_expires_at_epoch",
            float(record.get("operation_expires_at_epoch") or 0),
        )
        object.__setattr__(
            self,
            "permission_snapshot",
            MappingProxyType(computer_control_permissions(record.get("permission_snapshot") or {})),
        )
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_validator", _validator)

    def require_current(self) -> "RunOperationClaim":
        validated = self._validator(self)
        if validated is not self:
            raise PermissionError("run operation claim is invalid")
        return self

    def __reduce__(self):
        raise TypeError("run operation claims are process-local")


@dataclass(frozen=True, init=False)
class RunReadClaim:
    issuer_epoch: str
    origin_issuer_epoch: str
    authority_id: str
    execution_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    workflow_id: str
    version: int
    definition_digest: str
    inputs_digest: str
    scope_digest: str
    operation_expires_at_epoch: float
    permission_snapshot: Mapping[str, bool]
    revision: int
    state: str
    control_state: str
    control_revision: int
    _seal: object
    _validator: Callable[[Any], Any]

    def __init__(
        self,
        *,
        _seal: object,
        _authority_home: str,
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _READ_SEAL:
            raise TypeError("run read claims can only be created by validation")
        _bind_run_claim_owner(self, _authority_home)
        _set_execution_fields(
            self,
            record,
            state=str(record.get("execution_state") or ""),
            revision=int(record.get("execution_state_revision") or 0),
        )
        object.__setattr__(self, "control_state", str(record.get("control_state") or ""))
        object.__setattr__(self, "control_revision", int(record.get("control_revision") or 0))
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_validator", _validator)

    def require_current(self) -> "RunReadClaim":
        validated = self._validator(self)
        if validated is not self:
            raise PermissionError("run read claim is invalid")
        return self

    def __reduce__(self):
        raise TypeError("run read claims are process-local")


@dataclass(frozen=True, init=False)
class RunExecutionSeedClaim:
    issuer_epoch: str
    origin_issuer_epoch: str
    authority_id: str
    execution_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    workflow_id: str
    version: int
    definition_digest: str
    inputs_digest: str
    scope_digest: str
    operation_expires_at_epoch: float
    permission_snapshot: Mapping[str, bool]
    revision: int
    state: str
    _seal: object
    _validator: Callable[[Any], Any]

    def __init__(
        self,
        *,
        _seal: object,
        _authority_home: str,
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _EXECUTION_SEED_SEAL:
            raise TypeError("run execution seed claims can only be created by derivation")
        _bind_run_claim_owner(self, _authority_home)
        _set_execution_fields(self, record, state="prepared", revision=1)
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_validator", _validator)

    def require_current(self) -> "RunExecutionSeedClaim":
        validated = self._validator(self)
        if validated is not self:
            raise PermissionError("run execution seed claim is invalid")
        return self

    def lease_identity(self) -> Dict[str, Any]:
        return _execution_lease_identity(self)

    def __reduce__(self):
        raise TypeError("run execution seed claims are process-local")


@dataclass(frozen=True, init=False)
class RunExecutionClaim:
    issuer_epoch: str
    origin_issuer_epoch: str
    authority_id: str
    execution_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    workflow_id: str
    version: int
    definition_digest: str
    inputs_digest: str
    scope_digest: str
    operation_expires_at_epoch: float
    permission_snapshot: Mapping[str, bool]
    revision: int
    state: str
    _seal: object
    _validator: Callable[[Any], Any]

    def __init__(
        self,
        *,
        _seal: object,
        _authority_home: str,
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _EXECUTION_SEAL:
            raise TypeError("run execution claims can only be created by acceptance")
        _bind_run_claim_owner(self, _authority_home)
        _set_execution_fields(
            self,
            record,
            state=str(record.get("execution_state") or ""),
            revision=int(record.get("execution_state_revision") or 0),
        )
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_validator", _validator)

    def require_current(self) -> "RunExecutionClaim":
        validated = self._validator(self)
        if validated is not self:
            raise PermissionError("run execution claim is invalid")
        return self

    def lease_identity(self) -> Dict[str, Any]:
        return _execution_lease_identity(self)

    def __reduce__(self):
        raise TypeError("run execution claims are process-local")


@dataclass(frozen=True, init=False)
class RunTerminationClaim:
    issuer_epoch: str
    origin_issuer_epoch: str
    authority_id: str
    execution_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    workflow_id: str
    version: int
    definition_digest: str
    inputs_digest: str
    scope_digest: str
    operation_expires_at_epoch: float
    permission_snapshot: Mapping[str, bool]
    revision: int
    state: str
    _seal: object
    _validator: Callable[[Any], Any]

    def __init__(
        self,
        *,
        _seal: object,
        _authority_home: str,
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _TERMINATION_SEAL:
            raise TypeError("run termination claims can only be created by cancellation")
        _bind_run_claim_owner(self, _authority_home)
        _set_execution_fields(
            self,
            record,
            state=str(record.get("execution_state") or ""),
            revision=int(record.get("execution_state_revision") or 0),
        )
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_validator", _validator)

    def require_current(self) -> "RunTerminationClaim":
        validated = self._validator(self)
        if validated is not self:
            raise PermissionError("run termination claim is invalid")
        return self

    def lease_identity(self) -> Dict[str, Any]:
        return _execution_lease_identity(self)

    def __reduce__(self):
        raise TypeError("run termination claims are process-local")


@dataclass(frozen=True, init=False)
class RunStopOwnerClaim:
    group_id: str
    actor_id: str
    resource_id: str
    authority_id: str
    execution_id: str
    _seal: object

    def __init__(self, *, _seal: object, _authority_home: str, record: Dict[str, Any]):
        if _seal is not _STOP_OWNER_SEAL:
            raise TypeError("run stop-owner claims can only be created by validation")
        _bind_run_claim_owner(self, _authority_home)
        for field in ("group_id", "actor_id", "resource_id", "authority_id", "execution_id"):
            object.__setattr__(self, field, str(record.get(field) or ""))
        object.__setattr__(self, "_seal", _seal)

    def __reduce__(self):
        raise TypeError("run stop-owner claims are process-local")


@dataclass(frozen=True, init=False)
class RunRecoveryClaim:
    issuer_epoch: str
    origin_issuer_epoch: str
    authority_id: str
    execution_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    workflow_id: str
    version: int
    definition_digest: str
    inputs_digest: str
    scope_digest: str
    operation_expires_at_epoch: float
    permission_snapshot: Mapping[str, bool]
    revision: int
    state: str
    control_state: str
    control_revision: int
    target_state: str
    lease_phase: str
    run_status: str
    _seal: object
    _validator: Callable[[Any], Any]

    def __init__(
        self,
        *,
        _seal: object,
        _authority_home: str,
        record: Dict[str, Any],
        target_state: str,
        lease_phase: str,
        run_status: str,
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _RECOVERY_SEAL:
            raise TypeError("run recovery claims can only be created by restart recovery")
        _bind_run_claim_owner(self, _authority_home)
        _set_execution_fields(
            self,
            record,
            state=str(record.get("execution_state") or ""),
            revision=int(record.get("execution_state_revision") or 0),
        )
        if target_state not in {"start_failed", "interrupted"}:
            raise TypeError("run recovery target is invalid")
        if lease_phase not in {"prepared", "execution"}:
            raise TypeError("run recovery lease phase is invalid")
        object.__setattr__(self, "target_state", target_state)
        object.__setattr__(self, "lease_phase", lease_phase)
        object.__setattr__(self, "run_status", run_status)
        object.__setattr__(self, "control_state", str(record.get("control_state") or ""))
        object.__setattr__(
            self,
            "control_revision",
            int(record.get("control_revision") or 0),
        )
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_validator", _validator)

    def require_current(self) -> "RunRecoveryClaim":
        validated = self._validator(self)
        if validated is not self:
            raise PermissionError("run recovery claim is invalid")
        return self

    def lease_identity(self) -> Dict[str, Any]:
        return _execution_lease_identity(self, phase=self.lease_phase)

    def __reduce__(self):
        raise TypeError("run recovery claims are process-local")


@dataclass(frozen=True, init=False)
class RunTerminalReconciliationClaim:
    issuer_epoch: str
    origin_issuer_epoch: str
    authority_id: str
    execution_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    workflow_id: str
    version: int
    definition_digest: str
    inputs_digest: str
    scope_digest: str
    operation_expires_at_epoch: float
    permission_snapshot: Mapping[str, bool]
    revision: int
    state: str
    control_state: str
    control_revision: int
    _seal: object
    _validator: Callable[[Any], Any]

    def __init__(
        self,
        *,
        _seal: object,
        _authority_home: str,
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _TERMINAL_RECONCILIATION_SEAL:
            raise TypeError("run terminal reconciliation claims can only be created by recovery")
        _bind_run_claim_owner(self, _authority_home)
        _set_execution_fields(
            self,
            record,
            state=str(record.get("execution_state") or ""),
            revision=int(record.get("execution_state_revision") or 0),
        )
        object.__setattr__(self, "control_state", str(record.get("control_state") or ""))
        object.__setattr__(
            self,
            "control_revision",
            int(record.get("control_revision") or 0),
        )
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_validator", _validator)

    def require_current(self) -> "RunTerminalReconciliationClaim":
        validated = self._validator(self)
        if validated is not self:
            raise PermissionError("run terminal reconciliation claim is invalid")
        return self

    def __reduce__(self):
        raise TypeError("run terminal reconciliation claims are process-local")


def _set_execution_fields(value: Any, record: Dict[str, Any], *, state: str, revision: int) -> None:
    for field in _EXECUTION_SCOPE_FIELDS:
        if field in {"root_generation", "version"}:
            object.__setattr__(value, field, int(record.get(field) or 0))
        elif field == "operation_expires_at_epoch":
            object.__setattr__(value, field, float(record.get(field) or 0))
        else:
            object.__setattr__(value, field, str(record.get(field) or ""))
    object.__setattr__(
        value,
        "permission_snapshot",
        MappingProxyType(computer_control_permissions(record.get("permission_snapshot") or {})),
    )
    object.__setattr__(value, "revision", revision)
    object.__setattr__(value, "state", state)


def _execution_lease_identity(value: Any, *, phase: str = "") -> Dict[str, Any]:
    if not phase:
        phase = "prepared" if isinstance(value, RunExecutionSeedClaim) else "execution"
    if phase not in {"prepared", "execution"}:
        raise PermissionError("run execution lease phase is invalid")
    return {
        "issuer_epoch": value.issuer_epoch,
        "kind": "run",
        "authority_id": value.authority_id,
        "execution_id": value.execution_id,
        "group_id": value.group_id,
        "actor_id": value.actor_id,
        "resource_id": value.resource_id,
        "request_id": value.request_id,
        "root_authority_id": value.root_authority_id,
        "root_attempt_id": value.root_attempt_id,
        "root_generation": value.root_generation,
        "workflow_id": value.workflow_id,
        "version": value.version,
        "definition_digest": value.definition_digest,
        "inputs_digest": value.inputs_digest,
        "scope_digest": value.scope_digest,
        "operation_expires_at_epoch": value.operation_expires_at_epoch,
        "permission_snapshot": dict(value.permission_snapshot),
        "generation": 1,
        "revision": 1,
        "state": phase,
    }


@dataclass(frozen=True)
class RunAuthorityIssue:
    operation_claim: RunOperationClaim
    execution_seed: RunExecutionSeedClaim
    receipt: Dict[str, Any]


@dataclass(frozen=True)
class RunAuthorityAcceptance:
    operation_claim: RunOperationClaim
    execution_claim: RunExecutionClaim


class RunAuthorityStore:
    VERSION = 1
    OPERATION_TTL_SECONDS = 3600.0

    def __init__(
        self,
        home: Path,
        *,
        issuer_epoch_provider: Callable[[], str],
        generation_provider: Callable[[str, str], int],
        now_provider: Callable[[], float] = time.time,
    ):
        self.home = Path(home).resolve()
        self.authority_home = str(self.home)
        self._issuer_epoch_provider = issuer_epoch_provider
        self._generation_provider = generation_provider
        self._now_provider = now_provider

    def _require_claim_home(self, claim: Any) -> None:
        _require_run_claim_owner(claim, self.authority_home)

    def _root(self, group_id: str) -> Path:
        groups_root = (self.home / "groups").resolve()
        root = (groups_root / group_id / "state" / "computer-control" / "derived-authorities").resolve()
        try:
            root.relative_to(groups_root)
        except ValueError as exc:
            raise PermissionError("run authority group path is invalid") from exc
        return root

    def _path(self, group_id: str, resource_id: str) -> Path:
        identity = f"{group_id}\0run\0{resource_id}".encode("utf-8")
        return self._root(group_id) / f"{hashlib.sha256(identity).hexdigest()}.json"

    def _run_path(self, group_id: str, resource_id: str) -> Path:
        state_root = self._root(group_id).parent.resolve()
        runs_root = (state_root / "runs").resolve()
        path = (runs_root / f"{resource_id}.json").resolve()
        if runs_root.parent != state_root or path.parent != runs_root:
            raise PermissionError("run recovery path is invalid")
        return path

    @contextmanager
    def _locked(self, group_id: str) -> Iterator[None]:
        root = self._root(group_id)
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / "authority.lock"
        with _process_lock(lock_path):
            handle = acquire_lockfile(lock_path, blocking=True)
            try:
                yield
            finally:
                release_lockfile(handle)

    @staticmethod
    def _read(path: Path) -> Dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, path: Path, value: Dict[str, Any]) -> None:
        self._validate_record_for_write(value)
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        integrity = str(value.get("integrity_digest") or "")
        authority_id = str(value.get("authority_id") or "")
        with _RECORD_INTEGRITIES_GUARD:
            _RECORD_INTEGRITIES[(self.authority_home, authority_id)] = integrity

    @staticmethod
    def _required(value: Any, field: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise PermissionError(f"run authority {field} is required")
        return normalized

    def _issuer(self) -> str:
        return self._required(self._issuer_epoch_provider(), "issuer_epoch")

    def _generation(self, group_id: str, actor_id: str) -> int:
        try:
            value = int(self._generation_provider(group_id, actor_id))
        except Exception as exc:
            raise PermissionError("run authority actor generation is unavailable") from exc
        if value <= 0:
            raise PermissionError("run authority actor generation is unavailable")
        return value

    @staticmethod
    def _scope_digest(scope: Dict[str, Any]) -> str:
        encoded = json.dumps(
            scope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _scope(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "kind": "run",
            "origin_issuer_epoch": str(record.get("origin_issuer_epoch") or ""),
            "group_id": str(record.get("group_id") or ""),
            "actor_id": str(record.get("actor_id") or ""),
            "resource_id": str(record.get("resource_id") or ""),
            "request_id": str(record.get("request_id") or ""),
            "workflow_id": str(record.get("workflow_id") or ""),
            "version": int(record.get("version") or 0),
            "definition_digest": str(record.get("definition_digest") or ""),
            "inputs_digest": str(record.get("inputs_digest") or ""),
            "permission_snapshot": computer_control_permissions(
                record.get("permission_snapshot") or {}
            ),
            "root_authority_id": str(record.get("root_authority_id") or ""),
            "root_attempt_id": str(record.get("root_attempt_id") or ""),
            "root_generation": int(record.get("root_generation") or 0),
            "operation_expires_at_epoch": float(
                record.get("operation_expires_at_epoch") or 0
            ),
        }

    def _validate_record_for_write(self, record: Dict[str, Any]) -> None:
        self._validate_reduce_only_record_shape(record)
        try:
            integrity = str(record.get("integrity_digest") or "")
            expected_integrity = _record_integrity_digest(record)
            integrity_valid = bool(
                len(integrity) == 64
                and hmac.compare_digest(integrity, expected_integrity)
            )
        except (TypeError, ValueError):
            integrity_valid = False
        if not integrity_valid:
            raise PermissionError("run authority record integrity check failed")

    def _validate_reduce_only_record_shape(self, record: Dict[str, Any]) -> None:
        """Validate persisted structure without trusting the process-local MAC."""
        try:
            required = (
                "issuer_epoch",
                "origin_issuer_epoch",
                "authority_id",
                "execution_id",
                "group_id",
                "actor_id",
                "resource_id",
                "request_id",
                "root_authority_id",
                "root_attempt_id",
                "workflow_id",
            )
            permissions = computer_control_permissions(record.get("permission_snapshot") or {})
            state_revision = (
                str(record.get("control_state") or ""),
                int(record.get("control_revision") or 0),
                str(record.get("execution_state") or ""),
                int(record.get("execution_state_revision") or 0),
            )
            digests = (
                str(record.get("definition_digest") or ""),
                str(record.get("inputs_digest") or ""),
                str(record.get("scope_digest") or ""),
                str(record.get("secret_digest") or ""),
            )
            control_state, control_revision, execution_state, execution_revision = state_revision
            state_valid = bool(
                state_revision == ("pending", 1, "prepared", 1)
                or (
                    control_state == "active"
                    and control_revision == 2
                    and execution_state in _EXECUTION_CONSUMABLE_STATES
                    and execution_revision >= 2
                )
                or (
                    control_state == "terminating"
                    and control_revision == 3
                    and execution_state in _EXECUTION_CONSUMABLE_STATES
                    and execution_revision >= 3
                )
                or (
                    control_state == "revoked"
                    and control_revision >= 2
                    and execution_state
                    in {"completed", "failed", "cancelled", "interrupted", "start_failed"}
                    and execution_revision >= 2
                )
            )
            valid = bool(
                set(record) == _RECORD_FIELDS
                and int(record.get("v") or 0) == self.VERSION
                and str(record.get("kind") or "") == "run"
                and all(str(record.get(field) or "").strip() for field in required)
                and int(record.get("root_generation") or 0) > 0
                and int(record.get("version") or 0) > 0
                and float(record.get("operation_expires_at_epoch") or 0)
                > float(record.get("created_at_epoch") or 0)
                and str(record.get("control_state") or "") in _CONTROL_STATES
                and str(record.get("execution_state") or "") in _EXECUTION_STATES
                and state_valid
                and isinstance(record.get("permission_snapshot"), dict)
                and record.get("permission_snapshot") == permissions
                and all(_HEX_DIGEST_RE.fullmatch(value) is not None for value in digests)
                and _HEX_DIGEST_RE.fullmatch(str(record.get("integrity_digest") or ""))
                is not None
                and hmac.compare_digest(
                    str(record.get("scope_digest") or ""),
                    self._scope_digest(self._scope(record)),
                )
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise PermissionError("run authority record integrity check failed")

    def _validate_recovery_run_record(
        self,
        run: Dict[str, Any],
        authority_record: Dict[str, Any],
        *,
        allowed_statuses: frozenset[str] = _ACTIVE_RECOVERY_RUN_STATES,
    ) -> None:
        try:
            authority = (
                run.get("run_authority")
                if isinstance(run.get("run_authority"), dict)
                else {}
            )
            expected_authority = self._record_run_anchor_identity(authority_record)
            # Business progress fields remain mutable. Only this closed identity
            # projection is interpreted by restart recovery.
            valid = bool(
                str(run.get("origin") or "") == "manual_actor"
                and str(run.get("status") or "") in allowed_statuses
                and str(run.get("group_id") or "")
                == str(authority_record.get("group_id") or "")
                and str(run.get("actor_id") or "")
                == str(authority_record.get("actor_id") or "")
                and str(run.get("run_id") or "")
                == str(authority_record.get("resource_id") or "")
                and str(run.get("workflow_id") or "")
                == str(authority_record.get("workflow_id") or "")
                and int(run.get("version") or 0)
                == int(authority_record.get("version") or 0)
                and set(authority) == _RUN_ANCHOR_FIELDS
                and authority == expected_authority
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise PermissionError("run restart recovery record is invalid")

    def _read_recovery_run_record(
        self,
        group_id: str,
        resource_id: str,
        authority_record: Dict[str, Any],
        *,
        allowed_statuses: frozenset[str] = _ACTIVE_RECOVERY_RUN_STATES,
    ) -> Dict[str, Any]:
        run = self._read(self._run_path(group_id, resource_id))
        self._validate_recovery_run_record(
            run,
            authority_record,
            allowed_statuses=allowed_statuses,
        )
        return run

    def _read_restart_run_prefix(
        self,
        group_id: str,
        resource_id: str,
        authority_record: Dict[str, Any],
    ) -> tuple[Dict[str, Any] | None, str]:
        run_path = self._run_path(group_id, resource_id)
        if not run_path.exists():
            if (
                str(authority_record.get("control_state") or "") == "pending"
                and str(authority_record.get("execution_state") or "") == "prepared"
            ):
                return None, ""
            raise PermissionError("run restart recovery record is invalid")
        run = self._read(run_path)
        self._validate_recovery_run_record(run, authority_record)
        return run, str(run.get("status") or "")

    def _restart_lease_phase(
        self,
        authority_record: Dict[str, Any],
        run_status: str,
    ) -> str:
        if str(authority_record.get("execution_state") or "") == "prepared":
            return "prepared"
        if run_status == "initializing":
            lease = self._read(self.home / "state" / "computer-control" / "lease.json")
            if (
                str(lease.get("group_id") or "")
                == str(authority_record.get("group_id") or "")
                and str(lease.get("actor_id") or "")
                == str(authority_record.get("actor_id") or "")
                and str(lease.get("run_id") or "")
                == str(authority_record.get("resource_id") or "")
                and lease.get("reservation") is True
            ):
                return "prepared"
        return "execution"

    def _assert_no_same_run_lease(
        self,
        group_id: str,
        actor_id: str,
        resource_id: str,
    ) -> None:
        # Restart has no live task, so this unlocked atomic snapshot only
        # enforces write ordering. Lease ownership is proved and changed solely
        # by ComputerControlLease under its lock with the full sealed lineage.
        lease = self._read(self.home / "state" / "computer-control" / "lease.json")
        if all(
            str(lease.get(field) or "") == expected
            for field, expected in (
                ("group_id", group_id),
                ("actor_id", actor_id),
                ("run_id", resource_id),
            )
        ):
            raise PermissionError("run restart recovery lease must be released first")

    def _write_run_terminal_projection(
        self,
        run: Dict[str, Any],
        *,
        group_id: str,
        resource_id: str,
        target_state: str,
        authority_record: Dict[str, Any],
    ) -> Dict[str, Any]:
        updated = {
            **run,
            "status": target_state,
            "run_authority": self._record_run_anchor_identity(authority_record),
            "updated_at": float(self._now_provider()),
        }
        atomic_write_text(
            self._run_path(group_id, resource_id),
            json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        return updated

    def _validate_record(self, record: Dict[str, Any]) -> None:
        self._validate_record_for_write(record)
        authority_id = str(record.get("authority_id") or "")
        integrity = str(record.get("integrity_digest") or "")
        with _RECORD_INTEGRITIES_GUARD:
            current_integrity = _RECORD_INTEGRITIES.get(
                (self.authority_home, authority_id), ""
            )
        if not current_integrity or not hmac.compare_digest(integrity, current_integrity):
            raise PermissionError("run authority record integrity check failed")

    @staticmethod
    def _execution_identity(value: Any) -> Dict[str, Any]:
        result = {field: getattr(value, field, None) for field in _EXECUTION_SCOPE_FIELDS}
        result["permission_snapshot"] = dict(getattr(value, "permission_snapshot", {}))
        return result

    @staticmethod
    def _record_execution_identity(record: Dict[str, Any]) -> Dict[str, Any]:
        result = {field: record.get(field) for field in _EXECUTION_SCOPE_FIELDS}
        result["permission_snapshot"] = computer_control_permissions(
            record.get("permission_snapshot") or {}
        )
        return result

    @staticmethod
    def _record_run_anchor_identity(record: Dict[str, Any]) -> Dict[str, Any]:
        result = {field: record.get(field) for field in _RUN_ANCHOR_SCOPE_FIELDS}
        result["permission_snapshot"] = computer_control_permissions(
            record.get("permission_snapshot") or {}
        )
        return result

    @staticmethod
    def _operation_identity(value: RunOperationClaim) -> Dict[str, Any]:
        result = {
            field: getattr(value, field)
            for field in (
                "issuer_epoch",
                "origin_issuer_epoch",
                "authority_id",
                "execution_id",
                "group_id",
                "actor_id",
                "resource_id",
                "request_id",
                "root_authority_id",
                "root_attempt_id",
                "root_generation",
                "workflow_id",
                "version",
                "definition_digest",
                "inputs_digest",
                "scope_digest",
                "operation_expires_at_epoch",
            )
        }
        result["permission_snapshot"] = dict(value.permission_snapshot)
        return result

    @staticmethod
    def _record_operation_identity(record: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            field: record.get(field)
            for field in (
                "issuer_epoch",
                "origin_issuer_epoch",
                "authority_id",
                "execution_id",
                "group_id",
                "actor_id",
                "resource_id",
                "request_id",
                "root_authority_id",
                "root_attempt_id",
                "root_generation",
                "workflow_id",
                "version",
                "definition_digest",
                "inputs_digest",
                "scope_digest",
                "operation_expires_at_epoch",
            )
        }
        result["permission_snapshot"] = computer_control_permissions(
            record.get("permission_snapshot") or {}
        )
        return result

    def _operation_claim(self, record: Dict[str, Any]) -> RunOperationClaim:
        return RunOperationClaim(
            _seal=_OPERATION_SEAL,
            _authority_home=self.authority_home,
            record=record,
            _validator=self.require_operation_claim,
        )

    def _read_claim(self, record: Dict[str, Any]) -> RunReadClaim:
        return RunReadClaim(
            _seal=_READ_SEAL,
            _authority_home=self.authority_home,
            record=record,
            _validator=self.require_read_claim,
        )

    def _execution_seed(self, record: Dict[str, Any]) -> RunExecutionSeedClaim:
        return RunExecutionSeedClaim(
            _seal=_EXECUTION_SEED_SEAL,
            _authority_home=self.authority_home,
            record=record,
            _validator=self.require_execution_seed,
        )

    def _execution_claim(self, record: Dict[str, Any]) -> RunExecutionClaim:
        return RunExecutionClaim(
            _seal=_EXECUTION_SEAL,
            _authority_home=self.authority_home,
            record=record,
            _validator=self.require_execution_claim,
        )

    def _termination_claim(self, record: Dict[str, Any]) -> RunTerminationClaim:
        return RunTerminationClaim(
            _seal=_TERMINATION_SEAL,
            _authority_home=self.authority_home,
            record=record,
            _validator=self.require_termination_claim,
        )

    @classmethod
    def _execution_claim_matches(cls, claim: Any, record: Dict[str, Any]) -> bool:
        return bool(
            cls._execution_identity(claim) == cls._record_execution_identity(record)
            and str(getattr(claim, "state", "")) == str(record.get("execution_state") or "")
            and int(getattr(claim, "revision", 0))
            == int(record.get("execution_state_revision") or 0)
        )

    @staticmethod
    def _receipt_identity(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "v",
                "issuer_epoch",
                "origin_issuer_epoch",
                "kind",
                "authority_id",
                "execution_id",
                "group_id",
                "actor_id",
                "resource_id",
                "request_id",
                "root_authority_id",
                "root_attempt_id",
                "root_generation",
                "workflow_id",
                "version",
                "definition_digest",
                "inputs_digest",
                "scope_digest",
                "operation_expires_at_epoch",
                "permission_snapshot",
            )
        }

    @classmethod
    def _terminal_receipt_identity(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        identity = cls._receipt_identity(record)
        identity.pop("issuer_epoch", None)
        return identity

    def begin_run(
        self,
        *,
        group_id: str,
        actor_id: str,
        resource_id: str,
        request_id: str,
        start_claim: RunStartClaim,
    ) -> RunAuthorityIssue:
        group = self._required(group_id, "group_id")
        actor = self._required(actor_id, "actor_id")
        resource = self._required(resource_id, "resource_id")
        request = self._required(request_id, "request_id")
        if not isinstance(start_claim, RunStartClaim):
            raise PermissionError("validated run start claim is required")

        def derive(current: RunStartClaim) -> RunAuthorityIssue:
            issuer = self._issuer()
            generation = self._generation(group, actor)
            if (
                current.issuer_epoch != issuer
                or current.group_id != group
                or current.actor_id != actor
                or current.request_id != request
                or current.generation != generation
            ):
                raise PermissionError("run authority root claim does not match the current actor generation")
            permissions = computer_control_permissions(dict(current.permission_snapshot))
            now = float(self._now_provider())
            record = {
                "v": self.VERSION,
                "issuer_epoch": issuer,
                "origin_issuer_epoch": issuer,
                "kind": "run",
                "authority_id": f"runauth_{uuid.uuid4().hex}",
                "execution_id": f"runexec_{uuid.uuid4().hex}",
                "root_authority_id": current.root_authority_id,
                "root_attempt_id": current.root_attempt_id,
                "root_generation": current.generation,
                "group_id": group,
                "actor_id": actor,
                "resource_id": resource,
                "request_id": request,
                "workflow_id": current.workflow_id,
                "version": current.version,
                "definition_digest": current.definition_digest,
                "inputs_digest": current.inputs_digest,
                "permission_snapshot": permissions,
                "scope_digest": "",
                "control_state": "pending",
                "control_revision": 1,
                "execution_state": "prepared",
                "execution_state_revision": 1,
                "created_at_epoch": now,
                "updated_at_epoch": now,
                "operation_expires_at_epoch": now + self.OPERATION_TTL_SECONDS,
            }
            path = self._path(group, resource)
            with self._locked(group):
                if self._read(path):
                    raise PermissionError("run authority already exists for this resource")
                derivation = {
                    "kind": "run",
                    "root_authority_id": current.root_authority_id,
                    "root_attempt_id": current.root_attempt_id,
                    "root_generation": current.generation,
                    "request_id": request,
                }
                for candidate_path in self._root(group).glob("*.json"):
                    candidate = self._read(candidate_path)
                    if all(candidate.get(key) == value for key, value in derivation.items()):
                        raise PermissionError("run authority already exists for this root claim")
                current.require_fresh_current()
                secret = secrets.token_urlsafe(32)
                record["secret_digest"] = hashlib.sha256(secret.encode("utf-8")).hexdigest()
                record["scope_digest"] = self._scope_digest(self._scope(record))
                record = _sign_record_integrity(record)
                self._write(path, record)
            return RunAuthorityIssue(
                operation_claim=self._operation_claim(record),
                execution_seed=self._execution_seed(record),
                receipt={**self._receipt_identity(record), "authorization_secret": secret},
            )

        issue = start_claim.consume_current(derive)
        if not isinstance(issue, RunAuthorityIssue):
            raise PermissionError("run authority root claim is no longer current")
        return issue

    def accept_execution(self, seed: RunExecutionSeedClaim) -> RunAuthorityAcceptance:
        if not isinstance(seed, RunExecutionSeedClaim) or seed._seal is not _EXECUTION_SEED_SEAL:
            raise PermissionError("validated run execution seed claim is required")
        self._require_claim_home(seed)
        path = self._path(seed.group_id, seed.resource_id)
        with self._locked(seed.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or not self._execution_claim_matches(seed, record)
                or str(record.get("control_state") or "") != "pending"
                or int(record.get("control_revision") or 0) != 1
                or str(record.get("execution_state") or "") != "prepared"
                or int(record.get("execution_state_revision") or 0) != 1
            ):
                raise PermissionError("run execution seed claim is stale")
            updated = _sign_record_integrity({
                **record,
                "control_state": "active",
                "control_revision": 2,
                "execution_state": "accepted",
                "execution_state_revision": 2,
                "updated_at_epoch": float(self._now_provider()),
            })
            self._write(path, updated)
            return RunAuthorityAcceptance(
                operation_claim=self._operation_claim(updated),
                execution_claim=self._execution_claim(updated),
            )

    def validate_operation_receipt(
        self,
        receipt: Any,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str = "run",
    ) -> RunOperationClaim:
        if not isinstance(receipt, dict):
            raise PermissionError("run operation receipt is required")
        group = self._required(expected_group_id, "expected_group_id")
        actor = self._required(expected_actor_id, "expected_actor_id")
        resource = self._required(expected_resource_id, "expected_resource_id")
        if expected_kind != "run" or any(
            str(receipt.get(field) or "").strip() != expected
            for field, expected in (
                ("kind", "run"),
                ("group_id", group),
                ("actor_id", actor),
                ("resource_id", resource),
            )
        ):
            raise PermissionError("run operation receipt does not match the expected resource")
        with self._locked(group):
            record = self._read(self._path(group, resource))
            self._validate_record(record)
            secret = str(receipt.get("authorization_secret") or "")
            digest = hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else ""
            if (
                self._receipt_identity(receipt) != self._receipt_identity(record)
                or str(record.get("issuer_epoch") or "") != self._issuer()
                or int(record.get("root_generation") or 0) != self._generation(group, actor)
                or str(record.get("control_state") or "") != "active"
                or float(record.get("operation_expires_at_epoch") or 0)
                <= float(self._now_provider())
                or not digest
                or not hmac.compare_digest(digest, str(record.get("secret_digest") or ""))
            ):
                raise PermissionError("run operation receipt is invalid")
            return self._operation_claim(record)

    def validate_read_receipt(
        self,
        receipt: Any,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str = "run",
    ) -> RunReadClaim:
        if not isinstance(receipt, dict):
            raise PermissionError("run read receipt is required")
        group = self._required(expected_group_id, "expected_group_id")
        actor = self._required(expected_actor_id, "expected_actor_id")
        resource = self._required(expected_resource_id, "expected_resource_id")
        if expected_kind != "run" or any(
            str(receipt.get(field) or "").strip() != expected
            for field, expected in (
                ("kind", "run"),
                ("group_id", group),
                ("actor_id", actor),
                ("resource_id", resource),
            )
        ):
            raise PermissionError("run read receipt does not match the expected resource")
        with self._locked(group):
            record = self._read(self._path(group, resource))
            self._validate_record(record)
            secret = str(receipt.get("authorization_secret") or "")
            digest = hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else ""
            control = str(record.get("control_state") or "")
            execution = str(record.get("execution_state") or "")
            live = control in {"active", "terminating"} and execution in _EXECUTION_CONSUMABLE_STATES
            terminal = control == "revoked" and execution in _TERMINAL_EXECUTION_STATES
            receipt_matches = (
                self._receipt_identity(receipt) == self._receipt_identity(record)
                if live
                else bool(
                    terminal
                    and str(receipt.get("issuer_epoch") or "")
                    == str(record.get("origin_issuer_epoch") or "")
                    and self._terminal_receipt_identity(receipt)
                    == self._terminal_receipt_identity(record)
                )
            )
            live_current = bool(
                not live
                or (
                    int(record.get("root_generation") or 0) == self._generation(group, actor)
                    and float(record.get("operation_expires_at_epoch") or 0)
                    > float(self._now_provider())
                )
            )
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or not (live or terminal)
                or not receipt_matches
                or not live_current
                or not digest
                or not hmac.compare_digest(digest, str(record.get("secret_digest") or ""))
            ):
                raise PermissionError("run read receipt is invalid")
            return self._read_claim(record)

    def require_read_claim(self, claim: Any) -> RunReadClaim:
        if not isinstance(claim, RunReadClaim) or claim._seal is not _READ_SEAL:
            raise PermissionError("validated run read claim is required")
        self._require_claim_home(claim)
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_record(record)
            control = str(record.get("control_state") or "")
            execution = str(record.get("execution_state") or "")
            live = control in {"active", "terminating"} and execution in _EXECUTION_CONSUMABLE_STATES
            terminal = control == "revoked" and execution in _TERMINAL_EXECUTION_STATES
            live_current = bool(
                not live
                or (
                    int(record.get("root_generation") or 0)
                    == self._generation(claim.group_id, claim.actor_id)
                    and float(record.get("operation_expires_at_epoch") or 0)
                    > float(self._now_provider())
                )
            )
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or not (live or terminal)
                or not self._execution_claim_matches(claim, record)
                or control != claim.control_state
                or int(record.get("control_revision") or 0) != claim.control_revision
                or not live_current
            ):
                raise PermissionError("run read claim is stale")
        return claim

    def require_operation_claim(self, claim: Any) -> RunOperationClaim:
        if not isinstance(claim, RunOperationClaim) or claim._seal is not _OPERATION_SEAL:
            raise PermissionError("validated run operation claim is required")
        self._require_claim_home(claim)
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._require_current_operation_record(claim, record)
        return claim

    def _require_current_operation_record(
        self,
        claim: RunOperationClaim,
        record: Dict[str, Any],
    ) -> None:
        self._validate_record(record)
        if (
            self._operation_identity(claim) != self._record_operation_identity(record)
            or str(record.get("issuer_epoch") or "") != self._issuer()
            or int(record.get("root_generation") or 0)
            != self._generation(claim.group_id, claim.actor_id)
            or str(record.get("control_state") or "") != "active"
            or int(record.get("control_revision") or 0) != claim.control_revision
            or float(record.get("operation_expires_at_epoch") or 0)
            <= float(self._now_provider())
        ):
            raise PermissionError("run operation claim is stale")

    def perform_operation_with_callback(
        self,
        claim: RunOperationClaim,
        callback: Callable[[Dict[str, Any]], Any],
    ) -> Any:
        """Serialize one local control write with cancellation/finalization."""
        if not isinstance(claim, RunOperationClaim) or claim._seal is not _OPERATION_SEAL:
            raise PermissionError("validated run operation claim is required")
        self._require_claim_home(claim)
        if not callable(callback):
            raise TypeError("run operation callback is required")
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._require_current_operation_record(claim, record)
            result = callback(self._record_run_anchor_identity(record))
            if inspect.isawaitable(result):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise TypeError("run operation callback must complete synchronously")
            return result

    def require_execution_seed(self, claim: Any) -> RunExecutionSeedClaim:
        if not isinstance(claim, RunExecutionSeedClaim) or claim._seal is not _EXECUTION_SEED_SEAL:
            raise PermissionError("validated run execution seed claim is required")
        self._require_claim_home(claim)
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or not self._execution_claim_matches(claim, record)
                or str(record.get("control_state") or "") != "pending"
                or int(record.get("control_revision") or 0) != 1
                or str(record.get("execution_state") or "") != "prepared"
                or int(record.get("execution_state_revision") or 0) != 1
            ):
                raise PermissionError("run execution seed claim is stale")
        return claim

    def require_execution_claim(self, claim: Any) -> RunExecutionClaim:
        if not isinstance(claim, RunExecutionClaim) or claim._seal is not _EXECUTION_SEAL:
            raise PermissionError("validated run execution claim is required")
        self._require_claim_home(claim)
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or not self._execution_claim_matches(claim, record)
                or str(record.get("control_state") or "") != "active"
                or str(record.get("execution_state") or "") not in _EXECUTION_CONSUMABLE_STATES
            ):
                raise PermissionError("run execution claim is stale")
        return claim

    def run_anchor_for_execution_claim(
        self,
        claim: RunExecutionSeedClaim | RunExecutionClaim,
    ) -> Dict[str, Any]:
        if isinstance(claim, RunExecutionSeedClaim):
            self.require_execution_seed(claim)
        elif isinstance(claim, RunExecutionClaim):
            self.require_execution_claim(claim)
        else:
            raise PermissionError("validated run execution authority claim is required")
        return {
            field: getattr(claim, field)
            for field in _RUN_ANCHOR_SCOPE_FIELDS
        } | {"permission_snapshot": dict(claim.permission_snapshot)}

    def run_anchor_for_operation_claim(
        self,
        claim: RunOperationClaim,
    ) -> Dict[str, Any]:
        self.require_operation_claim(claim)
        return {
            field: getattr(claim, field)
            for field in _RUN_ANCHOR_SCOPE_FIELDS
        } | {"permission_snapshot": dict(claim.permission_snapshot)}

    def run_anchor_for_read_claim(self, claim: RunReadClaim) -> Dict[str, Any]:
        self.require_read_claim(claim)
        return {
            field: getattr(claim, field)
            for field in _RUN_ANCHOR_SCOPE_FIELDS
        } | {"permission_snapshot": dict(claim.permission_snapshot)}

    def require_termination_claim(self, claim: Any) -> RunTerminationClaim:
        if not isinstance(claim, RunTerminationClaim) or claim._seal is not _TERMINATION_SEAL:
            raise PermissionError("validated run termination claim is required")
        self._require_claim_home(claim)
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or not self._execution_claim_matches(claim, record)
                or str(record.get("control_state") or "") != "terminating"
                or str(record.get("execution_state") or "") not in _EXECUTION_CONSUMABLE_STATES
            ):
                raise PermissionError("run termination claim is stale")
        return claim

    def _transition_execution(
        self,
        claim: RunExecutionClaim,
        *,
        target: str,
        sources: frozenset[str],
    ) -> RunExecutionClaim:
        if not isinstance(claim, RunExecutionClaim) or claim._seal is not _EXECUTION_SEAL:
            raise PermissionError("validated run execution claim is required")
        self._require_claim_home(claim)
        path = self._path(claim.group_id, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("control_state") or "") != "active"
                or not self._execution_claim_matches(claim, record)
                or str(record.get("execution_state") or "") not in sources
            ):
                raise PermissionError("run execution claim is stale")
            updated = _sign_record_integrity(
                {
                    **record,
                    "execution_state": target,
                    "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write(path, updated)
            return self._execution_claim(updated)

    def mark_running(self, claim: RunExecutionClaim) -> RunExecutionClaim:
        return self._transition_execution(
            claim,
            target="running",
            sources=frozenset({"accepted", "waiting_recovery", "waiting_approval"}),
        )

    def mark_waiting_recovery(self, claim: RunExecutionClaim) -> RunExecutionClaim:
        return self._transition_execution(
            claim, target="waiting_recovery", sources=frozenset({"running"})
        )

    def mark_waiting_approval(self, claim: RunExecutionClaim) -> RunExecutionClaim:
        return self._transition_execution(
            claim, target="waiting_approval", sources=frozenset({"running"})
        )

    def mark_awaiting_verification(self, claim: RunExecutionClaim) -> RunExecutionClaim:
        return self._transition_execution(
            claim, target="awaiting_verification", sources=frozenset({"running"})
        )

    def _finish_execution(
        self,
        claim: RunExecutionClaim,
        *,
        target: str,
        sources: frozenset[str],
    ) -> RunOperationClaim:
        if not isinstance(claim, RunExecutionClaim) or claim._seal is not _EXECUTION_SEAL:
            raise PermissionError("validated run execution claim is required")
        self._require_claim_home(claim)
        path = self._path(claim.group_id, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("control_state") or "") != "active"
                or not self._execution_claim_matches(claim, record)
                or str(record.get("execution_state") or "") not in sources
            ):
                raise PermissionError("run execution claim is stale")
            updated = _sign_record_integrity(
                {
                    **record,
                    "control_state": "revoked",
                    "control_revision": int(record.get("control_revision") or 0) + 1,
                    "execution_state": target,
                    "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write(path, updated)
            return self._operation_claim(updated)

    def finish_execution_with_callback(
        self,
        claim: RunExecutionClaim,
        callback: Callable[[Callable[[], None]], str],
        *,
        operation_claim: RunOperationClaim | None = None,
    ) -> RunOperationClaim:
        """Serialize a local finalization side effect with cancellation.

        The callback runs while the authority lock is held. It may only perform
        local, bounded persistence such as workflow publish/trust; provider RPCs
        must remain outside this transaction.
        """
        if not isinstance(claim, RunExecutionClaim) or claim._seal is not _EXECUTION_SEAL:
            raise PermissionError("validated run execution claim is required")
        self._require_claim_home(claim)
        if operation_claim is not None and (
            not isinstance(operation_claim, RunOperationClaim)
            or operation_claim._seal is not _OPERATION_SEAL
        ):
            raise PermissionError("validated run operation claim is required")
        if operation_claim is not None:
            self._require_claim_home(operation_claim)
        if not callable(callback):
            raise TypeError("run finalization callback is required")
        path = self._path(claim.group_id, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)

            def require_current() -> None:
                current = self._read(path)
                self._validate_record(current)
                operation_valid = True
                if operation_claim is not None:
                    operation_valid = bool(
                        self._operation_identity(operation_claim)
                        == self._record_operation_identity(current)
                        and int(current.get("root_generation") or 0)
                        == self._generation(claim.group_id, claim.actor_id)
                        and int(current.get("control_revision") or 0)
                        == operation_claim.control_revision
                        and float(current.get("operation_expires_at_epoch") or 0)
                        > float(self._now_provider())
                    )
                if (
                    str(current.get("issuer_epoch") or "") != self._issuer()
                    or str(current.get("control_state") or "") != "active"
                    or not self._execution_claim_matches(claim, current)
                    or str(current.get("execution_state") or "")
                    not in _EXECUTION_CONSUMABLE_STATES
                    or not operation_valid
                ):
                    raise PermissionError("run finalization claim is stale")

            require_current()
            target = str(callback(require_current) or "")
            if target not in {"completed", "failed", "interrupted"}:
                raise PermissionError("run finalization target is invalid")
            if target == "completed" and str(record.get("execution_state") or "") not in {
                "running",
                "awaiting_verification",
            }:
                raise PermissionError("run execution claim is stale")
            require_current()
            updated = _sign_record_integrity(
                {
                    **record,
                    "control_state": "revoked",
                    "control_revision": int(record.get("control_revision") or 0) + 1,
                    "execution_state": target,
                    "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write(path, updated)
            return self._operation_claim(updated)

    def mark_completed(self, claim: RunExecutionClaim) -> RunOperationClaim:
        return self._finish_execution(
            claim,
            target="completed",
            sources=frozenset({"running", "awaiting_verification"}),
        )

    def mark_failed(self, claim: RunExecutionClaim) -> RunOperationClaim:
        return self._finish_execution(
            claim,
            target="failed",
            sources=frozenset(_EXECUTION_CONSUMABLE_STATES),
        )

    def mark_interrupted(self, claim: RunExecutionClaim) -> RunOperationClaim:
        return self._finish_execution(
            claim,
            target="interrupted",
            sources=frozenset(_EXECUTION_CONSUMABLE_STATES),
        )

    def fail_accepted_start(self, claim: RunExecutionClaim) -> RunOperationClaim:
        return self._finish_execution(
            claim, target="start_failed", sources=frozenset({"accepted", "running"})
        )

    def validate_stop_owner(
        self,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str = "run",
    ) -> RunStopOwnerClaim:
        group = self._required(expected_group_id, "expected_group_id")
        actor = self._required(expected_actor_id, "expected_actor_id")
        resource = self._required(expected_resource_id, "expected_resource_id")
        if expected_kind != "run":
            raise PermissionError("run stop owner does not match the expected resource")
        with self._locked(group):
            record = self._read(self._path(group, resource))
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("kind") or "") != "run"
                or str(record.get("group_id") or "") != group
                or str(record.get("actor_id") or "") != actor
                or str(record.get("resource_id") or "") != resource
                or str(record.get("control_state") or "") not in {"active", "terminating"}
            ):
                raise PermissionError("run stop owner does not match the expected resource")
            return RunStopOwnerClaim(
                _seal=_STOP_OWNER_SEAL,
                _authority_home=self.authority_home,
                record=record,
            )

    def begin_cancel(
        self,
        stop_claim: RunStopOwnerClaim,
    ) -> RunTerminationClaim:
        if not isinstance(stop_claim, RunStopOwnerClaim) or stop_claim._seal is not _STOP_OWNER_SEAL:
            raise PermissionError("validated run stop-owner claim is required")
        self._require_claim_home(stop_claim)
        path = self._path(stop_claim.group_id, stop_claim.resource_id)
        with self._locked(stop_claim.group_id):
            record = self._read(path)
            self._validate_record(record)
            if any(
                str(record.get(field) or "") != str(getattr(stop_claim, field))
                for field in ("group_id", "actor_id", "resource_id", "authority_id", "execution_id")
            ):
                raise PermissionError("run stop-owner claim is stale")
            if str(record.get("control_state") or "") == "terminating":
                return self._termination_claim(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("control_state") or "") != "active"
                or str(record.get("execution_state") or "") not in _EXECUTION_CONSUMABLE_STATES
            ):
                raise PermissionError("run stop-owner claim is stale")
            updated = _sign_record_integrity(
                {
                    **record,
                    "control_state": "terminating",
                    "control_revision": int(record.get("control_revision") or 0) + 1,
                    "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write(path, updated)
            return self._termination_claim(updated)

    def begin_cancel_for_execution(
        self,
        stop_claim: RunStopOwnerClaim,
        execution_claim: RunExecutionClaim,
        *,
        validate_live: Callable[[Dict[str, Any]], None],
        on_started: Callable[[RunTerminationClaim], None],
    ) -> RunTerminationClaim:
        """Begin cancellation only while the exact live execution is attached."""
        if not isinstance(stop_claim, RunStopOwnerClaim) or stop_claim._seal is not _STOP_OWNER_SEAL:
            raise PermissionError("validated run stop-owner claim is required")
        self._require_claim_home(stop_claim)
        if (
            not isinstance(execution_claim, RunExecutionClaim)
            or execution_claim._seal is not _EXECUTION_SEAL
        ):
            raise PermissionError("validated run execution claim is required")
        self._require_claim_home(execution_claim)
        if not callable(validate_live) or not callable(on_started):
            raise TypeError("run cancellation callbacks are required")
        path = self._path(stop_claim.group_id, stop_claim.resource_id)
        with self._locked(stop_claim.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                any(
                    str(record.get(field) or "") != str(getattr(stop_claim, field))
                    for field in (
                        "group_id",
                        "actor_id",
                        "resource_id",
                        "authority_id",
                        "execution_id",
                    )
                )
                or str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("control_state") or "") != "active"
                or str(record.get("execution_state") or "")
                not in _EXECUTION_CONSUMABLE_STATES
                or not self._execution_claim_matches(execution_claim, record)
            ):
                raise PermissionError("run cancellation claim is stale")
            validate_live(self._record_run_anchor_identity(record))
            updated = _sign_record_integrity(
                {
                    **record,
                    "control_state": "terminating",
                    "control_revision": int(record.get("control_revision") or 0) + 1,
                    "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write(path, updated)
            termination = self._termination_claim(updated)
            on_started(termination)
            return termination

    def mark_cancelled(self, claim: RunTerminationClaim) -> RunOperationClaim:
        if not isinstance(claim, RunTerminationClaim) or claim._seal is not _TERMINATION_SEAL:
            raise PermissionError("validated run termination claim is required")
        self._require_claim_home(claim)
        path = self._path(claim.group_id, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("control_state") or "") != "terminating"
                or not self._execution_claim_matches(claim, record)
            ):
                raise PermissionError("run termination claim is stale")
            updated = _sign_record_integrity(
                {
                    **record,
                    "control_state": "revoked",
                    "control_revision": int(record.get("control_revision") or 0) + 1,
                    "execution_state": "cancelled",
                    "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write(path, updated)
            return self._operation_claim(updated)

    def prepare_restart_recovery(
        self,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_authority_id: str,
        expected_execution_id: str,
    ) -> RunRecoveryClaim:
        group = self._required(expected_group_id, "expected_group_id")
        actor = self._required(expected_actor_id, "expected_actor_id")
        resource = self._required(expected_resource_id, "expected_resource_id")
        authority_id = self._required(expected_authority_id, "expected_authority_id")
        execution_id = self._required(expected_execution_id, "expected_execution_id")
        path = self._path(group, resource)
        with self._locked(group):
            record = self._read(path)
            self._validate_reduce_only_record_shape(record)
            if (
                str(record.get("issuer_epoch") or "") == self._issuer()
                or str(record.get("kind") or "") != "run"
                or str(record.get("group_id") or "") != group
                or str(record.get("actor_id") or "") != actor
                or str(record.get("resource_id") or "") != resource
                or str(record.get("authority_id") or "") != authority_id
                or str(record.get("execution_id") or "") != execution_id
                or str(record.get("control_state") or "") not in {"pending", "active", "terminating"}
                or str(record.get("execution_state") or "")
                not in {"prepared", *_EXECUTION_CONSUMABLE_STATES}
            ):
                raise PermissionError("run restart recovery identity is invalid")
            run, run_status = self._read_restart_run_prefix(group, resource, record)
            target_state = (
                "start_failed"
                if run is None or run_status == "initializing"
                else "interrupted"
            )
            return RunRecoveryClaim(
                _seal=_RECOVERY_SEAL,
                _authority_home=self.authority_home,
                record=record,
                target_state=target_state,
                lease_phase=self._restart_lease_phase(record, run_status),
                run_status=run_status,
                _validator=self.require_recovery_claim,
            )

    def require_recovery_claim(self, claim: Any) -> RunRecoveryClaim:
        if not isinstance(claim, RunRecoveryClaim) or claim._seal is not _RECOVERY_SEAL:
            raise PermissionError("validated run recovery claim is required")
        self._require_claim_home(claim)
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_reduce_only_record_shape(record)
            if (
                str(record.get("issuer_epoch") or "") == self._issuer()
                or any(
                    str(record.get(field) or "") != str(getattr(claim, field))
                    for field in (
                        "issuer_epoch",
                        "authority_id",
                        "execution_id",
                        "group_id",
                        "actor_id",
                        "resource_id",
                    )
                )
                or int(record.get("execution_state_revision") or 0) != claim.revision
                or str(record.get("execution_state") or "") != claim.state
                or str(record.get("control_state") or "") != claim.control_state
                or int(record.get("control_revision") or 0) != claim.control_revision
            ):
                raise PermissionError("run recovery claim is stale")
            if self._execution_identity(claim) != self._record_execution_identity(record):
                raise PermissionError("run recovery claim is stale")
            run, run_status = self._read_restart_run_prefix(
                claim.group_id,
                claim.resource_id,
                record,
            )
            target_state = (
                "start_failed"
                if run is None or run_status == "initializing"
                else "interrupted"
            )
            if run_status != claim.run_status or target_state != claim.target_state:
                raise PermissionError("run recovery claim is stale")
        return claim

    def finish_restart_recovery(self, claim: RunRecoveryClaim) -> Dict[str, Any]:
        if not isinstance(claim, RunRecoveryClaim) or claim._seal is not _RECOVERY_SEAL:
            raise PermissionError("validated run recovery claim is required")
        self._require_claim_home(claim)
        self.require_recovery_claim(claim)
        self._assert_no_same_run_lease(
            claim.group_id,
            claim.actor_id,
            claim.resource_id,
        )
        path = self._path(claim.group_id, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            self._validate_reduce_only_record_shape(record)
            if any(
                str(record.get(field) or "") != str(getattr(claim, field))
                for field in (
                    "issuer_epoch",
                    "authority_id",
                    "execution_id",
                    "group_id",
                    "actor_id",
                    "resource_id",
                )
            ) or (
                int(record.get("execution_state_revision") or 0) != claim.revision
                or str(record.get("execution_state") or "") != claim.state
                or str(record.get("control_state") or "") != claim.control_state
                or int(record.get("control_revision") or 0) != claim.control_revision
            ):
                raise PermissionError("run restart recovery claim is stale")
            if self._execution_identity(claim) != self._record_execution_identity(record):
                raise PermissionError("run restart recovery claim is stale")
            run, run_status = self._read_restart_run_prefix(
                claim.group_id,
                claim.resource_id,
                record,
            )
            if run_status != claim.run_status:
                raise PermissionError("run restart recovery claim is stale")
            updated = _sign_record_integrity(
                {
                    **record,
                    "issuer_epoch": self._issuer(),
                    "control_state": "revoked",
                    "control_revision": max(2, int(record.get("control_revision") or 0) + 1),
                    "execution_state": claim.target_state,
                    "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write(path, updated)
            if run is not None:
                self._write_run_terminal_projection(
                    run,
                    group_id=claim.group_id,
                    resource_id=claim.resource_id,
                    target_state=claim.target_state,
                    authority_record=updated,
                )
            return dict(updated)

    def prepare_terminal_reconciliation(
        self,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_authority_id: str,
        expected_execution_id: str,
    ) -> RunTerminalReconciliationClaim:
        group = self._required(expected_group_id, "expected_group_id")
        actor = self._required(expected_actor_id, "expected_actor_id")
        resource = self._required(expected_resource_id, "expected_resource_id")
        authority_id = self._required(expected_authority_id, "expected_authority_id")
        execution_id = self._required(expected_execution_id, "expected_execution_id")
        with self._locked(group):
            record = self._read(self._path(group, resource))
            self._validate_reduce_only_record_shape(record)
            target = str(record.get("execution_state") or "")
            if (
                str(record.get("issuer_epoch") or "") == self._issuer()
                or str(record.get("group_id") or "") != group
                or str(record.get("actor_id") or "") != actor
                or str(record.get("resource_id") or "") != resource
                or str(record.get("authority_id") or "") != authority_id
                or str(record.get("execution_id") or "") != execution_id
                or str(record.get("control_state") or "") != "revoked"
                or target not in _TERMINAL_EXECUTION_STATES
            ):
                raise PermissionError("run terminal reconciliation identity is invalid")
            self._read_recovery_run_record(
                group,
                resource,
                record,
                allowed_statuses=frozenset({*_ACTIVE_RECOVERY_RUN_STATES, target}),
            )
            self._assert_no_same_run_lease(group, actor, resource)
            return RunTerminalReconciliationClaim(
                _seal=_TERMINAL_RECONCILIATION_SEAL,
                _authority_home=self.authority_home,
                record=record,
                _validator=self.require_terminal_reconciliation_claim,
            )

    def require_terminal_reconciliation_claim(
        self,
        claim: Any,
    ) -> RunTerminalReconciliationClaim:
        if (
            not isinstance(claim, RunTerminalReconciliationClaim)
            or claim._seal is not _TERMINAL_RECONCILIATION_SEAL
        ):
            raise PermissionError("validated run terminal reconciliation claim is required")
        self._require_claim_home(claim)
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_reduce_only_record_shape(record)
            if (
                str(record.get("issuer_epoch") or "") == self._issuer()
                or str(record.get("control_state") or "") != "revoked"
                or claim.control_state != "revoked"
                or int(record.get("control_revision") or 0) != claim.control_revision
                or str(record.get("execution_state") or "") != claim.state
                or claim.state not in _TERMINAL_EXECUTION_STATES
                or int(record.get("execution_state_revision") or 0) != claim.revision
                or self._execution_identity(claim)
                != self._record_execution_identity(record)
            ):
                raise PermissionError("run terminal reconciliation claim is stale")
            self._read_recovery_run_record(
                claim.group_id,
                claim.resource_id,
                record,
                allowed_statuses=frozenset({*_ACTIVE_RECOVERY_RUN_STATES, claim.state}),
            )
            self._assert_no_same_run_lease(
                claim.group_id,
                claim.actor_id,
                claim.resource_id,
            )
        return claim

    def finish_terminal_reconciliation(
        self,
        claim: RunTerminalReconciliationClaim,
    ) -> Dict[str, Any]:
        if (
            not isinstance(claim, RunTerminalReconciliationClaim)
            or claim._seal is not _TERMINAL_RECONCILIATION_SEAL
        ):
            raise PermissionError("validated run terminal reconciliation claim is required")
        self._require_claim_home(claim)
        self.require_terminal_reconciliation_claim(claim)
        self._assert_no_same_run_lease(
            claim.group_id,
            claim.actor_id,
            claim.resource_id,
        )
        path = self._path(claim.group_id, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            self._validate_reduce_only_record_shape(record)
            if (
                str(record.get("issuer_epoch") or "") == self._issuer()
                or str(record.get("control_state") or "") != "revoked"
                or claim.control_state != "revoked"
                or int(record.get("control_revision") or 0) != claim.control_revision
                or str(record.get("execution_state") or "") != claim.state
                or int(record.get("execution_state_revision") or 0) != claim.revision
                or self._execution_identity(claim)
                != self._record_execution_identity(record)
            ):
                raise PermissionError("run terminal reconciliation claim is stale")
            run = self._read_recovery_run_record(
                claim.group_id,
                claim.resource_id,
                record,
                allowed_statuses=frozenset({*_ACTIVE_RECOVERY_RUN_STATES, claim.state}),
            )
            updated = _sign_record_integrity(
                {
                    **record,
                    "issuer_epoch": self._issuer(),
                    "updated_at_epoch": float(self._now_provider()),
                }
            )
            self._write_run_terminal_projection(
                run,
                group_id=claim.group_id,
                resource_id=claim.resource_id,
                target_state=claim.state,
                authority_record=updated,
            )
            self._write(path, updated)
            return dict(updated)

    def validate_terminal_record(self, group_id: str, resource_id: str) -> Dict[str, Any]:
        group = self._required(group_id, "group_id")
        resource = self._required(resource_id, "resource_id")
        with self._locked(group):
            record = self._read(self._path(group, resource))
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("control_state") or "") != "revoked"
                or str(record.get("execution_state") or "")
                not in {"completed", "failed", "cancelled", "interrupted", "start_failed"}
            ):
                raise PermissionError("run authority terminal record is invalid")
            return dict(record)

    def restart_candidates(self) -> list[Dict[str, Any]]:
        groups_root = (self.home / "groups").resolve()
        if not groups_root.exists():
            return []
        current_issuer = self._issuer()
        candidates: list[Dict[str, Any]] = []
        for group_path in groups_root.iterdir():
            if not group_path.is_dir():
                continue
            group_id = group_path.name
            try:
                root = self._root(group_id)
            except PermissionError:
                continue
            if not root.exists():
                continue
            for path in root.glob("*.json"):
                record = self._read(path)
                try:
                    self._validate_reduce_only_record_shape(record)
                except PermissionError:
                    continue
                if (
                    str(record.get("issuer_epoch") or "") == current_issuer
                    or str(record.get("group_id") or "") != group_id
                ):
                    continue
                candidates.append(
                    {
                        "group_id": group_id,
                        "actor_id": str(record["actor_id"]),
                        "resource_id": str(record["resource_id"]),
                        "authority_id": str(record["authority_id"]),
                        "execution_id": str(record["execution_id"]),
                        "control_state": str(record["control_state"]),
                        "execution_state": str(record["execution_state"]),
                    }
                )
        return candidates

    def revoke_pending(self, claim: RunOperationClaim) -> RunOperationClaim:
        if (
            not isinstance(claim, RunOperationClaim)
            or claim._seal is not _OPERATION_SEAL
        ):
            raise PermissionError("pending run operation claim is required")
        self._require_claim_home(claim)
        if claim.control_state != "pending":
            raise PermissionError("pending run operation claim is required")
        path = self._path(claim.group_id, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                self._operation_identity(claim) != self._record_operation_identity(record)
                or str(record.get("issuer_epoch") or "") != self._issuer()
                or str(record.get("control_state") or "") != "pending"
                or int(record.get("control_revision") or 0) != claim.control_revision
                or str(record.get("execution_state") or "") != "prepared"
                or int(record.get("execution_state_revision") or 0) != 1
            ):
                raise PermissionError("run operation claim is stale")
            updated = _sign_record_integrity({
                **record,
                "control_state": "revoked",
                "control_revision": int(record.get("control_revision") or 0) + 1,
                "execution_state": "start_failed",
                "execution_state_revision": int(record.get("execution_state_revision") or 0) + 1,
                "updated_at_epoch": float(self._now_provider()),
            })
            self._write(path, updated)
            return self._operation_claim(updated)

    def persisted_record(self, group_id: str, resource_id: str) -> Dict[str, Any]:
        group = self._required(group_id, "group_id")
        resource = self._required(resource_id, "resource_id")
        with self._locked(group):
            return dict(self._read(self._path(group, resource)))
