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
from typing import Any, Callable, Dict, FrozenSet, Iterator, Mapping, Optional

from ..util.file_lock import acquire_lockfile, release_lockfile
from ..util.fs import atomic_write_text
from .authorization import RecordingStartClaim
from .models import computer_control_permissions


_CLAIM_SEAL = object()
_STOP_OWNER_SEAL = object()
_AUTHORITY_STATES = frozenset(
    {"pending", "active", "suspended", "terminating", "revoked"}
)


@dataclass(frozen=True, init=False)
class DerivedAuthorityClaim:
    """A process-local, validated view of one persisted resource authority."""

    issuer_epoch: str
    kind: str
    authority_id: str
    group_id: str
    actor_id: str
    resource_id: str
    request_id: str
    root_authority_id: str
    root_attempt_id: str
    root_generation: int
    generation: int
    revision: int
    state: str
    scope_digest: str
    permission_snapshot: Mapping[str, bool]
    _seal: object

    def __init__(self, *, _seal: object, record: Dict[str, Any]):
        if _seal is not _CLAIM_SEAL:
            raise TypeError("derived authority claims can only be created by validation")
        object.__setattr__(self, "issuer_epoch", str(record.get("issuer_epoch") or ""))
        object.__setattr__(self, "kind", str(record.get("kind") or ""))
        object.__setattr__(self, "authority_id", str(record.get("authority_id") or ""))
        object.__setattr__(self, "group_id", str(record.get("group_id") or ""))
        object.__setattr__(self, "actor_id", str(record.get("actor_id") or ""))
        object.__setattr__(self, "resource_id", str(record.get("resource_id") or ""))
        object.__setattr__(self, "request_id", str(record.get("request_id") or ""))
        object.__setattr__(self, "root_authority_id", str(record.get("root_authority_id") or ""))
        object.__setattr__(self, "root_attempt_id", str(record.get("root_attempt_id") or ""))
        object.__setattr__(self, "root_generation", int(record.get("root_generation") or 0))
        object.__setattr__(self, "generation", int(record.get("generation") or 0))
        object.__setattr__(self, "revision", int(record.get("revision") or 0))
        object.__setattr__(self, "state", str(record.get("state") or ""))
        object.__setattr__(self, "scope_digest", str(record.get("scope_digest") or ""))
        object.__setattr__(
            self,
            "permission_snapshot",
            MappingProxyType(computer_control_permissions(record.get("permission_snapshot") or {})),
        )
        object.__setattr__(self, "_seal", _seal)

    def lease_identity(self) -> Dict[str, Any]:
        return {
            "issuer_epoch": self.issuer_epoch,
            "kind": self.kind,
            "authority_id": self.authority_id,
            "group_id": self.group_id,
            "actor_id": self.actor_id,
            "resource_id": self.resource_id,
            "generation": self.generation,
            "revision": self.revision,
            "state": self.state,
        }


@dataclass(frozen=True, init=False)
class RecordingStopOwnerClaim:
    """A read-only owner proof that cannot authorize recording operations."""

    kind: str
    authority_id: str
    group_id: str
    actor_id: str
    resource_id: str
    _seal: object

    def __init__(self, *, _seal: object, record: Dict[str, Any]):
        if _seal is not _STOP_OWNER_SEAL:
            raise TypeError("recording stop owner claims can only be created by validation")
        object.__setattr__(self, "kind", str(record.get("kind") or ""))
        object.__setattr__(self, "authority_id", str(record.get("authority_id") or ""))
        object.__setattr__(self, "group_id", str(record.get("group_id") or ""))
        object.__setattr__(self, "actor_id", str(record.get("actor_id") or ""))
        object.__setattr__(self, "resource_id", str(record.get("resource_id") or ""))
        object.__setattr__(self, "_seal", _seal)


@dataclass(frozen=True)
class DerivedAuthorityIssue:
    claim: DerivedAuthorityClaim
    receipt: Dict[str, Any]


@dataclass(frozen=True)
class DerivedAuthorityTransition:
    previous: DerivedAuthorityClaim
    current: DerivedAuthorityClaim


class DerivedAuthorityStore:
    VERSION = 1
    RECOVERY_TTL_SECONDS = 3600.0

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
        self._process_lock = threading.RLock()

    def _root(self, group_id: str) -> Path:
        groups_root = (self.home / "groups").resolve()
        root = (
            groups_root
            / group_id
            / "state"
            / "computer-control"
            / "derived-authorities"
        ).resolve()
        try:
            root.relative_to(groups_root)
        except ValueError as exc:
            raise PermissionError("derived authority group path is invalid") from exc
        return root

    def _path(self, group_id: str, kind: str, resource_id: str) -> Path:
        identity = f"{group_id}\0{kind}\0{resource_id}".encode("utf-8")
        return self._root(group_id) / f"{hashlib.sha256(identity).hexdigest()}.json"

    @contextmanager
    def _locked(self, group_id: str) -> Iterator[None]:
        root = self._root(group_id)
        root.mkdir(parents=True, exist_ok=True)
        with self._process_lock:
            handle = acquire_lockfile(root / "authority.lock", blocking=True)
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

    @staticmethod
    def _write(path: Path, value: Dict[str, Any]) -> None:
        atomic_write_text(
            path,
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )

    @staticmethod
    def _scope_digest(value: Dict[str, Any]) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _required_identity(value: Any, field: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise PermissionError(f"derived authority {field} is required")
        return normalized

    def _current_issuer(self) -> str:
        return self._required_identity(self._issuer_epoch_provider(), "issuer_epoch")

    def _current_generation(self, group_id: str, actor_id: str) -> int:
        try:
            generation = int(self._generation_provider(group_id, actor_id))
        except Exception as exc:
            raise PermissionError("derived authority actor generation is unavailable") from exc
        if generation <= 0:
            raise PermissionError("derived authority actor generation is unavailable")
        return generation

    @staticmethod
    def _claim(record: Dict[str, Any]) -> DerivedAuthorityClaim:
        return DerivedAuthorityClaim(_seal=_CLAIM_SEAL, record=record)

    @staticmethod
    def _record_identity(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: record.get(key)
            for key in (
                "issuer_epoch",
                "kind",
                "authority_id",
                "group_id",
                "actor_id",
                "resource_id",
                "request_id",
                "generation",
                "scope_digest",
                "root_authority_id",
                "root_attempt_id",
                "root_generation",
            )
        }

    def begin_recording(
        self,
        *,
        group_id: str,
        actor_id: str,
        resource_id: str,
        request_id: str,
        start_claim: RecordingStartClaim,
    ) -> DerivedAuthorityIssue:
        group = self._required_identity(group_id, "group_id")
        actor = self._required_identity(actor_id, "actor_id")
        resource = self._required_identity(resource_id, "resource_id")
        request = self._required_identity(request_id, "request_id")
        if not isinstance(start_claim, RecordingStartClaim):
            raise PermissionError("validated recording start claim is required")
        def derive(current_claim: RecordingStartClaim) -> DerivedAuthorityIssue:
            issuer = self._current_issuer()
            generation = self._current_generation(group, actor)
            root_authority_id = self._required_identity(
                current_claim.root_authority_id,
                "root_authority_id",
            )
            root_attempt_id = self._required_identity(
                current_claim.root_attempt_id,
                "root_attempt_id",
            )
            root_generation = int(current_claim.generation)
            if (
                current_claim.issuer_epoch != issuer
                or current_claim.group_id != group
                or current_claim.actor_id != actor
                or current_claim.request_id != request
                or root_generation != generation
            ):
                raise PermissionError(
                    "derived authority root claim does not match the current actor generation"
                )

            permissions = computer_control_permissions(dict(current_claim.permission_snapshot))
            scope = {
                "kind": "recording",
                "group_id": group,
                "actor_id": actor,
                "resource_id": resource,
                "request_id": request,
                "permission_snapshot": permissions,
                "root_authority_id": root_authority_id,
                "root_attempt_id": root_attempt_id,
                "root_generation": root_generation,
            }
            scope_digest = self._scope_digest(scope)
            path = self._path(group, "recording", resource)
            with self._locked(group):
                current = self._read(path)
                if current:
                    raise PermissionError("derived authority already exists for this resource")
                derivation_identity = {
                    "kind": "recording",
                    "root_authority_id": root_authority_id,
                    "root_attempt_id": root_attempt_id,
                    "root_generation": root_generation,
                    "request_id": request,
                }
                for candidate_path in self._root(group).glob("*.json"):
                    candidate = self._read(candidate_path)
                    if all(candidate.get(key) == value for key, value in derivation_identity.items()):
                        raise PermissionError("derived authority already exists for this root claim")
                current_claim.require_fresh_current()
                secret = secrets.token_urlsafe(32)
                now = float(self._now_provider())
                record = {
                    "v": self.VERSION,
                    "issuer_epoch": issuer,
                    "kind": "recording",
                    "authority_id": f"recordingauth_{uuid.uuid4().hex}",
                    "secret_digest": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                    "root_authority_id": root_authority_id,
                    "root_attempt_id": root_attempt_id,
                    "root_generation": root_generation,
                    "group_id": group,
                    "actor_id": actor,
                    "resource_id": resource,
                    "request_id": request,
                    "permission_snapshot": permissions,
                    "scope_digest": scope_digest,
                    "generation": generation,
                    "revision": 1,
                    "state": "pending",
                    "issued_at_epoch": now,
                    "updated_at_epoch": now,
                    "recovery_expires_at_epoch": now + self.RECOVERY_TTL_SECONDS,
                }
                self._write(path, record)
            receipt = {
                "v": self.VERSION,
                **self._record_identity(record),
                "authorization_secret": secret,
            }
            return DerivedAuthorityIssue(claim=self._claim(record), receipt=receipt)

        issue = start_claim.consume_current(derive)
        if not isinstance(issue, DerivedAuthorityIssue):
            raise PermissionError("derived authority root claim is no longer current")
        return issue

    def _validate_record(self, record: Dict[str, Any]) -> None:
        if (
            int(record.get("v") or 0) != self.VERSION
            or str(record.get("issuer_epoch") or "") != self._current_issuer()
            or str(record.get("kind") or "") != "recording"
            or str(record.get("state") or "") not in _AUTHORITY_STATES
            or int(record.get("generation") or 0)
            != self._current_generation(str(record.get("group_id") or ""), str(record.get("actor_id") or ""))
        ):
            raise PermissionError("derived authority is not valid for the current daemon and actor generation")
        permissions = computer_control_permissions(record.get("permission_snapshot") or {})
        scope = {
            "kind": record.get("kind"),
            "group_id": record.get("group_id"),
            "actor_id": record.get("actor_id"),
            "resource_id": record.get("resource_id"),
            "request_id": record.get("request_id"),
            "permission_snapshot": permissions,
            "root_authority_id": record.get("root_authority_id"),
            "root_attempt_id": record.get("root_attempt_id"),
            "root_generation": record.get("root_generation"),
        }
        if not hmac.compare_digest(
            str(record.get("scope_digest") or ""),
            self._scope_digest(scope),
        ):
            raise PermissionError("derived authority scope is invalid")

    def _validate_receipt(
        self,
        receipt: Any,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str,
        allowed_states: FrozenSet[str],
    ) -> DerivedAuthorityClaim:
        if not isinstance(receipt, dict):
            raise PermissionError("derived authority receipt is required")
        group = self._required_identity(expected_group_id, "expected_group_id")
        actor = self._required_identity(expected_actor_id, "expected_actor_id")
        resource = self._required_identity(expected_resource_id, "expected_resource_id")
        kind = self._required_identity(expected_kind, "expected_kind")
        if kind != "recording" or any(
            str(receipt.get(field) or "").strip() != expected
            for field, expected in (
                ("group_id", group),
                ("actor_id", actor),
                ("resource_id", resource),
                ("kind", kind),
            )
        ):
            raise PermissionError("derived authority receipt does not match the expected resource")
        with self._locked(group):
            record = self._read(self._path(group, kind, resource))
            self._validate_record(record)
            secret = str(receipt.get("authorization_secret") or "")
            presented_digest = hashlib.sha256(secret.encode("utf-8")).hexdigest() if secret else ""
            if (
                self._record_identity(receipt) != self._record_identity(record)
                or not presented_digest
                or not hmac.compare_digest(presented_digest, str(record.get("secret_digest") or ""))
                or str(record.get("state") or "") not in allowed_states
                or float(record.get("recovery_expires_at_epoch") or 0) <= float(self._now_provider())
            ):
                raise PermissionError("derived authority receipt is invalid")
            return self._claim(record)

    def validate_active_receipt(
        self,
        receipt: Any,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str,
    ) -> DerivedAuthorityClaim:
        return self._validate_receipt(
            receipt,
            expected_group_id=expected_group_id,
            expected_actor_id=expected_actor_id,
            expected_resource_id=expected_resource_id,
            expected_kind=expected_kind,
            allowed_states=frozenset({"active"}),
        )

    def validate_suspended_receipt(
        self,
        receipt: Any,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str,
    ) -> DerivedAuthorityClaim:
        return self._validate_receipt(
            receipt,
            expected_group_id=expected_group_id,
            expected_actor_id=expected_actor_id,
            expected_resource_id=expected_resource_id,
            expected_kind=expected_kind,
            allowed_states=frozenset({"suspended"}),
        )

    def validate_read_receipt(
        self,
        receipt: Any,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str,
    ) -> DerivedAuthorityClaim:
        return self._validate_receipt(
            receipt,
            expected_group_id=expected_group_id,
            expected_actor_id=expected_actor_id,
            expected_resource_id=expected_resource_id,
            expected_kind=expected_kind,
            allowed_states=frozenset({"active", "suspended"}),
        )

    def validate_recording_stop_owner(
        self,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
        expected_kind: str,
    ) -> RecordingStopOwnerClaim:
        group = self._required_identity(expected_group_id, "expected_group_id")
        actor = self._required_identity(expected_actor_id, "expected_actor_id")
        resource = self._required_identity(expected_resource_id, "expected_resource_id")
        kind = self._required_identity(expected_kind, "expected_kind")
        if kind != "recording":
            raise PermissionError("recording stop owner does not match the expected resource")
        record = self._read(self._path(group, kind, resource))
        if any(
            str(record.get(field) or "") != expected
            for field, expected in (
                ("kind", kind),
                ("group_id", group),
                ("actor_id", actor),
                ("resource_id", resource),
            )
        ) or not str(record.get("authority_id") or ""):
            raise PermissionError("recording stop owner does not match the expected resource")
        return RecordingStopOwnerClaim(_seal=_STOP_OWNER_SEAL, record=record)

    @staticmethod
    def require_recording_stop_owner_claim(
        claim: Any,
        *,
        expected_group_id: str,
        expected_actor_id: str,
        expected_resource_id: str,
    ) -> RecordingStopOwnerClaim:
        if (
            not isinstance(claim, RecordingStopOwnerClaim)
            or claim._seal is not _STOP_OWNER_SEAL
            or claim.kind != "recording"
            or claim.group_id != expected_group_id
            or claim.actor_id != expected_actor_id
            or claim.resource_id != expected_resource_id
        ):
            raise PermissionError("validated recording stop owner claim is required")
        return claim

    def _validate_claim(
        self,
        claim: Any,
        *,
        expected_state: str,
    ) -> DerivedAuthorityClaim:
        if not isinstance(claim, DerivedAuthorityClaim) or claim._seal is not _CLAIM_SEAL:
            raise PermissionError("validated derived authority claim is required")
        with self._locked(claim.group_id):
            record = self._read(self._path(claim.group_id, claim.kind, claim.resource_id))
            self._validate_record(record)
            if (
                self._record_identity(record) != self._record_identity(claim.__dict__)
                or int(record.get("revision") or 0) != claim.revision
                or str(record.get("state") or "") != expected_state
                or claim.state != expected_state
            ):
                raise PermissionError("derived authority claim is stale")
            return claim

    def validate_active_claim(self, claim: Any) -> DerivedAuthorityClaim:
        return self._validate_claim(claim, expected_state="active")

    def validate_suspended_claim(self, claim: Any) -> DerivedAuthorityClaim:
        return self._validate_claim(claim, expected_state="suspended")

    def _transition(
        self,
        claim: DerivedAuthorityClaim,
        *,
        from_states: FrozenSet[str],
        to_state: str,
    ) -> DerivedAuthorityClaim:
        if not isinstance(claim, DerivedAuthorityClaim) or claim._seal is not _CLAIM_SEAL:
            raise PermissionError("validated derived authority claim is required")
        path = self._path(claim.group_id, claim.kind, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            self._validate_record(record)
            if (
                self._record_identity(record) != self._record_identity(claim.__dict__)
                or int(record.get("revision") or 0) != claim.revision
                or str(record.get("state") or "") != claim.state
                or claim.state not in from_states
            ):
                raise PermissionError("derived authority claim is stale")
            updated = {
                **record,
                "state": to_state,
                "revision": claim.revision + 1,
                "updated_at_epoch": float(self._now_provider()),
            }
            self._write(path, updated)
            return self._claim(updated)

    def activate(self, claim: DerivedAuthorityClaim) -> DerivedAuthorityClaim:
        return self._transition(claim, from_states=frozenset({"pending"}), to_state="active")

    def suspend(self, claim: DerivedAuthorityClaim) -> DerivedAuthorityClaim:
        return self._transition(claim, from_states=frozenset({"active"}), to_state="suspended")

    def resume(self, claim: DerivedAuthorityClaim) -> DerivedAuthorityClaim:
        return self._transition(claim, from_states=frozenset({"suspended"}), to_state="active")

    def begin_termination(self, claim: DerivedAuthorityClaim) -> DerivedAuthorityClaim:
        return self._transition(
            claim,
            from_states=frozenset({"active", "suspended"}),
            to_state="terminating",
        )

    def revoke(self, claim: DerivedAuthorityClaim) -> DerivedAuthorityClaim:
        return self._transition(
            claim,
            from_states=frozenset({"pending", "active", "suspended", "terminating"}),
            to_state="revoked",
        )

    def begin_stop(
        self,
        *,
        group_id: str,
        actor_id: str,
        resource_id: str,
    ) -> DerivedAuthorityTransition:
        """Revoke new operations without requiring the bearer secret."""

        return self._begin_owner_stop(
            group_id=group_id,
            actor_id=actor_id,
            resource_id=resource_id,
            allowed_states=frozenset({"active", "suspended", "terminating", "revoked"}),
            error_message="recording authority cannot be stopped by this actor",
        )

    def begin_failed_start(
        self,
        *,
        group_id: str,
        actor_id: str,
        resource_id: str,
    ) -> DerivedAuthorityTransition:
        """Converge an unpublished recording start without creating a stop capability."""

        return self._begin_owner_stop(
            group_id=group_id,
            actor_id=actor_id,
            resource_id=resource_id,
            allowed_states=frozenset(
                {"pending", "active", "suspended", "terminating", "revoked"}
            ),
            error_message="recording authority cannot recover this failed start",
        )

    def _begin_owner_stop(
        self,
        *,
        group_id: str,
        actor_id: str,
        resource_id: str,
        allowed_states: FrozenSet[str],
        error_message: str,
    ) -> DerivedAuthorityTransition:

        group = self._required_identity(group_id, "group_id")
        actor = self._required_identity(actor_id, "actor_id")
        resource = self._required_identity(resource_id, "resource_id")
        path = self._path(group, "recording", resource)
        with self._locked(group):
            record = self._read(path)
            if (
                str(record.get("kind") or "") != "recording"
                or str(record.get("group_id") or "") != group
                or str(record.get("actor_id") or "") != actor
                or str(record.get("resource_id") or "") != resource
                or str(record.get("state") or "") not in allowed_states
            ):
                raise PermissionError(error_message)
            previous = self._claim(record)
            if str(record.get("state") or "") in {"terminating", "revoked"}:
                return DerivedAuthorityTransition(previous=previous, current=previous)
            updated = {
                **record,
                "state": "terminating",
                "revision": int(record.get("revision") or 0) + 1,
                "updated_at_epoch": float(self._now_provider()),
            }
            self._write(path, updated)
            return DerivedAuthorityTransition(previous=previous, current=self._claim(updated))

    def finish_stop(self, claim: DerivedAuthorityClaim) -> DerivedAuthorityClaim:
        if (
            not isinstance(claim, DerivedAuthorityClaim)
            or claim._seal is not _CLAIM_SEAL
            or claim.kind != "recording"
            or claim.state != "terminating"
        ):
            raise PermissionError("terminating recording authority claim is required")
        path = self._path(claim.group_id, claim.kind, claim.resource_id)
        with self._locked(claim.group_id):
            record = self._read(path)
            if (
                self._record_identity(record) != self._record_identity(claim.__dict__)
                or int(record.get("revision") or 0) != claim.revision
                or str(record.get("state") or "") != "terminating"
            ):
                raise PermissionError("derived authority claim is stale")
            updated = {
                **record,
                "state": "revoked",
                "revision": claim.revision + 1,
                "updated_at_epoch": float(self._now_provider()),
            }
            self._write(path, updated)
            return self._claim(updated)

    def suspend_after_restart(self, *, group_id: str, resource_id: str) -> Optional[DerivedAuthorityTransition]:
        group = self._required_identity(group_id, "group_id")
        resource = self._required_identity(resource_id, "resource_id")
        path = self._path(group, "recording", resource)
        with self._locked(group):
            record = self._read(path)
            if str(record.get("state") or "") != "active":
                return None
            previous = self._claim(record)
            updated = {
                **record,
                "state": "suspended",
                "revision": int(record.get("revision") or 0) + 1,
                "updated_at_epoch": float(self._now_provider()),
            }
            self._write(path, updated)
            return DerivedAuthorityTransition(previous=previous, current=self._claim(updated))

    def revoke_orphan_pending_recordings(
        self,
        *,
        group_id: str,
        existing_resource_ids: FrozenSet[str],
    ) -> int:
        group = self._required_identity(group_id, "group_id")
        root = self._root(group)
        if not root.exists():
            return 0
        revoked = 0
        with self._locked(group):
            for path in root.glob("*.json"):
                record = self._read(path)
                resource_id = str(record.get("resource_id") or "")
                if (
                    str(record.get("kind") or "") != "recording"
                    or str(record.get("group_id") or "") != group
                    or str(record.get("state") or "") != "pending"
                    or not resource_id
                    or resource_id in existing_resource_ids
                ):
                    continue
                updated = {
                    **record,
                    "state": "revoked",
                    "revision": int(record.get("revision") or 0) + 1,
                    "updated_at_epoch": float(self._now_provider()),
                }
                self._write(path, updated)
                revoked += 1
        return revoked

    def persisted_record(self, group_id: str, resource_id: str) -> Dict[str, Any]:
        record = self.persisted_record_snapshot(group_id, resource_id)
        return dict(record or {})

    def persisted_record_snapshot(
        self,
        group_id: str,
        resource_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Return None only when the authority file does not exist."""

        group = self._required_identity(group_id, "group_id")
        resource = self._required_identity(resource_id, "resource_id")
        with self._locked(group):
            path = self._path(group, "recording", resource)
            if not path.is_file():
                return None
            return dict(self._read(path))
