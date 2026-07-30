from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from .models import computer_control_permissions
from .authorization import (
    load_initial_request_record,
    request_turn_authorization,
    validate_request_event_binding,
)
from .storage import WorkflowStore


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
        }
    )

    def __init__(self, workflows: WorkflowStore):
        self.workflows = workflows

    def _path(self, group_id: str):
        return self.workflows.state_root(group_id) / "requests.jsonl"

    def _append_record(self, group_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
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

    def append(self, group_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        self._reject_public_authority_fields(request)
        return self._append_record(group_id, request)

    def _append_turn_activation(
        self,
        group_id: str,
        request_id: str,
        *,
        activation: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._append_record(
            group_id,
            {
                "request_id": request_id,
                "turn_authorization": activation,
                "activated_at": time.time(),
            },
        )

    def get(self, group_id: str, request_id: str) -> Optional[Dict[str, Any]]:
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

    def update(self, group_id: str, request_id: str, **patch: Any) -> Dict[str, Any]:
        current = self.get(group_id, request_id)
        if current is None:
            raise KeyError(request_id)
        return self.append(group_id, {"request_id": request_id, **patch})

    def require_authorized(self, group_id: str, request_id: str, actor_id: str) -> Dict[str, Any]:
        request = self.get(group_id, request_id)
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

    def activate_for_turn_claim(
        self,
        group_id: str,
        request_id: str,
        actor_id: str,
        *,
        claim: Dict[str, Any],
        provenance: Any,
        ledger_event: Dict[str, Any],
    ) -> Dict[str, Any]:
        request = self.require_authorized(group_id, request_id, actor_id)
        initial_request = load_initial_request_record(self._path(group_id), request_id)
        validate_request_event_binding(request, initial_request, ledger_event)
        activation = request_turn_authorization(request, claim, provenance)
        if request.get("turn_authorization") == activation:
            return request
        return {
            **request,
            **self._append_turn_activation(
                group_id,
                request_id,
                activation=activation,
            ),
        }
