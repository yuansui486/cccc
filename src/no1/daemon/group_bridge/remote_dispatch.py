"""Daemon-owned, enqueue-only Group Bridge remote delivery boundary."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ...contracts.v1.group_bridge import (
    GroupBridgeSessionMessage,
    RemoteSendQueuedRequest,
)
from ...kernel.group_bridge.receipts import (
    ReceiptConflictError,
    ReceiptStoreError,
    get_queued_request,
    get_receipt_strict,
    record_receipt_after_admission,
)
from ...kernel.group_bridge.registration import get_registration


class RemoteDispatchError(RuntimeError):
    """A stable, non-secret error at the remote enqueue boundary."""

    def __init__(self, code: str, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable


def _registration_or_error(registration_id: str, group_id: str, home: Optional[Path]) -> Dict[str, Any]:
    registration = get_registration(registration_id, home)
    if registration is None:
        raise RemoteDispatchError("remote_registration_not_found", "Group Bridge registration was not found")
    if str(registration.get("group_id") or "") != group_id:
        raise RemoteDispatchError("remote_group_mismatch", "Group Bridge registration does not belong to this group")
    if str(registration.get("status") or "") != "active":
        raise RemoteDispatchError("remote_registration_inactive", "Group Bridge registration is not active")
    if str(registration.get("transport") or "") != "group_bridge_session":
        raise RemoteDispatchError("remote_transport_unsupported", "Group Bridge transport is not supported for enqueue")
    return registration


def _validated_request(
    *,
    group_id: str,
    registration_id: str,
    idempotency_key: str,
    payload: Any,
) -> RemoteSendQueuedRequest:
    try:
        message = GroupBridgeSessionMessage.model_validate(copy.deepcopy(payload))
        return RemoteSendQueuedRequest(
            src_group_id=group_id,
            registration_id=registration_id,
            idempotency_key=idempotency_key,
            payload=message,
        )
    except Exception as exc:
        raise RemoteDispatchError("invalid_request", "Group Bridge remote request is invalid") from exc


def _request_facts(request: RemoteSendQueuedRequest, registration: Dict[str, Any]) -> Dict[str, Any]:
    """Capture all non-secret target and payload facts used by idempotency."""
    return {
        "src_group_id": request.src_group_id,
        "registration_id": request.registration_id,
        "idempotency_key": request.idempotency_key,
        "registration": {
            "registration_fingerprint": str(registration.get("registration_fingerprint") or ""),
            "group_id": str(registration.get("group_id") or ""),
            "url": str(registration.get("url") or ""),
            "transport": str(registration.get("transport") or ""),
            "remote_group_id": str(registration.get("remote_group_id") or ""),
            "remote_peer_id": str(registration.get("remote_peer_id") or ""),
        },
        "payload": request.payload.model_dump(),
    }


def enqueue_remote_send(
    *,
    group_id: str,
    registration_id: str,
    idempotency_key: str,
    payload: Any,
    home: Optional[Path] = None,
    admit_new: Optional[Callable[[Dict[str, Any], Callable[[], Dict[str, Any]]], None]] = None,
) -> Dict[str, Any]:
    """Persist one queued request; no network or local message delivery occurs here."""
    if type(group_id) is not str or not group_id:
        raise RemoteDispatchError("invalid_request", "Group Bridge remote request is invalid")
    registration = _registration_or_error(registration_id, group_id, home)
    request = _validated_request(
        group_id=group_id,
        registration_id=registration_id,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    facts = _request_facts(request, registration)

    def current_facts() -> Dict[str, Any]:
        current_registration = _registration_or_error(request.registration_id, request.src_group_id, home)
        return _request_facts(request, current_registration)

    try:
        receipt, created = record_receipt_after_admission(
            request.registration_id,
            request.idempotency_key,
            {
                "status": "queued",
                "transport": "group_bridge_session",
                "attempt": 0,
                "max_attempts": 5,
            },
            home,
            request_facts=facts,
            queued_request=request.model_dump(),
            admit_new=(
                lambda: admit_new(copy.deepcopy(facts), current_facts)
                if admit_new is not None
                else None
            ),
        )
    except ReceiptConflictError as exc:
        raise RemoteDispatchError("remote_receipt_conflict", "Idempotency key conflicts with the original request") from exc
    except ReceiptStoreError as exc:
        raise RemoteDispatchError("remote_receipt_store_corrupt", "Group Bridge receipt storage is unavailable") from exc
    return {"queued": created, "replayed": not created, "receipt": receipt}


def remote_delivery_status(
    *,
    group_id: str,
    registration_id: str,
    idempotency_key: str,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return a public receipt projection without exposing queued payload facts."""
    if type(group_id) is not str or not group_id:
        raise RemoteDispatchError("invalid_request", "Group Bridge remote request is invalid")
    _registration_or_error(registration_id, group_id, home)
    try:
        receipt = get_receipt_strict(registration_id, idempotency_key, home)
        if receipt is not None and receipt.get("status") == "queued" and get_queued_request(
            registration_id, idempotency_key, home
        ) is None:
            raise ReceiptStoreError("queued receipt facts are missing")
    except ReceiptStoreError as exc:
        raise RemoteDispatchError("remote_receipt_store_corrupt", "Group Bridge receipt storage is unavailable") from exc
    return {"receipt": receipt}


__all__ = ["RemoteDispatchError", "enqueue_remote_send", "remote_delivery_status"]
