"""Canonical MCP tool ownership metadata.

Every built-in tool has exactly one primary owner. Capability-pack membership
is compatibility catalog data only; product and disabled owners take
precedence over any overlapping pack entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .toolspecs import CANONICAL_MCP_TOOLS, canonical_mcp_tool_name

ToolOwnerKind = Literal["core", "pack", "product", "disabled"]


@dataclass(frozen=True, slots=True)
class MCPToolOwner:
    kind: ToolOwnerKind
    owner_id: str
    list_policy: str
    call_guard: str
    availability: str


_CORE_TOOL_NAMES = frozenset(
    {
        "onecolleague_help",
        "onecolleague_bootstrap",
        "onecolleague_project_info",
        "onecolleague_capability_search",
        "onecolleague_capability_state",
        "onecolleague_capability_enable",
        "onecolleague_capability_install",
        "onecolleague_capability_use",
        "onecolleague_inbox_list",
        "onecolleague_inbox_mark_read",
        "onecolleague_message_send",
        "onecolleague_tracked_send",
        "onecolleague_message_reply",
        "onecolleague_file",
        "onecolleague_repo",
        "onecolleague_presentation",
        "onecolleague_context_get",
        "onecolleague_coordination",
        "onecolleague_task",
        "onecolleague_agent_state",
        "onecolleague_memory",
        "onecolleague_experience",
    }
)

_PACK_OWNER_BY_TOOL = {
    "onecolleague_group": "pack:group-runtime",
    "onecolleague_actor": "pack:group-runtime",
    "onecolleague_runtime_list": "pack:group-runtime",
    "onecolleague_role_notes": "pack:group-runtime",
    "onecolleague_im_bind": "pack:file-im",
    "onecolleague_space": "pack:space",
    "onecolleague_automation": "pack:automation",
    "onecolleague_context_sync": "pack:context-advanced",
    "onecolleague_memory_admin": "pack:context-advanced",
    "onecolleague_headless": "pack:headless-notify",
    "onecolleague_notify": "pack:headless-notify",
    "onecolleague_terminal": "pack:diagnostics",
    "onecolleague_debug": "pack:diagnostics",
    "onecolleague_capability_import": "pack:capability-admin",
    "onecolleague_capability_block": "pack:capability-admin",
    "onecolleague_capability_uninstall": "pack:capability-admin",
}

_PRODUCT_TOOL_METADATA = {
    "onecolleague_group_bridge_session_send": ("group-bridge", "local_group_bridge", "local_mcp"),
    "onecolleague_group_bridge_remote_send": ("group-bridge", "local_group_bridge", "local_mcp"),
    "onecolleague_group_bridge_remote_delivery_status": ("group-bridge", "local_group_bridge", "local_mcp"),
    "onecolleague_computer_control_catalog": ("computer-control", "local_computer_control", "local_actor"),
    "onecolleague_computer_recording": ("computer-control", "local_computer_control", "local_actor"),
    "onecolleague_computer_workflow": ("computer-control", "local_computer_control", "local_actor"),
    "onecolleague_computer_run": ("computer-control", "local_computer_control", "local_actor"),
    "onecolleague_repo_edit": ("web-repo", "web_model_actor", "web_model"),
    "onecolleague_apply_patch": ("web-repo", "web_model_actor", "web_model"),
    "onecolleague_runtime_wait_next_turn": ("runtime-turn", "runtime_turn", "web_model"),
    "onecolleague_runtime_complete_turn": ("runtime-turn", "runtime_turn", "web_model"),
    "onecolleague_voice_secretary_document": ("voice-secretary", "voice_secretary", "fixed_actor"),
    "onecolleague_voice_secretary_request": ("voice-secretary", "voice_secretary", "fixed_actor"),
    "onecolleague_voice_secretary_composer": ("voice-secretary", "voice_secretary", "fixed_actor"),
}

_DISABLED_TOOL_NAMES = frozenset(
    {
        "onecolleague_shell",
        "onecolleague_exec_command",
        "onecolleague_write_stdin",
        "onecolleague_code_exec",
        "onecolleague_code_wait",
        "onecolleague_git",
    }
)


def _build_primary_owners() -> dict[str, MCPToolOwner]:
    owners: dict[str, MCPToolOwner] = {}
    owners.update(
        {
            name: MCPToolOwner("core", "core", "core", "core", "builtin")
            for name in _CORE_TOOL_NAMES
        }
    )
    owners.update(
        {
            name: MCPToolOwner("pack", pack_id, "current_grant", "exact_pack", "capability_state")
            for name, pack_id in _PACK_OWNER_BY_TOOL.items()
        }
    )
    owners.update(
        {
            name: MCPToolOwner("product", owner_id, guard, guard, availability)
            for name, (owner_id, guard, availability) in _PRODUCT_TOOL_METADATA.items()
        }
    )
    owners.update(
        {
            name: MCPToolOwner("disabled", "disabled", "never", "disabled", "never")
            for name in _DISABLED_TOOL_NAMES
        }
    )
    return owners


MCP_TOOL_PRIMARY_OWNERS = _build_primary_owners()
CANONICAL_MCP_TOOL_NAMES = frozenset(
    canonical_mcp_tool_name(str(spec.get("name") or ""))
    for spec in CANONICAL_MCP_TOOLS
    if isinstance(spec, dict) and str(spec.get("name") or "").strip()
)

# Compatibility view for callers that inspect independent product metadata.
# It is derived from the primary-owner table and is not an authorization source.
MCP_TOOL_SURFACE_METADATA = {
    name: {"precondition": owner.call_guard, "independent": True}
    for name, owner in MCP_TOOL_PRIMARY_OWNERS.items()
    if owner.kind == "product"
}


def resolve_mcp_tool_owner(name: str) -> Optional[MCPToolOwner]:
    """Return the primary owner for a canonical built-in, or None for dynamic names."""

    return MCP_TOOL_PRIMARY_OWNERS.get(canonical_mcp_tool_name(name))


def is_canonical_mcp_tool(name: str) -> bool:
    return canonical_mcp_tool_name(name) in CANONICAL_MCP_TOOL_NAMES


def pack_owned_tool_names() -> frozenset[str]:
    return frozenset(name for name, owner in MCP_TOOL_PRIMARY_OWNERS.items() if owner.kind == "pack")


def disabled_tool_names() -> frozenset[str]:
    return _DISABLED_TOOL_NAMES
