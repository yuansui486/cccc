from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
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
_EXECUTION_SEED_SEAL = object()
_EXECUTION_SEAL = object()
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: Dict[str, threading.RLock] = {}
_RECORD_INTEGRITIES_GUARD = threading.Lock()
_RECORD_INTEGRITIES: Dict[str, str] = {}
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
_EXECUTION_SCOPE_FIELDS = (
    "issuer_epoch",
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


@dataclass(frozen=True, init=False)
class RunOperationClaim:
    issuer_epoch: str
    authority_id: str
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

    def __init__(self, *, _seal: object, record: Dict[str, Any]):
        if _seal is not _OPERATION_SEAL:
            raise TypeError("run operation claims can only be created by validation")
        for field in (
            "issuer_epoch",
            "authority_id",
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


@dataclass(frozen=True, init=False)
class RunExecutionSeedClaim:
    issuer_epoch: str
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
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _EXECUTION_SEED_SEAL:
            raise TypeError("run execution seed claims can only be created by derivation")
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
        record: Dict[str, Any],
        _validator: Callable[[Any], Any],
    ):
        if _seal is not _EXECUTION_SEAL:
            raise TypeError("run execution claims can only be created by acceptance")
        _set_execution_fields(self, record, state="accepted", revision=2)
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


def _execution_lease_identity(value: Any) -> Dict[str, Any]:
    return {
        "issuer_epoch": value.issuer_epoch,
        "kind": "run",
        "authority_id": value.authority_id,
        "execution_id": value.execution_id,
        "group_id": value.group_id,
        "actor_id": value.actor_id,
        "resource_id": value.resource_id,
        "generation": 1,
        "revision": value.revision,
        "state": value.state,
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
        self.home = home
        self._issuer_epoch_provider = issuer_epoch_provider
        self._generation_provider = generation_provider
        self._now_provider = now_provider

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
            _RECORD_INTEGRITIES[authority_id] = integrity

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
        try:
            required = (
                "issuer_epoch",
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
            valid = bool(
                int(record.get("v") or 0) == self.VERSION
                and str(record.get("kind") or "") == "run"
                and all(str(record.get(field) or "").strip() for field in required)
                and int(record.get("root_generation") or 0) > 0
                and int(record.get("version") or 0) > 0
                and float(record.get("operation_expires_at_epoch") or 0)
                > float(record.get("created_at_epoch") or 0)
                and str(record.get("control_state") or "") in _CONTROL_STATES
                and str(record.get("execution_state") or "") in _EXECUTION_STATES
                and state_revision
                in {
                    ("pending", 1, "prepared", 1),
                    ("active", 2, "accepted", 2),
                    ("revoked", 2, "start_failed", 2),
                }
                and isinstance(record.get("permission_snapshot"), dict)
                and record.get("permission_snapshot") == permissions
                and all(len(value) == 64 and value == value.lower() for value in digests)
                and hmac.compare_digest(
                    str(record.get("scope_digest") or ""),
                    self._scope_digest(self._scope(record)),
                )
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise PermissionError("run authority record integrity check failed")

    def _validate_record(self, record: Dict[str, Any]) -> None:
        self._validate_record_for_write(record)
        authority_id = str(record.get("authority_id") or "")
        integrity = str(record.get("integrity_digest") or "")
        with _RECORD_INTEGRITIES_GUARD:
            current_integrity = _RECORD_INTEGRITIES.get(authority_id, "")
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
    def _operation_identity(value: RunOperationClaim) -> Dict[str, Any]:
        result = {
            field: getattr(value, field)
            for field in (
                "issuer_epoch",
                "authority_id",
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
                "authority_id",
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

    @staticmethod
    def _operation_claim(record: Dict[str, Any]) -> RunOperationClaim:
        return RunOperationClaim(_seal=_OPERATION_SEAL, record=record)

    def _execution_seed(self, record: Dict[str, Any]) -> RunExecutionSeedClaim:
        return RunExecutionSeedClaim(
            _seal=_EXECUTION_SEED_SEAL,
            record=record,
            _validator=self.require_execution_seed,
        )

    def _execution_claim(self, record: Dict[str, Any]) -> RunExecutionClaim:
        return RunExecutionClaim(
            _seal=_EXECUTION_SEAL,
            record=record,
            _validator=self.require_execution_claim,
        )

    @staticmethod
    def _receipt_identity(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "v",
                "issuer_epoch",
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
        path = self._path(seed.group_id, seed.resource_id)
        with self._locked(seed.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or self._execution_identity(seed) != self._record_execution_identity(record)
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

    def require_execution_seed(self, claim: Any) -> RunExecutionSeedClaim:
        if not isinstance(claim, RunExecutionSeedClaim) or claim._seal is not _EXECUTION_SEED_SEAL:
            raise PermissionError("validated run execution seed claim is required")
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or self._execution_identity(claim) != self._record_execution_identity(record)
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
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.resource_id))
            self._validate_record(record)
            if (
                str(record.get("issuer_epoch") or "") != self._issuer()
                or self._execution_identity(claim) != self._record_execution_identity(record)
                or str(record.get("execution_state") or "") not in _EXECUTION_CONSUMABLE_STATES
            ):
                raise PermissionError("run execution claim is stale")
        return claim

    def revoke_pending(self, claim: RunOperationClaim) -> RunOperationClaim:
        if (
            not isinstance(claim, RunOperationClaim)
            or claim._seal is not _OPERATION_SEAL
            or claim.control_state != "pending"
        ):
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
