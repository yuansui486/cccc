from __future__ import annotations

from typing import Any, Dict


def route_tool_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Exercise handler routing without manufacturing persisted authorization."""
    from no1.ports.mcp import server
    from no1.ports.mcp.toolspecs import canonical_mcp_tool_name

    requested_name = str(name or "").strip()
    canonical_name = canonical_mcp_tool_name(requested_name)
    principal, group_id, actor_id, source = server._dispatch_identity(arguments)
    certificate = server._ToolCallCertificate(
        requested_name=requested_name,
        canonical_name=canonical_name,
        principal=principal,
        group_id=group_id,
        actor_id=actor_id,
        source=source,
        session_id=server._TOOL_CALL_SESSION_ID,
        state_revision="test-router",
        grant="test-router",
        nonce="test-router",
        seal=server._TOOL_CALL_CERTIFICATE_SEAL,
    )
    server._PENDING_TOOL_CALL_CERTIFICATE.set(certificate)
    return server._route_tool_call(certificate, arguments)
