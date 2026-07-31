"""Stable local signing identity for Group Bridge."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from ...paths import ensure_home
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_text

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_IDENTITY_FIELDS = frozenset({"private_key", "public_key", "peer_id"})


class GroupBridgeIdentityStoreError(RuntimeError):
    """Raised when an existing signing identity cannot be trusted."""


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
            raise ConstructorError(None, None, "identity mapping is malformed", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_string_mapping)


@dataclass(frozen=True)
class GroupBridgeIdentity:
    peer_id: str
    public_key_b64: str

    def public_dict(self) -> Dict[str, str]:
        return {"peer_id": self.peer_id, "public_key": self.public_key_b64}


@dataclass(frozen=True)
class _IdentityMaterial:
    peer_id: str
    public_key_b64: str
    private_key_b64: str

    def public(self) -> GroupBridgeIdentity:
        return GroupBridgeIdentity(peer_id=self.peer_id, public_key_b64=self.public_key_b64)


def _identity_path(home: Optional[Path] = None) -> Path:
    base = Path(home) if home is not None else ensure_home()
    return base.expanduser().resolve(strict=False) / "group_bridge_identity_key.yaml"


def _lock_path(home: Optional[Path] = None) -> Path:
    return _identity_path(home).with_suffix(".yaml.lock")


def _store_error() -> GroupBridgeIdentityStoreError:
    return GroupBridgeIdentityStoreError("Group Bridge identity store is malformed")


def _b58encode(raw: bytes) -> str:
    value = int.from_bytes(raw, "big")
    encoded = ""
    while value:
        value, remainder = divmod(value, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    padding = 0
    for byte in raw:
        if byte != 0:
            break
        padding += 1
    return (_BASE58_ALPHABET[0] * padding) + (encoded or _BASE58_ALPHABET[0])


def _peer_id_for_public_key(public_key: bytes) -> str:
    protobuf = bytes([0x08, 0x01, 0x12, len(public_key)]) + public_key
    return _b58encode(bytes([0x00, len(protobuf)]) + protobuf)


def peer_id_from_public_key_b64(public_key_b64: str) -> str:
    try:
        if not isinstance(public_key_b64, str):
            raise ValueError
        public_key = base64.b64decode(public_key_b64.encode("ascii"), validate=True)
        if len(public_key) != 32:
            raise ValueError
    except Exception:
        raise ValueError("Group Bridge public key must be a 32-byte Ed25519 key") from None
    return _peer_id_for_public_key(public_key)


def canonical_payload_bytes(payload: Dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Group Bridge signed payload must be JSON serializable") from exc
    return encoded.encode("utf-8")


def _identity_from_doc(raw: Any) -> _IdentityMaterial:
    try:
        if not isinstance(raw, dict) or set(raw) != _IDENTITY_FIELDS:
            raise _store_error()
        private_b64 = raw["private_key"]
        public_b64 = raw["public_key"]
        peer_id = raw["peer_id"]
        if not all(isinstance(value, str) and value for value in (private_b64, public_b64, peer_id)):
            raise _store_error()
        private_raw = base64.b64decode(private_b64.encode("ascii"), validate=True)
        key = Ed25519PrivateKey.from_private_bytes(private_raw)
        public_raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        expected_public = base64.b64encode(public_raw).decode("ascii")
        expected_peer = _peer_id_for_public_key(public_raw)
        if public_b64 != expected_public or peer_id != expected_peer:
            raise _store_error()
        return _IdentityMaterial(peer_id=peer_id, public_key_b64=public_b64, private_key_b64=private_b64)
    except GroupBridgeIdentityStoreError:
        raise
    except Exception:
        raise _store_error() from None


def _load_unlocked(home: Optional[Path], *, for_write: bool) -> Optional[_IdentityMaterial]:
    path = _identity_path(home)
    if not path.exists():
        return None
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictSafeLoader)
        return _identity_from_doc(raw)
    except GroupBridgeIdentityStoreError:
        if for_write:
            raise
        return None
    except Exception:
        if for_write:
            raise _store_error() from None
        return None


def _new_identity() -> _IdentityMaterial:
    key = Ed25519PrivateKey.generate()
    private_raw = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _IdentityMaterial(
        peer_id=_peer_id_for_public_key(public_raw),
        public_key_b64=base64.b64encode(public_raw).decode("ascii"),
        private_key_b64=base64.b64encode(private_raw).decode("ascii"),
    )


def _enforce_private_mode(home: Optional[Path]) -> None:
    try:
        os.chmod(_identity_path(home), 0o600)
    except OSError:
        raise GroupBridgeIdentityStoreError("Group Bridge identity permissions could not be secured") from None


def load_group_bridge_identity(*, home: Optional[Path] = None) -> Optional[GroupBridgeIdentity]:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        material = _load_unlocked(home, for_write=False)
        if material is None:
            return None
        _enforce_private_mode(home)
        return material.public()
    finally:
        release_lockfile(lock)


def get_group_bridge_identity(*, home: Optional[Path] = None) -> GroupBridgeIdentity:
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        material = _load_unlocked(home, for_write=True)
        if material is not None:
            _enforce_private_mode(home)
            return material.public()
        material = _new_identity()
        payload = {
            "private_key": material.private_key_b64,
            "public_key": material.public_key_b64,
            "peer_id": material.peer_id,
        }
        atomic_write_text(
            _identity_path(home),
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=True, default_flow_style=False),
        )
        _enforce_private_mode(home)
        return material.public()
    finally:
        release_lockfile(lock)


def sign_group_bridge_payload(payload: bytes, *, home: Optional[Path] = None) -> str:
    if not isinstance(payload, bytes):
        raise TypeError("Group Bridge signing payload must be bytes")
    lock = acquire_lockfile(_lock_path(home), blocking=True)
    try:
        material = _load_unlocked(home, for_write=True)
        if material is None:
            material = _new_identity()
            atomic_write_text(
                _identity_path(home),
                yaml.safe_dump(
                    {
                        "private_key": material.private_key_b64,
                        "public_key": material.public_key_b64,
                        "peer_id": material.peer_id,
                    },
                    allow_unicode=True,
                    sort_keys=True,
                    default_flow_style=False,
                ),
            )
        _enforce_private_mode(home)
        raw = base64.b64decode(material.private_key_b64.encode("ascii"), validate=True)
        signature = Ed25519PrivateKey.from_private_bytes(raw).sign(payload)
        return base64.b64encode(signature).decode("ascii")
    finally:
        release_lockfile(lock)
