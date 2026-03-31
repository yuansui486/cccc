from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Optional

from ...kernel.actors import INTERNAL_KIND_PET, add_actor, find_actor, remove_actor, update_actor
from ...kernel.ledger import append_event
from ...kernel.pet_actor import PET_ACTOR_ID
from ...kernel.group import Group
from ...runners import headless as headless_runner
from ...runners import pty as pty_runner


def pet_runtime_signature(actor: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(actor, dict):
        return {}
    return {
        "runtime": str(actor.get("runtime") or "").strip(),
        "runner": str(actor.get("runner") or "").strip(),
        "command": list(actor.get("command") or []) if isinstance(actor.get("command"), list) else [],
        "env": dict(actor.get("env") or {}) if isinstance(actor.get("env"), dict) else {},
        "default_scope_key": str(actor.get("default_scope_key") or "").strip(),
        "submit": str(actor.get("submit") or "").strip(),
        "enabled": bool(actor.get("enabled")),
    }


def pet_runtime_changed(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> bool:
    return pet_runtime_signature(before) != pet_runtime_signature(after)


def is_pet_actor_running(
    group: Group,
    *,
    actor: Optional[Dict[str, Any]],
    effective_runner_kind: Callable[[str], str],
) -> bool:
    if not isinstance(actor, dict):
        return False
    runner_kind = str(actor.get("runner") or "pty").strip()
    if effective_runner_kind(runner_kind) == "headless":
        return bool(headless_runner.SUPERVISOR.actor_running(group.group_id, PET_ACTOR_ID))
    return bool(pty_runner.SUPERVISOR.actor_running(group.group_id, PET_ACTOR_ID))


def stop_pet_actor_runtime(
    group: Group,
    *,
    actor: Optional[Dict[str, Any]],
    by: str,
    effective_runner_kind: Callable[[str], str],
    remove_headless_state: Callable[[str, str], None],
    remove_pty_state_if_pid: Callable[..., None],
    emit_event: bool = True,
) -> None:
    if not isinstance(actor, dict):
        return
    runner_kind = str(actor.get("runner") or "pty").strip()
    if effective_runner_kind(runner_kind) == "headless":
        headless_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=PET_ACTOR_ID)
        remove_headless_state(group.group_id, PET_ACTOR_ID)
        remove_pty_state_if_pid(group.group_id, PET_ACTOR_ID, pid=0)
    else:
        pty_runner.SUPERVISOR.stop_actor(group_id=group.group_id, actor_id=PET_ACTOR_ID)
        remove_pty_state_if_pid(group.group_id, PET_ACTOR_ID, pid=0)
        remove_headless_state(group.group_id, PET_ACTOR_ID)
    if not emit_event:
        return
    event = append_event(
        group.ledger_path,
        kind="actor.stop",
        group_id=group.group_id,
        scope_key="",
        by=str(by or "system").strip() or "system",
        data={"actor_id": PET_ACTOR_ID},
    )
    try:
        from ...kernel.events import publish_event

        publish_event("actor.stop", {"group_id": group.group_id, "actor_id": PET_ACTOR_ID, "event_id": str(event.get("id") or "")})
    except Exception:
        pass


def restore_pet_actor_doc(group: Group, actor_doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    current = find_actor(group, PET_ACTOR_ID)
    if not isinstance(actor_doc, dict):
        if isinstance(current, dict):
            remove_actor(group, PET_ACTOR_ID)
        return None
    if not isinstance(current, dict):
        add_actor(
            group,
            actor_id=PET_ACTOR_ID,
            title=str(actor_doc.get("title") or ""),
            command=list(actor_doc.get("command") or []) if isinstance(actor_doc.get("command"), list) else [],
            env=dict(actor_doc.get("env") or {}) if isinstance(actor_doc.get("env"), dict) else {},
            default_scope_key=str(actor_doc.get("default_scope_key") or ""),
            submit=str(actor_doc.get("submit") or "enter"),
            enabled=bool(actor_doc.get("enabled", True)),
            runner=str(actor_doc.get("runner") or "pty"),  # type: ignore[arg-type]
            runtime=str(actor_doc.get("runtime") or "codex"),  # type: ignore[arg-type]
            internal_kind=INTERNAL_KIND_PET,
        )
    else:
        update_actor(
            group,
            PET_ACTOR_ID,
            {
                "title": str(actor_doc.get("title") or ""),
                "command": list(actor_doc.get("command") or []) if isinstance(actor_doc.get("command"), list) else [],
                "env": dict(actor_doc.get("env") or {}) if isinstance(actor_doc.get("env"), dict) else {},
                "default_scope_key": str(actor_doc.get("default_scope_key") or ""),
                "submit": str(actor_doc.get("submit") or "enter"),
                "enabled": bool(actor_doc.get("enabled", True)),
                "runner": str(actor_doc.get("runner") or "pty"),
                "runtime": str(actor_doc.get("runtime") or "codex"),
                "internal_kind": INTERNAL_KIND_PET,
            },
        )
    restored = find_actor(group, PET_ACTOR_ID)
    return deepcopy(restored) if isinstance(restored, dict) else None
