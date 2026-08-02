"""Messages-only MCP adapter for the existing Group Bridge session daemon op."""

from __future__ import annotations

import re
from typing import Any, Dict

from ...contracts.v1.group_bridge import GroupBridgeSessionMessage
from .common import MCPError, _call_daemon_or_raise, _runtime_context
from .actor_authority import ActorMessageAuthority, consume_actor_message_authority

_REMOTE_IDEMPOTENCY_PATTERN = re.compile(r"gbs_[0-9a-f]{32}")


def require_local_runtime_binding() -> Any:
    runtime = _runtime_context()
    if str(runtime.source or "").strip().lower() != "local_mcp":
        raise MCPError(code="permission_denied", message="Group Bridge tools require a local MCP caller")
    if not str(runtime.group_id or "").strip():
        raise MCPError(code="missing_group_id", message="Group Bridge tools require a runtime-bound group")
    if not str(runtime.actor_id or "").strip():
        raise MCPError(code="missing_actor_id", message="Group Bridge tools require a runtime-bound actor")
    return runtime


def _validate_remote_identity(*, group_id: str, registration_id: str, idempotency_key: str) -> None:
    runtime = require_local_runtime_binding()
    runtime_group = str(runtime.group_id or "").strip()
    runtime_actor = str(runtime.actor_id or "").strip()
    if not runtime_group:
        raise MCPError(code="missing_group_id", message="remote Group Bridge tools require a runtime-bound group")
    if not runtime_actor:
        raise MCPError(code="missing_actor_id", message="remote Group Bridge tools require a runtime-bound actor")
    if not isinstance(group_id, str) or not group_id.strip():
        raise MCPError(code="invalid_request", message="group_id is required")
    if runtime_group and group_id.strip() != runtime_group:
        raise MCPError(code="group_id_mismatch", message="group_id does not match the MCP runtime group")
    if not isinstance(registration_id, str) or not registration_id.strip() or len(registration_id.strip()) > 256:
        raise MCPError(code="invalid_request", message="registration_id is invalid")
    if not isinstance(idempotency_key, str) or not _REMOTE_IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
        raise MCPError(code="invalid_request", message="idempotency_key is invalid")


def group_bridge_session_send(
    *,
    group_id: str,
    local_endpoint: str,
    remote_group_id: str,
    remote_peer_id: str,
    remote_endpoint: str,
    client_nonce: str,
    payload: Dict[str, Any],
    actor_authority: ActorMessageAuthority,
) -> Dict[str, Any]:
    """Delegate one closed outbound session request to the daemon owner."""
    authority = consume_actor_message_authority(
        actor_authority,
        group_id=group_id,
        actor_id=str(actor_authority.actor_id if isinstance(actor_authority, ActorMessageAuthority) else ""),
        tool_names={"onecolleague_group_bridge_session_send"},
    )
    runtime = require_local_runtime_binding()
    if str(group_id or "").strip() != str(runtime.group_id or "").strip():
        raise MCPError(code="group_id_mismatch", message="group_id does not match the MCP runtime group")

    values = {
        "group_id": group_id,
        "local_endpoint": local_endpoint,
        "remote_group_id": remote_group_id,
        "remote_peer_id": remote_peer_id,
        "remote_endpoint": remote_endpoint,
        "client_nonce": client_nonce,
        "payload": payload,
    }
    for name in (
        "group_id",
        "local_endpoint",
        "remote_group_id",
        "remote_peer_id",
        "client_nonce",
    ):
        value = values[name]
        if not isinstance(value, str) or not value.strip():
            raise MCPError(code="invalid_request", message=f"{name} is required")
    if not isinstance(remote_endpoint, str):
        raise MCPError(code="invalid_request", message="remote_endpoint must be a string")
    if not isinstance(payload, dict):
        raise MCPError(code="invalid_request", message="payload must be an object")
    try:
        message = GroupBridgeSessionMessage.model_validate(payload).model_copy(update={"source_by": authority.actor_id})
    except Exception as exc:
        raise MCPError(code="invalid_request", message="session message payload is invalid") from exc

    return _call_daemon_or_raise(
        {
            "op": "actor_group_bridge_session_send",
            "args": {
                "group_id": str(group_id).strip(),
                "local_endpoint": str(local_endpoint).strip(),
                "remote_group_id": str(remote_group_id).strip(),
                "remote_peer_id": str(remote_peer_id).strip(),
                "remote_endpoint": remote_endpoint.strip(),
                "client_nonce": client_nonce.strip(),
                "payload": message.model_dump(),
                "by": authority.actor_id,
            },
        }
    )


def remote_send(
    *,
    group_id: str,
    registration_id: str,
    idempotency_key: str,
    payload: Dict[str, Any],
    actor_authority: ActorMessageAuthority,
) -> Dict[str, Any]:
    """Queue a remote Group Bridge message through the daemon owner."""
    authority = consume_actor_message_authority(
        actor_authority,
        group_id=group_id,
        actor_id=str(actor_authority.actor_id if isinstance(actor_authority, ActorMessageAuthority) else ""),
        tool_names={"onecolleague_group_bridge_remote_send"},
    )
    require_local_runtime_binding()
    _validate_remote_identity(
        group_id=group_id,
        registration_id=registration_id,
        idempotency_key=idempotency_key,
    )
    if not isinstance(payload, dict):
        raise MCPError(code="invalid_request", message="payload must be an object")
    try:
        message = GroupBridgeSessionMessage.model_validate(payload).model_copy(update={"source_by": authority.actor_id})
    except Exception as exc:
        raise MCPError(code="invalid_request", message="remote message payload is invalid") from exc
    return _call_daemon_or_raise(
        {
            "op": "actor_remote_send",
            "args": {
                "group_id": str(group_id).strip(),
                "registration_id": str(registration_id).strip(),
                "idempotency_key": str(idempotency_key).strip(),
                "payload": message.model_dump(),
                "by": authority.actor_id,
            },
        }
    )


def remote_delivery_status(
    *,
    group_id: str,
    registration_id: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    """Read the daemon-owned public remote delivery receipt."""
    require_local_runtime_binding()
    _validate_remote_identity(
        group_id=group_id,
        registration_id=registration_id,
        idempotency_key=idempotency_key,
    )
    return _call_daemon_or_raise(
        {
            "op": "remote_delivery_status",
            "args": {
                "group_id": str(group_id).strip(),
                "registration_id": str(registration_id).strip(),
                "idempotency_key": str(idempotency_key).strip(),
            },
        }
    )


__all__ = ["group_bridge_session_send", "remote_send", "remote_delivery_status", "require_local_runtime_binding"]
