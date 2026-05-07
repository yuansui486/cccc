"""Capability surface model for MCP tool progressive disclosure.

This module defines:
1) default core tool set (always visible),
2) optional built-in capability packs (enable-on-demand),
3) built-in capsule-runtime skills (enable-on-demand),
4) helper utilities for deriving visible MCP tool names.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Set, Tuple


CORE_BASIC_TOOLS: Tuple[str, ...] = (
    "cccc_help",
    "cccc_bootstrap",
    "cccc_project_info",
    "cccc_capability_search",
    "cccc_capability_state",
    "cccc_inbox_list",
    "cccc_inbox_mark_read",
    "cccc_message_send",
    "cccc_tracked_send",
    "cccc_message_reply",
    "cccc_file",
    "cccc_presentation",
    "cccc_context_get",
    "cccc_coordination",
    "cccc_task",
    "cccc_agent_state",
    "cccc_memory",
)

CORE_ADMIN_TOOLS: Tuple[str, ...] = (
    "cccc_capability_enable",
    "cccc_capability_block",
    "cccc_capability_import",
    "cccc_capability_uninstall",
    "cccc_capability_use",
)

CORE_TOOL_NAMES: Tuple[str, ...] = CORE_BASIC_TOOLS + CORE_ADMIN_TOOLS

# Pet keeps a dedicated minimal core surface. The mutation lane stays on
# cccc_pet_decisions, with cccc_agent_state reserved for profile refresh.
PET_CORE_TOOLS: Tuple[str, ...] = (
    "cccc_help",
    "cccc_bootstrap",
    "cccc_project_info",
    "cccc_inbox_list",
    "cccc_inbox_mark_read",
    "cccc_context_get",
    "cccc_agent_state",
)

VOICE_SECRETARY_CORE_TOOLS: Tuple[str, ...] = PET_CORE_TOOLS + (
    "cccc_voice_secretary_document",
    "cccc_voice_secretary_composer",
    "cccc_voice_secretary_request",
)

SPECIALIZED_CORE_TOOL_NAMES: Tuple[str, ...] = tuple(
    sorted((set(PET_CORE_TOOLS) | set(VOICE_SECRETARY_CORE_TOOLS)) - set(CORE_TOOL_NAMES))
)


BUILTIN_CAPABILITY_PACKS: Dict[str, Dict[str, object]] = {
    "pack:group-runtime": {
        "title": "Group + Runtime Operations",
        "description": "Group state operations and actor/runtime lifecycle controls.",
        "tool_names": (
            "cccc_group",
            "cccc_actor",
            "cccc_runtime_list",
            "cccc_role_notes",
        ),
        "tags": ("group", "actor", "runtime"),
    },
    "pack:file-im": {
        "title": "IM Bind",
        "description": "IM account bind and connection support.",
        "tool_names": (
            "cccc_im_bind",
        ),
        "tags": ("im", "bind"),
    },
    "pack:space": {
        "title": "Group Space",
        "description": "NotebookLM-backed Group Space operations (consolidated action tool).",
        "tool_names": (
            "cccc_space",
        ),
        "tags": ("space", "notebooklm", "knowledge"),
    },
    "pack:automation": {
        "title": "Automation",
        "description": "Automation reminder inspection and mutation (state/manage actions).",
        "tool_names": (
            "cccc_automation",
        ),
        "tags": ("automation", "ops"),
    },
    "pack:pet": {
        "title": "Pet Decision Surface",
        "description": "Structured Web Pet reminder decision storage for the internal pet actor.",
        "tool_names": (
            "cccc_pet_decisions",
        ),
        "tags": ("pet", "decision", "web-pet"),
    },
    "pack:context-advanced": {
        "title": "Context Advanced",
        "description": "Low-level context batch sync and memory admin operations.",
        "tool_names": (
            "cccc_context_sync",
            "cccc_memory_admin",
        ),
        "tags": ("context", "memory", "admin"),
    },
    "pack:headless-notify": {
        "title": "Headless + Notify",
        "description": "Headless runner control and system notifications.",
        "tool_names": (
            "cccc_headless",
            "cccc_notify",
        ),
        "tags": ("headless", "notify", "runner"),
    },
    "pack:diagnostics": {
        "title": "Terminal Debug",
        "description": "Terminal transcript and local debug diagnostics.",
        "tool_names": (
            "cccc_terminal",
            "cccc_debug",
        ),
        "tags": ("terminal", "debug", "diagnostics"),
    },
}


BUILTIN_CAPSULE_SKILLS: Dict[str, Dict[str, object]] = {
    "skill:cccc:runtime-bootstrap": {
        "name": "runtime-bootstrap",
        "description_short": (
            "Diagnose CCCC daemon/web startup, actor runtime launch, MCP injection, "
            "bind/LAN reachability, and shutdown residue issues."
        ),
        "use_when": (
            "CCCC daemon or Web fails to start, bind, or stay reachable.",
            "Actor runtime launch, MCP injection, or shutdown cleanup looks broken.",
        ),
        "avoid_when": (
            "The task is normal product work, not runtime diagnosis.",
        ),
        "gotchas": (
            "Separate configured Web binding from the live listener before changing settings or restarting anything.",
            "Treat process residue and stale pid files as evidence to verify, not proof that the current runtime is healthy.",
        ),
        "evidence_kind": "debug snapshot plus terminal/log proof for the failing layer",
        "capsule_text": (
            "You are the runtime-bootstrap skill for CCCC runtime diagnosis.\n\n"
            "Use this skill when the task is about daemon or web startup failure, port bind or LAN "
            "reachability, actor launch/runtime state, MCP injection, or residue left after shutdown.\n\n"
            "Protocol:\n"
            "1. Restate the exact symptom and isolate the failing layer before changing anything.\n"
            "2. Gather evidence first; prefer read-only inspection and existing diagnostics/runtime tools.\n"
            "3. Check one layer at a time: process start -> bind/port -> group/actor runtime -> MCP "
            "injection -> shutdown cleanup.\n"
            "4. Report findings as: Symptom, Evidence, Failed layer, Most likely root cause, Next safe action.\n"
            "5. Do not kill, restart, or mutate runtime state unless the user explicitly asks after evidence is gathered.\n"
            "6. Prefer the smallest reversible fix. If two hypotheses fail, stop stacking guards and surface evidence."
        ),
        "tags": ("runtime", "bootstrap", "diagnostics", "daemon", "web", "mcp"),
        "requires_capabilities": ("pack:diagnostics", "pack:group-runtime"),
    },
}


def all_builtin_pack_ids() -> List[str]:
    return sorted(BUILTIN_CAPABILITY_PACKS.keys())


def all_builtin_skill_ids() -> List[str]:
    return sorted(BUILTIN_CAPSULE_SKILLS.keys())


def core_tool_name_set() -> Set[str]:
    return set(CORE_TOOL_NAMES)


def all_pack_tool_name_set() -> Set[str]:
    names: Set[str] = set()
    for pack in BUILTIN_CAPABILITY_PACKS.values():
        for tool_name in pack.get("tool_names", ()):  # type: ignore[arg-type]
            names.add(str(tool_name))
    return names


def resolve_core_tool_names(
    *,
    actor_role: str = "",
    is_pet: bool = False,
    is_voice_secretary: bool = False,
) -> Set[str]:
    if bool(is_voice_secretary):
        return set(VOICE_SECRETARY_CORE_TOOLS)
    if bool(is_pet):
        return set(PET_CORE_TOOLS)
    role = str(actor_role or "").strip().lower()
    if role == "peer":
        return set(CORE_BASIC_TOOLS)
    return set(CORE_TOOL_NAMES)


def resolve_visible_tool_names(
    enabled_capability_ids: Iterable[str],
    *,
    actor_role: str = "",
    is_pet: bool = False,
    is_voice_secretary: bool = False,
) -> Set[str]:
    visible = resolve_core_tool_names(
        actor_role=actor_role,
        is_pet=is_pet,
        is_voice_secretary=is_voice_secretary,
    )
    for cap_id in enabled_capability_ids:
        cap = BUILTIN_CAPABILITY_PACKS.get(str(cap_id))
        if not isinstance(cap, dict):
            continue
        for tool_name in cap.get("tool_names", ()):  # type: ignore[arg-type]
            visible.add(str(tool_name))
    return visible
