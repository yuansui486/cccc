"""Remote-send receipt and idempotency store."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from ...contracts.v1.group_bridge import RemoteSendError, RemoteSendQueuedRequest, RemoteSendReceipt
from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_text

_KEY_SEP = "::"
_IMMUTABLE_RECEIPT_FIELDS = frozenset({"registration_id", "idempotency_key", "request_fingerprint"})
_PUBLIC_ERRORS = {
    "invalid_request": ("The remote request was invalid.", False),
    "not_found": ("The remote destination was not found.", False),
    "rate_limited": ("The remote service is temporarily unavailable.", True),
    "remote_rejected": ("The remote service rejected the request.", False),
    "timeout": ("The remote service did not respond in time.", True),
    "transport_error": ("Remote delivery failed.", True),
    "unauthorized": ("Remote authorization failed.", False),
}
_ALLOWED_TRANSPORTS = frozenset({"registry_hub", "group_bridge_session"})
_STATUS_TRANSITIONS = {
    "queued": frozenset({"queued", "sending", "retrying", "sent", "failed"}),
    "sending": frozenset({"sending", "retrying", "sent", "failed"}),
    "retrying": frozenset({"sending", "retrying", "sent", "failed"}),
    "sent": frozenset({"sent"}),
    "failed": frozenset({"failed"}),
}
_QUEUED_REQUEST_FIELD = "_queued_request"


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: yaml.Loader, node: yaml.MappingNode, deep: bool = False) -> Dict[Any, Any]:
    mapping: Dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(None, None, "duplicate receipt store key", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


class ReceiptConflictError(ValueError):
    """Raised when an idempotency key is reused for different request facts."""


class ReceiptStoreError(RuntimeError):
    """Raised when existing receipt storage cannot be safely mutated."""


def _path(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base / "group_bridge_receipts.yaml"


def _lock_path(home: Optional[Path] = None) -> Path:
    return _path(home).with_suffix(".yaml.lock")


def _compose_key(registration_id: str, idempotency_key: str) -> str:
    return f"{str(registration_id or '').strip()}{_KEY_SEP}{str(idempotency_key or '').strip()}"


def _load_unlocked(home: Optional[Path] = None, *, for_write: bool = False) -> Dict[str, Dict[str, Any]]:
    path = _path(home)
    if not path.exists():
        return {}
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    except Exception as exc:
        if for_write:
            raise ReceiptStoreError("Group Bridge receipt store is malformed") from exc
        return {}
    receipts = raw.get("receipts") if isinstance(raw, dict) else None
    if not isinstance(receipts, dict):
        if for_write:
            raise ReceiptStoreError("Group Bridge receipt store is malformed")
        return {}
    loaded = {str(key): copy.deepcopy(value) for key, value in receipts.items() if isinstance(value, dict)}
    if len(loaded) != len(receipts):
        if for_write:
            raise ReceiptStoreError("Group Bridge receipt store is malformed")
        return {}
    if for_write:
        _validate_stored_receipts(loaded)
    return loaded


def _save_unlocked(receipts: Dict[str, Dict[str, Any]], home: Optional[Path] = None) -> None:
    payload = {"receipts": {str(key): copy.deepcopy(value) for key, value in receipts.items()}}
    atomic_write_text(
        _path(home),
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=True, default_flow_style=False),
    )


def load_receipts(home: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    receipts = _load_unlocked(home)
    try:
        _validate_stored_receipts(receipts)
    except ReceiptStoreError:
        return {}
    return {key: _public_receipt(entry) for key, entry in receipts.items()}


def get_receipt(registration_id: str, idempotency_key: str, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    entry = load_receipts(home).get(_compose_key(registration_id, idempotency_key))
    return copy.deepcopy(entry) if isinstance(entry, dict) else None


def _public_receipt(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in entry.items() if key != _QUEUED_REQUEST_FIELD}


def get_receipt_strict(
    registration_id: str, idempotency_key: str, home: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Read a receipt while treating malformed persistent state as an error."""
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        entry = _load_unlocked(home, for_write=True).get(_compose_key(registration_id, idempotency_key))
        return copy.deepcopy(_public_receipt(entry)) if isinstance(entry, dict) else None
    finally:
        release_lockfile(lock)


def get_queued_request(
    registration_id: str, idempotency_key: str, home: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Return the private queued facts for a future daemon-owned worker."""
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        entry = _load_unlocked(home, for_write=True).get(_compose_key(registration_id, idempotency_key))
        if not isinstance(entry, dict) or _QUEUED_REQUEST_FIELD not in entry:
            return None
        return copy.deepcopy(entry[_QUEUED_REQUEST_FIELD])
    finally:
        release_lockfile(lock)


def _strict_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("request facts must be finite JSON values")
        return value
    if isinstance(value, list):
        return [_strict_json_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("request facts must use string object keys")
        return {key: _strict_json_value(item) for key, item in value.items()}
    raise ValueError("request facts must contain JSON values")


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _strict_json_value(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _text_fact(facts: Dict[str, Any], name: str) -> str:
    value = facts.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("request identity facts must be strings")
    return value


def _canonical_fingerprint(request_facts: Optional[Dict[str, Any]]) -> str:
    if request_facts is None:
        facts: Dict[str, Any] = {}
    elif isinstance(request_facts, dict):
        facts = request_facts
    else:
        raise ValueError("request facts must be a JSON object")
    try:
        canonical = _strict_json_value(facts)
        encoded = _canonical_json_bytes(canonical)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("request facts must be finite JSON values") from exc
    return hashlib.sha256(encoded).hexdigest()


def safe_error_projection(data: Dict[str, Any]) -> Dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    raw_code = source.get("code") if isinstance(source.get("code"), str) else ""
    raw_code = raw_code.strip()
    code = raw_code if raw_code in _PUBLIC_ERRORS else "remote_delivery_failed"
    message, retriable = _PUBLIC_ERRORS.get(code, ("Remote delivery failed.", False))
    out: Dict[str, Any] = {"code": code, "message": message, "retriable": retriable}
    transport = source.get("transport") if isinstance(source.get("transport"), str) else ""
    transport = transport.strip()
    if transport in _ALLOWED_TRANSPORTS:
        out["transport"] = transport
    status = source.get("http_status")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        out["http_status"] = status
    return out


def _validate_stored_receipts(receipts: Dict[str, Dict[str, Any]]) -> None:
    try:
        for key, entry in receipts.items():
            if not isinstance(entry, dict):
                raise ReceiptStoreError("Group Bridge receipt store is malformed")
            public_entry = _public_receipt(entry)
            normalized = RemoteSendReceipt.model_validate(public_entry).model_dump()
            if _canonical_json_bytes(normalized) != _canonical_json_bytes(public_entry) or _compose_key(
                normalized["registration_id"], normalized["idempotency_key"]
            ) != key:
                raise ReceiptStoreError("Group Bridge receipt store is malformed")
            if _QUEUED_REQUEST_FIELD in entry:
                queued = RemoteSendQueuedRequest.model_validate(entry[_QUEUED_REQUEST_FIELD]).model_dump()
                if _canonical_json_bytes(queued) != _canonical_json_bytes(entry[_QUEUED_REQUEST_FIELD]):
                    raise ReceiptStoreError("Group Bridge receipt store is malformed")
            elif normalized["status"] == "queued" and normalized["transport"] == "group_bridge_session":
                raise ReceiptStoreError("Group Bridge receipt store is malformed")
            fingerprint = normalized["request_fingerprint"]
            if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
                raise ReceiptStoreError("Group Bridge receipt store is malformed")
            if (
                normalized["attempt"] < 0
                or normalized["max_attempts"] < 1
                or normalized["attempt"] > normalized["max_attempts"]
            ):
                raise ReceiptStoreError("Group Bridge receipt store is malformed")
            if normalized["error"] is not None:
                projected = safe_error_projection(normalized["error"])
                projected = RemoteSendError.model_validate(projected).model_dump()
                if projected != normalized["error"]:
                    raise ReceiptStoreError("Group Bridge receipt store is malformed")
    except ReceiptStoreError:
        raise
    except Exception as exc:
        raise ReceiptStoreError("Group Bridge receipt store is malformed") from exc


def _normalize_receipt(
    registration_id: str, idempotency_key: str, fingerprint: str, receipt: Dict[str, Any]
) -> Dict[str, Any]:
    entry = copy.deepcopy(receipt or {})
    if entry.get("error") is not None:
        entry["error"] = safe_error_projection(entry["error"] if isinstance(entry["error"], dict) else {})
    entry.update(
        registration_id=registration_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    return RemoteSendReceipt.model_validate(entry).model_dump()


def record_receipt(
    registration_id: str,
    idempotency_key: str,
    receipt: Dict[str, Any],
    home: Optional[Path] = None,
    *,
    request_facts: Optional[Dict[str, Any]] = None,
    queued_request: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    rid = str(registration_id or "").strip()
    ik = str(idempotency_key or "").strip()
    if not rid or not ik:
        raise ValueError("registration_id and idempotency_key are required")
    fingerprint = _canonical_fingerprint(request_facts)
    queued = None
    if queued_request is not None:
        try:
            queued = RemoteSendQueuedRequest.model_validate(queued_request).model_dump()
        except Exception as exc:
            raise ValueError("queued request facts are invalid") from exc
        if queued["registration_id"] != rid or queued["idempotency_key"] != ik:
            raise ValueError("queued request identity does not match receipt identity")
    key = _compose_key(rid, ik)
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        receipts = _load_unlocked(home, for_write=True)
        existing = receipts.get(key)
        if isinstance(existing, dict):
            existing_fingerprint = str(existing.get("request_fingerprint") or _canonical_fingerprint(None))
            if not hmac.compare_digest(existing_fingerprint, fingerprint):
                raise ReceiptConflictError("idempotency key conflicts with the original request")
            if queued is not None:
                if _QUEUED_REQUEST_FIELD not in existing:
                    raise ReceiptStoreError("queued receipt facts are missing")
                existing_queued = RemoteSendQueuedRequest.model_validate(existing[_QUEUED_REQUEST_FIELD]).model_dump()
                if _canonical_json_bytes(existing_queued) != _canonical_json_bytes(queued):
                    raise ReceiptConflictError("queued request facts conflict with the original request")
            return _public_receipt(existing), False
        entry = _normalize_receipt(rid, ik, fingerprint, receipt)
        if queued is not None:
            entry[_QUEUED_REQUEST_FIELD] = queued
        receipts[key] = entry
        _validate_stored_receipts(receipts)
        _save_unlocked(receipts, home)
        return _public_receipt(entry), True
    finally:
        release_lockfile(lock)


def update_receipt(
    registration_id: str,
    idempotency_key: str,
    home: Optional[Path] = None,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    rid = str(registration_id or "").strip()
    ik = str(idempotency_key or "").strip()
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        receipts = _load_unlocked(home, for_write=True)
        key = _compose_key(rid, ik)
        existing = receipts.get(key)
        if not isinstance(existing, dict):
            return None
        unknown = set(fields) - set(RemoteSendReceipt.model_fields)
        immutable = set(fields) & _IMMUTABLE_RECEIPT_FIELDS
        if unknown or immutable:
            raise ValueError("receipt update contains unsupported fields")
        patch = copy.deepcopy(fields)
        current_status = str(existing.get("status") or "queued")
        next_status = str(patch.get("status") or current_status)
        if next_status not in _STATUS_TRANSITIONS.get(current_status, frozenset()):
            raise ValueError("receipt status transition is not allowed")
        if patch.get("error") is not None:
            patch["error"] = safe_error_projection(patch["error"] if isinstance(patch["error"], dict) else {})
        candidate = {_key: copy.deepcopy(value) for _key, value in existing.items() if _key != _QUEUED_REQUEST_FIELD}
        candidate.update(patch)
        normalized = RemoteSendReceipt.model_validate(candidate).model_dump()
        if _QUEUED_REQUEST_FIELD in existing:
            normalized[_QUEUED_REQUEST_FIELD] = copy.deepcopy(existing[_QUEUED_REQUEST_FIELD])
        receipts[key] = normalized
        _validate_stored_receipts(receipts)
        _save_unlocked(receipts, home)
        return _public_receipt(normalized)
    finally:
        release_lockfile(lock)
