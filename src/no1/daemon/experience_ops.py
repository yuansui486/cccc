from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts.v1 import DaemonError, DaemonResponse
from ..kernel.actors import find_actor
from ..kernel.experience import ExperienceDocument, ExperienceError, append_experience, read_experience, replace_experience
from ..kernel.group import load_group


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _authorize(group: Any, by: str) -> None:
    who = str(by or "user").strip() or "user"
    if who == "user":
        return
    if find_actor(group, who) is None:
        raise ExperienceError("permission_denied", f"unknown actor: {who}")


def _payload(doc: ExperienceDocument) -> Dict[str, Any]:
    return {
        "found": True,
        "path": str(doc.path),
        "content": doc.content,
        "revision": doc.revision,
        "created": bool(doc.created),
    }


def _handle(action: str, args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        _authorize(group, str(args.get("by") or "user"))
        if action == "read":
            return DaemonResponse(ok=True, result=_payload(read_experience(group, ensure=True)))
        if action == "append":
            doc = append_experience(group, content=str(args.get("content") or ""))
            return DaemonResponse(ok=True, result=_payload(doc))
        if action == "replace":
            doc = replace_experience(
                group,
                content=str(args.get("content") or ""),
                expected_revision=str(args.get("expected_revision") or ""),
            )
            return DaemonResponse(ok=True, result=_payload(doc))
        return _error("invalid_action", f"unknown experience action: {action}")
    except ExperienceError as exc:
        return _error(exc.code, str(exc))
    except OSError as exc:
        return _error("experience_io_error", str(exc))


def try_handle_experience_op(op: str, args: Dict[str, Any]) -> Optional[DaemonResponse]:
    actions = {
        "experience_read": "read",
        "experience_append": "append",
        "experience_replace": "replace",
    }
    action = actions.get(str(op or ""))
    if action is None:
        return None
    return _handle(action, args)
