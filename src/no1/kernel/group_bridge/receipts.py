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

from ...contracts.v1.group_bridge import RemoteSendError, RemoteSendReceipt
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
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
    return copy.deepcopy(receipts)


def get_receipt(registration_id: str, idempotency_key: str, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    entry = load_receipts(home).get(_compose_key(registration_id, idempotency_key))
    return copy.deepcopy(entry) if isinstance(entry, dict) else None


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
        canonical = {
            "group_bridge_thread": _text_fact(facts, "group_bridge_thread"),
            "payload": _strict_json_value(facts.get("payload") if facts.get("payload") is not None else {}),
            "reply_to_remote_event_id": _text_fact(facts, "reply_to_remote_event_id"),
            "source_event_id": _text_fact(facts, "source_event_id"),
            "src_group_id": _text_fact(facts, "src_group_id"),
        }
        encoded = json.dumps(
            canonical,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
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
            normalized = RemoteSendReceipt.model_validate(entry).model_dump()
            if normalized != entry or _compose_key(normalized["registration_id"], normalized["idempotency_key"]) != key:
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
) -> Tuple[Dict[str, Any], bool]:
    rid = str(registration_id or "").strip()
    ik = str(idempotency_key or "").strip()
    if not rid or not ik:
        raise ValueError("registration_id and idempotency_key are required")
    fingerprint = _canonical_fingerprint(request_facts)
    key = _compose_key(rid, ik)
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        receipts = _load_unlocked(home, for_write=True)
        existing = receipts.get(key)
        if isinstance(existing, dict):
            existing_fingerprint = str(existing.get("request_fingerprint") or _canonical_fingerprint(None))
            if not hmac.compare_digest(existing_fingerprint, fingerprint):
                raise ReceiptConflictError("idempotency key conflicts with the original request")
            return copy.deepcopy(existing), False
        entry = _normalize_receipt(rid, ik, fingerprint, receipt)
        receipts[key] = entry
        _validate_stored_receipts(receipts)
        _save_unlocked(receipts, home)
        return copy.deepcopy(entry), True
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
        candidate = {**existing, **patch}
        normalized = RemoteSendReceipt.model_validate(candidate).model_dump()
        receipts[key] = normalized
        _validate_stored_receipts(receipts)
        _save_unlocked(receipts, home)
        return copy.deepcopy(normalized)
    finally:
        release_lockfile(lock)
