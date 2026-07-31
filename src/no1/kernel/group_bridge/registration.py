"""Secret-free Group Bridge registration store."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import urllib.parse as urlparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ...contracts.v1.group_bridge import RegistrationRecord
from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_text
from ...util.time import utc_now_iso

_REG_PREFIX = "reg_"
_DEFAULT_PORTS = {"http": 80, "https": 443}
_TOKEN_SHAPED_REF = re.compile(r"^acc_[0-9A-Za-z_-]{4,}$")
_GENERATED_CREDENTIAL_REF = re.compile(r"^gbsec_(?:pairing|remote_send)_[0-9a-f]{24}$")
_LEGACY_CREDENTIAL_REF = re.compile(r"^(?:sec|fsec)_[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_JWT_SHAPED_REF = re.compile(r"^eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")
_RAW_SECRET_PATTERNS = (
    re.compile(r"^(?:ghp|github_pat|glpat|xox[baprs]|sk|pat|bearer)[A-Za-z0-9_-]*", re.IGNORECASE),
    _JWT_SHAPED_REF,
)
_SUPPORTED_TRANSPORTS = frozenset({"registry_hub", "group_bridge_session"})


class RegistrationConflictError(ValueError):
    """Raised when an existing principal is presented with different facts."""


class RegistrationStoreError(RuntimeError):
    """Raised when existing registration storage cannot be safely mutated."""


def is_valid_credential_ref(credential_ref: str) -> bool:
    ref = str(credential_ref or "").strip()
    return not ref or bool(_GENERATED_CREDENTIAL_REF.fullmatch(ref) or _LEGACY_CREDENTIAL_REF.fullmatch(ref))


def _reject_raw_credential_ref(credential_ref: str) -> None:
    ref = str(credential_ref or "").strip()
    if is_valid_credential_ref(ref):
        return
    if _TOKEN_SHAPED_REF.match(ref) or any(pattern.match(ref) for pattern in _RAW_SECRET_PATTERNS) or len(ref) >= 24:
        raise ValueError("credential_ref must be an opaque Group Bridge credential reference; raw secrets are not accepted")
    raise ValueError("credential_ref must be an opaque Group Bridge credential reference")


def normalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlparse.urlsplit(raw)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return raw
    port = parts.port
    if port is not None and _DEFAULT_PORTS.get(scheme) == port:
        port = None
    netloc = f"{host}:{port}" if port is not None else host
    path = (parts.path or "").rstrip("/")
    return f"{scheme}://{netloc}{path}"


def _path(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base / "group_bridge_registrations.yaml"


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
            raise RegistrationStoreError("Group Bridge registration store is malformed") from exc
        return {}
    records = raw.get("registrations") if isinstance(raw, dict) else None
    if not isinstance(records, dict):
        if for_write:
            raise RegistrationStoreError("Group Bridge registration store is malformed")
        return {}
    loaded = {str(key): copy.deepcopy(value) for key, value in records.items() if isinstance(value, dict)}
    if len(loaded) != len(records):
        if for_write:
            raise RegistrationStoreError("Group Bridge registration store is malformed")
        return {}
    if for_write:
        _validate_stored_registrations(loaded)
    return loaded


def _save_unlocked(records: Dict[str, Dict[str, Any]], home: Optional[Path] = None) -> None:
    payload = {"registrations": {str(key): copy.deepcopy(value) for key, value in records.items()}}
    atomic_write_text(
        _path(home),
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=True, default_flow_style=False),
    )


def load_registrations(home: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    records = _load_unlocked(home)
    try:
        _validate_stored_registrations(records)
    except RegistrationStoreError:
        return {}
    return copy.deepcopy(records)


def list_registrations(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    items = list(load_registrations(home).values())
    items.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("registration_id") or "")))
    return items


def get_registration(registration_id: str, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    entry = load_registrations(home).get(str(registration_id or "").strip())
    return copy.deepcopy(entry) if isinstance(entry, dict) else None


def _natural_key(
    *, group_id: str, transport: str, url: str, remote_group_id: str, remote_peer_id: str
) -> Tuple[str, ...]:
    if transport == "group_bridge_session":
        return (group_id, transport, url, remote_group_id, remote_peer_id)
    return (group_id, transport, url)


def _registration_fingerprint(
    *, group_id: str, transport: str, url: str, remote_group_id: str, remote_peer_id: str
) -> str:
    facts = {
        "group_id": group_id,
        "remote_group_id": remote_group_id,
        "remote_peer_id": remote_peer_id,
        "transport": transport,
        "url": url,
    }
    encoded = json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _entry_natural_key(entry: Dict[str, Any]) -> Tuple[str, ...]:
    return _natural_key(
        group_id=str(entry.get("group_id") or ""),
        transport=str(entry.get("transport") or "registry_hub"),
        url=str(entry.get("url") or ""),
        remote_group_id=str(entry.get("remote_group_id") or ""),
        remote_peer_id=str(entry.get("remote_peer_id") or ""),
    )


def _validate_stored_registrations(records: Dict[str, Dict[str, Any]]) -> None:
    natural_keys: set[Tuple[str, ...]] = set()
    try:
        for key, entry in records.items():
            normalized = RegistrationRecord.model_validate(entry).model_dump()
            if normalized != entry or normalized["registration_id"] != key:
                raise RegistrationStoreError("Group Bridge registration store is malformed")
            if normalized["transport"] not in _SUPPORTED_TRANSPORTS:
                raise RegistrationStoreError("Group Bridge registration store is malformed")
            if normalized["url"] != normalize_url(normalized["url"]):
                raise RegistrationStoreError("Group Bridge registration store is malformed")
            if not is_valid_credential_ref(normalized["credential_ref"]):
                raise RegistrationStoreError("Group Bridge registration store is malformed")
            if normalized["transport"] == "group_bridge_session" and (
                not normalized["remote_group_id"] or not normalized["remote_peer_id"]
            ):
                raise RegistrationStoreError("Group Bridge registration store is malformed")
            expected_fingerprint = _registration_fingerprint(
                group_id=normalized["group_id"],
                transport=normalized["transport"],
                url=normalized["url"],
                remote_group_id=normalized["remote_group_id"],
                remote_peer_id=normalized["remote_peer_id"],
            )
            if not secrets.compare_digest(normalized["registration_fingerprint"], expected_fingerprint):
                raise RegistrationStoreError("Group Bridge registration store is malformed")
            natural_key = _entry_natural_key(normalized)
            if natural_key in natural_keys:
                raise RegistrationStoreError("Group Bridge registration store is malformed")
            natural_keys.add(natural_key)
    except RegistrationStoreError:
        raise
    except Exception as exc:
        raise RegistrationStoreError("Group Bridge registration store is malformed") from exc


def get_registration_by_target(
    url: str,
    group_id: str,
    home: Optional[Path] = None,
    *,
    transport: str = "registry_hub",
    remote_group_id: str = "",
    remote_peer_id: str = "",
) -> Optional[Dict[str, Any]]:
    key = _natural_key(
        group_id=str(group_id or "").strip(),
        transport=str(transport or "registry_hub").strip() or "registry_hub",
        url=normalize_url(url),
        remote_group_id=str(remote_group_id or "").strip(),
        remote_peer_id=str(remote_peer_id or "").strip(),
    )
    for entry in load_registrations(home).values():
        if _entry_natural_key(entry) == key:
            return copy.deepcopy(entry)
    return None


def _new_registration_id(existing: Dict[str, Dict[str, Any]]) -> str:
    while True:
        candidate = f"{_REG_PREFIX}{secrets.token_hex(8)}"
        if candidate not in existing:
            return candidate


def upsert_registration(
    group_id: str,
    url: str,
    *,
    transport: str = "registry_hub",
    remote_group_id: str = "",
    remote_peer_id: str = "",
    multiaddrs: Optional[List[str]] = None,
    credential_ref: str = "",
    user_id: str = "",
    status: str = "active",
    home: Optional[Path] = None,
    _approved_by_pairing: bool = False,
) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    norm = normalize_url(url)
    if not gid:
        raise ValueError("group_id is required")
    if not norm:
        raise ValueError("url is required")
    cred_ref = str(credential_ref or "").strip()
    _reject_raw_credential_ref(cred_ref)
    transport_name = str(transport or "registry_hub").strip() or "registry_hub"
    if transport_name not in _SUPPORTED_TRANSPORTS:
        raise ValueError("unsupported Group Bridge transport")
    remote_gid = str(remote_group_id or "").strip()
    remote_pid = str(remote_peer_id or "").strip()
    state = str(status or "active").strip() or "active"
    if transport_name == "group_bridge_session":
        if state == "active" and not _approved_by_pairing:
            raise ValueError("active Group Bridge session registrations require pairing approval")
        if not remote_gid:
            raise ValueError("remote_group_id is required for Group Bridge sessions")
        if not remote_pid:
            raise ValueError("remote_peer_id is required for Group Bridge sessions")
    addrs = [str(addr or "").strip() for addr in (multiaddrs or []) if str(addr or "").strip()]
    key = _natural_key(
        group_id=gid,
        transport=transport_name,
        url=norm,
        remote_group_id=remote_gid,
        remote_peer_id=remote_pid,
    )
    fingerprint = _registration_fingerprint(
        group_id=gid,
        transport=transport_name,
        url=norm,
        remote_group_id=remote_gid,
        remote_peer_id=remote_pid,
    )
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        records = _load_unlocked(home, for_write=True)
        existing_id = next((rid for rid, entry in records.items() if _entry_natural_key(entry) == key), "")
        now = utc_now_iso()
        if existing_id:
            existing = records[existing_id]
            existing_fingerprint = str(existing.get("registration_fingerprint") or "")
            if existing_fingerprint and not secrets.compare_digest(existing_fingerprint, fingerprint):
                raise RegistrationConflictError("registration target conflicts with the existing principal")
            registration_id = existing_id
            created_at = str(existing.get("created_at") or now)
        else:
            registration_id = _new_registration_id(records)
            created_at = now
        record = RegistrationRecord(
            registration_id=registration_id,
            registration_fingerprint=fingerprint,
            group_id=gid,
            url=norm,
            transport=transport_name,
            remote_group_id=remote_gid,
            remote_peer_id=remote_pid,
            multiaddrs=addrs,
            credential_ref=cred_ref,
            user_id=str(user_id or "").strip(),
            status=state,  # type: ignore[arg-type]
            created_at=created_at,
            updated_at=now,
        ).model_dump()
        records[registration_id] = record
        _save_unlocked(records, home)
        return copy.deepcopy(record)
    finally:
        release_lockfile(lock)


def delete_registration(registration_id: str, home: Optional[Path] = None) -> bool:
    rid = str(registration_id or "").strip()
    if not rid:
        return False
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        records = _load_unlocked(home, for_write=True)
        if rid not in records:
            return False
        del records[rid]
        _save_unlocked(records, home)
        return True
    finally:
        release_lockfile(lock)
