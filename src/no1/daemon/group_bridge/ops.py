"""Closed daemon operations for signed Group Bridge message sessions."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Optional

from ...contracts.v1 import DaemonError, DaemonResponse
from ...contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
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
_RECEIVE_FIELDS = frozenset({"group_id", "local_endpoint", "envelope"})
_REMOTE_SEND_OP = "remote_send"
_REMOTE_STATUS_OP = "remote_delivery_status"
_REMOTE_SEND_FIELDS = frozenset({"group_id", "registration_id", "idempotency_key", "payload"})
_REMOTE_STATUS_FIELDS = frozenset({"group_id", "registration_id", "idempotency_key"})


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
            local_endpoint=args["local_endpoint"],
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
    return None


__all__ = ["try_handle_group_bridge_op"]
