from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple

from ..daemon.messaging.turn_provenance import (
    ValidatedTurnGrantClaim,
    require_validated_turn_grant_claim,
)
from .models import computer_control_permissions


_WORKFLOW_METADATA_ACTIONS = {"list", "get"}
_STATUS_ONLY_ACTIONS = {
    ("lease", "status"),
    ("setup", "status"),
}
_START_CLAIM_SEAL = object()


@dataclass(frozen=True, init=False)
class RecordingStartClaim:
    issuer_epoch: str
    root_authority_id: str
    root_attempt_id: str
    generation: int
    group_id: str
    actor_id: str
    request_id: str
    permission_snapshot: Mapping[str, bool]
    _seal: object
    _root_claim: ValidatedTurnGrantClaim

    def __init__(
        self,
        *,
        _seal: object,
        request: Dict[str, Any],
        claim: ValidatedTurnGrantClaim,
    ):
        if _seal is not _START_CLAIM_SEAL:
            raise TypeError("recording start claims can only be created by authorization")
        root_claim = require_validated_turn_grant_claim(claim)
        binding = claim.get("authorization_binding")
        object.__setattr__(self, "issuer_epoch", str(claim.get("issuer_epoch") or ""))
        object.__setattr__(
            self,
            "root_authority_id",
            str(binding.get("authority_id") or "") if isinstance(binding, dict) else "",
        )
        object.__setattr__(self, "root_attempt_id", str(claim.get("attempt_id") or ""))
        object.__setattr__(self, "generation", int(claim.get("generation") or 0))
        object.__setattr__(self, "group_id", str(request.get("group_id") or ""))
        object.__setattr__(self, "actor_id", str(request.get("actor_id") or ""))
        object.__setattr__(self, "request_id", str(request.get("request_id") or ""))
        object.__setattr__(
            self,
            "permission_snapshot",
            MappingProxyType(computer_control_permissions(request)),
        )
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_root_claim", root_claim)

    def consume_current(self, consumer):
        if self._seal is not _START_CLAIM_SEAL:
            return None
        return self._root_claim.consume_current(lambda _claim: consumer(self))

    def require_fresh_current(self) -> None:
        if self._seal is not _START_CLAIM_SEAL:
            raise PermissionError("recording start claim is invalid")
        self._root_claim.require_fresh_current()


def _recording_start_authorization(
    request: Dict[str, Any],
    initial_request: Dict[str, Any],
    ledger_event: Any,
    provenance: Any,
    claim: ValidatedTurnGrantClaim,
) -> Tuple[Dict[str, Any], RecordingStartClaim]:
    """Seal a persisted user request and its live root into one start capability."""

    root_claim = require_validated_turn_grant_claim(claim)
    validate_request_event_binding(request, initial_request, ledger_event)
    activation = request_turn_authorization(request, root_claim, provenance)
    activated = {**request, "turn_authorization": activation}
    return activation, RecordingStartClaim(
        _seal=_START_CLAIM_SEAL,
        request=activated,
        claim=root_claim,
    )


def requires_live_turn_claim(command: str, action: str = "") -> bool:
    command_name = str(command or "").strip().lower()
    action_name = str(action or "").strip().lower()
    if command_name == "workflow" and action_name in _WORKFLOW_METADATA_ACTIONS:
        return False
    if command_name == "recording" and action_name != "start":
        return False
    if (command_name, action_name) in _STATUS_ONLY_ACTIONS:
        return False
    return True


def request_id_requiring_turn_binding(command: str, action: str, args: Dict[str, Any]) -> str:
    command_name = str(command or "").strip().lower()
    action_name = str(action or "").strip().lower()
    if command_name == "recording" and action_name == "start":
        return str(args.get("request_id") or "").strip()
    if command_name == "workflow" and action_name not in {"list", "get", "validate"}:
        return str(args.get("request_id") or "").strip()
    if command_name == "run" and action_name == "start":
        return str(args.get("request_id") or "").strip()
    return ""


def load_initial_request_record(path: Path, request_id: str) -> Dict[str, Any]:
    wanted = str(request_id or "").strip()
    if not wanted or not path.exists():
        raise PermissionError("computer control request was not found")
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and str(value.get("request_id") or "").strip() == wanted:
                return value
    raise PermissionError("computer control request was not found")


def _request_authorization_facts(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "request_id": str(value.get("request_id") or "").strip(),
        "actor_id": str(value.get("actor_id") or "").strip(),
        "mode": str(value.get("mode") or "").strip(),
        "workflow_id": str(value.get("workflow_id") or "").strip(),
        **computer_control_permissions(value),
    }


def validate_request_event_binding(
    request: Dict[str, Any],
    initial_request: Dict[str, Any],
    ledger_event: Any,
) -> None:
    event_id = str(request.get("event_id") or "").strip()
    group_id = str(request.get("group_id") or "").strip()
    if not isinstance(ledger_event, dict):
        raise PermissionError("computer control request event was not found")
    event_data = ledger_event.get("data") if isinstance(ledger_event.get("data"), dict) else {}
    event_request = event_data.get("computer_control_request")
    if (
        str(ledger_event.get("id") or "").strip() != event_id
        or str(ledger_event.get("group_id") or "").strip() != group_id
        or str(ledger_event.get("kind") or "").strip() != "chat.message"
        or str(ledger_event.get("by") or "").strip() != "user"
        or not isinstance(event_request, dict)
    ):
        raise PermissionError("computer control request is not bound to its local user event")
    if (
        str(initial_request.get("group_id") or "").strip() != group_id
        or _request_authorization_facts(initial_request) != _request_authorization_facts(event_request)
        or _request_authorization_facts(request) != _request_authorization_facts(event_request)
    ):
        raise PermissionError("computer control request facts do not match the local user event")


def request_turn_authorization(
    request: Dict[str, Any],
    claim: ValidatedTurnGrantClaim,
    provenance: Any,
) -> Dict[str, Any]:
    claim = require_validated_turn_grant_claim(claim)
    request_id = str(request.get("request_id") or "").strip()
    request_group_id = str(request.get("group_id") or "").strip()
    request_actor_id = str(request.get("actor_id") or "").strip()
    event_id = str(request.get("event_id") or "").strip()
    local_request_id = str(request.get("local_request_id") or "").strip()
    claim_event_ids = [str(item or "").strip() for item in claim.get("event_ids") or []]
    claim_local_request_ids = [str(item or "").strip() for item in claim.get("local_request_ids") or []]
    provenance_local_request_id = str(getattr(provenance, "local_request_id", "") or "").strip()
    if not request_id or not request_group_id or not request_actor_id:
        raise PermissionError("computer control request identity is incomplete")
    if (
        request_group_id != str(claim.get("group_id") or "").strip()
        or request_actor_id != str(claim.get("actor_id") or "").strip()
    ):
        raise PermissionError("computer control request does not match the current turn grant")
    if (
        getattr(provenance, "origin", "") != "local_user"
        or not bool(getattr(provenance, "fresh_local_request", False))
        or not event_id
        or event_id not in claim_event_ids
        or not local_request_id
        or local_request_id != provenance_local_request_id
        or local_request_id not in claim_local_request_ids
    ):
        raise PermissionError("computer control request is not bound to this local user turn")
    authorization_binding = claim.get("authorization_binding")
    if not isinstance(authorization_binding, dict) or not authorization_binding:
        raise PermissionError("computer control turn authorization binding is missing")
    activation = {
        "v": 1,
        "issuer_epoch": str(claim.get("issuer_epoch") or ""),
        "authority_id": str(authorization_binding.get("authority_id") or ""),
        "secret_digest": str(authorization_binding.get("secret_digest") or ""),
        "group_id": request_group_id,
        "actor_id": request_actor_id,
        "request_id": request_id,
        "request_event_id": event_id,
        "local_request_id": local_request_id,
        "attempt_id": str(claim.get("attempt_id") or ""),
        "generation": int(claim.get("generation") or 0),
        "event_ids": claim_event_ids,
        "authorization_binding": dict(authorization_binding),
    }
    if not all(
        (
            activation["issuer_epoch"],
            activation["authority_id"],
            activation["secret_digest"],
            activation["attempt_id"],
            activation["generation"] > 0,
        )
    ):
        raise PermissionError("computer control turn authorization identity is incomplete")
    existing = request.get("turn_authorization")
    if isinstance(existing, dict) and existing != activation:
        raise PermissionError("computer control request was activated by another turn")
    return activation
