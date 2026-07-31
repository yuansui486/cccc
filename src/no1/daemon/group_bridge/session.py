"""Signed HTTP session kernel for Group Bridge message delivery.

Crash prefixes are deliberately explicit. A request is persisted as ``reserved``
before signing or external work. An attempt then receives a short persisted lease
and the store lock is released before identity signing, HTTP I/O, or local
delivery. A crash before the side effect leaves a retryable reservation. A crash
after the side effect reuses the same nonce and fingerprint; the receiving side
must return its persisted terminal receipt, and local delivery must use the
deterministic delivery id. Terminal receipts are replayed only after current B1
authorization is checked again.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib import parse as urlparse

import httpx

from ...contracts.v1.group_bridge import (
    GroupBridgeSessionMessage,
    GroupBridgeSignedMessageEnvelope,
    GroupBridgeSignedReceiptEnvelope,
)
from ...kernel.group_bridge.pairing import authorize_remote_principal
from ...kernel.group_bridge.peer_addresses import resolve_peer_multiaddrs
from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_text
from .identity import (
    canonical_payload_bytes,
    get_group_bridge_identity,
    sign_group_bridge_payload,
    verify_group_bridge_signature,
)

_TRANSPORT = "group_bridge_session"
_STORE_VERSION = 1
_NONCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_HEX_PATTERN = re.compile(r"[0-9a-f]{64}")
_DNS_HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*"
)
_ENTRY_FIELDS = frozenset(
    {
        "direction",
        "nonce_hash",
        "request_fingerprint",
        "status",
        "attempt",
        "revision",
        "lease_expires_at",
        "remote_event_id",
        "error_code",
        "created_at",
        "updated_at",
    }
)
_OUTBOUND_STATUSES = frozenset({"reserved", "sending", "retrying", "accepted"})
_INBOUND_STATUSES = frozenset({"reserved", "delivering", "retrying", "accepted"})
_OUTBOUND_RETRY_ERRORS = frozenset({"invalid_response", "signing_failed", "transport_error"})
_INBOUND_RETRY_ERRORS = frozenset({"delivery_failed"})
_RETRY_ERRORS = _OUTBOUND_RETRY_ERRORS | _INBOUND_RETRY_ERRORS
_LEASE_SECONDS = 30
_MAX_PAST_SECONDS = 300
_MAX_FUTURE_SECONDS = 60
_MAX_RESPONSE_BYTES = 256_000
_HTTP_RAW_CHUNK_BYTES = 64 * 1024
_MAX_STORE_BYTES = 8 * 1024 * 1024
_MAX_ENTRIES_PER_DIRECTION = 1024
_MAX_ATTEMPTS = 1_000_000
_TERMINAL_RETENTION_SECONDS = 7 * 24 * 60 * 60
_INACTIVE_RETENTION_SECONDS = 24 * 60 * 60
_EXPIRED_LEASE_RETENTION_SECONDS = 60 * 60


class GroupBridgeSessionError(RuntimeError):
    """A fixed, non-secret session failure."""

    def __init__(self, code: str, message: str, *, retriable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retriable = retriable

    def public_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": str(self), "retriable": self.retriable}


class GroupBridgeSessionStoreError(GroupBridgeSessionError):
    def __init__(self) -> None:
        super().__init__("session_store_malformed", "Group Bridge session store is malformed")


def _error(code: str, message: str, *, retriable: bool = False) -> GroupBridgeSessionError:
    return GroupBridgeSessionError(code, message, retriable=retriable)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Optional[datetime] = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value or value != value.strip() or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    parsed = parsed.astimezone(timezone.utc)
    return parsed if _timestamp(parsed) == value else None


def _is_fresh(value: str, *, now: Optional[datetime] = None) -> bool:
    parsed = _parse_timestamp(value)
    current = (now or _now()).astimezone(timezone.utc)
    if parsed is None:
        return False
    delta = (current - parsed).total_seconds()
    return -_MAX_FUTURE_SECONDS <= delta <= _MAX_PAST_SECONDS


def _identity(value: Any, *, field: str, max_length: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_length
    ):
        raise _error("invalid_request", f"{field} must be a trimmed non-empty string")
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
        raise _error("invalid_request", f"{field} contains unsupported characters")
    return value


def _resolved_home(home: Optional[Path]) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base.expanduser().resolve(strict=False)


def _canonical_http_endpoint(value: Any, *, optional: bool = False) -> str:
    if optional and isinstance(value, str) and value == "":
        return ""
    endpoint = _identity(value, field="endpoint", max_length=2048)
    try:
        parts = urlparse.urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise _error("invalid_request", "endpoint must be a canonical HTTP(S) URL") from None
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.netloc.endswith(":")
    ):
        raise _error("invalid_request", "endpoint must be a canonical HTTP(S) URL")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    bracketed = parts.netloc.startswith("[")
    if "%" in host or (bracketed and ":" not in host):
        raise _error("invalid_request", "endpoint must be a canonical HTTP(S) URL")
    if ":" in host:
        try:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        except ValueError:
            raise _error("invalid_request", "endpoint must be a canonical HTTP(S) URL") from None
    elif not _DNS_HOST_PATTERN.fullmatch(host):
        raise _error("invalid_request", "endpoint must be a canonical HTTP(S) URL")
    if port == {"http": 80, "https": 443}[scheme]:
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = (parts.path or "").rstrip("/")
    canonical = f"{scheme}://{netloc}{path}"
    if endpoint != canonical:
        raise _error("invalid_request", "endpoint must use its canonical representation")
    return canonical


def _nonce(value: Any) -> str:
    if not isinstance(value, str) or _NONCE_PATTERN.fullmatch(value) is None:
        raise _error("invalid_request", "session nonce must be a high-entropy URL-safe value")
    return value


def new_group_bridge_session_nonce() -> str:
    return secrets.token_urlsafe(32)


def _nonce_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def session_reservation_path(*, home: Optional[Path] = None) -> Path:
    return _resolved_home(home) / "state" / "group_bridge" / "session_reservations.json"


def _lock_path(home: Optional[Path]) -> Path:
    return session_reservation_path(home=home).with_suffix(".json.lock")


def _empty_store() -> Dict[str, Any]:
    return {"version": _STORE_VERSION, "outbound": {}, "inbound": {}}


def _unique_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise GroupBridgeSessionStoreError()
        result[key] = value
    return result


def _validate_entry(key: str, entry: Any, *, direction: str) -> Dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
        raise GroupBridgeSessionStoreError()
    if entry.get("direction") != direction or entry.get("nonce_hash") != key or _HEX_PATTERN.fullmatch(key) is None:
        raise GroupBridgeSessionStoreError()
    fingerprint = entry.get("request_fingerprint")
    status = entry.get("status")
    allowed_statuses = _OUTBOUND_STATUSES if direction == "outbound" else _INBOUND_STATUSES
    if (
        not isinstance(fingerprint, str)
        or _HEX_PATTERN.fullmatch(fingerprint) is None
        or status not in allowed_statuses
    ):
        raise GroupBridgeSessionStoreError()
    attempt = entry.get("attempt")
    revision = entry.get("revision")
    if (
        isinstance(attempt, bool)
        or not isinstance(attempt, int)
        or attempt < 0
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or attempt > _MAX_ATTEMPTS
        or revision > _MAX_ATTEMPTS
        or attempt != revision
    ):
        raise GroupBridgeSessionStoreError()
    created = _parse_timestamp(entry.get("created_at"))
    updated = _parse_timestamp(entry.get("updated_at"))
    lease = entry.get("lease_expires_at")
    event_id = entry.get("remote_event_id")
    error_code = entry.get("error_code")
    if created is None or updated is None or updated < created:
        raise GroupBridgeSessionStoreError()
    if not all(isinstance(value, str) for value in (lease, event_id, error_code)):
        raise GroupBridgeSessionStoreError()
    active_status = "sending" if direction == "outbound" else "delivering"
    if status == active_status:
        lease_at = _parse_timestamp(lease)
        if (
            lease_at is None
            or lease_at <= updated
            or lease_at - updated != timedelta(seconds=_LEASE_SECONDS)
            or event_id
            or error_code
            or attempt < 1
        ):
            raise GroupBridgeSessionStoreError()
    elif status == "retrying":
        allowed_errors = _OUTBOUND_RETRY_ERRORS if direction == "outbound" else _INBOUND_RETRY_ERRORS
        if lease or event_id or error_code not in allowed_errors or attempt < 1:
            raise GroupBridgeSessionStoreError()
    elif status == "accepted":
        if (
            lease
            or error_code
            or attempt < 1
            or not event_id
            or len(event_id) > 512
            or event_id != event_id.strip()
            or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in event_id)
        ):
            raise GroupBridgeSessionStoreError()
    elif status == "reserved":
        if attempt != 0 or revision != 0 or lease or event_id or error_code or created != updated:
            raise GroupBridgeSessionStoreError()
    else:
        raise GroupBridgeSessionStoreError()
    return copy.deepcopy(entry)


def _validate_store(raw: Any) -> Dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or set(raw) != {"version", "outbound", "inbound"}
        or isinstance(raw.get("version"), bool)
        or not isinstance(raw.get("version"), int)
        or raw.get("version") != _STORE_VERSION
    ):
        raise GroupBridgeSessionStoreError()
    normalized = _empty_store()
    for direction in ("outbound", "inbound"):
        entries = raw.get(direction)
        if not isinstance(entries, dict) or len(entries) > _MAX_ENTRIES_PER_DIRECTION:
            raise GroupBridgeSessionStoreError()
        normalized[direction] = {
            key: _validate_entry(key, entry, direction=direction) for key, entry in entries.items()
        }
    return normalized


def _load_store(home: Optional[Path]) -> Dict[str, Any]:
    path = session_reservation_path(home=home)
    if not path.exists():
        return _empty_store()
    try:
        with path.open("rb") as handle:
            encoded = handle.read(_MAX_STORE_BYTES + 1)
        if len(encoded) > _MAX_STORE_BYTES:
            raise GroupBridgeSessionStoreError()
        raw = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
        return _validate_store(raw)
    except GroupBridgeSessionStoreError:
        raise
    except Exception:
        raise GroupBridgeSessionStoreError() from None


def _save_store(store: Dict[str, Any], home: Optional[Path]) -> None:
    validated = _validate_store(store)
    encoded = json.dumps(validated, ensure_ascii=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > _MAX_STORE_BYTES:
        raise _error("session_capacity", "Group Bridge session capacity is exhausted", retriable=True)
    atomic_write_text(session_reservation_path(home=home), encoded)


def load_session_reservations(*, home: Optional[Path] = None) -> Dict[str, Any]:
    return copy.deepcopy(_load_store(home))


def _elapsed_at_least(*, anchor: datetime, now: datetime, seconds: int) -> bool:
    if now < anchor:
        return False
    return now - anchor >= timedelta(seconds=seconds)


def _active_lease_expiration(updated: datetime) -> datetime:
    try:
        return updated + timedelta(seconds=_LEASE_SECONDS)
    except OverflowError:
        raise GroupBridgeSessionStoreError() from None


def _reclaimable(entry: Dict[str, Any], *, now: datetime) -> bool:
    status = entry["status"]
    updated = _parse_timestamp(entry["updated_at"])
    if updated is None:
        raise GroupBridgeSessionStoreError()
    if status == "accepted":
        return _elapsed_at_least(anchor=updated, now=now, seconds=_TERMINAL_RETENTION_SECONDS)
    if status in {"reserved", "retrying"}:
        return _elapsed_at_least(anchor=updated, now=now, seconds=_INACTIVE_RETENTION_SECONDS)
    lease = _parse_timestamp(entry["lease_expires_at"])
    if lease is None:
        raise GroupBridgeSessionStoreError()
    return _elapsed_at_least(anchor=lease, now=now, seconds=_EXPIRED_LEASE_RETENTION_SECONDS)


def _reclaim_for_capacity(store: Dict[str, Any], *, direction: str, now: datetime) -> None:
    entries = store[direction]
    if len(entries) < _MAX_ENTRIES_PER_DIRECTION:
        return
    candidates = sorted(
        (
            (_parse_timestamp(entry["updated_at"]), key)
            for key, entry in entries.items()
            if _reclaimable(entry, now=now)
        ),
        key=lambda item: (item[0], item[1]),
    )
    for _updated, key in candidates:
        entries.pop(key)
        if len(entries) < _MAX_ENTRIES_PER_DIRECTION:
            return


def _new_entry(*, direction: str, nonce_hash: str, fingerprint: str) -> Dict[str, Any]:
    now = _timestamp()
    return {
        "direction": direction,
        "nonce_hash": nonce_hash,
        "request_fingerprint": fingerprint,
        "status": "reserved",
        "attempt": 0,
        "revision": 0,
        "lease_expires_at": "",
        "remote_event_id": "",
        "error_code": "",
        "created_at": now,
        "updated_at": now,
    }


def _reserve(
    *,
    direction: str,
    nonce_hash: str,
    fingerprint: str,
    home: Optional[Path],
    fresh: bool = True,
) -> Dict[str, Any]:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home)
        existing = store[direction].get(nonce_hash)
        if existing is not None:
            if not hmac.compare_digest(existing["request_fingerprint"], fingerprint):
                raise _error("nonce_conflict", "session nonce conflicts with the original request")
            if existing["status"] != "accepted" and not fresh:
                raise _error("stale_request", "signed session request is outside the freshness window")
            return copy.deepcopy(existing)
        if not fresh:
            raise _error("stale_request", "signed session request is outside the freshness window")
        _reclaim_for_capacity(store, direction=direction, now=_now())
        store = _validate_store(store)
        if len(store[direction]) >= _MAX_ENTRIES_PER_DIRECTION:
            raise _error("session_capacity", "Group Bridge session capacity is exhausted", retriable=True)
        entry = _new_entry(direction=direction, nonce_hash=nonce_hash, fingerprint=fingerprint)
        store[direction][nonce_hash] = entry
        _save_store(store, home)
        return copy.deepcopy(entry)
    finally:
        release_lockfile(lock)


def _claim_attempt(*, direction: str, nonce_hash: str, home: Optional[Path]) -> tuple[Dict[str, Any], int]:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home)
        entry = store[direction].get(nonce_hash)
        if entry is None:
            raise GroupBridgeSessionStoreError()
        if entry["status"] == "accepted":
            return copy.deepcopy(entry), entry["revision"]
        active_status = "sending" if direction == "outbound" else "delivering"
        now = _now()
        if entry["status"] == active_status:
            lease = _parse_timestamp(entry["lease_expires_at"])
            if lease is not None and lease > now:
                raise _error("session_in_progress", "session request is already in progress", retriable=True)
        if entry["attempt"] >= _MAX_ATTEMPTS:
            raise _error("session_capacity", "Group Bridge session capacity is exhausted", retriable=True)
        candidate = copy.deepcopy(store)
        claimed = candidate[direction][nonce_hash]
        claimed["status"] = active_status
        claimed["attempt"] += 1
        claimed["revision"] += 1
        claimed["updated_at"] = _timestamp(now)
        claimed["lease_expires_at"] = _timestamp(_active_lease_expiration(now))
        claimed["remote_event_id"] = ""
        claimed["error_code"] = ""
        _save_store(candidate, home)
        return copy.deepcopy(claimed), claimed["revision"]
    finally:
        release_lockfile(lock)


def _finish_attempt(
    *,
    direction: str,
    nonce_hash: str,
    revision: int,
    home: Optional[Path],
    accepted_event_id: str = "",
    error_code: str = "",
) -> Dict[str, Any]:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home)
        entry = store[direction].get(nonce_hash)
        if entry is None:
            raise GroupBridgeSessionStoreError()
        if entry["status"] == "accepted":
            return copy.deepcopy(entry)
        active_status = "sending" if direction == "outbound" else "delivering"
        if entry["revision"] != revision or entry["status"] != active_status:
            raise _error("session_in_progress", "a newer session attempt is in progress", retriable=True)
        candidate = copy.deepcopy(store)
        finished = candidate[direction][nonce_hash]
        finished["updated_at"] = _timestamp()
        finished["lease_expires_at"] = ""
        if accepted_event_id:
            event_id = _identity(accepted_event_id, field="remote_event_id", max_length=512)
            finished["status"] = "accepted"
            finished["remote_event_id"] = event_id
            finished["error_code"] = ""
        else:
            allowed_errors = _OUTBOUND_RETRY_ERRORS if direction == "outbound" else _INBOUND_RETRY_ERRORS
            if error_code not in allowed_errors:
                raise ValueError("unsupported session error code")
            finished["status"] = "retrying"
            finished["remote_event_id"] = ""
            finished["error_code"] = error_code
        _save_store(candidate, home)
        return copy.deepcopy(finished)
    finally:
        release_lockfile(lock)


def _unsigned_message(envelope: GroupBridgeSignedMessageEnvelope) -> Dict[str, Any]:
    return envelope.model_dump(exclude={"signature"})


def _unsigned_receipt(envelope: GroupBridgeSignedReceiptEnvelope) -> Dict[str, Any]:
    return envelope.model_dump(exclude={"signature"})


def _request_fingerprint(envelope: GroupBridgeSignedMessageEnvelope) -> str:
    facts = _unsigned_message(envelope)
    facts.pop("issued_at", None)
    facts["nonce_hash"] = _nonce_hash(facts.pop("nonce"))
    return hashlib.sha256(canonical_payload_bytes(facts)).hexdigest()


def _safe_result(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": entry["status"],
        "request_fingerprint": entry["request_fingerprint"],
        "remote_event_id": entry["remote_event_id"] or None,
        "attempt": entry["attempt"],
    }


def _authorize(
    *,
    group_id: str,
    remote_endpoint: str,
    remote_group_id: str,
    remote_peer_id: str,
    home: Optional[Path],
) -> Dict[str, Any]:
    try:
        trust = authorize_remote_principal(
            group_id=group_id,
            transport=_TRANSPORT,
            remote_endpoint=remote_endpoint,
            remote_group_id=remote_group_id,
            remote_peer_id=remote_peer_id,
            required_access_level="messages",
            home=home,
        )
    except (TypeError, ValueError):
        raise _error("invalid_request", "remote principal is malformed") from None
    if trust is None:
        raise _error("unauthorized", "remote principal is not authorized")
    if trust.get("remote_endpoint") != remote_endpoint:
        raise _error("unauthorized", "remote principal endpoint is not exact")
    resolve_peer_multiaddrs(remote_peer_id, remote_group_id=remote_group_id, home=home)
    return trust


def _strict_response(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, bytes):
        if len(value) > _MAX_RESPONSE_BYTES:
            raise _error("invalid_response", "remote response is invalid", retriable=True)
        try:
            raw = value.decode("utf-8")
        except UnicodeDecodeError:
            raise _error("invalid_response", "remote response is invalid", retriable=True) from None
    elif isinstance(value, str):
        try:
            encoded_length = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise _error("invalid_response", "remote response is invalid", retriable=True) from None
        if encoded_length > _MAX_RESPONSE_BYTES:
            raise _error("invalid_response", "remote response is invalid", retriable=True)
        raw = value
    else:
        raise _error("invalid_response", "remote response is invalid", retriable=True)
    try:
        parsed = json.loads(raw, object_pairs_hook=_unique_object)
    except Exception:
        raise _error("invalid_response", "remote response is invalid", retriable=True) from None
    if not isinstance(parsed, dict):
        raise _error("invalid_response", "remote response is invalid", retriable=True)
    return parsed


def _default_http_post(endpoint: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with httpx.stream(
            "POST",
            endpoint,
            json=body,
            headers={"Accept-Encoding": "identity"},
            timeout=15.0,
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise _error("transport_error", "remote session transport failed", retriable=True)
            content_encoding = response.headers.get("content-encoding")
            if content_encoding and content_encoding != "identity":
                raise _error("invalid_response", "remote response is invalid", retriable=True)
            content_length = response.headers.get("content-length")
            if content_length is not None:
                if len(content_length) > 20 or not content_length.isascii() or not content_length.isdigit():
                    raise _error("invalid_response", "remote response is invalid", retriable=True)
                if int(content_length) > _MAX_RESPONSE_BYTES:
                    raise _error("invalid_response", "remote response is too large", retriable=True)
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_raw(chunk_size=_HTTP_RAW_CHUNK_BYTES):
                if not isinstance(chunk, bytes):
                    raise _error("invalid_response", "remote response is invalid", retriable=True)
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise _error("invalid_response", "remote response is too large", retriable=True)
                chunks.append(chunk)
            return _strict_response(b"".join(chunks))
    except GroupBridgeSessionError:
        raise
    except httpx.HTTPError:
        raise _error("transport_error", "remote session transport failed", retriable=True) from None


def _signed_message(
    *,
    nonce: str,
    source_group_id: str,
    source_endpoint: str,
    target_group_id: str,
    target_peer_id: str,
    target_endpoint: str,
    payload: GroupBridgeSessionMessage,
    home: Optional[Path],
) -> GroupBridgeSignedMessageEnvelope:
    identity = get_group_bridge_identity(home=home)
    unsigned = {
        "version": 1,
        "kind": "message",
        "transport": _TRANSPORT,
        "nonce": nonce,
        "issued_at": _timestamp(),
        "source_group_id": source_group_id,
        "source_peer_id": identity.peer_id,
        "source_public_key": identity.public_key_b64,
        "source_endpoint": source_endpoint,
        "target_group_id": target_group_id,
        "target_peer_id": target_peer_id,
        "target_endpoint": target_endpoint,
        "payload": payload.model_dump(),
    }
    signature = sign_group_bridge_payload(canonical_payload_bytes(unsigned), home=home)
    return GroupBridgeSignedMessageEnvelope.model_validate({**unsigned, "signature": signature})


def _signed_receipt(
    *,
    request: GroupBridgeSignedMessageEnvelope,
    fingerprint: str,
    remote_event_id: str,
    home: Optional[Path],
) -> Dict[str, Any]:
    identity = get_group_bridge_identity(home=home)
    unsigned = {
        "version": 1,
        "kind": "receipt",
        "transport": _TRANSPORT,
        "request_nonce_hash": _nonce_hash(request.nonce),
        "request_fingerprint": fingerprint,
        "issued_at": _timestamp(),
        "source_group_id": request.target_group_id,
        "source_peer_id": identity.peer_id,
        "source_public_key": identity.public_key_b64,
        "source_endpoint": request.target_endpoint,
        "target_group_id": request.source_group_id,
        "target_peer_id": request.source_peer_id,
        "target_endpoint": request.source_endpoint,
        "status": "accepted",
        "remote_event_id": remote_event_id,
    }
    signature = sign_group_bridge_payload(canonical_payload_bytes(unsigned), home=home)
    return GroupBridgeSignedReceiptEnvelope.model_validate({**unsigned, "signature": signature}).model_dump()


def _verify_receipt(
    raw: Any,
    *,
    request: GroupBridgeSignedMessageEnvelope,
    fingerprint: str,
) -> GroupBridgeSignedReceiptEnvelope:
    try:
        receipt = GroupBridgeSignedReceiptEnvelope.model_validate(_strict_response(raw))
    except GroupBridgeSessionError:
        raise
    except Exception:
        raise _error("invalid_response", "remote response is invalid", retriable=True) from None
    if (
        receipt.request_nonce_hash != _nonce_hash(request.nonce)
        or receipt.request_fingerprint != fingerprint
        or receipt.source_group_id != request.target_group_id
        or receipt.source_peer_id != request.target_peer_id
        or receipt.source_endpoint != request.target_endpoint
        or receipt.target_group_id != request.source_group_id
        or receipt.target_peer_id != request.source_peer_id
        or receipt.target_endpoint != request.source_endpoint
        or _HEX_PATTERN.fullmatch(receipt.request_nonce_hash) is None
        or _HEX_PATTERN.fullmatch(receipt.request_fingerprint) is None
        or not _is_fresh(receipt.issued_at)
    ):
        raise _error("invalid_response", "remote response identity is invalid", retriable=True)
    if not verify_group_bridge_signature(
        canonical_payload_bytes(_unsigned_receipt(receipt)),
        receipt.signature,
        public_key_b64=receipt.source_public_key,
        peer_id=receipt.source_peer_id,
    ):
        raise _error("invalid_response", "remote response signature is invalid", retriable=True)
    try:
        _identity(receipt.remote_event_id, field="remote_event_id", max_length=512)
    except GroupBridgeSessionError:
        raise _error("invalid_response", "remote response event identity is invalid", retriable=True) from None
    return receipt


def send_group_bridge_session_message(
    *,
    group_id: str,
    local_endpoint: str,
    remote_group_id: str,
    remote_peer_id: str,
    remote_endpoint: str,
    client_nonce: str,
    payload: Dict[str, Any] | GroupBridgeSessionMessage,
    home: Optional[Path] = None,
    http_post: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    canonical_home = _resolved_home(home)
    local_group = _identity(group_id, field="group_id")
    remote_group = _identity(remote_group_id, field="remote_group_id")
    remote_peer = _identity(remote_peer_id, field="remote_peer_id")
    local_url = _canonical_http_endpoint(local_endpoint)
    remote_url = _canonical_http_endpoint(remote_endpoint, optional=True)
    nonce = _nonce(client_nonce)
    try:
        raw_payload = copy.deepcopy(vars(payload)) if isinstance(payload, GroupBridgeSessionMessage) else payload
        message = GroupBridgeSessionMessage.model_validate(raw_payload)
    except Exception:
        raise _error("invalid_request", "session message payload is invalid") from None

    _authorize(
        group_id=local_group,
        remote_endpoint=remote_url,
        remote_group_id=remote_group,
        remote_peer_id=remote_peer,
        home=canonical_home,
    )
    if not remote_url:
        raise _error("transport_unavailable", "endpointless Group Bridge transport is unavailable")

    identity = get_group_bridge_identity(home=canonical_home)
    seed = GroupBridgeSignedMessageEnvelope(
        nonce=nonce,
        issued_at=_timestamp(),
        source_group_id=local_group,
        source_peer_id=identity.peer_id,
        source_public_key=identity.public_key_b64,
        source_endpoint=local_url,
        target_group_id=remote_group,
        target_peer_id=remote_peer,
        target_endpoint=remote_url,
        payload=message,
        signature=("A" * 86) + "==",
    )
    fingerprint = _request_fingerprint(seed)
    hashed_nonce = _nonce_hash(nonce)
    existing = _reserve(
        direction="outbound",
        nonce_hash=hashed_nonce,
        fingerprint=fingerprint,
        home=canonical_home,
    )
    if existing["status"] == "accepted":
        return _safe_result(existing)
    claimed, revision = _claim_attempt(direction="outbound", nonce_hash=hashed_nonce, home=canonical_home)
    if claimed["status"] == "accepted":
        return _safe_result(claimed)
    try:
        envelope = _signed_message(
            nonce=nonce,
            source_group_id=local_group,
            source_endpoint=local_url,
            target_group_id=remote_group,
            target_peer_id=remote_peer,
            target_endpoint=remote_url,
            payload=message,
            home=canonical_home,
        )
    except Exception:
        _finish_attempt(
            direction="outbound",
            nonce_hash=hashed_nonce,
            revision=revision,
            home=canonical_home,
            error_code="signing_failed",
        )
        raise _error("signing_failed", "session request could not be signed", retriable=True) from None
    try:
        raw_response = (http_post or _default_http_post)(remote_url, copy.deepcopy(envelope.model_dump()))
        receipt = _verify_receipt(raw_response, request=envelope, fingerprint=fingerprint)
    except GroupBridgeSessionError as exc:
        code = exc.code if exc.code in _RETRY_ERRORS else "transport_error"
        _finish_attempt(
            direction="outbound",
            nonce_hash=hashed_nonce,
            revision=revision,
            home=canonical_home,
            error_code=code,
        )
        raise
    except Exception:
        _finish_attempt(
            direction="outbound",
            nonce_hash=hashed_nonce,
            revision=revision,
            home=canonical_home,
            error_code="transport_error",
        )
        raise _error("transport_error", "remote session transport failed", retriable=True) from None
    accepted = _finish_attempt(
        direction="outbound",
        nonce_hash=hashed_nonce,
        revision=revision,
        home=canonical_home,
        accepted_event_id=receipt.remote_event_id,
    )
    return _safe_result(accepted)


def receive_group_bridge_session_message(
    envelope: Any,
    *,
    group_id: str,
    local_endpoint: str,
    deliver: Callable[[Dict[str, Any], str], str],
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    canonical_home = _resolved_home(home)
    local_group = _identity(group_id, field="group_id")
    local_url = _canonical_http_endpoint(local_endpoint)
    try:
        request = GroupBridgeSignedMessageEnvelope.model_validate(_strict_response(envelope))
    except GroupBridgeSessionError:
        raise
    except Exception:
        raise _error("invalid_envelope", "signed session envelope is invalid") from None
    _nonce(request.nonce)
    source_url = _canonical_http_endpoint(request.source_endpoint)
    target_url = _canonical_http_endpoint(request.target_endpoint)
    local_identity = get_group_bridge_identity(home=canonical_home)
    if (
        request.target_group_id != local_group
        or request.target_peer_id != local_identity.peer_id
        or target_url != local_url
        or request.source_endpoint != source_url
        or request.target_endpoint != target_url
    ):
        raise _error("invalid_target", "signed session target is invalid")
    if not verify_group_bridge_signature(
        canonical_payload_bytes(_unsigned_message(request)),
        request.signature,
        public_key_b64=request.source_public_key,
        peer_id=request.source_peer_id,
    ):
        raise _error("invalid_signature", "signed session signature is invalid")
    _authorize(
        group_id=local_group,
        remote_endpoint=source_url,
        remote_group_id=request.source_group_id,
        remote_peer_id=request.source_peer_id,
        home=canonical_home,
    )
    fingerprint = _request_fingerprint(request)
    hashed_nonce = _nonce_hash(request.nonce)
    existing = _reserve(
        direction="inbound",
        nonce_hash=hashed_nonce,
        fingerprint=fingerprint,
        home=canonical_home,
        fresh=_is_fresh(request.issued_at),
    )
    if existing["status"] == "accepted":
        return _signed_receipt(
            request=request,
            fingerprint=fingerprint,
            remote_event_id=existing["remote_event_id"],
            home=canonical_home,
        )
    claimed, revision = _claim_attempt(direction="inbound", nonce_hash=hashed_nonce, home=canonical_home)
    if claimed["status"] == "accepted":
        return _signed_receipt(
            request=request,
            fingerprint=fingerprint,
            remote_event_id=claimed["remote_event_id"],
            home=canonical_home,
        )
    delivery_id = f"gbs_{fingerprint[:32]}"
    try:
        remote_event_id = deliver(copy.deepcopy(request.payload.model_dump()), delivery_id)
        remote_event_id = _identity(remote_event_id, field="remote_event_id", max_length=512)
    except Exception:
        _finish_attempt(
            direction="inbound",
            nonce_hash=hashed_nonce,
            revision=revision,
            home=canonical_home,
            error_code="delivery_failed",
        )
        raise _error("delivery_failed", "local session delivery failed", retriable=True) from None
    accepted = _finish_attempt(
        direction="inbound",
        nonce_hash=hashed_nonce,
        revision=revision,
        home=canonical_home,
        accepted_event_id=remote_event_id,
    )
    try:
        return _signed_receipt(
            request=request,
            fingerprint=fingerprint,
            remote_event_id=accepted["remote_event_id"],
            home=canonical_home,
        )
    except Exception:
        raise _error("signing_failed", "session receipt could not be signed", retriable=True) from None


__all__ = [
    "GroupBridgeSessionError",
    "GroupBridgeSessionStoreError",
    "load_session_reservations",
    "new_group_bridge_session_nonce",
    "receive_group_bridge_session_message",
    "send_group_bridge_session_message",
    "session_reservation_path",
]
