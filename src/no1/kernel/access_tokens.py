from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import secrets
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from ..paths import ensure_home
from ..util.file_lock import acquire_lockfile, release_lockfile
from ..util.fs import atomic_write_text
from ..util.time import parse_utc_iso, utc_now_iso

_TOKEN_PREFIX = "acc_"
_PERSISTED_FIELDS = frozenset({"user_id", "allowed_groups", "is_admin", "created_at", "updated_at"})
_PUBLIC_FIELDS = frozenset({"token", "kind", *_PERSISTED_FIELDS})
_STORE_SCHEMA_ID = "access-token-store-v1"
_CLAIM_SEAL = object()
_ClaimResult = TypeVar("_ClaimResult")


class AccessTokenStoreError(RuntimeError):
    """Raised when access-token storage cannot be safely mutated."""


class AccessTokenClaimError(PermissionError):
    """Raised when a process-local access-token claim is invalid."""


class AccessTokenClaimStaleError(AccessTokenClaimError):
    """Raised when a claim no longer describes the current token entry."""


class AccessTokenLockOrderError(RuntimeError):
    """Raised when token APIs are re-entered from a claim consumer."""


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_string_mapping(
    loader: _StrictSafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> Dict[str, Any]:
    if not isinstance(node, MappingNode):
        raise ConstructorError(None, None, "expected a mapping node", node.start_mark)
    mapping: Dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ConstructorError(None, None, "mapping keys must be strings", key_node.start_mark)
        if key in mapping:
            raise ConstructorError(None, None, "duplicate mapping key", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_string_mapping)


@dataclass(frozen=True)
class AccessTokenPrincipal:
    user_id: str
    group_id: str
    is_admin: bool
    allowed_groups: tuple[str, ...]


@dataclass(frozen=True)
class _ClaimState:
    raw_token: str
    home: Path
    entry_fingerprint: str
    principal: AccessTokenPrincipal
    require_admin: bool
    issued_pid: int
    schema_id: str
    spent: bool = False


_CLAIM_STATES: weakref.WeakKeyDictionary[AccessTokenPrincipalClaim, _ClaimState] = weakref.WeakKeyDictionary()
_CLAIM_STATES_LOCK = threading.Lock()
_CONSUME_LOCAL = threading.local()


def _guard_token_api() -> None:
    if bool(getattr(_CONSUME_LOCAL, "active", False)):
        raise AccessTokenLockOrderError("Access token APIs are unavailable during claim consumption")


def _claim_state(claim: AccessTokenPrincipalClaim, *, require_unspent: bool) -> _ClaimState:
    if not isinstance(claim, AccessTokenPrincipalClaim):
        raise AccessTokenClaimError("Access token principal claim is invalid")
    with _CLAIM_STATES_LOCK:
        state = _CLAIM_STATES.get(claim)
        if state is None or state.issued_pid != os.getpid() or state.schema_id != _STORE_SCHEMA_ID:
            raise AccessTokenClaimError("Access token principal claim is invalid")
        if require_unspent and state.spent:
            raise AccessTokenClaimStaleError("Access token principal claim is no longer current")
        return state


class AccessTokenPrincipalClaim:
    """A sealed, process-local capability for one exact access-token entry."""

    __slots__ = ("__weakref__",)

    def __init__(self, *, _seal: object):
        if _seal is not _CLAIM_SEAL:
            raise TypeError("Access token principal claims cannot be constructed directly")

    @property
    def principal(self) -> AccessTokenPrincipal:
        _guard_token_api()
        return _claim_state(self, require_unspent=True).principal

    def consume_current(
        self,
        callback: Callable[[AccessTokenPrincipal], _ClaimResult],
    ) -> _ClaimResult:
        _guard_token_api()
        if not isinstance(self, AccessTokenPrincipalClaim):
            raise AccessTokenClaimError("Access token principal claim is invalid")
        if not callable(callback) or inspect.iscoroutinefunction(callback):
            raise AccessTokenClaimError("Access token principal claim requires a synchronous callback")
        with _CLAIM_STATES_LOCK:
            state = _CLAIM_STATES.get(self)
            if (
                state is None
                or state.spent
                or state.issued_pid != os.getpid()
                or state.schema_id != _STORE_SCHEMA_ID
            ):
                raise AccessTokenClaimStaleError("Access token principal claim is no longer current")
            state = _ClaimState(**{**state.__dict__, "spent": True})
            _CLAIM_STATES[self] = state

        lock = acquire_lockfile(_access_tokens_lock_path(state.home), blocking=True)
        try:
            tokens = _load_unlocked(state.home, for_write=False)
            entry = tokens.get(state.raw_token)
            if (
                entry is None
                or not secrets.compare_digest(_entry_fingerprint(entry), state.entry_fingerprint)
                or entry["user_id"] != state.principal.user_id
                or bool(entry["is_admin"]) != state.principal.is_admin
                or tuple(entry["allowed_groups"]) != state.principal.allowed_groups
                or (not state.principal.is_admin and state.principal.group_id not in entry["allowed_groups"])
                or (state.require_admin and not bool(entry["is_admin"]))
            ):
                raise AccessTokenClaimStaleError("Access token principal claim is no longer current")
            previous = bool(getattr(_CONSUME_LOCAL, "active", False))
            _CONSUME_LOCAL.active = True
            try:
                result = callback(state.principal)
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise AccessTokenClaimError("Access token principal claim requires a synchronous callback")
                return result
            finally:
                _CONSUME_LOCAL.active = previous
        finally:
            release_lockfile(lock)

    def __copy__(self):
        raise TypeError("Access token principal claims cannot be copied")

    def __deepcopy__(self, memo):
        _ = memo
        raise TypeError("Access token principal claims cannot be copied")

    def __reduce__(self):
        raise TypeError("Access token principal claims cannot be serialized")

    def __reduce_ex__(self, protocol):
        _ = protocol
        raise TypeError("Access token principal claims cannot be serialized")

    def __repr__(self) -> str:
        return "<AccessTokenPrincipalClaim sealed>"


def _resolved_home(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base.expanduser().resolve(strict=False)


def _access_tokens_path(home: Optional[Path] = None) -> Path:
    return _resolved_home(home) / "access_tokens.yaml"


def _access_tokens_lock_path(home: Optional[Path] = None) -> Path:
    return _access_tokens_path(home).with_suffix(".yaml.lock")


def _malformed_store() -> AccessTokenStoreError:
    return AccessTokenStoreError("Access token store is malformed")


def _valid_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _valid_token(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _validate_allowed_groups(value: Any, *, is_admin: bool) -> List[str]:
    if not isinstance(value, list):
        raise _malformed_store()
    if any(not _valid_identity(group_id) for group_id in value) or len(set(value)) != len(value):
        raise _malformed_store()
    return [] if is_admin else list(value)


def _validated_entry(token: Any, raw: Any, *, public_input: bool) -> Dict[str, Any]:
    if not _valid_token(token) or not isinstance(raw, dict):
        raise _malformed_store()
    expected_fields = _PUBLIC_FIELDS if public_input else _PERSISTED_FIELDS
    if set(raw) != expected_fields:
        raise _malformed_store()
    if public_input and (raw.get("token") != token or raw.get("kind") != "access"):
        raise _malformed_store()
    user_id = raw.get("user_id")
    is_admin = raw.get("is_admin")
    created_at = raw.get("created_at")
    updated_at = raw.get("updated_at")
    if not _valid_identity(user_id) or not isinstance(is_admin, bool):
        raise _malformed_store()
    created = parse_utc_iso(created_at) if isinstance(created_at, str) else None
    updated = parse_utc_iso(updated_at) if isinstance(updated_at, str) else None
    if created is None or updated is None or updated < created:
        raise _malformed_store()
    allowed_groups = _validate_allowed_groups(raw.get("allowed_groups"), is_admin=is_admin)
    return {
        "token": token,
        "kind": "access",
        "user_id": user_id,
        "allowed_groups": allowed_groups,
        "is_admin": is_admin,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _validate_candidate_store(tokens: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(tokens, dict):
        raise _malformed_store()
    normalized: Dict[str, Dict[str, Any]] = {}
    for token, entry in tokens.items():
        if not isinstance(token, str):
            raise _malformed_store()
        if not isinstance(entry, dict):
            raise _malformed_store()
        if set(entry) == _PUBLIC_FIELDS:
            normalized[token] = _validated_entry(token, entry, public_input=True)
        elif set(entry) == _PERSISTED_FIELDS:
            normalized[token] = _validated_entry(token, entry, public_input=False)
        else:
            raise _malformed_store()
    return normalized


def _validate_persisted_store(tokens: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(tokens, dict):
        raise _malformed_store()
    normalized: Dict[str, Dict[str, Any]] = {}
    for token, entry in tokens.items():
        if not isinstance(token, str) or not isinstance(entry, dict):
            raise _malformed_store()
        normalized[token] = _validated_entry(token, entry, public_input=False)
    return normalized


def _persisted_store_candidate(tokens: Any) -> tuple[bool, Dict[str, Dict[str, Any]]]:
    try:
        return True, _validate_persisted_store(tokens)
    except AccessTokenStoreError:
        return False, {}


def _load_unlocked(home: Optional[Path] = None, *, for_write: bool = False) -> Dict[str, Dict[str, Any]]:
    path = _access_tokens_path(home)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        raw = yaml.load(text, Loader=_StrictSafeLoader)
        if raw is None or not isinstance(raw, dict):
            raise _malformed_store()
        if set(raw) == {"tokens"}:
            legacy_valid, legacy_tokens = _persisted_store_candidate(raw)
            wrapper_valid, wrapper_tokens = _persisted_store_candidate(raw["tokens"])
            if legacy_valid == wrapper_valid:
                raise _malformed_store()
            return legacy_tokens if legacy_valid else wrapper_tokens
        return _validate_persisted_store(raw)
    except AccessTokenStoreError:
        if for_write:
            raise
        return {}
    except Exception:
        if for_write:
            raise _malformed_store() from None
        return {}


def _save_unlocked(tokens: Dict[str, Dict[str, Any]], home: Optional[Path] = None) -> None:
    payload: Dict[str, Any] = {"tokens": {}}
    for token, entry in sorted(tokens.items()):
        payload["tokens"][token] = {
            "user_id": entry["user_id"],
            "allowed_groups": list(entry["allowed_groups"]),
            "is_admin": entry["is_admin"],
            "created_at": entry["created_at"],
            "updated_at": entry["updated_at"],
        }
    atomic_write_text(
        _access_tokens_path(home),
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False),
    )


def _clone_tokens(tokens: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return copy.deepcopy(tokens)


def _entry_fingerprint(entry: Dict[str, Any]) -> str:
    encoded = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_allowed_groups(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    groups: List[str] = []
    for item in raw:
        group_id = str(item or "").strip()
        if not group_id or group_id in seen:
            continue
        if not _valid_identity(group_id):
            raise ValueError("allowed_groups contains an invalid group id")
        seen.add(group_id)
        groups.append(group_id)
    return groups


def _normalized_token_argument(token: Any) -> str:
    normalized = str(token or "").strip()
    if not _valid_token(normalized):
        return ""
    return normalized


def load_access_tokens(home: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    _guard_token_api()
    return _clone_tokens(_load_unlocked(home, for_write=False))


def save_access_tokens(tokens: Dict[str, Dict[str, Any]], home: Optional[Path] = None) -> None:
    _guard_token_api()
    lock = acquire_lockfile(_access_tokens_lock_path(home), blocking=True)
    try:
        _load_unlocked(home, for_write=True)
        candidate = _validate_candidate_store(tokens)
        _save_unlocked(candidate, home)
    finally:
        release_lockfile(lock)


def lookup_access_token(token: str, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    _guard_token_api()
    normalized = _normalized_token_argument(token)
    if not normalized:
        return None
    entry = _load_unlocked(home, for_write=False).get(normalized)
    return copy.deepcopy(entry) if entry is not None else None


def _new_access_token_value(existing: Dict[str, Dict[str, Any]]) -> str:
    while True:
        candidate = f"{_TOKEN_PREFIX}{secrets.token_hex(16)}"
        if candidate not in existing:
            return candidate


def create_access_token(
    user_id: str,
    *,
    allowed_groups: Optional[List[str]] = None,
    is_admin: bool = False,
    custom_token: Optional[str] = None,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    _guard_token_api()
    uid = str(user_id or "").strip()
    if not _valid_identity(uid):
        raise ValueError("user_id is required" if not uid else "user_id is invalid")
    groups = _normalize_allowed_groups(allowed_groups or [])
    requested_custom_token = str(custom_token or "").strip()
    if requested_custom_token and not _valid_token(requested_custom_token):
        raise ValueError("custom_token is invalid")
    lock = acquire_lockfile(_access_tokens_lock_path(home), blocking=True)
    try:
        tokens = _load_unlocked(home, for_write=True)
        now = utc_now_iso()
        if requested_custom_token:
            token = requested_custom_token
            if token in tokens:
                raise ValueError("access token already exists")
        else:
            token = _new_access_token_value(tokens)
        effective_is_admin = bool(is_admin)
        entry = {
            "token": token,
            "kind": "access",
            "user_id": uid,
            "allowed_groups": [] if effective_is_admin else groups,
            "is_admin": effective_is_admin,
            "created_at": now,
            "updated_at": now,
        }
        candidate = {**tokens, token: entry}
        candidate = _validate_candidate_store(candidate)
        _save_unlocked(candidate, home)
        return copy.deepcopy(candidate[token])
    finally:
        release_lockfile(lock)


def update_access_token(
    token: str,
    *,
    allowed_groups: Optional[List[str]] = None,
    is_admin: Optional[bool] = None,
    home: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    _guard_token_api()
    normalized = _normalized_token_argument(token)
    if not normalized:
        return None
    groups = _normalize_allowed_groups(allowed_groups) if allowed_groups is not None else None
    lock = acquire_lockfile(_access_tokens_lock_path(home), blocking=True)
    try:
        tokens = _load_unlocked(home, for_write=True)
        current = tokens.get(normalized)
        if current is None:
            return None
        entry = copy.deepcopy(current)
        next_is_admin = entry["is_admin"] if is_admin is None else bool(is_admin)
        if next_is_admin:
            entry["allowed_groups"] = []
        elif groups is not None:
            entry["allowed_groups"] = groups
        if is_admin is not None:
            entry["is_admin"] = bool(is_admin)
        entry["updated_at"] = utc_now_iso()
        candidate = {**tokens, normalized: entry}
        candidate = _validate_candidate_store(candidate)
        _save_unlocked(candidate, home)
        return copy.deepcopy(candidate[normalized])
    finally:
        release_lockfile(lock)


def delete_access_token(token: str, home: Optional[Path] = None) -> bool:
    _guard_token_api()
    normalized = _normalized_token_argument(token)
    if not normalized:
        return False
    lock = acquire_lockfile(_access_tokens_lock_path(home), blocking=True)
    try:
        tokens = _load_unlocked(home, for_write=True)
        if normalized not in tokens:
            return False
        candidate = {key: value for key, value in tokens.items() if key != normalized}
        candidate = _validate_candidate_store(candidate)
        _save_unlocked(candidate, home)
        return True
    finally:
        release_lockfile(lock)


def list_access_tokens(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    _guard_token_api()
    items = list(_clone_tokens(_load_unlocked(home, for_write=False)).values())
    items.sort(key=lambda item: (item["created_at"], item["token"]), reverse=True)
    return items


def issue_access_token_principal_claim(
    token: str,
    *,
    group_id: str,
    require_admin: bool = False,
    home: Optional[Path] = None,
) -> AccessTokenPrincipalClaim:
    _guard_token_api()
    normalized_token = _normalized_token_argument(token)
    normalized_group = str(group_id or "").strip()
    if not normalized_token or not _valid_identity(normalized_group):
        raise AccessTokenClaimError("Access token principal claim is invalid")
    resolved_home = _resolved_home(home)
    lock = acquire_lockfile(_access_tokens_lock_path(resolved_home), blocking=True)
    try:
        tokens = _load_unlocked(resolved_home, for_write=False)
        entry = tokens.get(normalized_token)
        if entry is None:
            raise AccessTokenClaimError("Access token principal claim is invalid")
        is_admin = bool(entry["is_admin"])
        if (require_admin and not is_admin) or (not is_admin and normalized_group not in entry["allowed_groups"]):
            raise AccessTokenClaimError("Access token principal claim is not authorized")
        principal = AccessTokenPrincipal(
            user_id=entry["user_id"],
            group_id=normalized_group,
            is_admin=is_admin,
            allowed_groups=tuple(entry["allowed_groups"]),
        )
        claim = AccessTokenPrincipalClaim(_seal=_CLAIM_SEAL)
        state = _ClaimState(
            raw_token=normalized_token,
            home=resolved_home,
            entry_fingerprint=_entry_fingerprint(entry),
            principal=principal,
            require_admin=bool(require_admin),
            issued_pid=os.getpid(),
            schema_id=_STORE_SCHEMA_ID,
        )
        with _CLAIM_STATES_LOCK:
            _CLAIM_STATES[claim] = state
        return claim
    finally:
        release_lockfile(lock)
