"""Trusted-local messaging dispatch and daemon-owned Peer Insight admission.

The closed operations bind facts supplied by official adapters inside the
existing trusted-local IPC boundary. They do not authenticate arbitrary local
daemon clients.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import threading
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from ...contracts.v1.message import normalize_insight
from ...kernel.actors import find_actor, is_internal_actor, list_visible_actors
from ...kernel.group import Group, load_group
from ...kernel.peer_insight import peer_insight_required_details
from ..messaging.turn_provenance import (
    INGRESS_ACTOR_MCP,
    INGRESS_CLI_USER,
    INGRESS_IM,
    INGRESS_WEB_USER,
    TRUSTED_INGRESS_ARG,
)


INGRESS_CLAIM_ARG = "__message_ingress_claim"
ADMISSION_CLAIM_ARG = "__message_admission_claim"

_CLOSED_OPS: dict[str, tuple[str, str, str]] = {
    "actor_message_send": ("send", "actor", INGRESS_ACTOR_MCP),
    "actor_tracked_send": ("tracked_send", "actor", INGRESS_ACTOR_MCP),
    "actor_message_reply": ("reply", "actor", INGRESS_ACTOR_MCP),
    "actor_file_send": ("file_send", "actor", INGRESS_ACTOR_MCP),
    "actor_send_cross_group": ("send_cross_group", "actor", INGRESS_ACTOR_MCP),
    "actor_group_bridge_session_send": ("group_bridge_session_send", "actor", INGRESS_ACTOR_MCP),
    "actor_remote_send": ("remote_send", "actor", INGRESS_ACTOR_MCP),
    "user_message_send": ("send", "user", INGRESS_WEB_USER),
    "user_tracked_send": ("tracked_send", "user", INGRESS_WEB_USER),
    "user_message_reply": ("reply", "user", INGRESS_WEB_USER),
    "user_send_cross_group": ("send_cross_group", "user", INGRESS_WEB_USER),
    "user_group_bridge_session_send": ("group_bridge_session_send", "user", INGRESS_WEB_USER),
    "user_remote_send": ("remote_send", "user", INGRESS_WEB_USER),
    "cli_message_send": ("send", "user", INGRESS_CLI_USER),
    "cli_tracked_send": ("tracked_send", "user", INGRESS_CLI_USER),
    "cli_message_reply": ("reply", "user", INGRESS_CLI_USER),
    "im_message_send": ("send", "im", INGRESS_IM),
}

_LOCK = threading.RLock()
_T = TypeVar("_T")


class MessageAdmissionError(ValueError):
    def __init__(self, code: str, message: str, *, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class _MessageIngressClaim:
    __slots__ = ("__weakref__",)

    def __reduce__(self) -> object:
        raise TypeError("message ingress claims cannot be serialized")


@dataclass(frozen=True)
class MessageAuthority:
    kind: str
    ingress: str
    group_id: str
    sender_id: str


@dataclass(frozen=True)
class CommittedMessageAdmission:
    group_id: str
    sender_id: str
    to: tuple[str, ...]
    insight: Optional[str]
    authority_kind: str
    ingress: str


class _AdmissionClaim:
    __slots__ = ("__weakref__",)

    def __reduce__(self) -> object:
        raise TypeError("message admission claims cannot be serialized")


_INGRESS_STATES: weakref.WeakKeyDictionary[_MessageIngressClaim, tuple[int, str, str, str, bool]] = (
    weakref.WeakKeyDictionary()
)
_ADMISSION_STATES: weakref.WeakKeyDictionary[
    _AdmissionClaim,
    tuple[int, CommittedMessageAdmission, bool],
] = weakref.WeakKeyDictionary()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _args_fingerprint(args: dict[str, Any]) -> str:
    public = {key: value for key, value in args.items() if key != INGRESS_CLAIM_ARG}
    return hashlib.sha256(_canonical(public).encode("utf-8")).hexdigest()


def close_message_dispatch(op: str, args: Any) -> tuple[str, dict[str, Any]]:
    """Bind one allowlisted trusted-adapter operation to internal ingress facts."""

    mapping = _CLOSED_OPS.get(str(op or "").strip())
    if mapping is None:
        return str(op or "").strip(), args if isinstance(args, dict) else {}
    if type(args) is not dict or any(str(key).startswith("__") for key in args):
        raise MessageAdmissionError("invalid_request", "reserved messaging arguments are not accepted")
    canonical_op, authority_kind, ingress = mapping
    closed = copy.deepcopy(args)
    if authority_kind in {"user", "im"}:
        supplied_by = str(closed.get("by") or "user").strip() or "user"
        if supplied_by != "user":
            raise MessageAdmissionError("permission_denied", "this messaging surface only sends as user")
        closed["by"] = "user"
    elif not str(closed.get("by") or "").strip():
        raise MessageAdmissionError("missing_actor_id", "actor messaging dispatch requires an explicit actor id")
    closed[TRUSTED_INGRESS_ARG] = ingress
    claim = _MessageIngressClaim()
    fingerprint = _args_fingerprint(closed)
    closed[INGRESS_CLAIM_ARG] = claim
    with _LOCK:
        _INGRESS_STATES[claim] = (os.getpid(), canonical_op, authority_kind, fingerprint, False)
    return canonical_op, closed


def _consume_ingress(args: dict[str, Any], *, expected_op: str) -> MessageAuthority:
    claim = args.get(INGRESS_CLAIM_ARG)
    if type(claim) is not _MessageIngressClaim:
        raise MessageAdmissionError("message_ingress_required", "a closed messaging ingress is required")
    with _LOCK:
        state = _INGRESS_STATES.get(claim)
        if state is None:
            raise MessageAdmissionError("invalid_message_ingress", "message ingress claim is invalid")
        pid, op, authority_kind, fingerprint, used = state
        if pid != os.getpid() or used or op != expected_op or fingerprint != _args_fingerprint(args):
            raise MessageAdmissionError("invalid_message_ingress", "message ingress claim is stale or mismatched")
        _INGRESS_STATES[claim] = (pid, op, authority_kind, fingerprint, True)
    group_id = str(args.get("group_id") or "").strip()
    sender_id = str(args.get("by") or "").strip()
    ingress = str(args.get(TRUSTED_INGRESS_ARG) or "").strip()
    if not group_id:
        raise MessageAdmissionError("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        raise MessageAdmissionError("group_not_found", f"group not found: {group_id}")
    if authority_kind == "actor":
        actor = find_actor(group, sender_id)
        visible_ids = {
            str(item.get("id") or "").strip()
            for item in list_visible_actors(group)
            if isinstance(item, dict)
        }
        if not isinstance(actor, dict) or sender_id not in visible_ids or is_internal_actor(actor):
            raise MessageAdmissionError("invalid_actor_sender", "dispatch sender is not a visible group actor")
    elif sender_id != "user":
        raise MessageAdmissionError("invalid_user_sender", "user messaging ingress must send as user")
    return MessageAuthority(authority_kind, ingress, group_id, sender_id)


def _authority_facts(group: Group, authority: MessageAuthority) -> dict[str, Any]:
    actor = find_actor(group, authority.sender_id) if authority.kind == "actor" else None
    return {
        "group_id": group.group_id,
        "kind": authority.kind,
        "ingress": authority.ingress,
        "sender_id": authority.sender_id,
        "actor": copy.deepcopy(actor) if isinstance(actor, dict) else None,
    }


def _audience_facts(group: Group, *, input_tokens: list[str], to: list[str], peers: list[str]) -> dict[str, Any]:
    actors = [
        copy.deepcopy(actor)
        for actor in list_visible_actors(group)
        if isinstance(actor, dict)
    ]
    return {
        "group_id": group.group_id,
        "input_tokens": list(input_tokens),
        "to": list(to),
        "peer_actor_ids": list(peers),
        "actors": actors,
        "messaging": copy.deepcopy(group.doc.get("messaging")) if isinstance(group.doc.get("messaging"), dict) else {},
    }


def _facts_digest(source: dict[str, Any], destination: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({"source": source, "destination": destination}).encode("utf-8")).hexdigest()


@dataclass
class PendingMessageAdmission:
    authority: MessageAuthority
    source_group_id: str
    destination_group_id: str
    input_tokens: tuple[str, ...]
    to: tuple[str, ...]
    peer_actor_ids: tuple[str, ...]
    insight: Optional[str]
    facts_digest: str
    _used: bool = False

    def consume_current(self, consumer: Callable[[CommittedMessageAdmission], _T]) -> _T:
        with _LOCK:
            if self._used:
                raise MessageAdmissionError("stale_message_admission", "message admission was already consumed")
            source = load_group(self.source_group_id)
            destination = load_group(self.destination_group_id)
            if source is None or destination is None:
                raise MessageAdmissionError("stale_message_admission", "message admission group is no longer current")
            current = _facts_digest(
                _authority_facts(source, self.authority),
                _audience_facts(
                    destination,
                    input_tokens=list(self.input_tokens),
                    to=list(self.to),
                    peers=list(self.peer_actor_ids),
                ),
            )
            if current != self.facts_digest:
                raise MessageAdmissionError("stale_message_admission", "message authority or audience changed")
            self._used = True
            committed = CommittedMessageAdmission(
                group_id=self.destination_group_id,
                sender_id=self.authority.sender_id,
                to=self.to,
                insight=self.insight,
                authority_kind=self.authority.kind,
                ingress=self.authority.ingress,
            )
            return consumer(committed)


@dataclass
class PendingRemoteMessageAdmission:
    authority: MessageAuthority
    insight: Optional[str]
    destination_facts: dict[str, Any]
    current_destination_facts: Callable[[], dict[str, Any]]
    facts_digest: str
    _used: bool = False

    def consume_current(self) -> MessageAuthority:
        with _LOCK:
            if self._used:
                raise MessageAdmissionError("stale_message_admission", "remote admission was already consumed")
            source = load_group(self.authority.group_id)
            if source is None:
                raise MessageAdmissionError("stale_message_admission", "source group is no longer current")
            current = _facts_digest(
                _authority_facts(source, self.authority),
                self.current_destination_facts(),
            )
            if current != self.facts_digest:
                raise MessageAdmissionError("stale_message_admission", "remote authority or destination changed")
            self._used = True
            return self.authority


@dataclass
class BoundFileAdmission:
    admission: CommittedMessageAdmission
    device: int
    inode: int
    size: int
    mtime_ns: int
    _used: bool = False

    def consume_descriptor(self, file_fd: int, consumer: Callable[[CommittedMessageAdmission], _T]) -> _T:
        with _LOCK:
            if self._used:
                raise MessageAdmissionError("stale_file_admission", "file admission was already consumed")
            current = os.fstat(file_fd)
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
                != (self.device, self.inode, self.size, self.mtime_ns)
            ):
                raise MessageAdmissionError("stale_file_admission", "bound file descriptor changed")
            self._used = True
        return consumer(self.admission)


def bind_file_descriptor(
    pending: PendingMessageAdmission,
    file_fd: int,
    opened_stat: os.stat_result,
) -> BoundFileAdmission:
    if not stat.S_ISREG(opened_stat.st_mode):
        raise MessageAdmissionError("not_found", "file is not a regular active-scope file")

    def bind(committed: CommittedMessageAdmission) -> BoundFileAdmission:
        current = os.fstat(file_fd)
        if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
            opened_stat.st_size,
            opened_stat.st_mtime_ns,
        ):
            raise MessageAdmissionError("file_changed", "file changed before admission was bound")
        return BoundFileAdmission(
            admission=committed,
            device=current.st_dev,
            inode=current.st_ino,
            size=current.st_size,
            mtime_ns=current.st_mtime_ns,
        )

    return pending.consume_current(bind)


def issue_message_admission(
    args: dict[str, Any],
    *,
    expected_op: str,
    destination_group: Group,
    input_tokens: list[str],
    to: list[str],
    peer_actor_ids: list[str],
) -> PendingMessageAdmission:
    authority = _consume_ingress(args, expected_op=expected_op)
    try:
        insight = normalize_insight(args.get("insight"))
    except ValueError as exc:
        raise MessageAdmissionError("invalid_insight", str(exc)) from exc
    if authority.kind == "actor" and peer_actor_ids and insight is None:
        raise MessageAdmissionError(
            "peer_insight_required",
            "Not sent: this peer-facing message is missing `insight`.",
            details=peer_insight_required_details(),
        )
    source = load_group(authority.group_id)
    if source is None:
        raise MessageAdmissionError("group_not_found", f"group not found: {authority.group_id}")
    source_facts = _authority_facts(source, authority)
    destination_facts = _audience_facts(
        destination_group,
        input_tokens=input_tokens,
        to=to,
        peers=peer_actor_ids,
    )
    return PendingMessageAdmission(
        authority=authority,
        source_group_id=source.group_id,
        destination_group_id=destination_group.group_id,
        input_tokens=tuple(input_tokens),
        to=tuple(to),
        peer_actor_ids=tuple(peer_actor_ids),
        insight=insight,
        facts_digest=_facts_digest(source_facts, destination_facts),
    )


def issue_remote_message_admission(
    args: dict[str, Any],
    *,
    expected_op: str,
    destination_facts: dict[str, Any],
    current_destination_facts: Callable[[], dict[str, Any]],
) -> PendingRemoteMessageAdmission:
    authority = _consume_ingress(args, expected_op=expected_op)
    payload = args.get("payload") if isinstance(args.get("payload"), dict) else {}
    if str(payload.get("source_by") or "").strip() != authority.sender_id:
        raise MessageAdmissionError("invalid_actor_sender", "remote payload sender does not match ingress authority")
    try:
        insight = normalize_insight(payload.get("insight"))
    except ValueError as exc:
        raise MessageAdmissionError("invalid_insight", str(exc)) from exc
    if authority.kind == "actor" and insight is None:
        raise MessageAdmissionError(
            "peer_insight_required",
            "Not sent: this peer-facing message is missing `insight`.",
            details=peer_insight_required_details(),
        )
    source = load_group(authority.group_id)
    if source is None:
        raise MessageAdmissionError("group_not_found", f"group not found: {authority.group_id}")
    facts = copy.deepcopy(destination_facts)
    return PendingRemoteMessageAdmission(
        authority=authority,
        insight=insight,
        destination_facts=facts,
        current_destination_facts=current_destination_facts,
        facts_digest=_facts_digest(_authority_facts(source, authority), facts),
    )


def issue_admission_claim(admission: CommittedMessageAdmission) -> _AdmissionClaim:
    claim = _AdmissionClaim()
    with _LOCK:
        _ADMISSION_STATES[claim] = (os.getpid(), admission, False)
    return claim


def consume_admission_claim(args: dict[str, Any], *, group_id: str, sender_id: str) -> CommittedMessageAdmission:
    claim = args.get(ADMISSION_CLAIM_ARG)
    if type(claim) is not _AdmissionClaim:
        raise MessageAdmissionError("message_admission_required", "message admission claim is required")
    with _LOCK:
        state = _ADMISSION_STATES.get(claim)
        if state is None:
            raise MessageAdmissionError("invalid_message_admission", "message admission claim is invalid")
        pid, admission, used = state
        if pid != os.getpid() or used or admission.group_id != group_id or admission.sender_id != sender_id:
            raise MessageAdmissionError("invalid_message_admission", "message admission claim is stale or mismatched")
        _ADMISSION_STATES[claim] = (pid, admission, True)
        return admission


def message_admission_error(error: MessageAdmissionError) -> tuple[str, str, dict[str, Any]]:
    return error.code, error.message, copy.deepcopy(error.details)


__all__ = [
    "ADMISSION_CLAIM_ARG",
    "BoundFileAdmission",
    "INGRESS_CLAIM_ARG",
    "CommittedMessageAdmission",
    "MessageAdmissionError",
    "PendingMessageAdmission",
    "PendingRemoteMessageAdmission",
    "bind_file_descriptor",
    "close_message_dispatch",
    "consume_admission_claim",
    "issue_admission_claim",
    "issue_message_admission",
    "issue_remote_message_admission",
]
