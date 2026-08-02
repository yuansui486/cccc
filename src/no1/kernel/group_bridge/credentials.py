"""Secret storage for Group Bridge registrations.

Safe metadata APIs never return bearer tokens. Token resolution is kept as an
explicit transport-only boundary, while generated tokens are returned by the
creation call exactly once.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_text
from ...util.time import utc_now_iso

_PAIRING_REF_PREFIX = "gbsec_pairing_"
_REMOTE_SEND_REF_PREFIX = "gbsec_remote_send_"
_REMOTE_SEND_TOKEN_PREFIX = "gbrs_"
_BEARER_FIELDS = frozenset(
    {
        "credential_ref",
        "kind",
        "token",
        "local_group_id",
        "remote_group_id",
        "remote_endpoint",
        "created_at",
        "updated_at",
    }
)
_REMOTE_SEND_FIELDS = frozenset(
    {
        "credential_ref",
        "kind",
        "token",
        "group_id",
        "remote_group_id",
        "remote_peer_id",
        "request_id",
        "created_at",
        "updated_at",
    }
)


class CredentialStoreError(RuntimeError):
    """Raised when existing credential storage cannot be safely mutated."""


def _validate_credential_record(ref: str, record: Dict[str, Any]) -> None:
    kind = record.get("kind")
    fields = _BEARER_FIELDS if kind == "bearer" else _REMOTE_SEND_FIELDS if kind == "remote_send" else None
    if fields is None or set(record) != fields or record.get("credential_ref") != ref:
        raise CredentialStoreError("Group Bridge credential store is malformed")
    if not all(isinstance(record.get(name), str) and record[name] for name in ("token", "created_at", "updated_at")):
        raise CredentialStoreError("Group Bridge credential store is malformed")
    if kind == "bearer":
        fact_names = ("local_group_id", "remote_group_id", "remote_endpoint")
        if not all(isinstance(record.get(name), str) and record[name] for name in fact_names):
            raise CredentialStoreError("Group Bridge credential store is malformed")
        token_hash = hashlib.sha256(record["token"].encode("utf-8")).hexdigest()
        suffix = hashlib.sha256(
            "|".join((record["local_group_id"], record["remote_group_id"], record["remote_endpoint"], token_hash)).encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        valid_refs = {_PAIRING_REF_PREFIX + suffix, "fsec_pairing_" + suffix}
    else:
        fact_names = ("group_id", "remote_group_id", "remote_peer_id", "request_id")
        if not all(isinstance(record.get(name), str) and record[name] for name in fact_names):
            raise CredentialStoreError("Group Bridge credential store is malformed")
        suffix = hashlib.sha256(
            "|".join(("remote_send", *(record[name] for name in fact_names))).encode("utf-8")
        ).hexdigest()[:24]
        valid_refs = {_REMOTE_SEND_REF_PREFIX + suffix, "fsec_remote_send_" + suffix}
        if not (record["token"].startswith(_REMOTE_SEND_TOKEN_PREFIX) or record["token"].startswith("frs_")):
            raise CredentialStoreError("Group Bridge credential store is malformed")
    if ref not in valid_refs:
        raise CredentialStoreError("Group Bridge credential store is malformed")


def _validate_credential_records(records: Dict[str, Dict[str, Any]]) -> None:
    for ref, record in records.items():
        _validate_credential_record(ref, record)


def _path(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base / "group_bridge_credentials.yaml"


def _lock_path(home: Optional[Path] = None) -> Path:
    return _path(home).with_suffix(".yaml.lock")


def _load_unlocked(home: Optional[Path] = None, *, for_write: bool = False) -> Dict[str, Dict[str, Any]]:
    path = _path(home)
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        if for_write:
            raise CredentialStoreError("Group Bridge credential store is malformed") from exc
        return {}
    records = raw.get("credentials") if isinstance(raw, dict) else None
    if not isinstance(records, dict):
        if for_write:
            raise CredentialStoreError("Group Bridge credential store is malformed")
        return {}
    loaded = {str(key): copy.deepcopy(value) for key, value in records.items() if isinstance(value, dict)}
    if len(loaded) != len(records):
        if for_write:
            raise CredentialStoreError("Group Bridge credential store is malformed")
        return {}
    if for_write:
        _validate_credential_records(loaded)
    return loaded


def _load_validated(home: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    records = _load_unlocked(home)
    try:
        _validate_credential_records(records)
    except CredentialStoreError:
        return {}
    return records


def _save_unlocked(records: Dict[str, Dict[str, Any]], home: Optional[Path] = None) -> None:
    payload = {"credentials": {str(key): copy.deepcopy(value) for key, value in records.items()}}
    atomic_write_text(
        _path(home),
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=True, default_flow_style=False),
    )


def _safe_projection(record: Dict[str, Any]) -> Dict[str, Any]:
    return {str(key): copy.deepcopy(value) for key, value in record.items() if key != "token"}


def _new_remote_send_token(records: Dict[str, Dict[str, Any]]) -> str:
    existing = {str(record.get("token") or "") for record in records.values()}
    while True:
        token = _REMOTE_SEND_TOKEN_PREFIX + secrets.token_urlsafe(32)
        if token not in existing:
            return token


def save_pairing_bearer_token(
    *,
    local_group_id: str,
    remote_group_id: str,
    remote_endpoint: str,
    token: str,
    home: Optional[Path] = None,
) -> str:
    raw_token = str(token or "").strip()
    local_gid = str(local_group_id or "").strip()
    remote_gid = str(remote_group_id or "").strip()
    endpoint = str(remote_endpoint or "").strip()
    if not raw_token:
        return ""
    if not local_gid or not remote_gid or not endpoint:
        raise ValueError("pairing bearer principal facts are required")
    facts = (
        local_gid,
        remote_gid,
        endpoint,
        hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
    )
    ref = _PAIRING_REF_PREFIX + hashlib.sha256("|".join(facts).encode("utf-8")).hexdigest()[:24]
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        records = _load_unlocked(home, for_write=True)
        now = utc_now_iso()
        existing = records.get(ref) if isinstance(records.get(ref), dict) else {}
        records[ref] = {
            "credential_ref": ref,
            "kind": "bearer",
            "token": raw_token,
            "local_group_id": facts[0],
            "remote_group_id": facts[1],
            "remote_endpoint": facts[2],
            "created_at": str(existing.get("created_at") or now),
            "updated_at": now,
        }
        _save_unlocked(records, home)
    finally:
        release_lockfile(lock)
    return ref


def resolve_group_bridge_credential(
    credential_ref: str,
    *,
    expected_local_group_id: str,
    expected_remote_group_id: str,
    expected_remote_endpoint: str,
    home: Optional[Path] = None,
) -> str:
    """Resolve a bearer for a trusted transport; never use for API projection."""
    ref = str(credential_ref or "").strip()
    if not ref:
        return ""
    record = _load_validated(home).get(ref)
    expected = (
        str(expected_local_group_id or "").strip(),
        str(expected_remote_group_id or "").strip(),
        str(expected_remote_endpoint or "").strip(),
    )
    actual = (
        str(record.get("local_group_id") or "") if isinstance(record, dict) else "",
        str(record.get("remote_group_id") or "") if isinstance(record, dict) else "",
        str(record.get("remote_endpoint") or "") if isinstance(record, dict) else "",
    )
    if (
        not all(expected)
        or not isinstance(record, dict)
        or str(record.get("kind") or "") != "bearer"
        or actual != expected
    ):
        return ""
    return str(record.get("token") or "").strip()


def create_pairing_remote_send_credential(
    *,
    group_id: str,
    remote_group_id: str,
    remote_peer_id: str,
    request_id: str,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    remote_gid = str(remote_group_id or "").strip()
    peer_id = str(remote_peer_id or "").strip()
    rid = str(request_id or "").strip()
    if not gid or not remote_gid or not peer_id or not rid:
        return {"credential_ref": "", "created": False}
    material = "|".join(("remote_send", gid, remote_gid, peer_id, rid))
    ref = _REMOTE_SEND_REF_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        records = _load_unlocked(home, for_write=True)
        existing = records.get(ref)
        if isinstance(existing, dict):
            return {"credential_ref": ref, "created": False}
        now = utc_now_iso()
        token = _new_remote_send_token(records)
        records[ref] = {
            "credential_ref": ref,
            "kind": "remote_send",
            "token": token,
            "group_id": gid,
            "remote_group_id": remote_gid,
            "remote_peer_id": peer_id,
            "request_id": rid,
            "created_at": now,
            "updated_at": now,
        }
        _save_unlocked(records, home)
        return {"credential_ref": ref, "token": token, "created": True}
    finally:
        release_lockfile(lock)


def resolve_pairing_remote_send_token(
    credential_ref: str,
    *,
    expected_group_id: str,
    expected_remote_group_id: str,
    expected_remote_peer_id: str,
    expected_request_id: str,
    home: Optional[Path] = None,
) -> str:
    """Resolve a remote-send bearer for a trusted transport only."""
    ref = str(credential_ref or "").strip()
    if not ref:
        return ""
    record = _load_validated(home).get(ref)
    expected = (
        str(expected_group_id or "").strip(),
        str(expected_remote_group_id or "").strip(),
        str(expected_remote_peer_id or "").strip(),
        str(expected_request_id or "").strip(),
    )
    actual = (
        str(record.get("group_id") or "") if isinstance(record, dict) else "",
        str(record.get("remote_group_id") or "") if isinstance(record, dict) else "",
        str(record.get("remote_peer_id") or "") if isinstance(record, dict) else "",
        str(record.get("request_id") or "") if isinstance(record, dict) else "",
    )
    if (
        not all(expected)
        or not isinstance(record, dict)
        or str(record.get("kind") or "") != "remote_send"
        or actual != expected
    ):
        return ""
    return str(record.get("token") or "").strip()


def lookup_pairing_remote_send_credential(token: str, *, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    raw_token = str(token or "").strip()
    if not raw_token:
        return None
    for ref, record in _load_validated(home).items():
        if str(record.get("kind") or "") != "remote_send":
            continue
        stored = str(record.get("token") or "")
        if stored and hmac.compare_digest(stored, raw_token):
            projected = _safe_projection(record)
            projected["credential_ref"] = str(record.get("credential_ref") or ref)
            return projected
    return None


def get_group_bridge_credential(credential_ref: str, *, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    ref = str(credential_ref or "").strip()
    if not ref:
        return None
    record = _load_validated(home).get(ref)
    return _safe_projection(record) if isinstance(record, dict) else None


def list_group_bridge_credentials(*, home: Optional[Path] = None) -> List[Dict[str, Any]]:
    items = [_safe_projection(record) for record in _load_validated(home).values()]
    items.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("credential_ref") or "")))
    return items


def delete_group_bridge_credential(credential_ref: str, *, home: Optional[Path] = None) -> bool:
    ref = str(credential_ref or "").strip()
    if not ref:
        return False
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        records = _load_unlocked(home, for_write=True)
        if ref not in records:
            return False
        del records[ref]
        _save_unlocked(records, home)
        return True
    finally:
        release_lockfile(lock)
