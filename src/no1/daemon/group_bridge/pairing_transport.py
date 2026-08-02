"""Daemon-owned signed HTTP transport for Group Bridge pairing."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib import parse as urlparse

from ...contracts.v1.group_bridge import (
    GroupBridgePairingConnectionEnvelope,
    GroupBridgePairingRequestEnvelope,
    GroupBridgePairingStatusEnvelope,
)
from ...kernel.access_tokens import AccessTokenPrincipalClaim
from ...kernel.group_bridge.credentials import (
    delete_group_bridge_credential,
    resolve_group_bridge_credential,
    save_pairing_bearer_token,
)
from ...kernel.group_bridge.pairing import (
    create_pairing_invite,
    create_pairing_request,
    get_pairing_request,
    install_remote_pairing_approval,
)
from ...kernel.group_bridge.pairing_outbound import (
    PairingOutboundConflictError,
    PairingOutboundStoreError,
    get_pairing_outbound,
    pairing_outbound_id,
    public_pairing_outbound,
    reserve_pairing_outbound,
    update_pairing_outbound,
)
from ...util.time import parse_utc_iso, utc_now_iso
from .identity import (
    canonical_payload_bytes,
    get_group_bridge_identity,
    sign_group_bridge_payload,
    verify_group_bridge_signature,
)
from .session import (
    GroupBridgeSessionError,
    _canonical_http_endpoint,
    _default_http_post,
    _is_fresh,
)

_RECEIVE_PATH = "/api/group-bridge/session/receive"
_PAIRING_REQUEST_PATH = "/api/group-bridge/pairing/remote/requests"
_PAIRING_STATUS_PATH = "/api/group-bridge/pairing/remote/status"
_TERMINAL_OUTBOUND_STATUSES = frozenset({"approved", "rejected", "expired"})


class PairingTransportError(RuntimeError):
    def __init__(self, code: str, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable


HttpPost = Callable[[str, Dict[str, Any]], Any]


def canonical_receive_endpoint(value: Any) -> str:
    try:
        endpoint = _canonical_http_endpoint(value)
    except GroupBridgeSessionError as exc:
        raise PairingTransportError("invalid_request", "Group Bridge receive endpoint is invalid") from exc
    if urlparse.urlsplit(endpoint).path != _RECEIVE_PATH:
        raise PairingTransportError("invalid_request", "Group Bridge receive endpoint path is invalid")
    return endpoint


def _pairing_endpoint(receive_endpoint: str, path: str) -> str:
    parts = urlparse.urlsplit(canonical_receive_endpoint(receive_endpoint))
    return urlparse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _unsigned(value: Any) -> Dict[str, Any]:
    return value.model_dump(exclude={"signature"})


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_payload_bytes(_unsigned(value))).hexdigest()


def _status_fingerprint(value: GroupBridgePairingStatusEnvelope) -> str:
    stable = value.model_dump(exclude={"issued_at", "signature"})
    return hashlib.sha256(canonical_payload_bytes(stable)).hexdigest()


def _cleanup_terminal_credential(entry: Dict[str, Any], *, home: Optional[Path]) -> None:
    if entry.get("status") in _TERMINAL_OUTBOUND_STATUSES:
        delete_group_bridge_credential(entry["credential_ref"], home=home)


def _verify_signature(value: Any, *, public_key: str, peer_id: str) -> None:
    if not verify_group_bridge_signature(
        canonical_payload_bytes(_unsigned(value)),
        value.signature,
        public_key_b64=public_key,
        peer_id=peer_id,
    ):
        raise PairingTransportError("invalid_signature", "Group Bridge pairing signature is invalid")


def _not_expired(value: str) -> None:
    expires = parse_utc_iso(value)
    if expires is None or expires <= datetime.now(timezone.utc):
        raise PairingTransportError("pairing_expired", "Group Bridge pairing invitation expired")


def create_pairing_connection(
    *,
    group_id: str,
    local_endpoint: str,
    remote_group_id: str = "",
    remote_peer_id: str = "",
    multiaddrs: Optional[list[str]] = None,
    ttl_seconds: int = 600,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    endpoint = canonical_receive_endpoint(local_endpoint)
    invite = create_pairing_invite(
        group_id=group_id,
        remote_group_id=remote_group_id,
        remote_peer_id=remote_peer_id,
        multiaddrs=multiaddrs or [],
        ttl_seconds=ttl_seconds,
        home=home,
    )
    identity = get_group_bridge_identity(home=home)
    unsigned = {
        "version": 1,
        "kind": "pairing_connection",
        "transport": "group_bridge_session",
        "invite_id": invite["invite_id"],
        "pairing_code": invite["pairing_code"],
        "issuer_group_id": group_id,
        "issuer_peer_id": identity.peer_id,
        "issuer_public_key": identity.public_key_b64,
        "issuer_endpoint": endpoint,
        "issued_at": utc_now_iso(),
        "expires_at": invite["expires_at"],
    }
    signature = sign_group_bridge_payload(canonical_payload_bytes(unsigned), home=home)
    return GroupBridgePairingConnectionEnvelope.model_validate({**unsigned, "signature": signature}).model_dump()


def _connection(value: Any) -> GroupBridgePairingConnectionEnvelope:
    try:
        connection = GroupBridgePairingConnectionEnvelope.model_validate(copy.deepcopy(value))
    except Exception:
        raise PairingTransportError("invalid_connection", "Group Bridge pairing connection is invalid") from None
    canonical_receive_endpoint(connection.issuer_endpoint)
    issued = parse_utc_iso(connection.issued_at)
    expires = parse_utc_iso(connection.expires_at)
    now = datetime.now(timezone.utc)
    if issued is None or expires is None or issued >= expires or issued > now + timedelta(seconds=60):
        raise PairingTransportError("invalid_connection", "Group Bridge pairing connection is stale")
    _verify_signature(connection, public_key=connection.issuer_public_key, peer_id=connection.issuer_peer_id)
    return connection


def _secret_bundle(pairing_code: str, client_nonce: str) -> str:
    return json.dumps(
        {"client_nonce": client_nonce, "pairing_code": pairing_code},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _client_nonce(*, pairing_code: str, local_group_id: str, requester_peer_id: str) -> str:
    digest = hashlib.sha256(
        "|".join(("pairing-request", pairing_code, local_group_id, requester_peer_id)).encode("utf-8")
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _decode_secret(value: str) -> tuple[str, str]:
    try:
        raw = json.loads(value)
    except Exception:
        raise PairingTransportError("pairing_state_corrupt", "Group Bridge pairing secret is unavailable") from None
    if not isinstance(raw, dict) or set(raw) != {"client_nonce", "pairing_code"}:
        raise PairingTransportError("pairing_state_corrupt", "Group Bridge pairing secret is unavailable")
    code = raw.get("pairing_code")
    nonce = raw.get("client_nonce")
    if not isinstance(code, str) or not isinstance(nonce, str):
        raise PairingTransportError("pairing_state_corrupt", "Group Bridge pairing secret is unavailable")
    return code, nonce


def _request_from_outbound(entry: Dict[str, Any], *, home: Optional[Path]) -> GroupBridgePairingRequestEnvelope:
    secret = resolve_group_bridge_credential(
        entry["credential_ref"],
        expected_local_group_id=entry["local_group_id"],
        expected_remote_group_id=entry["issuer_group_id"],
        expected_remote_endpoint=entry["issuer_endpoint"],
        home=home,
    )
    pairing_code, client_nonce = _decode_secret(secret)
    identity = get_group_bridge_identity(home=home)
    unsigned = {
        "version": 1,
        "kind": "pairing_request",
        "transport": "group_bridge_session",
        "invite_id": entry["invite_id"],
        "pairing_code": pairing_code,
        "client_nonce": client_nonce,
        "expires_at": entry["expires_at"],
        "issuer_group_id": entry["issuer_group_id"],
        "issuer_peer_id": entry["issuer_peer_id"],
        "issuer_public_key": entry["issuer_public_key"],
        "issuer_endpoint": entry["issuer_endpoint"],
        "requester_group_id": entry["local_group_id"],
        "requester_group_title": entry["local_group_title"],
        "requester_peer_id": identity.peer_id,
        "requester_public_key": identity.public_key_b64,
        "requester_endpoint": entry["requester_endpoint"],
    }
    signature = sign_group_bridge_payload(canonical_payload_bytes(unsigned), home=home)
    return GroupBridgePairingRequestEnvelope.model_validate({**unsigned, "signature": signature})


def _post(endpoint: str, request: GroupBridgePairingRequestEnvelope, http_post: Optional[HttpPost]) -> Any:
    try:
        return (http_post or _default_http_post)(endpoint, request.model_dump())
    except GroupBridgeSessionError as exc:
        raise PairingTransportError(exc.code, "Group Bridge pairing transport failed", retriable=exc.retriable) from exc
    except PairingTransportError:
        raise
    except Exception:
        raise PairingTransportError(
            "transport_error", "Group Bridge pairing transport failed", retriable=True
        ) from None


def _status(value: Any) -> GroupBridgePairingStatusEnvelope:
    try:
        return GroupBridgePairingStatusEnvelope.model_validate(copy.deepcopy(value))
    except Exception:
        raise PairingTransportError(
            "invalid_response", "Group Bridge pairing response is invalid", retriable=True
        ) from None


def _apply_status(
    entry: Dict[str, Any],
    raw_status: Any,
    *,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path],
) -> Dict[str, Any]:
    status = _status(raw_status)
    request = _request_from_outbound(entry, home=home)
    expected = (
        status.invite_id,
        status.request_fingerprint,
        status.expires_at,
        status.issuer_group_id,
        status.issuer_peer_id,
        status.issuer_public_key,
        status.issuer_endpoint,
        status.requester_group_id,
        status.requester_peer_id,
        status.requester_endpoint,
    )
    actual = (
        entry["invite_id"],
        _fingerprint(request),
        entry["expires_at"],
        entry["issuer_group_id"],
        entry["issuer_peer_id"],
        entry["issuer_public_key"],
        entry["issuer_endpoint"],
        entry["local_group_id"],
        request.requester_peer_id,
        entry["requester_endpoint"],
    )
    if expected != actual or not _is_fresh(status.issued_at):
        raise PairingTransportError(
            "invalid_response", "Group Bridge pairing response identity is invalid", retriable=True
        )
    _verify_signature(status, public_key=entry["issuer_public_key"], peer_id=entry["issuer_peer_id"])
    registration_id = trust_id = ""
    if status.status == "approved":
        installed = install_remote_pairing_approval(
            local_group_id=entry["local_group_id"],
            remote_group_id=entry["issuer_group_id"],
            remote_group_title="",
            remote_endpoint=entry["issuer_endpoint"],
            remote_peer_id=entry["issuer_peer_id"],
            remote_request_id=status.request_id,
            client_nonce_hash=entry["client_nonce_hash"],
            claim=claim,
            home=home,
        )
        registration_id = str(installed["registration"]["registration_id"])
        trust_id = str(installed["trust"]["trust_id"])
    response_fingerprint = _status_fingerprint(status)
    updated = update_pairing_outbound(
        entry["outbound_id"],
        expected_revision=entry["revision"],
        home=home,
        request_id=status.request_id,
        status=status.status,
        response_fingerprint=response_fingerprint,
        registration_id=registration_id,
        trust_id=trust_id,
        last_error_code="",
    )
    if updated is None:
        current = get_pairing_outbound(entry["outbound_id"], home=home, strict=True)
        if current is None or current.get("response_fingerprint") != response_fingerprint:
            raise PairingOutboundConflictError("pairing outbound changed during status commit")
        updated = current
    _cleanup_terminal_credential(updated, home=home)
    return public_pairing_outbound(updated)


def _post_and_apply(
    entry: Dict[str, Any],
    *,
    path: str,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path],
    http_post: Optional[HttpPost],
) -> Dict[str, Any]:
    request = _request_from_outbound(entry, home=home)
    try:
        response = _post(_pairing_endpoint(entry["issuer_endpoint"], path), request, http_post)
    except PairingTransportError as exc:
        update_pairing_outbound(
            entry["outbound_id"],
            expected_revision=entry["revision"],
            home=home,
            status="retrying",
            last_error_code=exc.code,
        )
        raise
    return _apply_status(entry, response, claim=claim, home=home)


def submit_remote_pairing(
    connection_value: Any,
    *,
    local_group_id: str,
    local_group_title: str,
    requester_endpoint: str,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path] = None,
    http_post: Optional[HttpPost] = None,
) -> Dict[str, Any]:
    connection = _connection(connection_value)
    local_endpoint = canonical_receive_endpoint(requester_endpoint)
    outbound_id = pairing_outbound_id(
        local_group_id=local_group_id,
        invite_id=connection.invite_id,
        issuer_peer_id=connection.issuer_peer_id,
    )
    entry = get_pairing_outbound(outbound_id, home=home, strict=True)
    if entry is None:
        _not_expired(connection.expires_at)
        identity = get_group_bridge_identity(home=home)
        nonce = _client_nonce(
            pairing_code=connection.pairing_code,
            local_group_id=local_group_id,
            requester_peer_id=identity.peer_id,
        )
        secret_ref = save_pairing_bearer_token(
            local_group_id=local_group_id,
            remote_group_id=connection.issuer_group_id,
            remote_endpoint=connection.issuer_endpoint,
            token=_secret_bundle(connection.pairing_code, nonce),
            home=home,
        )
        entry = reserve_pairing_outbound(
            {
                "local_group_id": local_group_id,
                "local_group_title": local_group_title,
                "issuer_group_id": connection.issuer_group_id,
                "issuer_peer_id": connection.issuer_peer_id,
                "issuer_public_key": connection.issuer_public_key,
                "issuer_endpoint": connection.issuer_endpoint,
                "invite_id": connection.invite_id,
                "expires_at": connection.expires_at,
                "requester_endpoint": local_endpoint,
                "client_nonce_hash": hashlib.sha256(nonce.encode("ascii")).hexdigest(),
                "pairing_code_hash": hashlib.sha256(connection.pairing_code.encode("ascii")).hexdigest(),
                "credential_ref": secret_ref,
            },
            home=home,
        )
    else:
        expected = (
            entry["local_group_id"],
            entry["local_group_title"],
            entry["issuer_group_id"],
            entry["issuer_peer_id"],
            entry["issuer_public_key"],
            entry["issuer_endpoint"],
            entry["invite_id"],
            entry["expires_at"],
            entry["requester_endpoint"],
            entry["pairing_code_hash"],
        )
        actual = (
            local_group_id,
            local_group_title,
            connection.issuer_group_id,
            connection.issuer_peer_id,
            connection.issuer_public_key,
            connection.issuer_endpoint,
            connection.invite_id,
            connection.expires_at,
            local_endpoint,
            hashlib.sha256(connection.pairing_code.encode("ascii")).hexdigest(),
        )
        if expected != actual:
            raise PairingOutboundConflictError("pairing connection conflicts with its original outbound")
        if entry["status"] in _TERMINAL_OUTBOUND_STATUSES:
            _cleanup_terminal_credential(entry, home=home)
            return public_pairing_outbound(entry)
    return _post_and_apply(
        entry,
        path=_PAIRING_REQUEST_PATH,
        claim=claim,
        home=home,
        http_post=http_post,
    )


def sync_remote_pairing(
    outbound_id: str,
    *,
    local_group_id: str,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path] = None,
    http_post: Optional[HttpPost] = None,
) -> Dict[str, Any]:
    entry = get_pairing_outbound(outbound_id, home=home, strict=True)
    if entry is None:
        raise PairingTransportError("not_found", "Group Bridge pairing outbound was not found")
    if entry["local_group_id"] != local_group_id:
        raise PairingTransportError("permission_denied", "Group Bridge pairing outbound is not authorized")
    if entry["status"] in _TERMINAL_OUTBOUND_STATUSES:
        _cleanup_terminal_credential(entry, home=home)
        return public_pairing_outbound(entry)
    path = _PAIRING_STATUS_PATH if entry["request_id"] else _PAIRING_REQUEST_PATH
    return _post_and_apply(entry, path=path, claim=claim, home=home, http_post=http_post)


def _validate_remote_request(
    value: Any,
    *,
    local_endpoint: str,
    home: Optional[Path],
    allow_expired: bool = False,
) -> GroupBridgePairingRequestEnvelope:
    try:
        request = GroupBridgePairingRequestEnvelope.model_validate(copy.deepcopy(value))
    except Exception:
        raise PairingTransportError("invalid_request", "Group Bridge pairing request is invalid") from None
    identity = get_group_bridge_identity(home=home)
    endpoint = canonical_receive_endpoint(local_endpoint)
    canonical_receive_endpoint(request.requester_endpoint)
    if not allow_expired:
        _not_expired(request.expires_at)
    if (
        request.issuer_peer_id != identity.peer_id
        or request.issuer_public_key != identity.public_key_b64
        or request.issuer_endpoint != endpoint
    ):
        raise PairingTransportError("invalid_request", "Group Bridge pairing request target is invalid")
    _verify_signature(request, public_key=request.requester_public_key, peer_id=request.requester_peer_id)
    return request


def _signed_status(
    request: GroupBridgePairingRequestEnvelope,
    stored: Dict[str, Any],
    *,
    home: Optional[Path],
) -> Dict[str, Any]:
    if stored.get("expires_at") != request.expires_at:
        raise PairingTransportError("pairing_rejected", "Group Bridge pairing request expiry is invalid")
    identity = get_group_bridge_identity(home=home)
    unsigned = {
        "version": 1,
        "kind": "pairing_status",
        "transport": "group_bridge_session",
        "request_id": stored["request_id"],
        "invite_id": request.invite_id,
        "request_fingerprint": _fingerprint(request),
        "status": stored["status"],
        "issued_at": utc_now_iso(),
        "expires_at": request.expires_at,
        "issuer_group_id": stored["group_id"],
        "issuer_peer_id": identity.peer_id,
        "issuer_public_key": identity.public_key_b64,
        "issuer_endpoint": request.issuer_endpoint,
        "requester_group_id": request.requester_group_id,
        "requester_peer_id": request.requester_peer_id,
        "requester_endpoint": request.requester_endpoint,
    }
    signature = sign_group_bridge_payload(canonical_payload_bytes(unsigned), home=home)
    return GroupBridgePairingStatusEnvelope.model_validate({**unsigned, "signature": signature}).model_dump()


def receive_remote_pairing_request(
    value: Any,
    *,
    local_endpoint: str,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    request = _validate_remote_request(value, local_endpoint=local_endpoint, home=home)
    try:
        stored = create_pairing_request(
            request.pairing_code,
            client_nonce=request.client_nonce,
            requester_group_id=request.requester_group_id,
            requester_group_title=request.requester_group_title,
            requester_peer_id=request.requester_peer_id,
            requester_endpoint=request.requester_endpoint,
            invite_id=request.invite_id,
            home=home,
        )
    except ValueError as exc:
        raise PairingTransportError("pairing_rejected", "Group Bridge pairing request was rejected") from exc
    return _signed_status(request, stored, home=home)


def remote_pairing_status(
    value: Any,
    *,
    local_endpoint: str,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    request = _validate_remote_request(
        value,
        local_endpoint=local_endpoint,
        home=home,
        allow_expired=True,
    )
    try:
        replay = create_pairing_request(
            request.pairing_code,
            client_nonce=request.client_nonce,
            requester_group_id=request.requester_group_id,
            requester_group_title=request.requester_group_title,
            requester_peer_id=request.requester_peer_id,
            requester_endpoint=request.requester_endpoint,
            invite_id=request.invite_id,
            home=home,
        )
    except ValueError as exc:
        raise PairingTransportError("pairing_rejected", "Group Bridge pairing status proof was rejected") from exc
    stored = get_pairing_request(replay["request_id"], home=home)
    if stored is None:
        raise PairingTransportError("pairing_state_corrupt", "Group Bridge pairing request is unavailable")
    return _signed_status(request, stored, home=home)


__all__ = [
    "PairingTransportError",
    "canonical_receive_endpoint",
    "create_pairing_connection",
    "receive_remote_pairing_request",
    "remote_pairing_status",
    "submit_remote_pairing",
    "sync_remote_pairing",
]
