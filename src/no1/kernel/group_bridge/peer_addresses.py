"""Locked runtime peer address book for Group Bridge transports."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_json
from ...util.time import parse_utc_iso, utc_now_iso

_ENTRY_FIELDS = frozenset({"peer_id", "remote_group_id", "multiaddrs", "updated_at"})


class PeerAddressStoreError(RuntimeError):
    """Raised when an existing peer address book cannot be safely mutated."""


def address_book_path(*, home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base.expanduser().resolve(strict=False) / "state" / "group_bridge" / "peer_address_book.json"


def _lock_path(home: Optional[Path] = None) -> Path:
    return address_book_path(home=home).with_suffix(".json.lock")


def _store_error() -> PeerAddressStoreError:
    return PeerAddressStoreError("Group Bridge peer address store is malformed")


def _unique_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in out:
            raise _store_error()
        out[key] = value
    return out


def _identity(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} is required")
    return value


def _clean_multiaddrs(values: Iterable[str]) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("multiaddrs must be a list of non-empty strings")
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("multiaddrs must contain only trimmed non-empty strings")
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _validate_book(peers: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not isinstance(peers, dict):
        raise _store_error()
    normalized: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for peer_id, groups in peers.items():
        if (
            not isinstance(peer_id, str)
            or not peer_id
            or peer_id != peer_id.strip()
            or not isinstance(groups, dict)
            or not groups
        ):
            raise _store_error()
        normalized_groups: Dict[str, Dict[str, Any]] = {}
        for remote_group_id, entry in groups.items():
            if (
                not isinstance(remote_group_id, str)
                or not remote_group_id
                or remote_group_id != remote_group_id.strip()
                or not isinstance(entry, dict)
                or set(entry) != _ENTRY_FIELDS
                or entry.get("peer_id") != peer_id
                or entry.get("remote_group_id") != remote_group_id
            ):
                raise _store_error()
            multiaddrs = entry.get("multiaddrs")
            updated_at = entry.get("updated_at")
            if not isinstance(multiaddrs, list):
                raise _store_error()
            if any(not isinstance(value, str) or not value or value != value.strip() for value in multiaddrs):
                raise _store_error()
            if len(set(multiaddrs)) != len(multiaddrs) or not isinstance(updated_at, str) or parse_utc_iso(updated_at) is None:
                raise _store_error()
            normalized_groups[remote_group_id] = {
                "peer_id": peer_id,
                "remote_group_id": remote_group_id,
                "multiaddrs": list(multiaddrs),
                "updated_at": updated_at,
            }
        normalized[peer_id] = normalized_groups
    return normalized


def _load_unlocked(home: Optional[Path], *, for_write: bool) -> Dict[str, Dict[str, Dict[str, Any]]]:
    path = address_book_path(home=home)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(raw, dict) or set(raw) != {"peers"}:
            raise _store_error()
        return _validate_book(raw["peers"])
    except PeerAddressStoreError:
        if for_write:
            raise
        return {}
    except Exception:
        if for_write:
            raise _store_error() from None
        return {}


def load_address_book(*, home: Optional[Path] = None) -> Dict[str, Dict[str, Dict[str, Any]]]:
    return copy.deepcopy(_load_unlocked(home, for_write=False))


def record_peer_addresses(
    peer_id: str,
    multiaddrs: Iterable[str],
    *,
    remote_group_id: str = "",
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    normalized_peer_id = _identity(peer_id, field="peer_id")
    normalized_remote_group_id = _identity(remote_group_id, field="remote_group_id")
    addresses = _clean_multiaddrs(multiaddrs)
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        book = _load_unlocked(home, for_write=True)
        entry = {
            "peer_id": normalized_peer_id,
            "remote_group_id": normalized_remote_group_id,
            "multiaddrs": addresses,
            "updated_at": utc_now_iso(),
        }
        candidate = copy.deepcopy(book)
        candidate.setdefault(normalized_peer_id, {})[normalized_remote_group_id] = entry
        candidate = _validate_book(candidate)
        atomic_write_json(address_book_path(home=home), {"peers": candidate}, indent=2)
        return copy.deepcopy(candidate[normalized_peer_id][normalized_remote_group_id])
    finally:
        release_lockfile(lock)


def resolve_peer_multiaddrs(
    peer_id: str,
    *,
    remote_group_id: str = "",
    home: Optional[Path] = None,
) -> tuple[str, ...]:
    if not isinstance(peer_id, str) or not peer_id or peer_id != peer_id.strip():
        return ()
    if not isinstance(remote_group_id, str) or not remote_group_id or remote_group_id != remote_group_id.strip():
        return ()
    groups = _load_unlocked(home, for_write=False).get(peer_id)
    if groups is None:
        return ()
    entry = groups.get(remote_group_id)
    if entry is None:
        return ()
    return tuple(entry["multiaddrs"])
