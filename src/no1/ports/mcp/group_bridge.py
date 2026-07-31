"""Messages-only MCP adapter for the existing Group Bridge session daemon op."""

from __future__ import annotations

from typing import Any, Dict

from ...contracts.v1.group_bridge import GroupBridgeSessionMessage
from .common import MCPError, _call_daemon_or_raise, _runtime_context


def group_bridge_session_send(
    *,
    group_id: str,
    local_endpoint: str,
    remote_group_id: str,
    remote_peer_id: str,
    remote_endpoint: str,
    client_nonce: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Delegate one closed outbound session request to the daemon owner."""
    runtime = _runtime_context()
    if str(runtime.source or "").strip().lower() != "local_mcp":
        raise MCPError(
            code="permission_denied",
            message="Group Bridge session send requires a local MCP caller",
        )

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
        message = GroupBridgeSessionMessage.model_validate(payload)
    except Exception as exc:
        raise MCPError(code="invalid_request", message="session message payload is invalid") from exc

    return _call_daemon_or_raise(
        {
            "op": "group_bridge_session_send",
            "args": {
                "group_id": str(group_id).strip(),
                "local_endpoint": str(local_endpoint).strip(),
                "remote_group_id": str(remote_group_id).strip(),
                "remote_peer_id": str(remote_peer_id).strip(),
                "remote_endpoint": remote_endpoint.strip(),
                "client_nonce": client_nonce.strip(),
                "payload": message.model_dump(),
            },
        }
    )


__all__ = ["group_bridge_session_send"]
