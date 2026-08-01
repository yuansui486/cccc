"""Strict requester-side persistence for remote Group Bridge pairing."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_text
from ...util.time import parse_utc_iso, utc_now_iso
from .registration import is_valid_credential_ref

_VERSION = 1
_MAX_STORE_BYTES = 2 * 1024 * 1024
_MAX_OUTBOUNDS = 1024
_OUTBOUND_ID = re.compile(r"pout_[0-9a-f]{16}")
_REQUEST_ID = re.compile(r"preq_[0-9a-f]{16}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_STATUSES = frozenset({"reserved", "submitted", "pending", "approving", "approved", "rejected", "expired", "retrying"})
_FACT_FIELDS = frozenset(
    {
        "local_group_id",
        "local_group_title",
        "issuer_group_id",
        "issuer_peer_id",
        "issuer_public_key",
        "issuer_endpoint",
        "invite_id",
        "expires_at",
        "requester_endpoint",
        "client_nonce_hash",
        "pairing_code_hash",
        "credential_ref",
    }
)
_FIELDS = frozenset(
    {
        "outbound_id",
        "local_group_id",
        "local_group_title",
        "issuer_group_id",
        "issuer_peer_id",
        "issuer_public_key",
        "issuer_endpoint",
        "invite_id",
        "expires_at",
        "requester_endpoint",
        "client_nonce_hash",
        "pairing_code_hash",
        "credential_ref",
        "connection_fingerprint",
        "request_id",
        "status",
        "response_fingerprint",
        "registration_id",
        "trust_id",
        "last_error_code",
        "revision",
        "created_at",
        "updated_at",
    }
)


class PairingOutboundStoreError(RuntimeError):
    pass


class PairingOutboundConflictError(ValueError):
    pass


def _path(home: Optional[Path]) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base.expanduser().resolve(strict=False) / "state" / "group_bridge" / "pairing_outbounds.json"


def _lock_path(home: Optional[Path]) -> Path:
    return _path(home).with_suffix(".json.lock")


def _unique_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
        result[key] = value
    return result


def _text(entry: Dict[str, Any], field: str, *, optional: bool = False) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or value != value.strip() or (not optional and not value):
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    return value


def _validate_entry(key: str, entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _FIELDS or entry.get("outbound_id") != key:
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    if _OUTBOUND_ID.fullmatch(key) is None:
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    for field in (
        "local_group_id",
        "issuer_group_id",
        "issuer_peer_id",
        "issuer_public_key",
        "issuer_endpoint",
        "invite_id",
        "expires_at",
        "requester_endpoint",
        "client_nonce_hash",
        "credential_ref",
        "connection_fingerprint",
        "status",
        "created_at",
        "updated_at",
    ):
        _text(entry, field)
    for field in (
        "local_group_title",
        "request_id",
        "response_fingerprint",
        "registration_id",
        "trust_id",
        "last_error_code",
    ):
        _text(entry, field, optional=True)
    if entry["status"] not in _STATUSES or not is_valid_credential_ref(entry["credential_ref"]):
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    if any(
        _HEX_64.fullmatch(entry[field]) is None
        for field in ("client_nonce_hash", "pairing_code_hash", "connection_fingerprint")
    ):
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    if entry["response_fingerprint"] and _HEX_64.fullmatch(entry["response_fingerprint"]) is None:
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    if entry["request_id"] and _REQUEST_ID.fullmatch(entry["request_id"]) is None:
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    if (
        entry["status"] in {"submitted", "pending", "approving", "approved", "rejected", "expired"}
        and not entry["request_id"]
    ):
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    if entry["status"] == "approved" and not all(
        (entry["response_fingerprint"], entry["registration_id"], entry["trust_id"])
    ):
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    if entry["status"] != "approved" and (entry["registration_id"] or entry["trust_id"]):
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    revision = entry.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    created = parse_utc_iso(entry["created_at"])
    updated = parse_utc_iso(entry["updated_at"])
    expires = parse_utc_iso(entry["expires_at"])
    if created is None or updated is None or expires is None or updated < created:
        raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
    return copy.deepcopy(entry)


def _load_unlocked(home: Optional[Path], *, for_write: bool) -> Dict[str, Dict[str, Any]]:
    path = _path(home)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            encoded = handle.read(_MAX_STORE_BYTES + 1)
        if len(encoded) > _MAX_STORE_BYTES:
            raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
        raw = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_object)
        if (
            not isinstance(raw, dict)
            or set(raw) != {"version", "outbounds"}
            or type(raw.get("version")) is not int
            or raw.get("version") != _VERSION
        ):
            raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
        outbounds = raw.get("outbounds")
        if not isinstance(outbounds, dict) or len(outbounds) > _MAX_OUTBOUNDS:
            raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed")
        return {key: _validate_entry(key, value) for key, value in outbounds.items()}
    except PairingOutboundStoreError:
        if for_write:
            raise
        return {}
    except Exception:
        if for_write:
            raise PairingOutboundStoreError("Group Bridge pairing outbound store is malformed") from None
        return {}


def _save_unlocked(outbounds: Dict[str, Dict[str, Any]], home: Optional[Path]) -> None:
    validated = {key: _validate_entry(key, value) for key, value in outbounds.items()}
    encoded = (
        json.dumps(
            {"version": _VERSION, "outbounds": validated},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    if len(encoded.encode("utf-8")) > _MAX_STORE_BYTES:
        raise PairingOutboundStoreError("Group Bridge pairing outbound capacity is exhausted")
    atomic_write_text(_path(home), encoded)


def pairing_outbound_id(*, local_group_id: str, invite_id: str, issuer_peer_id: str) -> str:
    values = (local_group_id, invite_id, issuer_peer_id)
    if any(not isinstance(value, str) or not value or value != value.strip() for value in values):
        raise ValueError("pairing outbound identity facts are invalid")
    return "pout_" + hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:16]


def reserve_pairing_outbound(facts: Dict[str, str], *, home: Optional[Path] = None) -> Dict[str, Any]:
    if not isinstance(facts, dict) or set(facts) != _FACT_FIELDS:
        raise ValueError("pairing outbound facts are invalid")
    if any(not isinstance(value, str) or value != value.strip() for value in facts.values()):
        raise ValueError("pairing outbound facts are invalid")
    limits = {
        "local_group_id": 256,
        "local_group_title": 256,
        "issuer_group_id": 256,
        "issuer_peer_id": 256,
        "issuer_public_key": 44,
        "issuer_endpoint": 2048,
        "invite_id": 21,
        "expires_at": 40,
        "requester_endpoint": 2048,
        "client_nonce_hash": 64,
        "pairing_code_hash": 64,
        "credential_ref": 128,
    }
    required = _FACT_FIELDS - {"local_group_title"}
    if any(not facts[field] for field in required) or any(len(facts[field]) > limits[field] for field in _FACT_FIELDS):
        raise ValueError("pairing outbound facts are invalid")
    canonical = json.dumps(
        facts,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    outbound_id = pairing_outbound_id(
        local_group_id=facts["local_group_id"],
        invite_id=facts["invite_id"],
        issuer_peer_id=facts["issuer_peer_id"],
    )
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        outbounds = _load_unlocked(home, for_write=True)
        existing = outbounds.get(outbound_id)
        if existing is not None:
            if existing["connection_fingerprint"] != fingerprint or any(
                existing.get(key) != value for key, value in facts.items()
            ):
                raise PairingOutboundConflictError("pairing outbound conflicts with its original facts")
            return copy.deepcopy(existing)
        if len(outbounds) >= _MAX_OUTBOUNDS:
            raise PairingOutboundStoreError("Group Bridge pairing outbound capacity is exhausted")
        now = utc_now_iso()
        entry: Dict[str, Any] = {
            **facts,
            "outbound_id": outbound_id,
            "connection_fingerprint": fingerprint,
            "request_id": "",
            "status": "reserved",
            "response_fingerprint": "",
            "registration_id": "",
            "trust_id": "",
            "last_error_code": "",
            "revision": 0,
            "created_at": now,
            "updated_at": now,
        }
        candidate = copy.deepcopy(outbounds)
        candidate[outbound_id] = entry
        _save_unlocked(candidate, home)
        return copy.deepcopy(entry)
    finally:
        release_lockfile(lock)


def update_pairing_outbound(
    outbound_id: str,
    *,
    expected_revision: int,
    home: Optional[Path] = None,
    **changes: Any,
) -> Optional[Dict[str, Any]]:
    allowed = {"request_id", "status", "response_fingerprint", "registration_id", "trust_id", "last_error_code"}
    if set(changes) - allowed:
        raise ValueError("unsupported pairing outbound update")
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        outbounds = _load_unlocked(home, for_write=True)
        entry = outbounds.get(outbound_id)
        if entry is None or entry["revision"] != expected_revision:
            return None
        candidate = copy.deepcopy(outbounds)
        updated = candidate[outbound_id]
        updated.update(changes)
        updated["revision"] += 1
        updated["updated_at"] = utc_now_iso()
        _save_unlocked(candidate, home)
        return copy.deepcopy(updated)
    finally:
        release_lockfile(lock)


def get_pairing_outbound(
    outbound_id: str,
    *,
    home: Optional[Path] = None,
    strict: bool = False,
) -> Optional[Dict[str, Any]]:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        entry = _load_unlocked(home, for_write=strict).get(str(outbound_id or "").strip())
        return copy.deepcopy(entry) if entry is not None else None
    finally:
        release_lockfile(lock)


def public_pairing_outbound(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in entry.items()
        if key not in {"credential_ref", "client_nonce_hash", "pairing_code_hash"}
    }


__all__ = [
    "PairingOutboundConflictError",
    "PairingOutboundStoreError",
    "get_pairing_outbound",
    "pairing_outbound_id",
    "public_pairing_outbound",
    "reserve_pairing_outbound",
    "update_pairing_outbound",
]
