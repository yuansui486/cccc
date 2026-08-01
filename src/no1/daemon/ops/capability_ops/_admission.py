"""Stable current capability admission certificates.

The certificate is a short-lived authorization projection. Persisted bindings
remain observable, but only capabilities admitted by the current policy,
catalog, source, and runtime snapshots may reach executable surfaces.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from ....kernel.actors import find_actor, get_effective_role
from ....kernel.capabilities import BUILTIN_CAPABILITY_PACKS, BUILTIN_CAPSULE_SKILLS, canonical_builtin_skill_id
from ....kernel.group import load_group
from ....util.time import parse_utc_iso
from ._common import (
    BUILTIN_SOURCE_ID,
    _CATALOG_LOCK,
    _POLICY_CACHE,
    _POLICY_LOCK,
    _RUNTIME_LOCK,
    _STATE_LOCK,
    _catalog_path,
    _capability_root,
    _normalize_source_id,
    _runtime_path,
    _state_path,
)
from ._documents import _load_catalog_doc, _load_runtime_doc, _load_state_doc
from ._install import _record_enable_supported, _tool_name_aliases
from ._policy import _allowlist_effective_snapshot, _allowlist_policy, _effective_policy_level, _policy_level_visible
from ._removed import _collect_removed_capabilities
from ._runtime import _runtime_actor_bindings, _runtime_artifacts, _runtime_capability_artifacts
from ._state import (
    _binding_state_allows_external_tool,
    _collect_blocked_capabilities,
    _collect_enabled_capabilities,
    _install_state_allows_external_tool,
)

_MAX_SNAPSHOT_ATTEMPTS = 3
_SOURCE_ENV_FLAGS = {
    "mcp_registry_official": "CCCC_CAPABILITY_SOURCE_MCP_REGISTRY_ENABLED",
    "anthropic_skills": "CCCC_CAPABILITY_SOURCE_ANTHROPIC_SKILLS_ENABLED",
    "skillsmp_remote": "CCCC_CAPABILITY_SOURCE_SKILLSMP_REMOTE_ENABLED",
    "clawhub_remote": "CCCC_CAPABILITY_SOURCE_CLAWHUB_REMOTE_ENABLED",
    "openclaw_skills_remote": "CCCC_CAPABILITY_SOURCE_OPENCLAW_SKILLS_REMOTE_ENABLED",
    "clawskills_remote": "CCCC_CAPABILITY_SOURCE_CLAWSKILLS_REMOTE_ENABLED",
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _read_raw(path: Path) -> Tuple[str, str]:
    if not path.exists():
        return "<missing>", ""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return "", f"read_failed:{path.name}:{e}"
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("document root must be an object")
    except Exception as e:
        return text, f"parse_failed:{path.name}:{e}"
    return text, ""


def _document_snapshot(
    lock: Any,
    path: Path,
    loader: Callable[[], Tuple[Path, Dict[str, Any]]],
) -> Tuple[Dict[str, Any], str, str]:
    with lock:
        raw, error = _read_raw(path)
        _, doc = loader()
        return copy.deepcopy(doc), raw, error


def _env_enabled(name: str) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return True
    return raw not in {"0", "false", "no", "off"}


def _source_control_snapshot() -> Tuple[Dict[str, bool], Dict[str, Any]]:
    enabled = {source_id: _env_enabled(env_name) for source_id, env_name in _SOURCE_ENV_FLAGS.items()}
    identity: Dict[str, Any] = {
        "environment": {env_name: os.environ.get(env_name) for env_name in _SOURCE_ENV_FLAGS.values()},
    }
    path = _capability_root() / "onecolleague_skill_library_source.json"
    raw = "<missing>"
    error = ""
    library_enabled = True
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("document root must be an object")
            source = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
            library_enabled = bool(source.get("enabled", True))
        except Exception as e:
            error = f"onecolleague_source_invalid:{e}"
            library_enabled = False
    enabled["onecolleague_skill_library"] = library_enabled
    identity["onecolleague_skill_library_raw"] = raw
    identity["onecolleague_skill_library_error"] = error
    return enabled, identity


def _snapshot_once(group_id: str, actor_id: str) -> Dict[str, Any]:
    with _POLICY_LOCK:
        policy_snapshot = _allowlist_effective_snapshot()
        policy = copy.deepcopy(_allowlist_policy())
        policy_error = str(_POLICY_CACHE.get("error") or "").strip()

    group = load_group(group_id)
    group_raw = ""
    group_error = ""
    actor_role = ""
    actor_exists = actor_id == "user"
    if group is None:
        group_error = "group_not_found_or_invalid"
    else:
        try:
            group_raw = (group.path / "group.yaml").read_text(encoding="utf-8")
        except Exception as e:
            group_error = f"group_read_failed:{e}"
        if actor_id == "user":
            actor_exists = True
        else:
            actor_exists = isinstance(find_actor(group, actor_id), dict)
            try:
                actor_role = str(get_effective_role(group, actor_id) or "").strip().lower()
            except Exception:
                actor_role = ""

    state_doc, state_raw, state_error = _document_snapshot(
        _STATE_LOCK, _state_path(), _load_state_doc
    )
    catalog_doc, catalog_raw, catalog_error = _document_snapshot(
        _CATALOG_LOCK, _catalog_path(), _load_catalog_doc
    )
    runtime_doc, runtime_raw, runtime_error = _document_snapshot(
        _RUNTIME_LOCK, _runtime_path(), _load_runtime_doc
    )
    source_controls, source_control_identity = _source_control_snapshot()
    errors = [
        item
        for item in (
            str(policy_snapshot.get("default_error") or "").strip(),
            str(policy_snapshot.get("overlay_error") or "").strip(),
            policy_error,
            group_error,
            "" if actor_exists else "actor_not_found",
            state_error,
            catalog_error,
            runtime_error,
        )
        if item
    ]
    identity = {
        "group_raw": group_raw,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "policy_revision": str(policy_snapshot.get("revision") or ""),
        "policy_default_text": str(policy_snapshot.get("default_text") or ""),
        "policy_overlay_text": str(policy_snapshot.get("overlay_text") or ""),
        "policy_errors": [
            str(policy_snapshot.get("default_error") or ""),
            str(policy_snapshot.get("overlay_error") or ""),
            policy_error,
        ],
        "policy": policy,
        "state_raw": state_raw,
        "catalog_raw": catalog_raw,
        "runtime_raw": runtime_raw,
        "document_errors": [state_error, catalog_error, runtime_error],
        "source_controls": source_control_identity,
    }
    return {
        "fingerprint": _fingerprint(identity),
        "errors": errors,
        "group": group,
        "actor_role": actor_role,
        "policy_snapshot": copy.deepcopy(policy_snapshot),
        "policy": policy,
        "state_doc": state_doc,
        "catalog_doc": catalog_doc,
        "runtime_doc": runtime_doc,
        "source_controls": source_controls,
    }


def _activation_sources(state_doc: Dict[str, Any], *, group_id: str, actor_id: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    group_enabled = state_doc.get("group_enabled") if isinstance(state_doc.get("group_enabled"), dict) else {}
    for cap_id in group_enabled.get(group_id) if isinstance(group_enabled.get(group_id), list) else []:
        cid = canonical_builtin_skill_id(str(cap_id or "").strip())
        if cid:
            out.setdefault(cid, []).append({"scope": "group"})
    actor_enabled = state_doc.get("actor_enabled") if isinstance(state_doc.get("actor_enabled"), dict) else {}
    per_group_actor = actor_enabled.get(group_id) if isinstance(actor_enabled.get(group_id), dict) else {}
    for cap_id in per_group_actor.get(actor_id) if isinstance(per_group_actor.get(actor_id), list) else []:
        cid = canonical_builtin_skill_id(str(cap_id or "").strip())
        if cid:
            out.setdefault(cid, []).append({"scope": "actor", "actor_id": actor_id})
    session_enabled = state_doc.get("session_enabled") if isinstance(state_doc.get("session_enabled"), dict) else {}
    per_group_session = session_enabled.get(group_id) if isinstance(session_enabled.get(group_id), dict) else {}
    now = datetime.now(timezone.utc)
    for item in per_group_session.get(actor_id) if isinstance(per_group_session.get(actor_id), list) else []:
        if not isinstance(item, dict):
            continue
        cid = canonical_builtin_skill_id(str(item.get("capability_id") or "").strip())
        expires_at = str(item.get("expires_at") or "").strip()
        expires = parse_utc_iso(expires_at)
        if cid and expires is not None and expires > now:
            out.setdefault(cid, []).append(
                {"scope": "session", "actor_id": actor_id, "expires_at": expires_at}
            )
    return out


def _record_denial_reason(
    *,
    capability_id: str,
    record: Dict[str, Any],
    policy: Dict[str, Any],
    catalog_doc: Dict[str, Any],
    actor_role: str,
    source_controls: Dict[str, bool] | None = None,
) -> str:
    source_id = _normalize_source_id(record.get("source_id"))
    current_source_controls = source_controls
    if not isinstance(current_source_controls, dict):
        current_source_controls, _ = _source_control_snapshot()
    if source_id and current_source_controls.get(source_id) is False:
        return "source_disabled_by_runtime_config"
    source_enabled = policy.get("source_enabled") if isinstance(policy.get("source_enabled"), dict) else {}
    if source_id and source_enabled.get(source_id) is False:
        return "source_disabled_by_policy"
    sources = catalog_doc.get("sources") if isinstance(catalog_doc.get("sources"), dict) else {}
    source_state = sources.get(source_id) if isinstance(sources.get(source_id), dict) else {}
    if str(source_state.get("sync_state") or "").strip().lower() == "disabled":
        return "source_disabled"
    policy_level = _effective_policy_level(
        policy,
        capability_id=capability_id,
        kind=str(record.get("kind") or ""),
        source_id=source_id,
        actor_role=actor_role,
    )
    if not _policy_level_visible(policy_level):
        return "policy_level_indexed"
    if str(record.get("qualification_status") or "").strip().lower() != "qualified":
        return "qualification_not_qualified"
    if not _record_enable_supported(record, capability_id=capability_id):
        return "capability_unavailable"
    return ""


def _zero_certificate(group_id: str, actor_id: str, reason: str, snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    snap = snapshot if isinstance(snapshot, dict) else {}
    policy_snapshot = snap.get("policy_snapshot") if isinstance(snap.get("policy_snapshot"), dict) else {}
    state_doc = copy.deepcopy(snap.get("state_doc") or {})
    raw_bindings: List[str] = []
    activation_sources: Dict[str, List[Dict[str, Any]]] = {}
    try:
        raw_bindings, _ = _collect_enabled_capabilities(state_doc, group_id=group_id, actor_id=actor_id)
        activation_sources = _activation_sources(state_doc, group_id=group_id, actor_id=actor_id)
    except Exception:
        raw_bindings = []
        activation_sources = {}
    return {
        "stable": bool(snap) and reason != "unstable_current_admission",
        "valid": False,
        "group_id": group_id,
        "actor_id": actor_id,
        "actor_role": str(snap.get("actor_role") or ""),
        "fingerprint": str(snap.get("fingerprint") or ""),
        "policy_revision": str(policy_snapshot.get("revision") or ""),
        "policy_error": reason,
        "raw_bindings": raw_bindings,
        "activation_sources": activation_sources,
        "authorized_capabilities": [],
        "admitted_capabilities": [],
        "denied_capabilities": {capability_id: reason for capability_id in raw_bindings},
        "builtin_tool_grants": {},
        "external_tool_grants": [],
        "admitted_records": {},
        "_group": snap.get("group"),
        "_policy": copy.deepcopy(snap.get("policy") or {}),
        "_state_doc": state_doc,
        "_catalog_doc": copy.deepcopy(snap.get("catalog_doc") or {}),
        "_runtime_doc": copy.deepcopy(snap.get("runtime_doc") or {}),
        "_source_controls": copy.deepcopy(snap.get("source_controls") or {}),
    }


def _build_certificate(group_id: str, actor_id: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    errors = [str(item or "").strip() for item in snapshot.get("errors") or [] if str(item or "").strip()]
    if errors:
        return _zero_certificate(group_id, actor_id, errors[0], snapshot)
    policy = snapshot.get("policy") if isinstance(snapshot.get("policy"), dict) else {}
    state_doc = copy.deepcopy(snapshot.get("state_doc") or {})
    catalog_doc = snapshot.get("catalog_doc") if isinstance(snapshot.get("catalog_doc"), dict) else {}
    runtime_doc = snapshot.get("runtime_doc") if isinstance(snapshot.get("runtime_doc"), dict) else {}
    actor_role = str(snapshot.get("actor_role") or "")
    source_controls = snapshot.get("source_controls") if isinstance(snapshot.get("source_controls"), dict) else {}
    raw_bindings, _ = _collect_enabled_capabilities(state_doc, group_id=group_id, actor_id=actor_id)
    blocked, _ = _collect_blocked_capabilities(state_doc, group_id=group_id)
    removed = set(_collect_removed_capabilities(state_doc, group_id=group_id))
    activation_sources = _activation_sources(state_doc, group_id=group_id, actor_id=actor_id)
    denied: Dict[str, str] = {}
    authorized: List[str] = []
    authorized_records: Dict[str, Dict[str, Any]] = {}
    records = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
    for raw_capability_id in raw_bindings:
        capability_id = canonical_builtin_skill_id(str(raw_capability_id or "").strip())
        if not capability_id:
            continue
        if capability_id in blocked:
            denied[capability_id] = "blocked"
            continue
        if capability_id in removed:
            denied[capability_id] = "removed"
            continue
        if capability_id in BUILTIN_CAPABILITY_PACKS:
            level = _effective_policy_level(
                policy,
                capability_id=capability_id,
                kind="mcp_toolpack",
                source_id=BUILTIN_SOURCE_ID,
                actor_role=actor_role,
            )
            if not _policy_level_visible(level):
                denied[capability_id] = "policy_level_indexed"
                continue
            authorized.append(capability_id)
            continue
        if capability_id in BUILTIN_CAPSULE_SKILLS:
            skill = BUILTIN_CAPSULE_SKILLS.get(capability_id) or {}
            record = {
                **dict(skill),
                "capability_id": capability_id,
                "kind": "skill",
                "source_id": BUILTIN_SOURCE_ID,
                "source_tier": "builtin",
                "trust_tier": "builtin",
                "qualification_status": "qualified",
                "sync_state": "fresh",
                "enable_supported": True,
            }
            reason = _record_denial_reason(
                capability_id=capability_id,
                record=record,
                policy=policy,
                catalog_doc=catalog_doc,
                actor_role=actor_role,
                source_controls=source_controls,
            )
            if reason:
                denied[capability_id] = reason
                continue
            authorized.append(capability_id)
            authorized_records[capability_id] = record
            continue
        record = records.get(capability_id) if isinstance(records.get(capability_id), dict) else None
        if not isinstance(record, dict):
            denied[capability_id] = "catalog_record_missing"
            continue
        reason = _record_denial_reason(
            capability_id=capability_id,
            record=record,
            policy=policy,
            catalog_doc=catalog_doc,
            actor_role=actor_role,
            source_controls=source_controls,
        )
        if reason:
            denied[capability_id] = reason
            continue
        authorized.append(capability_id)
        authorized_records[capability_id] = copy.deepcopy(record)

    artifacts = _runtime_artifacts(runtime_doc)
    capability_artifacts = _runtime_capability_artifacts(runtime_doc)
    actor_bindings = _runtime_actor_bindings(runtime_doc)
    per_group = actor_bindings.get(group_id) if isinstance(actor_bindings.get(group_id), dict) else {}
    per_actor = per_group.get(actor_id) if isinstance(per_group.get(actor_id), dict) else {}
    external_tool_grants: List[Dict[str, Any]] = []
    executable_external_mcp: set[str] = set()
    for capability_id in authorized:
        record = authorized_records.get(capability_id)
        if not isinstance(record, dict) or str(record.get("kind") or "").strip().lower() != "mcp_toolpack":
            continue
        binding = per_actor.get(capability_id) if isinstance(per_actor.get(capability_id), dict) else None
        if not isinstance(binding, dict) or not _binding_state_allows_external_tool(binding.get("state")):
            continue
        artifact_id = str(binding.get("artifact_id") or capability_artifacts.get(capability_id) or "").strip()
        install = artifacts.get(artifact_id) if isinstance(artifacts.get(artifact_id), dict) else None
        if not isinstance(install, dict) or not _install_state_allows_external_tool(install.get("state")):
            continue
        tools = install.get("tools") if isinstance(install.get("tools"), list) else []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            synthetic_name = str(tool.get("name") or "").strip()
            real_name = str(tool.get("real_tool_name") or "").strip()
            if not synthetic_name or not real_name:
                continue
            external_tool_grants.append(
                {
                    "capability_id": capability_id,
                    "artifact_id": artifact_id,
                    "install": copy.deepcopy(install),
                    "name": synthetic_name,
                    "real_tool_name": real_name,
                    "aliases": sorted(
                        set(_tool_name_aliases(synthetic_name))
                        | set(_tool_name_aliases(real_name))
                    ),
                    "description": str(tool.get("description") or "").strip(),
                    "inputSchema": copy.deepcopy(tool.get("inputSchema") or {"type": "object", "properties": {}}),
                }
            )
            executable_external_mcp.add(capability_id)

    admitted: List[str] = []
    admitted_records: Dict[str, Dict[str, Any]] = {}
    for capability_id in authorized:
        record = authorized_records.get(capability_id)
        if isinstance(record, dict) and str(record.get("kind") or "").strip().lower() == "mcp_toolpack":
            if capability_id not in executable_external_mcp:
                denied[capability_id] = "runtime_not_executable"
                continue
        admitted.append(capability_id)
        if isinstance(record, dict):
            admitted_records[capability_id] = copy.deepcopy(record)

    builtin_tool_grants: Dict[str, List[str]] = {}
    for capability_id in admitted:
        pack = BUILTIN_CAPABILITY_PACKS.get(capability_id)
        if not isinstance(pack, dict):
            continue
        for raw_tool_name in pack.get("tool_names") if isinstance(pack.get("tool_names"), (list, tuple)) else []:
            tool_name = str(raw_tool_name or "").strip()
            if tool_name:
                builtin_tool_grants.setdefault(tool_name, []).append(capability_id)

    policy_snapshot = snapshot.get("policy_snapshot") if isinstance(snapshot.get("policy_snapshot"), dict) else {}
    return {
        "stable": True,
        "valid": True,
        "group_id": group_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "fingerprint": str(snapshot.get("fingerprint") or ""),
        "policy_revision": str(policy_snapshot.get("revision") or ""),
        "policy_error": "",
        "raw_bindings": raw_bindings,
        "activation_sources": activation_sources,
        "authorized_capabilities": authorized,
        "admitted_capabilities": admitted,
        "denied_capabilities": denied,
        "builtin_tool_grants": builtin_tool_grants,
        "external_tool_grants": external_tool_grants,
        "admitted_records": admitted_records,
        "_group": snapshot.get("group"),
        "_policy": copy.deepcopy(policy),
        "_state_doc": state_doc,
        "_catalog_doc": copy.deepcopy(catalog_doc),
        "_runtime_doc": copy.deepcopy(runtime_doc),
        "_source_controls": copy.deepcopy(source_controls),
    }


def resolve_current_admission(
    *,
    group_id: str,
    actor_id: str,
    max_attempts: int = _MAX_SNAPSHOT_ATTEMPTS,
) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip() or "user"
    if not gid:
        return _zero_certificate(gid, aid, "missing_group_id")
    attempts = max(1, min(int(max_attempts or _MAX_SNAPSHOT_ATTEMPTS), 8))
    previous: Dict[str, Any] | None = None
    for _ in range(attempts + 1):
        current = _snapshot_once(gid, aid)
        if previous is not None and previous.get("fingerprint") == current.get("fingerprint"):
            return _build_certificate(gid, aid, current)
        previous = current
    return _zero_certificate(gid, aid, "unstable_current_admission", previous)
