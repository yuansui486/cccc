"""Route-free local pairing and one-way trust state machine."""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import re
import secrets
import string
import urllib.parse as urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_text
from ...util.time import parse_utc_iso
from ..access_tokens import AccessTokenPrincipal, AccessTokenPrincipalClaim
from .credentials import delete_group_bridge_credential, get_group_bridge_credential
from .peer_addresses import record_peer_addresses, resolve_peer_multiaddrs
from .registration import (
    delete_registration,
    get_registration,
    get_registration_by_target,
    is_valid_credential_ref,
    upsert_registration,
)

ACCESS_LEVEL_MESSAGES = "messages"
ACCESS_LEVEL_READ = "read"
ACCESS_LEVEL_FULL = "full"
ACCESS_LEVELS = frozenset({ACCESS_LEVEL_MESSAGES, ACCESS_LEVEL_READ, ACCESS_LEVEL_FULL})
_ACCESS_LEVEL_RANK = {
    ACCESS_LEVEL_MESSAGES: 0,
    ACCESS_LEVEL_READ: 1,
    ACCESS_LEVEL_FULL: 2,
}

_STORE_VERSION = 1
_STORE_FIELDS = frozenset({"version", "invites", "requests", "trusts"})
_INVITE_PREFIX = "pinv_"
_REQUEST_PREFIX = "preq_"
_TRUST_PREFIX = "ptrust_"
_PAIRING_TRANSPORT = "group_bridge_session"
_PAIRING_CODE_PATTERN = re.compile(r"^[0-9A-F]{8}(?:-[0-9A-F]{8}){3}$")
_DNS_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)
_DEFAULT_TTL_SECONDS = 600
_MIN_TTL_SECONDS = 60
_MAX_TTL_SECONDS = 3600
_INVITE_STATUSES = frozenset({"pending", "requested", "expired", "cancelled"})
_REQUEST_STATUSES = frozenset({"pending", "approving", "approved", "rejected", "expired"})
_TRUST_STATUSES = frozenset({"active", "revoking", "revoked"})
_INVITE_FIELDS = frozenset(
    {
        "invite_id",
        "group_id",
        "expected_remote_group_id",
        "expected_remote_peer_id",
        "local_multiaddrs",
        "transport",
        "code_nonce",
        "pairing_code_hash",
        "status",
        "created_at",
        "updated_at",
        "expires_at",
        "request_id",
    }
)
_REQUEST_FIELDS = frozenset(
    {
        "request_id",
        "invite_id",
        "group_id",
        "remote_group_id",
        "remote_group_title",
        "remote_endpoint",
        "remote_peer_id",
        "remote_multiaddrs",
        "transport",
        "status",
        "created_at",
        "updated_at",
        "approved_by",
        "rejected_by",
        "rejection_reason",
        "registration_id",
        "registration_fingerprint",
        "registration_url",
        "trust_id",
        "approval_nonce",
        "approval_fingerprint",
        "client_nonce_hash",
        "request_fingerprint",
        "expires_at",
    }
)
_TRUST_FIELDS = frozenset(
    {
        "trust_id",
        "request_id",
        "registration_id",
        "registration_fingerprint",
        "registration_url",
        "group_id",
        "remote_group_id",
        "remote_group_title",
        "remote_endpoint",
        "remote_peer_id",
        "remote_multiaddrs",
        "transport",
        "access_level",
        "status",
        "approved_by",
        "access_updated_by",
        "revoked_by",
        "revocation_nonce",
        "revocation_base_revision",
        "revocation_fingerprint",
        "cleanup_credential_ref",
        "cleanup_phase",
        "revision",
        "created_at",
        "updated_at",
    }
)


class PairingStoreError(RuntimeError):
    """Raised when pairing storage cannot be safely mutated."""


class PairingAuthorizationError(PermissionError):
    """Raised when a live local principal claim is required."""


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_string_mapping(
    loader: _StrictSafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> Dict[str, Any]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(None, None, "expected a mapping", node.start_mark)
    mapping: Dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in mapping:
            raise ConstructorError(None, None, "pairing mapping is malformed", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_string_mapping)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Optional[datetime] = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pairing_path(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base.expanduser().resolve(strict=False) / "group_bridge_pairing.yaml"


def _lock_path(home: Optional[Path] = None) -> Path:
    return _pairing_path(home).with_suffix(".yaml.lock")


def _empty_store() -> Dict[str, Any]:
    return {"version": _STORE_VERSION, "invites": {}, "requests": {}, "trusts": {}}


def _store_error() -> PairingStoreError:
    return PairingStoreError("Group Bridge pairing store is malformed")


def _identity(value: Any, *, optional: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or (not optional and not value):
        raise _store_error()
    return value


def _record_id(value: Any, *, prefix: str) -> str:
    identity = _identity(value)
    suffix = identity[len(prefix) :] if identity.startswith(prefix) else ""
    if len(suffix) != 16 or any(character not in string.hexdigits.lower() for character in suffix):
        raise _store_error()
    return identity


def _lower_hex(value: Any, *, length: int, optional: bool = False) -> str:
    identity = _identity(value, optional=optional)
    if not identity and optional:
        return identity
    if len(identity) != length or any(character not in string.hexdigits.lower() for character in identity):
        raise _store_error()
    return identity


def _input_identity(value: Any, *, field: str, optional: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or (not optional and not value):
        raise ValueError(f"{field} is required" if not optional else f"{field} must be a trimmed string")
    return value


def _input_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of trimmed strings")
    if any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise ValueError(f"{field} must contain only trimmed non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} must not contain duplicates")
    return list(value)


def _normalize_remote_endpoint(value: Any) -> str:
    endpoint = _input_identity(value, field="remote_endpoint", optional=True)
    if not endpoint:
        return ""
    try:
        parts = urlparse.urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise ValueError("remote_endpoint must be a normalized HTTP(S) endpoint") from None
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.netloc.endswith(":")
        or "?" in endpoint
        or "#" in endpoint
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in endpoint)
    ):
        raise ValueError("remote_endpoint must be a normalized HTTP(S) endpoint")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    bracketed_host = parts.netloc.startswith("[")
    if "%" in host or (bracketed_host and ":" not in host):
        raise ValueError("remote_endpoint must be a normalized HTTP(S) endpoint")
    if ":" in host:
        try:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        except ValueError:
            raise ValueError("remote_endpoint must be a normalized HTTP(S) endpoint") from None
    elif not _DNS_HOST_PATTERN.fullmatch(host):
        raise ValueError("remote_endpoint must be a normalized HTTP(S) endpoint")
    if port == {"http": 80, "https": 443}[scheme]:
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = (parts.path or "").rstrip("/")
    return f"{scheme}://{netloc}{path}"


def _registration_url_for(*, remote_endpoint: str, remote_peer_id: str) -> str:
    if remote_endpoint:
        return remote_endpoint
    encoded_peer_id = urlparse.quote(remote_peer_id, safe="")
    return f"group-bridge-session://peer/{encoded_peer_id}"


def _principal_key(entry: Dict[str, Any]) -> tuple[str, str, str, str, str]:
    registration_url = entry.get("registration_url")
    if not registration_url:
        registration_url = _registration_url_for(
            remote_endpoint=entry["remote_endpoint"],
            remote_peer_id=entry["remote_peer_id"],
        )
    return (
        entry["group_id"],
        entry.get("transport", _PAIRING_TRANSPORT),
        registration_url,
        entry["remote_group_id"],
        entry["remote_peer_id"],
    )


def _request_blocks_principal(store: Dict[str, Any], request: Dict[str, Any]) -> bool:
    if request["status"] in {"pending", "approving"}:
        return True
    if request["status"] != "approved":
        return False
    trust = store["trusts"].get(request["trust_id"])
    return isinstance(trust, dict) and trust["status"] != "revoked"


def _mark_request_expired(request: Dict[str, Any]) -> None:
    request["status"] = "expired"
    request["approved_by"] = ""
    request["approval_nonce"] = ""
    request["approval_fingerprint"] = ""
    request["updated_at"] = _timestamp()


def _approval_fingerprint(request: Dict[str, Any]) -> str:
    fields = (
        "request_id",
        "request_fingerprint",
        "approved_by",
        "approval_nonce",
        "registration_id",
        "registration_fingerprint",
        "registration_url",
        "trust_id",
    )
    encoded = json.dumps(
        {field: request[field] for field in fields},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _revocation_fingerprint(trust: Dict[str, Any]) -> str:
    fields = (
        "trust_id",
        "request_id",
        "registration_id",
        "registration_fingerprint",
        "registration_url",
        "group_id",
        "remote_group_id",
        "remote_endpoint",
        "remote_peer_id",
        "transport",
        "revocation_base_revision",
        "revoked_by",
        "revocation_nonce",
        "cleanup_credential_ref",
        "cleanup_phase",
    )
    encoded = json.dumps(
        {field: trust[field] for field in fields},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise _store_error()
    if any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise _store_error()
    if len(set(value)) != len(value):
        raise _store_error()
    return list(value)


def _times(entry: Dict[str, Any], *, expires: bool = False) -> None:
    created = parse_utc_iso(entry.get("created_at")) if isinstance(entry.get("created_at"), str) else None
    updated = parse_utc_iso(entry.get("updated_at")) if isinstance(entry.get("updated_at"), str) else None
    if created is None or updated is None or updated < created:
        raise _store_error()
    if expires:
        expiry = parse_utc_iso(entry.get("expires_at")) if isinstance(entry.get("expires_at"), str) else None
        if expiry is None:
            raise _store_error()
        ttl_seconds = (expiry - created).total_seconds()
        if ttl_seconds < _MIN_TTL_SECONDS or ttl_seconds > _MAX_TTL_SECONDS:
            raise _store_error()


def _request_times(entry: Dict[str, Any]) -> None:
    _times(entry)
    created = parse_utc_iso(entry["created_at"])
    expiry = parse_utc_iso(entry["expires_at"]) if isinstance(entry.get("expires_at"), str) else None
    if created is None or expiry is None or expiry <= created:
        raise _store_error()


def _is_due(entry: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    expiry = parse_utc_iso(entry.get("expires_at")) if isinstance(entry.get("expires_at"), str) else None
    return expiry is None or expiry <= (now or _now())


def _validate_invite(key: str, entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _INVITE_FIELDS or entry.get("invite_id") != key:
        raise _store_error()
    normalized = copy.deepcopy(entry)
    _record_id(key, prefix=_INVITE_PREFIX)
    _identity(entry.get("group_id"))
    _identity(entry.get("expected_remote_group_id"), optional=True)
    _identity(entry.get("expected_remote_peer_id"), optional=True)
    normalized["local_multiaddrs"] = _string_list(entry.get("local_multiaddrs"))
    if entry.get("transport") != _PAIRING_TRANSPORT or entry.get("status") not in _INVITE_STATUSES:
        raise _store_error()
    _lower_hex(entry.get("code_nonce"), length=32)
    _lower_hex(entry.get("pairing_code_hash"), length=64)
    request_id = _identity(entry.get("request_id"), optional=True)
    if request_id:
        _record_id(request_id, prefix=_REQUEST_PREFIX)
    if (entry["status"] == "requested") != bool(request_id):
        raise _store_error()
    _times(entry, expires=True)
    return normalized


def _validate_request(key: str, entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _REQUEST_FIELDS or entry.get("request_id") != key:
        raise _store_error()
    normalized = copy.deepcopy(entry)
    _record_id(key, prefix=_REQUEST_PREFIX)
    for field in (
        "request_id",
        "invite_id",
        "group_id",
        "remote_group_id",
        "remote_peer_id",
        "transport",
        "status",
    ):
        _identity(entry.get(field))
    for field in (
        "remote_group_title",
        "remote_endpoint",
        "approved_by",
        "rejected_by",
        "rejection_reason",
        "registration_id",
        "registration_fingerprint",
        "registration_url",
        "trust_id",
        "approval_nonce",
        "approval_fingerprint",
        "client_nonce_hash",
        "request_fingerprint",
    ):
        _identity(entry.get(field), optional=True)
    normalized["remote_multiaddrs"] = _string_list(entry.get("remote_multiaddrs"))
    status = entry["status"]
    if entry["transport"] != _PAIRING_TRANSPORT or status not in _REQUEST_STATUSES:
        raise _store_error()
    try:
        if entry["remote_endpoint"] != _normalize_remote_endpoint(entry["remote_endpoint"]):
            raise _store_error()
    except ValueError:
        raise _store_error() from None
    approval_values = [entry[field] for field in ("registration_id", "registration_fingerprint", "registration_url", "trust_id")]
    if status == "approved" and not all(approval_values):
        raise _store_error()
    if status != "approved" and any(approval_values):
        raise _store_error()
    if (status == "approving") != bool(entry["approval_nonce"]):
        raise _store_error()
    if (status in {"approving", "approved"}) != bool(entry["approved_by"]):
        raise _store_error()
    if (status in {"approving", "approved"}) != bool(entry["approval_fingerprint"]):
        raise _store_error()
    if (status == "rejected") != bool(entry["rejected_by"]):
        raise _store_error()
    if status != "rejected" and entry["rejection_reason"]:
        raise _store_error()
    _record_id(entry["invite_id"], prefix=_INVITE_PREFIX)
    _lower_hex(entry["client_nonce_hash"], length=64)
    _lower_hex(entry["request_fingerprint"], length=64)
    _lower_hex(entry["approval_nonce"], length=32, optional=True)
    _lower_hex(entry["approval_fingerprint"], length=64, optional=True)
    if entry["registration_fingerprint"]:
        _lower_hex(entry["registration_fingerprint"], length=64)
    if entry["trust_id"]:
        _record_id(entry["trust_id"], prefix=_TRUST_PREFIX)
    if not secrets.compare_digest(entry["request_fingerprint"], _request_fingerprint(entry)):
        raise _store_error()
    if entry["approval_fingerprint"] and not secrets.compare_digest(
        entry["approval_fingerprint"],
        _approval_fingerprint(entry),
    ):
        raise _store_error()
    _request_times(entry)
    return normalized


def _validate_trust(key: str, entry: Any) -> Dict[str, Any]:
    if not isinstance(entry, dict) or set(entry) != _TRUST_FIELDS or entry.get("trust_id") != key:
        raise _store_error()
    normalized = copy.deepcopy(entry)
    _record_id(key, prefix=_TRUST_PREFIX)
    for field in (
        "trust_id",
        "request_id",
        "registration_id",
        "registration_fingerprint",
        "registration_url",
        "group_id",
        "remote_group_id",
        "remote_peer_id",
        "transport",
        "access_level",
        "status",
        "approved_by",
    ):
        _identity(entry.get(field))
    for field in (
        "remote_group_title",
        "remote_endpoint",
        "access_updated_by",
        "revoked_by",
        "revocation_nonce",
        "cleanup_credential_ref",
        "cleanup_phase",
    ):
        _identity(entry.get(field), optional=True)
    normalized["remote_multiaddrs"] = _string_list(entry.get("remote_multiaddrs"))
    status = entry["status"]
    if entry["transport"] != _PAIRING_TRANSPORT or entry["access_level"] not in ACCESS_LEVELS or status not in _TRUST_STATUSES:
        raise _store_error()
    try:
        if entry["remote_endpoint"] != _normalize_remote_endpoint(entry["remote_endpoint"]):
            raise _store_error()
    except ValueError:
        raise _store_error() from None
    if entry["registration_url"] != _registration_url_for(
        remote_endpoint=entry["remote_endpoint"],
        remote_peer_id=entry["remote_peer_id"],
    ):
        raise _store_error()
    revision = entry.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise _store_error()
    revocation_base_revision = entry.get("revocation_base_revision")
    if (
        isinstance(revocation_base_revision, bool)
        or not isinstance(revocation_base_revision, int)
        or revocation_base_revision < 0
    ):
        raise _store_error()
    revocation_fingerprint = _identity(entry.get("revocation_fingerprint"), optional=True)
    if revocation_fingerprint:
        _lower_hex(revocation_fingerprint, length=64)
    cleanup_ref = entry["cleanup_credential_ref"]
    cleanup_phase = entry["cleanup_phase"]
    if cleanup_ref and not is_valid_credential_ref(cleanup_ref):
        raise _store_error()
    if (status == "revoking") != bool(entry["revocation_nonce"]):
        raise _store_error()
    if (status == "active" and entry["revoked_by"]) or (status != "active" and not entry["revoked_by"]):
        raise _store_error()
    if status == "active" and (entry["revoked_by"] or cleanup_ref or cleanup_phase):
        raise _store_error()
    if status == "revoked" and (cleanup_ref or cleanup_phase):
        raise _store_error()
    if status == "active" and (revocation_base_revision != 0 or revocation_fingerprint):
        raise _store_error()
    if status == "revoking" and revision != revocation_base_revision + 1:
        raise _store_error()
    if status == "revoking" and cleanup_phase not in {"validate", "credential_delete", "registration_delete"}:
        raise _store_error()
    if status == "revoking" and not cleanup_ref and cleanup_phase != "registration_delete":
        raise _store_error()
    if status == "revoked" and revision != revocation_base_revision + 2:
        raise _store_error()
    if status != "active" and not secrets.compare_digest(revocation_fingerprint, _revocation_fingerprint(entry)):
        raise _store_error()
    _record_id(entry["request_id"], prefix=_REQUEST_PREFIX)
    _lower_hex(entry["registration_fingerprint"], length=64)
    _lower_hex(entry["revocation_nonce"], length=32, optional=True)
    _times(entry)
    return normalized


def _validate_store(raw: Any) -> Dict[str, Any]:
    version = raw.get("version") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or set(raw) != _STORE_FIELDS
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != _STORE_VERSION
    ):
        raise _store_error()
    for section in ("invites", "requests", "trusts"):
        if not isinstance(raw.get(section), dict):
            raise _store_error()
    store = _empty_store()
    store["invites"] = {key: _validate_invite(key, value) for key, value in raw["invites"].items()}
    store["requests"] = {key: _validate_request(key, value) for key, value in raw["requests"].items()}
    store["trusts"] = {key: _validate_trust(key, value) for key, value in raw["trusts"].items()}

    registration_ids: set[str] = set()
    principals: set[tuple[str, str, str, str, str]] = set()
    request_principals: set[tuple[str, str, str, str, str]] = set()
    request_nonce_hashes: set[str] = set()
    for invite in store["invites"].values():
        if invite["status"] != "requested":
            continue
        request = store["requests"].get(invite["request_id"])
        if request is None or request["invite_id"] != invite["invite_id"]:
            raise _store_error()
    for request in store["requests"].values():
        if request["client_nonce_hash"] in request_nonce_hashes:
            raise _store_error()
        request_nonce_hashes.add(request["client_nonce_hash"])
        invite = store["invites"].get(request["invite_id"])
        if invite is None or invite["request_id"] != request["request_id"]:
            raise _store_error()
        if invite["status"] != "requested" or invite["group_id"] != request["group_id"]:
            raise _store_error()
        invite_expiry = parse_utc_iso(invite["expires_at"])
        request_expiry = parse_utc_iso(request["expires_at"])
        if invite_expiry is None or request_expiry is None or request_expiry > invite_expiry:
            raise _store_error()
        if invite["expected_remote_group_id"] and invite["expected_remote_group_id"] != request["remote_group_id"]:
            raise _store_error()
        if invite["expected_remote_peer_id"] and invite["expected_remote_peer_id"] != request["remote_peer_id"]:
            raise _store_error()
        if request["status"] == "approved":
            trust = store["trusts"].get(request["trust_id"])
            if trust is None or trust["request_id"] != request["request_id"]:
                raise _store_error()
        linked_trust = store["trusts"].get(request["trust_id"]) if request["trust_id"] else None
        if request["status"] in {"pending", "approving"} or (
            request["status"] == "approved" and linked_trust is not None and linked_trust["status"] != "revoked"
        ):
            request_principal = _principal_key(request)
            if request_principal in request_principals:
                raise _store_error()
            request_principals.add(request_principal)
    for trust in store["trusts"].values():
        request = store["requests"].get(trust["request_id"])
        if request is None:
            raise _store_error()
        if any(
            trust[field] != request[field]
            for field in (
                "registration_id",
                "registration_fingerprint",
                "registration_url",
                "group_id",
                "remote_group_id",
                "remote_peer_id",
                "transport",
                "approved_by",
                "remote_endpoint",
            )
        ):
            raise _store_error()
        if request["registration_url"] != trust["registration_url"] or _principal_key(request) != _principal_key(trust):
            raise _store_error()
        if request["status"] != "approved":
            raise _store_error()
        principal = _principal_key(trust)
        if trust["registration_id"] in registration_ids or (trust["status"] != "revoked" and principal in principals):
            raise _store_error()
        registration_ids.add(trust["registration_id"])
        if trust["status"] != "revoked":
            principals.add(principal)
    return store


def _load_store(home: Optional[Path] = None, *, for_write: bool = False) -> Dict[str, Any]:
    path = _pairing_path(home)
    if not path.exists():
        return _empty_store()
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictSafeLoader)
        return _validate_store(raw)
    except PairingStoreError:
        if for_write:
            raise
        return _empty_store()
    except Exception:
        if for_write:
            raise _store_error() from None
        return _empty_store()


def _save_store(store: Dict[str, Any], home: Optional[Path] = None) -> None:
    validated = _validate_store(store)
    atomic_write_text(
        _pairing_path(home),
        yaml.safe_dump(validated, allow_unicode=True, sort_keys=True, default_flow_style=False),
    )


def _new_id(prefix: str, existing: Dict[str, Any]) -> str:
    while True:
        candidate = prefix + secrets.token_hex(8)
        if candidate not in existing:
            return candidate


def _new_code() -> str:
    encoded = secrets.token_hex(16).upper()
    return "-".join(encoded[index : index + 8] for index in range(0, len(encoded), 8))


def _normalized_code(code: Any) -> Optional[str]:
    if not isinstance(code, str) or code != code.strip():
        return None
    normalized = code.upper()
    return normalized if _PAIRING_CODE_PATTERN.fullmatch(normalized) else None


def _code_hash(nonce: str, code: str) -> str:
    return hashlib.sha256(f"{nonce}|{code}".encode("utf-8")).hexdigest()


def _find_invite_by_code(store: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    normalized = _normalized_code(code)
    if normalized is None:
        return None
    found: Optional[Dict[str, Any]] = None
    for invite in store["invites"].values():
        candidate = _code_hash(invite["code_nonce"], normalized)
        if secrets.compare_digest(candidate, invite["pairing_code_hash"]):
            found = invite
    return found


def _project_invite(invite: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        "invite_id",
        "group_id",
        "expected_remote_group_id",
        "expected_remote_peer_id",
        "local_multiaddrs",
        "transport",
        "status",
        "created_at",
        "updated_at",
        "expires_at",
        "request_id",
    )
    projected = copy.deepcopy({field: invite[field] for field in fields})
    if projected["status"] == "pending" and _is_due(invite):
        projected["status"] = "expired"
    return projected


def _project_request(request: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        "request_id",
        "invite_id",
        "group_id",
        "remote_group_id",
        "remote_group_title",
        "remote_endpoint",
        "remote_peer_id",
        "transport",
        "status",
        "created_at",
        "updated_at",
        "expires_at",
        "approved_by",
        "rejected_by",
        "rejection_reason",
        "registration_id",
        "trust_id",
    )
    projected = copy.deepcopy({field: request[field] for field in fields})
    if projected["status"] in {"pending", "approving"} and _is_due(request):
        projected["status"] = "expired"
    return projected


def _project_trust(trust: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        "trust_id",
        "request_id",
        "registration_id",
        "group_id",
        "remote_group_id",
        "remote_group_title",
        "remote_endpoint",
        "remote_peer_id",
        "remote_multiaddrs",
        "transport",
        "access_level",
        "status",
        "approved_by",
        "access_updated_by",
        "revoked_by",
        "revision",
        "created_at",
        "updated_at",
    )
    return copy.deepcopy({field: trust[field] for field in fields})


def normalize_access_level(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("access_level must be one of: messages, read, full")
    level = value.lower()
    if level not in ACCESS_LEVELS:
        raise ValueError("access_level must be one of: messages, read, full")
    return level


def get_local_identity(*, home: Optional[Path] = None) -> Dict[str, str]:
    from ...daemon.group_bridge.identity import get_group_bridge_identity

    identity = get_group_bridge_identity(home=home)
    node_id = "node_" + hashlib.sha256(identity.peer_id.encode("utf-8")).hexdigest()[:24]
    return {"node_id": node_id, "peer_id": identity.peer_id}


def create_pairing_invite(
    *,
    group_id: str,
    remote_group_id: str = "",
    remote_peer_id: str = "",
    multiaddrs: Optional[List[str]] = None,
    transport: str = _PAIRING_TRANSPORT,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    local_group_id = _input_identity(group_id, field="group_id")
    expected_remote_group_id = _input_identity(remote_group_id, field="remote_group_id", optional=True)
    expected_remote_peer_id = _input_identity(remote_peer_id, field="remote_peer_id", optional=True)
    if transport != _PAIRING_TRANSPORT:
        raise ValueError("unsupported pairing transport")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise ValueError("ttl_seconds must be an integer")
    ttl = ttl_seconds
    if ttl < _MIN_TTL_SECONDS or ttl > _MAX_TTL_SECONDS:
        raise ValueError("ttl_seconds must be between 60 and 3600")
    local_addresses = _input_string_list([] if multiaddrs is None else multiaddrs, field="multiaddrs")

    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        now = _now()
        invite_id = _new_id(_INVITE_PREFIX, store["invites"])
        nonce = secrets.token_hex(16)
        code = _new_code()
        invite = {
            "invite_id": invite_id,
            "group_id": local_group_id,
            "expected_remote_group_id": expected_remote_group_id,
            "expected_remote_peer_id": expected_remote_peer_id,
            "local_multiaddrs": local_addresses,
            "transport": _PAIRING_TRANSPORT,
            "code_nonce": nonce,
            "pairing_code_hash": _code_hash(nonce, code),
            "status": "pending",
            "created_at": _timestamp(now),
            "updated_at": _timestamp(now),
            "expires_at": _timestamp(now + timedelta(seconds=ttl)),
            "request_id": "",
        }
        candidate = copy.deepcopy(store)
        candidate["invites"][invite_id] = invite
        _save_store(candidate, home)
    finally:
        release_lockfile(lock)
    result = _project_invite(invite)
    result["pairing_code"] = code
    return result


def get_pairing_invite(invite_id: str, *, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(invite_id, str) or not invite_id or invite_id != invite_id.strip():
        return None
    invite = _load_store(home)["invites"].get(invite_id)
    return _project_invite(invite) if isinstance(invite, dict) else None


def get_pairing_invite_for_code(
    pairing_code: str,
    *,
    invite_id: str = "",
    home: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(pairing_code, str) or not pairing_code:
        return None
    if not isinstance(invite_id, str) or invite_id != invite_id.strip():
        return None
    invite = _find_invite_by_code(_load_store(home), pairing_code)
    if invite is None or (invite_id and invite["invite_id"] != invite_id):
        return None
    return _project_invite(invite)


def cancel_pairing_invite(
    invite_id: str,
    *,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    sealed = _require_claim(claim)
    normalized_invite_id = _input_identity(invite_id, field="invite_id")

    def cancel(principal: AccessTokenPrincipal, canonical_home: Path) -> Dict[str, Any]:
        lock = acquire_lockfile(_lock_path(canonical_home), blocking=True)
        try:
            store = _load_store(canonical_home, for_write=True)
            invite = store["invites"].get(normalized_invite_id)
            if invite is None:
                raise ValueError("pairing invite not found")
            _require_group(principal, invite["group_id"])
            if invite["status"] == "cancelled":
                return _project_invite(invite)
            if invite["status"] != "pending":
                raise ValueError("pairing invite is not pending")
            candidate = copy.deepcopy(store)
            cancelled = candidate["invites"][normalized_invite_id]
            if _is_due(cancelled):
                cancelled["status"] = "expired"
                cancelled["updated_at"] = _timestamp()
                _save_store(candidate, canonical_home)
                raise ValueError("pairing invite expired")
            cancelled["status"] = "cancelled"
            cancelled["updated_at"] = _timestamp()
            _save_store(candidate, canonical_home)
            return _project_invite(cancelled)
        finally:
            release_lockfile(lock)

    return sealed.consume_current_for_home(home, cancel)


def create_pairing_request(
    pairing_code: str,
    *,
    client_nonce: str,
    requester_group_id: str,
    requester_group_title: str = "",
    requester_peer_id: str,
    requester_endpoint: str = "",
    requester_multiaddrs: Optional[List[str]] = None,
    invite_id: str = "",
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    code = _normalized_code(pairing_code)
    if code is None:
        raise ValueError("pairing_code format is invalid")
    nonce = _input_identity(client_nonce, field="client_nonce")
    if len(nonce) < 24 or len(nonce) > 256 or any(character not in (string.ascii_letters + string.digits + "_-") for character in nonce):
        raise ValueError("client_nonce must be a high-entropy opaque identifier")
    nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    remote_group_id = _input_identity(requester_group_id, field="requester_group_id")
    remote_peer_id = _input_identity(requester_peer_id, field="requester_peer_id")
    remote_group_title = _input_identity(requester_group_title, field="requester_group_title", optional=True)
    remote_endpoint = _normalize_remote_endpoint(requester_endpoint)
    if not remote_group_id or not remote_peer_id:
        raise ValueError("remote principal is required")
    remote_addresses = _input_string_list(
        [] if requester_multiaddrs is None else requester_multiaddrs,
        field="requester_multiaddrs",
    )

    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        invite = _find_invite_by_code(store, code)
        expected_invite_id = _input_identity(invite_id, field="invite_id", optional=True)
        if invite is None or (expected_invite_id and invite["invite_id"] != expected_invite_id):
            raise ValueError("pairing code not found")
        requested_principal = {
            "group_id": invite["group_id"],
            "transport": _PAIRING_TRANSPORT,
            "remote_endpoint": remote_endpoint,
            "remote_group_id": remote_group_id,
            "remote_peer_id": remote_peer_id,
        }
        principal = _principal_key(requested_principal)
        replay_facts = {
            "client_nonce_hash": nonce_hash,
            "request_id": "",
            "invite_id": invite["invite_id"],
            "group_id": invite["group_id"],
            "remote_group_id": remote_group_id,
            "remote_group_title": remote_group_title,
            "remote_endpoint": remote_endpoint,
            "remote_peer_id": remote_peer_id,
            "remote_multiaddrs": remote_addresses,
            "transport": _PAIRING_TRANSPORT,
            "expires_at": invite["expires_at"],
        }
        requested_fingerprint = _request_fingerprint(replay_facts, include_request_id=False)
        for existing in store["requests"].values():
            if secrets.compare_digest(existing["client_nonce_hash"], nonce_hash):
                if (
                    existing["invite_id"] == invite["invite_id"]
                    and secrets.compare_digest(existing["request_fingerprint"], requested_fingerprint)
                ):
                    return _project_request(existing)
                raise ValueError("Pairing request conflicts with existing replay facts")
        if invite["expected_remote_group_id"] and invite["expected_remote_group_id"] != remote_group_id:
            raise ValueError("pairing code does not authorize this remote group")
        if invite["expected_remote_peer_id"] and invite["expected_remote_peer_id"] != remote_peer_id:
            raise ValueError("pairing code does not authorize this remote peer")
        if invite["status"] != "pending":
            raise ValueError("Pairing request conflicts with existing replay facts")
        expires_at = parse_utc_iso(invite["expires_at"])
        if expires_at is None or expires_at <= _now():
            candidate = copy.deepcopy(store)
            candidate_invite = candidate["invites"][invite["invite_id"]]
            candidate_invite["status"] = "expired"
            candidate_invite["updated_at"] = _timestamp()
            _save_store(candidate, home)
            raise ValueError("pairing code expired")
        for request in store["requests"].values():
            if _principal_key(request) == principal and _request_blocks_principal(store, request):
                raise ValueError("remote principal already has a pairing request")
        now = _timestamp()
        request_id = _new_id(_REQUEST_PREFIX, store["requests"])
        request = {
            "request_id": request_id,
            "invite_id": invite["invite_id"],
            "group_id": invite["group_id"],
            "remote_group_id": remote_group_id,
            "remote_group_title": remote_group_title,
            "remote_endpoint": remote_endpoint,
            "remote_peer_id": remote_peer_id,
            "remote_multiaddrs": remote_addresses,
            "transport": _PAIRING_TRANSPORT,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "expires_at": invite["expires_at"],
            "approved_by": "",
            "rejected_by": "",
            "rejection_reason": "",
            "registration_id": "",
            "registration_fingerprint": "",
            "registration_url": "",
            "trust_id": "",
            "approval_nonce": "",
            "approval_fingerprint": "",
            "client_nonce_hash": nonce_hash,
            "request_fingerprint": "",
        }
        request["request_fingerprint"] = _request_fingerprint(request)
        candidate = copy.deepcopy(store)
        candidate["requests"][request_id] = request
        candidate_invite = candidate["invites"][invite["invite_id"]]
        candidate_invite["status"] = "requested"
        candidate_invite["request_id"] = request_id
        candidate_invite["updated_at"] = now
        _save_store(candidate, home)
        return _project_request(request)
    finally:
        release_lockfile(lock)


def get_pairing_request(request_id: str, *, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(request_id, str) or not request_id or request_id != request_id.strip():
        return None
    request = _load_store(home)["requests"].get(request_id)
    return _project_request(request) if isinstance(request, dict) else None


def list_pairing_requests(*, group_id: str = "", home: Optional[Path] = None) -> List[Dict[str, Any]]:
    if not isinstance(group_id, str) or group_id != group_id.strip():
        return []
    expected_group = group_id
    requests = list(_load_store(home)["requests"].values())
    if expected_group:
        requests = [request for request in requests if request["group_id"] == expected_group]
    requests.sort(key=lambda request: (request["created_at"], request["request_id"]))
    return [_project_request(request) for request in requests]


def install_remote_pairing_approval(
    *,
    local_group_id: str,
    remote_group_id: str,
    remote_group_title: str,
    remote_endpoint: str,
    remote_peer_id: str,
    remote_request_id: str,
    client_nonce_hash: str,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    """Install an issuer-approved principal as a normal local pairing triad.

    The remote approval is verified by the daemon transport before entering
    this kernel boundary.  This helper preserves the pairing store's invariant
    that every trust is backed by one approved local request and invite.
    """

    sealed = _require_claim(claim)
    local_group = _input_identity(local_group_id, field="local_group_id")
    remote_group = _input_identity(remote_group_id, field="remote_group_id")
    remote_title = _input_identity(remote_group_title, field="remote_group_title", optional=True)
    remote_url = _normalize_remote_endpoint(remote_endpoint)
    remote_peer = _input_identity(remote_peer_id, field="remote_peer_id")
    remote_request = _input_identity(remote_request_id, field="remote_request_id")
    _record_id(remote_request, prefix=_REQUEST_PREFIX)
    nonce_hash = _lower_hex(client_nonce_hash, length=64)

    def install(principal: AccessTokenPrincipal, canonical_home: Path) -> Dict[str, Any]:
        _require_group(principal, local_group)
        material = "|".join((local_group, remote_group, remote_peer, remote_request))
        invite_id = _INVITE_PREFIX + hashlib.sha256(f"invite|{material}".encode("utf-8")).hexdigest()[:16]
        request_id = _REQUEST_PREFIX + hashlib.sha256(f"request|{material}".encode("utf-8")).hexdigest()[:16]
        phase_lock = acquire_lockfile(_phase_lock_path("remote-approval", request_id, canonical_home), blocking=True)
        try:
            lock = acquire_lockfile(_lock_path(canonical_home), blocking=True)
            try:
                store = _load_store(canonical_home, for_write=True)
                principal_facts = {
                    "group_id": local_group,
                    "transport": _PAIRING_TRANSPORT,
                    "remote_endpoint": remote_url,
                    "remote_group_id": remote_group,
                    "remote_peer_id": remote_peer,
                }
                for trust in store["trusts"].values():
                    if (
                        trust["status"] == "active"
                        and _principal_key(trust) == _principal_key(principal_facts)
                        and _trust_registration_current(trust, canonical_home)
                    ):
                        request = store["requests"].get(trust["request_id"])
                        if not isinstance(request, dict):
                            raise PairingStoreError("Active pairing trust lost its request")
                        return _approved_result(store, request, home=canonical_home)

                existing_request = store["requests"].get(request_id)
                if existing_request is not None:
                    expected = (
                        existing_request["group_id"],
                        existing_request["remote_group_id"],
                        existing_request["remote_endpoint"],
                        existing_request["remote_peer_id"],
                        existing_request["client_nonce_hash"],
                    )
                    if expected != (local_group, remote_group, remote_url, remote_peer, nonce_hash):
                        raise PairingStoreError("Remote pairing approval conflicts with existing local facts")
                else:
                    if invite_id in store["invites"]:
                        raise PairingStoreError("Remote pairing approval invite identity conflicts")
                    now = _now()
                    now_text = _timestamp(now)
                    expires_at = _timestamp(now + timedelta(seconds=_DEFAULT_TTL_SECONDS))
                    code_nonce = hashlib.sha256(f"nonce|{material}".encode("utf-8")).hexdigest()[:32]
                    invite = {
                        "invite_id": invite_id,
                        "group_id": local_group,
                        "expected_remote_group_id": remote_group,
                        "expected_remote_peer_id": remote_peer,
                        "local_multiaddrs": [],
                        "transport": _PAIRING_TRANSPORT,
                        "code_nonce": code_nonce,
                        "pairing_code_hash": hashlib.sha256(f"code|{material}".encode("utf-8")).hexdigest(),
                        "status": "requested",
                        "created_at": now_text,
                        "updated_at": now_text,
                        "expires_at": expires_at,
                        "request_id": request_id,
                    }
                    request = {
                        "request_id": request_id,
                        "invite_id": invite_id,
                        "group_id": local_group,
                        "remote_group_id": remote_group,
                        "remote_group_title": remote_title,
                        "remote_endpoint": remote_url,
                        "remote_peer_id": remote_peer,
                        "remote_multiaddrs": [],
                        "transport": _PAIRING_TRANSPORT,
                        "status": "pending",
                        "created_at": now_text,
                        "updated_at": now_text,
                        "expires_at": expires_at,
                        "approved_by": "",
                        "rejected_by": "",
                        "rejection_reason": "",
                        "registration_id": "",
                        "registration_fingerprint": "",
                        "registration_url": "",
                        "trust_id": "",
                        "approval_nonce": "",
                        "approval_fingerprint": "",
                        "client_nonce_hash": nonce_hash,
                        "request_fingerprint": "",
                    }
                    request["request_fingerprint"] = _request_fingerprint(request)
                    candidate = copy.deepcopy(store)
                    candidate["invites"][invite_id] = invite
                    candidate["requests"][request_id] = request
                    _save_store(candidate, canonical_home)
            finally:
                release_lockfile(lock)
            return _approve_pairing_request(request_id, principal, home=canonical_home)
        finally:
            release_lockfile(phase_lock)

    return sealed.consume_current_for_home(home, install)


def _require_claim(claim: Any) -> AccessTokenPrincipalClaim:
    if not isinstance(claim, AccessTokenPrincipalClaim):
        raise PairingAuthorizationError("A live access token principal claim is required")
    return claim


def _require_group(principal: AccessTokenPrincipal, group_id: str) -> None:
    if principal.group_id != group_id:
        raise PairingAuthorizationError("Access token principal does not authorize this group")


def _request_fingerprint(request: Dict[str, Any], *, include_request_id: bool = False) -> str:
    fields = [
        "client_nonce_hash",
        "invite_id",
        "group_id",
        "remote_group_id",
        "remote_group_title",
        "remote_endpoint",
        "remote_peer_id",
        "remote_multiaddrs",
        "transport",
        "expires_at",
    ]
    if include_request_id:
        fields.insert(0, "request_id")
    facts = {
        field: request[field]
        for field in fields
    }
    encoded = json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _registration_url(request: Dict[str, Any]) -> str:
    return _registration_url_for(
        remote_endpoint=request["remote_endpoint"],
        remote_peer_id=request["remote_peer_id"],
    )


def _registration_matches(record: Optional[Dict[str, Any]], facts: Dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    matches = all(
        (
            record.get("registration_id") == facts["registration_id"],
            record.get("registration_fingerprint") == facts["registration_fingerprint"],
            record.get("group_id") == facts["group_id"],
            record.get("url") == facts["registration_url"],
            record.get("transport") == _PAIRING_TRANSPORT,
            record.get("remote_group_id") == facts["remote_group_id"],
            record.get("remote_peer_id") == facts["remote_peer_id"],
            record.get("status") == "active",
        )
    )
    if not matches:
        return False
    record_principal = (
        record["group_id"],
        record["transport"],
        record["url"],
        record["remote_group_id"],
        record["remote_peer_id"],
    )
    return record_principal == _principal_key(facts)


def _registration_targets_request(record: Optional[Dict[str, Any]], request: Dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        record.get("group_id"),
        record.get("transport"),
        record.get("url"),
        record.get("remote_group_id"),
        record.get("remote_peer_id"),
        record.get("status"),
        record.get("credential_ref"),
    ) == (*_principal_key(request), "active", "")


def _credential_matches_trust(
    record: Optional[Dict[str, Any]],
    credential_ref: str,
    trust: Dict[str, Any],
) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        record.get("credential_ref"),
        record.get("kind"),
        record.get("local_group_id"),
        record.get("remote_group_id"),
        record.get("remote_endpoint"),
    ) == (
        credential_ref,
        "bearer",
        trust["group_id"],
        trust["remote_group_id"],
        trust["registration_url"],
    )


def _expire_approving_request(request: Dict[str, Any], *, home: Path) -> None:
    registration = get_registration_by_target(
        _registration_url(request),
        request["group_id"],
        home,
        transport=_PAIRING_TRANSPORT,
        remote_group_id=request["remote_group_id"],
        remote_peer_id=request["remote_peer_id"],
    )
    if registration is not None:
        if not _registration_targets_request(registration, request):
            raise PairingStoreError("Expired pairing registration facts changed during cleanup")
        delete_registration(registration["registration_id"], home)

    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        current = store["requests"].get(request["request_id"])
        if current is None:
            raise PairingStoreError("Pairing expiration lost its request")
        if current["status"] == "expired":
            return
        if (
            current["status"] != "approving"
            or current["approval_nonce"] != request["approval_nonce"]
            or _request_fingerprint(current) != _request_fingerprint(request)
            or not _is_due(current)
        ):
            raise PairingStoreError("Pairing expiration facts changed during cleanup")
        candidate = copy.deepcopy(store)
        _mark_request_expired(candidate["requests"][request["request_id"]])
        _save_store(candidate, home)
    finally:
        release_lockfile(lock)


def approve_pairing_request(
    request_id: str,
    *,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    sealed = _require_claim(claim)
    return sealed.consume_current_for_home(
        home,
        lambda principal, canonical_home: _approve_pairing_request(
            request_id,
            principal,
            home=canonical_home,
        ),
    )


def _phase_lock_path(kind: str, object_id: str, home: Optional[Path]) -> Path:
    digest = hashlib.sha256(f"{kind}|{object_id}".encode("utf-8")).hexdigest()[:24]
    return _pairing_path(home).with_name(f"group_bridge_pairing.{kind}.{digest}.lock")


def _approve_pairing_request(
    request_id: str,
    principal: AccessTokenPrincipal,
    *,
    home: Optional[Path],
) -> Dict[str, Any]:
    normalized_request_id = _input_identity(request_id, field="request_id")
    phase_lock = acquire_lockfile(_phase_lock_path("approve", normalized_request_id, home), blocking=True)
    try:
        return _approve_pairing_request_phase(normalized_request_id, principal, home=home)
    finally:
        release_lockfile(phase_lock)


def _approve_pairing_request_phase(
    normalized_request_id: str,
    principal: AccessTokenPrincipal,
    *,
    home: Optional[Path],
) -> Dict[str, Any]:
    expire_phase: Optional[Dict[str, Any]] = None
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        request = store["requests"].get(normalized_request_id)
        if request is None:
            raise ValueError("pairing request not found")
        _require_group(principal, request["group_id"])
        if request["status"] == "approved":
            return _approved_result(store, request, home=home)
        if request["status"] in {"rejected", "expired"}:
            raise ValueError("pairing request is terminal")
        if _is_due(request):
            if request["status"] == "pending":
                candidate = copy.deepcopy(store)
                _mark_request_expired(candidate["requests"][normalized_request_id])
                _save_store(candidate, home)
                raise ValueError("pairing request expired")
            expire_phase = copy.deepcopy(request)
            phase_request = copy.deepcopy(request)
            phase_fingerprint = _request_fingerprint(request)
        else:
            candidate = copy.deepcopy(store)
            pending = candidate["requests"][normalized_request_id]
            if pending["status"] == "pending":
                pending["status"] = "approving"
                pending["approval_nonce"] = secrets.token_hex(16)
                pending["approved_by"] = principal.user_id
                pending["approval_fingerprint"] = _approval_fingerprint(pending)
                pending["updated_at"] = _timestamp()
                _save_store(candidate, home)
            phase_request = copy.deepcopy(pending)
            phase_fingerprint = _request_fingerprint(phase_request)
    finally:
        release_lockfile(lock)

    if expire_phase is not None:
        _expire_approving_request(expire_phase, home=Path(home))
        raise ValueError("pairing request expired")

    registration = upsert_registration(
        phase_request["group_id"],
        _registration_url(phase_request),
        transport=_PAIRING_TRANSPORT,
        remote_group_id=phase_request["remote_group_id"],
        remote_peer_id=phase_request["remote_peer_id"],
        multiaddrs=[],
        credential_ref="",
        user_id=phase_request["approved_by"],
        status="active",
        home=home,
        _approved_by_pairing=True,
    )
    expire_phase = None
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        current = store["requests"].get(normalized_request_id)
        if current is None:
            raise PairingStoreError("Pairing approval lost its request")
        if current["status"] == "approved":
            return _approved_result(store, current, home=home)
        if (
            current["status"] != "approving"
            or current["approval_nonce"] != phase_request["approval_nonce"]
            or _request_fingerprint(current) != phase_fingerprint
        ):
            raise PairingStoreError("Pairing approval facts changed during commit")
        if _is_due(current):
            expire_phase = copy.deepcopy(current)
        else:
            registration_facts = {
                "registration_id": registration["registration_id"],
                "registration_fingerprint": registration["registration_fingerprint"],
                "registration_url": registration["url"],
                "group_id": current["group_id"],
                "remote_group_id": current["remote_group_id"],
                "remote_peer_id": current["remote_peer_id"],
            }
            if not _registration_matches(get_registration(registration["registration_id"], home), registration_facts):
                raise PairingStoreError("Pairing registration is no longer current")
            now = _timestamp()
            trust_id = _new_id(_TRUST_PREFIX, store["trusts"])
            trust = {
                "trust_id": trust_id,
                "request_id": current["request_id"],
                "registration_id": registration["registration_id"],
                "registration_fingerprint": registration["registration_fingerprint"],
                "registration_url": registration["url"],
                "group_id": current["group_id"],
                "remote_group_id": current["remote_group_id"],
                "remote_group_title": current["remote_group_title"],
                "remote_endpoint": current["remote_endpoint"],
                "remote_peer_id": current["remote_peer_id"],
                "remote_multiaddrs": list(current["remote_multiaddrs"]),
                "transport": _PAIRING_TRANSPORT,
                "access_level": ACCESS_LEVEL_MESSAGES,
                "status": "active",
                "approved_by": phase_request["approved_by"],
                "access_updated_by": "",
                "revoked_by": "",
                "revocation_nonce": "",
                "revocation_base_revision": 0,
                "revocation_fingerprint": "",
                "cleanup_credential_ref": "",
                "cleanup_phase": "",
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            candidate = copy.deepcopy(store)
            candidate_request = candidate["requests"][normalized_request_id]
            candidate_request.update(
                {
                    "status": "approved",
                    "registration_id": registration["registration_id"],
                    "registration_fingerprint": registration["registration_fingerprint"],
                    "registration_url": registration["url"],
                    "trust_id": trust_id,
                    "approval_nonce": "",
                    "updated_at": now,
                }
            )
            candidate_request["approval_fingerprint"] = _approval_fingerprint(candidate_request)
            candidate["trusts"][trust_id] = trust
            _save_store(candidate, home)
            return _approved_result(candidate, candidate_request, home=home)
    finally:
        release_lockfile(lock)

    if expire_phase is not None:
        _expire_approving_request(expire_phase, home=Path(home))
        raise ValueError("pairing request expired")
    raise PairingStoreError("Pairing approval did not reach a terminal result")


def _approved_result(store: Dict[str, Any], request: Dict[str, Any], *, home: Optional[Path]) -> Dict[str, Any]:
    trust = store["trusts"].get(request["trust_id"])
    registration = get_registration(request["registration_id"], home)
    if trust is None or not _registration_matches(registration, trust):
        raise PairingStoreError("Approved pairing registration is no longer current")
    expected_addresses = tuple(request["remote_multiaddrs"])
    if expected_addresses and resolve_peer_multiaddrs(
        request["remote_peer_id"],
        remote_group_id=request["remote_group_id"],
        home=home,
    ) != expected_addresses:
        record_peer_addresses(
            request["remote_peer_id"],
            list(expected_addresses),
            remote_group_id=request["remote_group_id"],
            home=home,
        )
    return {
        "status": "approved",
        "request": _project_request(request),
        "registration": copy.deepcopy(registration),
        "trust": _project_trust(trust),
    }


def reject_pairing_request(
    request_id: str,
    *,
    claim: AccessTokenPrincipalClaim,
    reason: str = "",
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    sealed = _require_claim(claim)
    normalized_request_id = _input_identity(request_id, field="request_id")
    normalized_reason = _input_identity(reason, field="reason", optional=True)

    def reject(principal: AccessTokenPrincipal, canonical_home: Path) -> Dict[str, Any]:
        lock = acquire_lockfile(_lock_path(canonical_home), blocking=True)
        try:
            store = _load_store(canonical_home, for_write=True)
            request = store["requests"].get(normalized_request_id)
            if request is None:
                raise ValueError("pairing request not found")
            _require_group(principal, request["group_id"])
            if request["status"] == "rejected":
                return _project_request(request)
            if request["status"] != "pending":
                raise ValueError("pairing request is not pending")
            candidate = copy.deepcopy(store)
            rejected = candidate["requests"][request["request_id"]]
            if _is_due(rejected):
                _mark_request_expired(rejected)
                _save_store(candidate, canonical_home)
                raise ValueError("pairing request expired")
            rejected["status"] = "rejected"
            rejected["rejected_by"] = principal.user_id
            rejected["rejection_reason"] = normalized_reason
            rejected["updated_at"] = _timestamp()
            _save_store(candidate, canonical_home)
            return _project_request(rejected)
        finally:
            release_lockfile(lock)

    return sealed.consume_current_for_home(home, reject)


def _trust_registration_current(trust: Dict[str, Any], home: Optional[Path]) -> bool:
    return _registration_matches(get_registration(trust["registration_id"], home), trust)


def get_trust(trust_id: str, *, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(trust_id, str) or not trust_id or trust_id != trust_id.strip():
        return None
    trust = _load_store(home)["trusts"].get(trust_id)
    if trust is None or (trust["status"] == "active" and not _trust_registration_current(trust, home)):
        return None
    return _project_trust(trust)


def list_trusts(*, group_id: str = "", home: Optional[Path] = None) -> List[Dict[str, Any]]:
    if not isinstance(group_id, str) or group_id != group_id.strip():
        return []
    expected_group = group_id
    trusts = list(_load_store(home)["trusts"].values())
    if expected_group:
        trusts = [trust for trust in trusts if trust["group_id"] == expected_group]
    trusts = [
        trust
        for trust in trusts
        if trust["status"] != "active" or _trust_registration_current(trust, home)
    ]
    trusts.sort(key=lambda trust: (trust["created_at"], trust["trust_id"]))
    return [_project_trust(trust) for trust in trusts]


def authorize_remote_principal(
    *,
    group_id: str,
    transport: str,
    remote_endpoint: str,
    remote_group_id: str,
    remote_peer_id: str,
    required_access_level: str = ACCESS_LEVEL_MESSAGES,
    home: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    local_group_id = _input_identity(group_id, field="group_id")
    if transport != _PAIRING_TRANSPORT:
        return None
    endpoint = _normalize_remote_endpoint(remote_endpoint)
    expected_remote_group_id = _input_identity(remote_group_id, field="remote_group_id")
    expected_remote_peer_id = _input_identity(remote_peer_id, field="remote_peer_id")
    required = normalize_access_level(required_access_level)
    expected_principal = (
        local_group_id,
        _PAIRING_TRANSPORT,
        _registration_url_for(remote_endpoint=endpoint, remote_peer_id=expected_remote_peer_id),
        expected_remote_group_id,
        expected_remote_peer_id,
    )

    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home)
        for trust in store["trusts"].values():
            if trust["status"] != "active" or _principal_key(trust) != expected_principal:
                continue
            if _ACCESS_LEVEL_RANK[trust["access_level"]] < _ACCESS_LEVEL_RANK[required]:
                return None
            registration = get_registration(trust["registration_id"], home)
            if not _registration_matches(registration, trust):
                return None
            return _project_trust(trust)
        return None
    finally:
        release_lockfile(lock)


def update_trust_access_level(
    trust_id: str,
    access_level: str,
    *,
    expected_revision: int,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    sealed = _require_claim(claim)
    level = normalize_access_level(access_level)
    normalized_trust_id = _input_identity(trust_id, field="trust_id")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expected_revision must be an integer")

    def update(principal: AccessTokenPrincipal, canonical_home: Path) -> Dict[str, Any]:
        lock = acquire_lockfile(_lock_path(canonical_home), blocking=True)
        try:
            store = _load_store(canonical_home, for_write=True)
            trust = store["trusts"].get(normalized_trust_id)
            if trust is None:
                raise ValueError("trust not found")
            _require_group(principal, trust["group_id"])
            if trust["revision"] != expected_revision:
                raise PairingStoreError("Trust revision does not match")
            if trust["status"] != "active" or not _trust_registration_current(trust, canonical_home):
                raise ValueError("trust is not active")
            if level == ACCESS_LEVEL_FULL and trust["access_level"] != ACCESS_LEVEL_FULL and not principal.is_admin:
                raise PairingAuthorizationError("Admin access is required to grant full Group Bridge access")
            candidate = copy.deepcopy(store)
            updated = candidate["trusts"][trust["trust_id"]]
            updated["access_level"] = level
            updated["access_updated_by"] = principal.user_id
            updated["revision"] += 1
            updated["updated_at"] = _timestamp()
            _save_store(candidate, canonical_home)
            return _project_trust(updated)
        finally:
            release_lockfile(lock)

    return sealed.consume_current_for_home(home, update)


def revoke_trust(
    trust_id: str,
    *,
    expected_revision: int,
    claim: AccessTokenPrincipalClaim,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    sealed = _require_claim(claim)
    normalized_trust_id = _input_identity(trust_id, field="trust_id")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expected_revision must be an integer")
    return sealed.consume_current_for_home(
        home,
        lambda principal, canonical_home: _revoke_trust(
            normalized_trust_id,
            principal,
            expected_revision=expected_revision,
            home=canonical_home,
        )
    )


def _revoke_trust(
    trust_id: str,
    principal: AccessTokenPrincipal,
    *,
    expected_revision: int,
    home: Optional[Path],
) -> Dict[str, Any]:
    normalized_trust_id = trust_id
    phase_lock = acquire_lockfile(_phase_lock_path("revoke", normalized_trust_id, home), blocking=True)
    try:
        return _revoke_trust_phase(
            normalized_trust_id,
            principal,
            expected_revision=expected_revision,
            home=home,
        )
    finally:
        release_lockfile(phase_lock)


def _advance_revocation_cleanup_phase(
    phase_trust: Dict[str, Any],
    next_phase: str,
    *,
    home: Optional[Path],
) -> Dict[str, Any]:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        current = store["trusts"].get(phase_trust["trust_id"])
        if (
            current is None
            or current["status"] != "revoking"
            or current["revision"] != phase_trust["revision"]
            or current["revocation_nonce"] != phase_trust["revocation_nonce"]
            or current["cleanup_credential_ref"] != phase_trust["cleanup_credential_ref"]
            or current["cleanup_phase"] != phase_trust["cleanup_phase"]
            or current["revocation_fingerprint"] != phase_trust["revocation_fingerprint"]
        ):
            raise PairingStoreError("Trust revocation facts changed during cleanup")
        candidate = copy.deepcopy(store)
        advanced = candidate["trusts"][phase_trust["trust_id"]]
        advanced["cleanup_phase"] = next_phase
        advanced["revocation_fingerprint"] = _revocation_fingerprint(advanced)
        advanced["updated_at"] = _timestamp()
        _save_store(candidate, home)
        return copy.deepcopy(advanced)
    finally:
        release_lockfile(lock)


def _revoke_trust_phase(
    normalized_trust_id: str,
    principal: AccessTokenPrincipal,
    *,
    expected_revision: int,
    home: Optional[Path],
) -> Dict[str, Any]:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        trust = store["trusts"].get(normalized_trust_id)
        if trust is None:
            raise ValueError("trust not found")
        _require_group(principal, trust["group_id"])
        if trust["status"] == "revoked":
            if trust["revocation_base_revision"] != expected_revision:
                raise PairingStoreError("Trust revision does not match")
            return _project_trust(trust)
        if trust["status"] == "active" and trust["revision"] != expected_revision:
            raise PairingStoreError("Trust revision does not match")
        if trust["status"] == "revoking" and trust["revocation_base_revision"] != expected_revision:
            raise PairingStoreError("Trust revision does not match")
        if trust["status"] not in {"active", "revoking"}:
            raise ValueError("trust is not active")
        phase_trust = copy.deepcopy(trust)
    finally:
        release_lockfile(lock)

    if phase_trust["status"] == "active":
        registration = get_registration(phase_trust["registration_id"], home)
        cleanup_ref = ""
        if _registration_matches(registration, phase_trust):
            cleanup_ref = registration["credential_ref"]
        lock = acquire_lockfile(_lock_path(home), blocking=True)
        try:
            store = _load_store(home, for_write=True)
            current = store["trusts"].get(normalized_trust_id)
            if current is None:
                raise PairingStoreError("Trust revocation lost its trust record")
            if current["status"] != "active" or current["revision"] != expected_revision:
                raise PairingStoreError("Trust revision does not match")
            candidate = copy.deepcopy(store)
            revoking = candidate["trusts"][normalized_trust_id]
            revoking["status"] = "revoking"
            revoking["revocation_nonce"] = secrets.token_hex(16)
            revoking["revocation_base_revision"] = expected_revision
            revoking["revoked_by"] = principal.user_id
            revoking["cleanup_credential_ref"] = cleanup_ref
            revoking["cleanup_phase"] = "validate" if cleanup_ref else "registration_delete"
            revoking["revocation_fingerprint"] = _revocation_fingerprint(revoking)
            revoking["revision"] += 1
            revoking["updated_at"] = _timestamp()
            _save_store(candidate, home)
            phase_trust = copy.deepcopy(revoking)
        finally:
            release_lockfile(lock)

    cleanup_ref = phase_trust["cleanup_credential_ref"]
    if phase_trust["cleanup_phase"] == "validate":
        registration = get_registration(phase_trust["registration_id"], home)
        credential = get_group_bridge_credential(cleanup_ref, home=home)
        if (
            registration is None
            or not _registration_matches(registration, phase_trust)
            or registration["credential_ref"] != cleanup_ref
            or not _credential_matches_trust(credential, cleanup_ref, phase_trust)
        ):
            raise PairingStoreError("Trust cleanup facts changed during revocation")
        phase_trust = _advance_revocation_cleanup_phase(
            phase_trust,
            "credential_delete",
            home=home,
        )

    if phase_trust["cleanup_phase"] == "credential_delete":
        registration = get_registration(phase_trust["registration_id"], home)
        credential = get_group_bridge_credential(cleanup_ref, home=home)
        if (
            registration is None
            or not _registration_matches(registration, phase_trust)
            or registration["credential_ref"] != cleanup_ref
            or (credential is not None and not _credential_matches_trust(credential, cleanup_ref, phase_trust))
        ):
            raise PairingStoreError("Trust cleanup facts changed during revocation")
        if credential is not None:
            delete_group_bridge_credential(cleanup_ref, home=home)
        phase_trust = _advance_revocation_cleanup_phase(
            phase_trust,
            "registration_delete",
            home=home,
        )

    if phase_trust["cleanup_phase"] == "registration_delete":
        credential = get_group_bridge_credential(cleanup_ref, home=home) if cleanup_ref else None
        registration = get_registration(phase_trust["registration_id"], home)
        if credential is not None or (
            registration is not None
            and (
                not _registration_matches(registration, phase_trust)
                or registration["credential_ref"] != cleanup_ref
            )
        ):
            raise PairingStoreError("Trust cleanup facts changed during revocation")
        if registration is not None:
            delete_registration(phase_trust["registration_id"], home)

    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        store = _load_store(home, for_write=True)
        current = store["trusts"].get(normalized_trust_id)
        if current is None:
            raise PairingStoreError("Trust revocation lost its trust record")
        if current["status"] == "revoked":
            return _project_trust(current)
        if (
            current["status"] != "revoking"
            or current["revision"] != phase_trust["revision"]
            or current["revocation_nonce"] != phase_trust["revocation_nonce"]
            or current["cleanup_credential_ref"] != phase_trust["cleanup_credential_ref"]
            or current["cleanup_phase"] != phase_trust["cleanup_phase"]
            or current["revocation_fingerprint"] != phase_trust["revocation_fingerprint"]
        ):
            raise PairingStoreError("Trust revocation facts changed during commit")
        candidate = copy.deepcopy(store)
        revoked = candidate["trusts"][normalized_trust_id]
        revoked["status"] = "revoked"
        revoked["revocation_nonce"] = ""
        revoked["cleanup_credential_ref"] = ""
        revoked["cleanup_phase"] = ""
        revoked["revocation_fingerprint"] = _revocation_fingerprint(revoked)
        revoked["revision"] += 1
        revoked["updated_at"] = _timestamp()
        _save_store(candidate, home)
        return _project_trust(revoked)
    finally:
        release_lockfile(lock)
