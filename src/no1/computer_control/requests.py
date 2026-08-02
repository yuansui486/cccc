from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from ..util.file_lock import acquire_lockfile, release_lockfile
from .models import computer_control_permissions
from .authorization import (
    RecordingStartClaim,
    RunStartClaim,
    _recording_start_authorization,
    _run_start_authorization,
    load_initial_request_record,
    request_turn_authorization,
    validate_request_event_binding,
    workflow_definition_digest,
)
from .storage import WorkflowNotFound, WorkflowStore


_REQUEST_PROCESS_LOCKS_GUARD = threading.Lock()
_REQUEST_PROCESS_LOCKS: Dict[str, Any] = {}


def _request_process_lock(path: Any) -> Any:
    key = str(path.resolve())
    with _REQUEST_PROCESS_LOCKS_GUARD:
        lock = _REQUEST_PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REQUEST_PROCESS_LOCKS[key] = lock
        return lock


class ComputerRequestStore:
    AUTHORIZATION_TTL_SECONDS = 3600
    RESERVED_AUTHORITY_FIELDS = frozenset(
        {
            "turn_authorization",
            "activated_at",
            "derived_authority",
            "derived_authority_id",
            "recording_authority",
            "run_authority",
            "recording_id",
            "created_workflow_id",
            "run_id",
        }
    )
    IMMUTABLE_ACTIVATED_FIELDS = frozenset(
        {"actor_id", "mode", "workflow_id", "event_id", "local_request_id"}
    )
    LIFECYCLE_FIELDS = frozenset(
        {"status", "completed_at", "failure_reason"}
    )

    def __init__(self, workflows: WorkflowStore):
        self.workflows = workflows

    def _path(self, group_id: str):
        return self.workflows.state_root(group_id) / "requests.jsonl"

    @contextmanager
    def _locked(self, group_id: str) -> Iterator[None]:
        lock_path = self._path(group_id).with_name("requests.lock")
        with _request_process_lock(lock_path):
            handle = acquire_lockfile(lock_path, blocking=True)
            try:
                yield
            finally:
                release_lockfile(handle)

    def _append_record_unlocked(self, group_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        path = self._path(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {**request, "group_id": group_id, "updated_ts": time.time()}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record

    @classmethod
    def _reject_public_authority_fields(cls, value: Dict[str, Any]) -> None:
        blocked = cls.RESERVED_AUTHORITY_FIELDS.intersection(value)
        if blocked:
            names = ", ".join(sorted(blocked))
            raise PermissionError(f"computer control authority fields are reserved: {names}")

    def _append_public_record(
        self,
        group_id: str,
        request: Dict[str, Any],
        *,
        require_existing: bool = False,
    ) -> Dict[str, Any]:
        self._reject_public_authority_fields(request)
        request_id = str(request.get("request_id") or "").strip()
        with self._locked(group_id):
            current = self._get_unlocked(group_id, request_id) if request_id else None
            if require_existing and current is None:
                raise KeyError(request_id)
            if isinstance(current, dict) and isinstance(current.get("turn_authorization"), dict):
                if computer_control_permissions(current) != computer_control_permissions({**current, **request}):
                    raise PermissionError("activated computer control request facts are immutable")
                for field in self.IMMUTABLE_ACTIVATED_FIELDS:
                    if field in request and str(request.get(field) or "") != str(current.get(field) or ""):
                        raise PermissionError("activated computer control request facts are immutable")
                if "inputs" in request:
                    from .authorization import normalized_run_inputs

                    if normalized_run_inputs(request.get("inputs")) != normalized_run_inputs(
                        current.get("inputs")
                    ):
                        raise PermissionError("activated computer control request facts are immutable")
            return self._append_record_unlocked(group_id, request)

    def append(self, group_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        return self._append_public_record(group_id, request)

    def _append_turn_activation(
        self,
        group_id: str,
        request_id: str,
        *,
        activation: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._locked(group_id):
            return self._append_turn_activation_unlocked(
                group_id,
                request_id,
                activation=activation,
            )

    def _append_turn_activation_unlocked(
        self,
        group_id: str,
        request_id: str,
        *,
        activation: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._append_record_unlocked(
            group_id,
            {
                "request_id": request_id,
                "turn_authorization": activation,
                "activated_at": time.time(),
            },
        )

    def _get_unlocked(self, group_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(group_id)
        if not path.exists():
            return None
        found: Optional[Dict[str, Any]] = None
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and str(value.get("request_id") or "") == request_id:
                found = {**(found or {}), **value}
        return found

    def get(self, group_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        with self._locked(group_id):
            return self._get_unlocked(group_id, request_id)

    def update(self, group_id: str, request_id: str, **patch: Any) -> Dict[str, Any]:
        return self._append_public_record(
            group_id,
            {"request_id": request_id, **patch},
            require_existing=True,
        )

    def update_lifecycle(self, group_id: str, request_id: str, **patch: Any) -> Dict[str, Any]:
        if set(patch) - self.LIFECYCLE_FIELDS:
            raise PermissionError("computer control request lifecycle patch is invalid")
        with self._locked(group_id):
            current = self._get_unlocked(group_id, request_id)
            if current is None:
                raise KeyError(request_id)
            return {
                **current,
                **self._append_record_unlocked(group_id, {"request_id": request_id, **patch}),
            }

    def _mark_lifecycle_resource(
        self,
        group_id: str,
        request_id: str,
        *,
        field: str,
        resource_id: str,
        status: str,
    ) -> Dict[str, Any]:
        resource = str(resource_id or "").strip()
        lifecycle_status = str(status or "").strip()
        if field not in {"recording_id", "created_workflow_id", "run_id"} or not resource or not lifecycle_status:
            raise PermissionError("computer control lifecycle resource is invalid")
        with self._locked(group_id):
            current = self._get_unlocked(group_id, request_id)
            if current is None:
                raise KeyError(request_id)
            existing = str(current.get(field) or "").strip()
            if existing:
                if existing != resource:
                    raise PermissionError("computer control lifecycle resource is immutable")
                return current
            return {
                **current,
                **self._append_record_unlocked(
                    group_id,
                    {"request_id": request_id, "status": lifecycle_status, field: resource},
                ),
            }

    def mark_recording_started(
        self,
        group_id: str,
        request_id: str,
        *,
        recording_id: str,
        status: str = "exploring",
    ) -> Dict[str, Any]:
        return self._mark_lifecycle_resource(
            group_id,
            request_id,
            field="recording_id",
            resource_id=recording_id,
            status=status,
        )

    def mark_workflow_created(
        self,
        group_id: str,
        request_id: str,
        *,
        created_workflow_id: str,
        status: str = "draft_created",
    ) -> Dict[str, Any]:
        return self._mark_lifecycle_resource(
            group_id,
            request_id,
            field="created_workflow_id",
            resource_id=created_workflow_id,
            status=status,
        )

    def mark_run_started(
        self,
        group_id: str,
        request_id: str,
        *,
        run_id: str,
        status: str = "running",
    ) -> Dict[str, Any]:
        return self._mark_lifecycle_resource(
            group_id,
            request_id,
            field="run_id",
            resource_id=run_id,
            status=status,
        )

    def _require_authorized_unlocked(
        self,
        group_id: str,
        request_id: str,
        actor_id: str,
    ) -> Dict[str, Any]:
        request = self._get_unlocked(group_id, request_id)
        if request is None:
            raise PermissionError("computer control request was not found")
        if str(request.get("actor_id") or "") != actor_id:
            raise PermissionError("computer control request belongs to another actor")
        status = str(request.get("status") or "")
        if status in {"rejected", "cancelled", "completed"}:
            raise PermissionError("computer control request is no longer active")
        # Once a recording has acquired the global lease, its authorization
        # follows that recording. The pre-start TTL prevents stale requests
        # from being used, but must not interrupt an intentionally long task.
        if not str(request.get("recording_id") or "").strip():
            created_ts = float(request.get("created_ts") or request.get("updated_ts") or 0)
            if created_ts <= 0 or time.time() - created_ts > self.AUTHORIZATION_TTL_SECONDS:
                raise PermissionError("computer control request authorization has expired")
        return {**request, **computer_control_permissions(request)}

    def require_authorized(self, group_id: str, request_id: str, actor_id: str) -> Dict[str, Any]:
        with self._locked(group_id):
            return self._require_authorized_unlocked(group_id, request_id, actor_id)

    def activate_for_turn_claim(
        self,
        group_id: str,
        request_id: str,
        actor_id: str,
        *,
        claim: Any,
        provenance: Any,
        ledger_event: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._locked(group_id):
            request = self._require_authorized_unlocked(group_id, request_id, actor_id)
            initial_request = load_initial_request_record(self._path(group_id), request_id)
            validate_request_event_binding(request, initial_request, ledger_event)
            activation = request_turn_authorization(request, claim, provenance)
            if request.get("turn_authorization") == activation:
                return request
            return {
                **request,
                **self._append_turn_activation_unlocked(
                    group_id,
                    request_id,
                    activation=activation,
                ),
            }

    def activate_recording_start_for_turn_claim(
        self,
        group_id: str,
        request_id: str,
        actor_id: str,
        *,
        claim: Any,
    ) -> RecordingStartClaim:
        from ..daemon.messaging.turn_provenance import (
            load_event_turn_provenance,
            require_validated_turn_grant_claim,
        )
        from ..kernel.group import load_group
        from ..kernel.ledger_index import lookup_event_by_id

        root_claim = require_validated_turn_grant_claim(claim)

        def activate(_current_claim: Any) -> RecordingStartClaim:
            with self._locked(group_id):
                request = self._require_authorized_unlocked(group_id, request_id, actor_id)
                initial_request = load_initial_request_record(self._path(group_id), request_id)
                event_id = str(request.get("event_id") or "").strip()
                group = load_group(group_id)
                if group is None or not event_id:
                    raise PermissionError("computer control request event was not found")
                ledger_event = lookup_event_by_id(group.ledger_path, event_id)
                provenance = load_event_turn_provenance(group, event_id)
                activation, start_claim = _recording_start_authorization(
                    request,
                    initial_request,
                    ledger_event,
                    provenance,
                    _current_claim,
                )
                if request.get("turn_authorization") != activation:
                    self._append_turn_activation_unlocked(
                        group_id,
                        request_id,
                        activation=activation,
                    )
                return start_claim

        start_claim = root_claim.consume_current(activate)
        if not isinstance(start_claim, RecordingStartClaim):
            raise PermissionError("computer control turn grant is no longer current")
        return start_claim

    def activate_run_start_for_turn_claim(
        self,
        group_id: str,
        request_id: str,
        actor_id: str,
        *,
        claim: Any,
        workflow_id: str,
        version: int,
        inputs: Dict[str, Any],
    ) -> RunStartClaim:
        from ..daemon.messaging.turn_provenance import (
            load_event_turn_provenance,
            require_validated_turn_grant_claim,
        )
        from ..kernel.group import load_group
        from ..kernel.ledger_index import lookup_event_by_id

        root_claim = require_validated_turn_grant_claim(claim)

        def activate(_current_claim: Any) -> RunStartClaim:
            with self._locked(group_id):
                request = self._require_authorized_unlocked(group_id, request_id, actor_id)
                if str(request.get("run_id") or "").strip():
                    raise PermissionError("computer control request already has a run")
                initial_request = load_initial_request_record(self._path(group_id), request_id)
                event_id = str(request.get("event_id") or "").strip()
                group = load_group(group_id)
                if group is None or not event_id:
                    raise PermissionError("computer control request event was not found")
                ledger_event = lookup_event_by_id(group.ledger_path, event_id)
                provenance = load_event_turn_provenance(group, event_id)
                try:
                    resolved_version = int(version)
                except (TypeError, ValueError) as exc:
                    raise PermissionError("computer control run scope is incomplete") from exc
                if resolved_version <= 0:
                    raise PermissionError("computer control run scope is incomplete")
                try:
                    workflow = self.workflows.get(
                        group_id,
                        str(workflow_id or "").strip(),
                        version=resolved_version,
                    )
                except (ValueError, WorkflowNotFound) as exc:
                    raise PermissionError("computer control workflow version was not found") from exc
                if int(workflow.get("version") or 0) != resolved_version:
                    raise PermissionError("computer control workflow version does not match")
                activation, start_claim = _run_start_authorization(
                    request,
                    initial_request,
                    ledger_event,
                    provenance,
                    _current_claim,
                    workflow_id=workflow_id,
                    version=resolved_version,
                    definition_digest=workflow_definition_digest(workflow.get("definition")),
                    inputs=inputs,
                )
                if request.get("turn_authorization") != activation:
                    self._append_turn_activation_unlocked(
                        group_id,
                        request_id,
                        activation=activation,
                    )
                return start_claim

        start_claim = root_claim.consume_current(activate)
        if not isinstance(start_claim, RunStartClaim):
            raise PermissionError("computer control turn grant is no longer current")
        return start_claim
