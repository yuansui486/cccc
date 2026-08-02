"""Process-local projection of one validated T095 MCP tool-call certificate."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from .common import MCPError


_SEAL = object()
_PENDING: ContextVar[Optional["ActorMessageAuthority"]] = ContextVar(
    "onecolleague_pending_actor_message_authority",
    default=None,
)


@dataclass(frozen=True, slots=True)
class ActorMessageAuthority:
    group_id: str
    actor_id: str
    source: str
    tool_name: str
    nonce: str
    seal: object


def _issue_actor_message_authority(
    *,
    group_id: str,
    actor_id: str,
    source: str,
    tool_name: str,
    nonce: str,
) -> ActorMessageAuthority:
    _PENDING.set(None)
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    src = str(source or "").strip().lower()
    if not gid or not aid or aid == "user" or src != "local_mcp" or not str(nonce or "").strip():
        raise MCPError(code="permission_denied", message="actor messaging requires a validated local MCP certificate")
    authority = ActorMessageAuthority(gid, aid, src, str(tool_name or "").strip(), str(nonce), _SEAL)
    _PENDING.set(authority)
    return authority


def consume_actor_message_authority(
    value: object,
    *,
    group_id: str,
    actor_id: str,
    tool_names: set[str],
) -> ActorMessageAuthority:
    pending = _PENDING.get()
    _PENDING.set(None)
    if (
        pending is not value
        or not isinstance(value, ActorMessageAuthority)
        or value.seal is not _SEAL
        or value.group_id != str(group_id or "").strip()
        or value.actor_id != str(actor_id or "").strip()
        or value.source != "local_mcp"
        or value.tool_name not in tool_names
    ):
        raise MCPError(code="permission_denied", message="actor messaging authority is missing or mismatched")
    return value


__all__ = ["ActorMessageAuthority", "consume_actor_message_authority"]
