"""Closed daemon operations for signed Group Bridge message sessions."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Optional

from ...contracts.v1 import DaemonError, DaemonResponse
from ...contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
from ...kernel.access_tokens import AccessTokenClaimError, issue_access_token_principal_claim
from ...kernel.group_bridge.pairing import (
    ACCESS_LEVELS,
    approve_pairing_request,
    create_pairing_invite,
    get_local_identity,
    list_pairing_requests,
    list_trusts,
    reject_pairing_request,
    revoke_trust,
    update_trust_access_level,
)
from ...kernel.group_bridge.registration import list_registrations
from .identity import get_group_bridge_identity
from ..messaging.chat_ops import (
    _GROUP_BRIDGE_DELIVERY_CLAIM_ARG,
    _issue_group_bridge_delivery_claim,
)
from ..messaging.turn_provenance import INGRESS_GROUP_BRIDGE, TRUSTED_INGRESS_ARG
from .session import (
    GroupBridgeSessionError,
    receive_group_bridge_session_message,
    send_group_bridge_session_message,
)
from .remote_dispatch import RemoteDispatchError, enqueue_remote_send, remote_delivery_status
from .pairing_transport import (
    PairingTransportError,
    create_pairing_connection,
    receive_remote_pairing_request,
    remote_pairing_status,
    submit_remote_pairing,
    sync_remote_pairing,
)
from .remote_outbox_worker import default_local_endpoint

_SEND_OP = "group_bridge_session_send"
_RECEIVE_OP = "group_bridge_session_receive"
_SEND_FIELDS = frozenset(
    {
        "group_id",
        "local_endpoint",
        "remote_group_id",
        "remote_peer_id",
        "remote_endpoint",
        "client_nonce",
        "payload",
    }
)
_RECEIVE_FIELDS = frozenset({"group_id", "envelope"})
_REMOTE_SEND_OP = "remote_send"
_REMOTE_STATUS_OP = "remote_delivery_status"
_REMOTE_SEND_FIELDS = frozenset({"group_id", "registration_id", "idempotency_key", "payload"})
_REMOTE_STATUS_FIELDS = frozenset({"group_id", "registration_id", "idempotency_key"})
_MGMT_IDENTITY_OP = "group_bridge_management_identity"
_MGMT_REGISTRATIONS_OP = "group_bridge_management_registrations"
_MGMT_TRUSTS_OP = "group_bridge_management_trusts"
_MGMT_REQUESTS_OP = "group_bridge_management_pairing_requests"
_MGMT_INVITE_OP = "group_bridge_management_pairing_invite"
_MGMT_APPROVE_OP = "group_bridge_management_pairing_approve"
_MGMT_REJECT_OP = "group_bridge_management_pairing_reject"
_MGMT_ACCESS_OP = "group_bridge_management_trust_access"
_MGMT_REVOKE_OP = "group_bridge_management_trust_revoke"
_MGMT_CONNECTION_OP = "group_bridge_management_pairing_connection"
_MGMT_REMOTE_SUBMIT_OP = "group_bridge_management_pairing_remote_submit"
_MGMT_REMOTE_SYNC_OP = "group_bridge_management_pairing_remote_sync"
_PUBLIC_PAIRING_REQUEST_OP = "group_bridge_pairing_remote_request"
_PUBLIC_PAIRING_STATUS_OP = "group_bridge_pairing_remote_status"
_MGMT_ALIASES = {
    "group_bridge_identity": _MGMT_IDENTITY_OP,
    "group_bridge_registrations": _MGMT_REGISTRATIONS_OP,
    "group_bridge_trusts": _MGMT_TRUSTS_OP,
    "group_bridge_pairing_requests": _MGMT_REQUESTS_OP,
    "group_bridge_pairing_invite_create": _MGMT_INVITE_OP,
    "group_bridge_pairing_request_approve": _MGMT_APPROVE_OP,
    "group_bridge_pairing_request_reject": _MGMT_REJECT_OP,
    "group_bridge_trust_access_update": _MGMT_ACCESS_OP,
    "group_bridge_trust_revoke": _MGMT_REVOKE_OP,
}
_MGMT_IDENTITY_FIELDS = frozenset({"group_id", "access_token"})
_MGMT_REGISTRATIONS_FIELDS = frozenset({"group_id", "access_token"})
_MGMT_TRUSTS_FIELDS = frozenset({"group_id", "access_token"})
_MGMT_REQUESTS_FIELDS = frozenset({"group_id", "access_token"})
_MGMT_INVITE_FIELDS = frozenset(
    {"group_id", "expected_remote_group_id", "expected_remote_peer_id", "multiaddrs", "ttl_seconds", "access_token"}
)
_MGMT_APPROVE_FIELDS = frozenset({"group_id", "request_id", "access_token"})
_MGMT_REJECT_FIELDS = frozenset({"group_id", "request_id", "reason", "access_token"})
_MGMT_ACCESS_FIELDS = frozenset({"group_id", "trust_id", "access_level", "expected_revision", "access_token"})
_MGMT_REVOKE_FIELDS = frozenset({"group_id", "trust_id", "expected_revision", "access_token"})
_MGMT_CONNECTION_FIELDS = frozenset(
    {"group_id", "expected_remote_group_id", "expected_remote_peer_id", "multiaddrs", "ttl_seconds", "access_token"}
)
_MGMT_REMOTE_SUBMIT_FIELDS = frozenset({"group_id", "group_title", "connection", "access_token"})
_MGMT_REMOTE_SYNC_FIELDS = frozenset({"group_id", "outbound_id", "access_token"})
_PUBLIC_PAIRING_FIELDS = frozenset({"envelope"})


def _error(code: str, message: str, *, retriable: bool = False) -> DaemonResponse:
    return DaemonResponse(
        ok=False,
        error=DaemonError(code=code, message=message, details={"retriable": retriable}),
    )


def _session_error(error: GroupBridgeSessionError) -> DaemonResponse:
    return _error(error.code, str(error), retriable=error.retriable)


def _closed_args(args: Any, fields: frozenset[str]) -> Optional[DaemonResponse]:
    if not isinstance(args, dict) or set(args) != fields:
        return _error("invalid_request", "Group Bridge session arguments are invalid")
    return None


def _dispatch_response(value: Any) -> DaemonResponse:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], DaemonResponse)
        or type(value[1]) is not bool
        or value[1]
    ):
        raise RuntimeError("local delivery returned an invalid response")
    response = value[0]
    if not response.ok:
        error = response.error
        code = str(error.code or "local_delivery_failed") if error is not None else "local_delivery_failed"
        message = str(error.message or "local delivery failed") if error is not None else "local delivery failed"
        raise _LocalDeliveryError(code, message)
    return response


class _LocalDeliveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _local_send_args(
    envelope: GroupBridgeSignedMessageEnvelope,
    payload: Dict[str, Any],
    delivery_id: str,
) -> Dict[str, Any]:
    args = {
        "group_id": envelope.target_group_id,
        "text": payload["text"],
        "format": payload["format"],
        "priority": payload["priority"],
        "reply_required": payload["reply_required"],
        "collaboration_required": False,
        "by": "system",
        "to": ["user"],
        "attachments": [],
        "refs": [],
        "path": "",
        "quote_text": "",
        "source_platform": "group_bridge_session",
        "source_user_id": envelope.source_peer_id,
        "src_group_id": envelope.source_group_id,
        "src_event_id": delivery_id,
        "client_id": delivery_id,
        TRUSTED_INGRESS_ARG: INGRESS_GROUP_BRIDGE,
    }
    args[_GROUP_BRIDGE_DELIVERY_CLAIM_ARG] = _issue_group_bridge_delivery_claim(args)
    return args


def _handle_send(args: Dict[str, Any]) -> DaemonResponse:
    invalid = _closed_args(args, _SEND_FIELDS)
    if invalid is not None:
        return invalid
    try:
        result = send_group_bridge_session_message(
            group_id=args["group_id"],
            local_endpoint=args["local_endpoint"],
            remote_group_id=args["remote_group_id"],
            remote_peer_id=args["remote_peer_id"],
            remote_endpoint=args["remote_endpoint"],
            client_nonce=args["client_nonce"],
            payload=copy.deepcopy(args["payload"]),
        )
        return DaemonResponse(ok=True, result={"session": result})
    except GroupBridgeSessionError as exc:
        return _session_error(exc)
    except Exception:
        return _error("session_failed", "Group Bridge session operation failed", retriable=True)


def _handle_receive(
    args: Dict[str, Any],
    *,
    dispatch_send: Callable[[Dict[str, Any]], Any],
) -> DaemonResponse:
    invalid = _closed_args(args, _RECEIVE_FIELDS)
    if invalid is not None:
        return invalid
    try:
        envelope = GroupBridgeSignedMessageEnvelope.model_validate(copy.deepcopy(args["envelope"]))
    except Exception:
        return _error("invalid_envelope", "signed session envelope is invalid")

    delivery_error_code = ""

    def deliver(payload: Dict[str, Any], delivery_id: str) -> str:
        nonlocal delivery_error_code
        try:
            response = _dispatch_response(dispatch_send(_local_send_args(envelope, payload, delivery_id)))
        except _LocalDeliveryError as exc:
            delivery_error_code = exc.code
            raise
        event = response.result.get("event")
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("id"), str)
            or event["id"] != delivery_id
        ):
            raise RuntimeError("local delivery did not return an event identity")
        return event["id"]

    try:
        receipt = receive_group_bridge_session_message(
            envelope.model_dump(),
            group_id=args["group_id"],
            local_endpoint=default_local_endpoint(),
            deliver=deliver,
        )
        return DaemonResponse(ok=True, result={"receipt": receipt})
    except GroupBridgeSessionError as exc:
        if delivery_error_code == "group_bridge_delivery_conflict":
            return _error(
                "group_bridge_delivery_conflict",
                "Group Bridge delivery identity conflicts with the ledger",
            )
        return _session_error(exc)
    except Exception:
        return _error("session_failed", "Group Bridge session operation failed", retriable=True)


def _remote_error(error: RemoteDispatchError) -> DaemonResponse:
    return _error(error.code, str(error), retriable=error.retriable)


def _management_error(code: str, message: str = "Group Bridge management request failed") -> DaemonResponse:
    return _error(code, message)


def _management_claim(args: Dict[str, Any], group_id: str):
    token = args.get("access_token")
    if type(token) is not str or not token:
        raise AccessTokenClaimError("authenticated user is required")
    if type(group_id) is not str or not group_id or group_id != group_id.strip():
        raise ValueError("group_id is invalid")
    return issue_access_token_principal_claim(token, group_id=group_id)


def _public_registration(record: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        "registration_id",
        "registration_fingerprint",
        "group_id",
        "url",
        "transport",
        "remote_group_id",
        "remote_peer_id",
        "multiaddrs",
        "status",
        "created_at",
        "updated_at",
        "last_sync_at",
    )
    return {field: copy.deepcopy(record[field]) for field in fields if field in record}


def _management_read_principal(args: Dict[str, Any], group_id: str) -> None:
    _management_claim(args, group_id)


def _handle_management(args: Dict[str, Any], *, op: str) -> DaemonResponse:
    field_sets = {
        _MGMT_IDENTITY_OP: _MGMT_IDENTITY_FIELDS,
        _MGMT_REGISTRATIONS_OP: _MGMT_REGISTRATIONS_FIELDS,
        _MGMT_TRUSTS_OP: _MGMT_TRUSTS_FIELDS,
        _MGMT_REQUESTS_OP: _MGMT_REQUESTS_FIELDS,
        _MGMT_INVITE_OP: _MGMT_INVITE_FIELDS,
        _MGMT_APPROVE_OP: _MGMT_APPROVE_FIELDS,
        _MGMT_REJECT_OP: _MGMT_REJECT_FIELDS,
        _MGMT_ACCESS_OP: _MGMT_ACCESS_FIELDS,
        _MGMT_REVOKE_OP: _MGMT_REVOKE_FIELDS,
        _MGMT_CONNECTION_OP: _MGMT_CONNECTION_FIELDS,
        _MGMT_REMOTE_SUBMIT_OP: _MGMT_REMOTE_SUBMIT_FIELDS,
        _MGMT_REMOTE_SYNC_OP: _MGMT_REMOTE_SYNC_FIELDS,
    }
    invalid = _closed_args(args, field_sets[op])
    if invalid is not None:
        return invalid
    group_id = args.get("group_id")
    if type(group_id) is not str or not group_id or group_id != group_id.strip():
        return _management_error("invalid_request", "Group Bridge management group is invalid")
    try:
        if op == _MGMT_IDENTITY_OP:
            _management_read_principal(args, group_id)
            identity = get_group_bridge_identity().public_dict()
            identity.update(get_local_identity())
            return DaemonResponse(ok=True, result={"identity": identity})
        if op == _MGMT_REGISTRATIONS_OP:
            _management_read_principal(args, group_id)
            registrations = [
                _public_registration(item)
                for item in list_registrations()
                if item.get("group_id") == group_id and item.get("status") == "active"
            ]
            return DaemonResponse(ok=True, result={"registrations": registrations})
        if op == _MGMT_TRUSTS_OP:
            _management_read_principal(args, group_id)
            trusts = [trust for trust in list_trusts(group_id=group_id) if trust.get("status") == "active"]
            return DaemonResponse(ok=True, result={"trusts": trusts})
        if op == _MGMT_REQUESTS_OP:
            _management_read_principal(args, group_id)
            return DaemonResponse(ok=True, result={"requests": list_pairing_requests(group_id=group_id)})
        claim = _management_claim(args, group_id)
        if op == _MGMT_CONNECTION_OP:
            if type(args["expected_remote_group_id"]) is not str or type(args["expected_remote_peer_id"]) is not str:
                raise ValueError("pairing connection identities are invalid")
            if type(args["multiaddrs"]) is not list or not all(type(item) is str for item in args["multiaddrs"]):
                raise ValueError("pairing connection addresses are invalid")
            if type(args["ttl_seconds"]) is not int or isinstance(args["ttl_seconds"], bool):
                raise ValueError("pairing connection ttl is invalid")
            connection = create_pairing_connection(
                group_id=group_id,
                local_endpoint=default_local_endpoint(),
                remote_group_id=args["expected_remote_group_id"],
                remote_peer_id=args["expected_remote_peer_id"],
                multiaddrs=args["multiaddrs"],
                ttl_seconds=args["ttl_seconds"],
            )
            return DaemonResponse(ok=True, result={"connection": connection})
        if op == _MGMT_REMOTE_SUBMIT_OP:
            if type(args["group_title"]) is not str or not isinstance(args["connection"], dict):
                raise ValueError("pairing remote submit facts are invalid")
            outbound = submit_remote_pairing(
                args["connection"],
                local_group_id=group_id,
                local_group_title=args["group_title"],
                requester_endpoint=default_local_endpoint(),
                claim=claim,
            )
            return DaemonResponse(ok=True, result={"outbound": outbound})
        if op == _MGMT_REMOTE_SYNC_OP:
            if type(args["outbound_id"]) is not str or not args["outbound_id"]:
                raise ValueError("pairing outbound identity is invalid")
            outbound = sync_remote_pairing(args["outbound_id"], local_group_id=group_id, claim=claim)
            return DaemonResponse(ok=True, result={"outbound": outbound})
        if op == _MGMT_INVITE_OP:
            if type(args["expected_remote_group_id"]) is not str or type(args["expected_remote_peer_id"]) is not str:
                raise ValueError("pairing invite identities are invalid")
            if type(args["multiaddrs"]) is not list or not all(type(item) is str for item in args["multiaddrs"]):
                raise ValueError("pairing invite addresses are invalid")
            if type(args["ttl_seconds"]) is not int or isinstance(args["ttl_seconds"], bool):
                raise ValueError("pairing invite ttl is invalid")
            invite = create_pairing_invite(
                group_id=group_id,
                remote_group_id=args["expected_remote_group_id"],
                remote_peer_id=args["expected_remote_peer_id"],
                multiaddrs=args["multiaddrs"],
                ttl_seconds=args["ttl_seconds"],
            )
            return DaemonResponse(ok=True, result={"invite": invite})
        if op == _MGMT_APPROVE_OP:
            if type(args["request_id"]) is not str or not args["request_id"]:
                raise ValueError("request_id is invalid")
            result = approve_pairing_request(args["request_id"], claim=claim)
            return DaemonResponse(ok=True, result={"request": result.get("request"), "trust": result.get("trust")})
        if op == _MGMT_REJECT_OP:
            if type(args["request_id"]) is not str or not args["request_id"] or type(args["reason"]) is not str:
                raise ValueError("pairing request action is invalid")
            result = reject_pairing_request(args["request_id"], claim=claim, reason=args["reason"])
            return DaemonResponse(ok=True, result={"request": result})
        if op == _MGMT_ACCESS_OP:
            if type(args["access_level"]) is not str or args["access_level"] not in ACCESS_LEVELS:
                raise ValueError("access_level must be one of: messages, read, full")
            if type(args["trust_id"]) is not str or not args["trust_id"]:
                raise ValueError("trust_id is invalid")
            if type(args["expected_revision"]) is not int or isinstance(args["expected_revision"], bool):
                raise ValueError("expected_revision is invalid")
            result = update_trust_access_level(
                args["trust_id"],
                args["access_level"],
                expected_revision=args["expected_revision"],
                claim=claim,
            )
            return DaemonResponse(ok=True, result={"trust": result})
        if type(args["trust_id"]) is not str or not args["trust_id"]:
            raise ValueError("trust_id is invalid")
        if type(args["expected_revision"]) is not int or isinstance(args["expected_revision"], bool):
            raise ValueError("expected_revision is invalid")
        result = revoke_trust(
            args["trust_id"],
            expected_revision=args["expected_revision"],
            claim=claim,
        )
        return DaemonResponse(ok=True, result={"trust": result})
    except AccessTokenClaimError:
        return _management_error("permission_denied", "authenticated user is required")
    except PermissionError:
        return _management_error("permission_denied", "Group Bridge management permission denied")
    except ValueError as exc:
        message = str(exc)
        if "access_level" in message:
            return _management_error("invalid_access_level", "access level is invalid")
        return _management_error("invalid_request", "Group Bridge management request is invalid")
    except PairingTransportError as exc:
        return _error(exc.code, str(exc), retriable=exc.retriable)
    except Exception:
        return _management_error("management_failed")


def _handle_public_pairing(args: Dict[str, Any], *, op: str) -> DaemonResponse:
    invalid = _closed_args(args, _PUBLIC_PAIRING_FIELDS)
    if invalid is not None:
        return invalid
    try:
        if not isinstance(args["envelope"], dict):
            raise PairingTransportError("invalid_request", "Group Bridge pairing envelope is invalid")
        if op == _PUBLIC_PAIRING_REQUEST_OP:
            status = receive_remote_pairing_request(
                args["envelope"],
                local_endpoint=default_local_endpoint(),
            )
        else:
            status = remote_pairing_status(
                args["envelope"],
                local_endpoint=default_local_endpoint(),
            )
        return DaemonResponse(ok=True, result={"status": status})
    except PairingTransportError as exc:
        return _error(exc.code, str(exc), retriable=exc.retriable)
    except Exception:
        return _error("pairing_failed", "Group Bridge pairing operation failed", retriable=True)


def _handle_remote_send(args: Dict[str, Any]) -> DaemonResponse:
    invalid = _closed_args(args, _REMOTE_SEND_FIELDS)
    if invalid is not None:
        return invalid
    try:
        result = enqueue_remote_send(
            group_id=args["group_id"],
            registration_id=args["registration_id"],
            idempotency_key=args["idempotency_key"],
            payload=copy.deepcopy(args["payload"]),
        )
        return DaemonResponse(ok=True, result=result)
    except RemoteDispatchError as exc:
        return _remote_error(exc)
    except Exception:
        return _error("remote_enqueue_failed", "Group Bridge remote enqueue failed", retriable=True)


def _handle_remote_status(args: Dict[str, Any]) -> DaemonResponse:
    invalid = _closed_args(args, _REMOTE_STATUS_FIELDS)
    if invalid is not None:
        return invalid
    try:
        result = remote_delivery_status(
            group_id=args["group_id"],
            registration_id=args["registration_id"],
            idempotency_key=args["idempotency_key"],
        )
        return DaemonResponse(ok=True, result=result)
    except RemoteDispatchError as exc:
        return _remote_error(exc)
    except Exception:
        return _error("remote_status_failed", "Group Bridge remote status failed", retriable=True)


def try_handle_group_bridge_op(
    op: str,
    args: Dict[str, Any],
    *,
    dispatch_send: Callable[[Dict[str, Any]], Any],
) -> Optional[DaemonResponse]:
    if op == _SEND_OP:
        return _handle_send(args)
    if op == _RECEIVE_OP:
        return _handle_receive(args, dispatch_send=dispatch_send)
    if op == _REMOTE_SEND_OP:
        return _handle_remote_send(args)
    if op == _REMOTE_STATUS_OP:
        return _handle_remote_status(args)
    if op in {_PUBLIC_PAIRING_REQUEST_OP, _PUBLIC_PAIRING_STATUS_OP}:
        return _handle_public_pairing(args, op=op)
    canonical_management_op = _MGMT_ALIASES.get(op, op)
    if canonical_management_op in {
        _MGMT_IDENTITY_OP,
        _MGMT_REGISTRATIONS_OP,
        _MGMT_TRUSTS_OP,
        _MGMT_REQUESTS_OP,
        _MGMT_INVITE_OP,
        _MGMT_APPROVE_OP,
        _MGMT_REJECT_OP,
        _MGMT_ACCESS_OP,
        _MGMT_REVOKE_OP,
        _MGMT_CONNECTION_OP,
        _MGMT_REMOTE_SUBMIT_OP,
        _MGMT_REMOTE_SYNC_OP,
    }:
        return _handle_management(args, op=canonical_management_op)
    return None


__all__ = ["try_handle_group_bridge_op"]
