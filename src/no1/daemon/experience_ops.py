from __future__ import annotations

from typing import Any, Dict, Optional

from ..contracts.v1 import DaemonError, DaemonResponse
from ..kernel.actors import find_actor, find_foreman
from ..kernel.experience import ExperienceDocument, ExperienceError, append_experience, read_experience, replace_experience
from ..kernel.group import load_group


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _authorize(group: Any, by: str, *, write: bool = False) -> None:
    who = str(by or "user").strip() or "user"
    if who == "user":
        return
    if find_actor(group, who) is None:
        raise ExperienceError("permission_denied", f"unknown actor: {who}")
    if write:
        foreman = find_foreman(group) or {}
        if who != str(foreman.get("id") or "").strip():
            raise ExperienceError("permission_denied", "only the foreman can write automatic project experience")


def _payload(doc: ExperienceDocument) -> Dict[str, Any]:
    return {
        "found": True,
        "path": str(doc.path),
        "content": doc.content,
        "revision": doc.revision,
        "created": bool(doc.created),
    }


def _validate_cycle_for_write(group: Any, args: Dict[str, Any], by: str) -> None:
    cycle_id = str(args.get("cycle_id") or "").strip()
    if not cycle_id:
        return
    from .messaging.experience_reminder import review_status

    status = review_status(group, cycle_id=cycle_id)
    cycle = status.get("cycle") if isinstance(status, dict) else None
    if not isinstance(cycle, dict):
        raise ExperienceError("review_not_found", "review cycle not found")
    if str(cycle.get("foreman_id") or "") != by:
        raise ExperienceError("permission_denied", "only the review foreman can write for this cycle")
    if str(cycle.get("state") or "") not in {"awaiting_foreman", "collecting"}:
        raise ExperienceError("review_not_active", "review cycle is no longer active")


def _handle(action: str, args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        by = str(args.get("by") or "user")
        _authorize(group, by, write=action in {"append", "replace"})
        if action == "read":
            payload = _payload(read_experience(group, ensure=True))
            from .messaging.experience_reminder import get_distillation_status

            payload["distillation"] = get_distillation_status(group)
            return DaemonResponse(ok=True, result=payload)
        if action == "append":
            _validate_cycle_for_write(group, args, by)
            doc = append_experience(group, content=str(args.get("content") or ""))
            return DaemonResponse(ok=True, result=_payload(doc))
        if action == "replace":
            _validate_cycle_for_write(group, args, by)
            doc = replace_experience(
                group,
                content=str(args.get("content") or ""),
                expected_revision=str(args.get("expected_revision") or ""),
            )
            return DaemonResponse(ok=True, result=_payload(doc))
        from .messaging.experience_reminder import complete_review, review_status, submit_candidate

        if action == "review_status":
            return DaemonResponse(ok=True, result=review_status(group, cycle_id=str(args.get("cycle_id") or "")))
        if action == "candidate_submit":
            return DaemonResponse(
                ok=True,
                result=submit_candidate(
                    group,
                    cycle_id=str(args.get("cycle_id") or ""),
                    actor_id=by,
                    content=str(args.get("content") or ""),
                ),
            )
        if action == "review_complete":
            return DaemonResponse(
                ok=True,
                result=complete_review(
                    group,
                    cycle_id=str(args.get("cycle_id") or ""),
                    actor_id=by,
                    result=str(args.get("result") or ""),
                    summary=str(args.get("summary") or ""),
                ),
            )
        return _error("invalid_action", f"unknown experience action: {action}")
    except ExperienceError as exc:
        return _error(exc.code, str(exc))
    except PermissionError as exc:
        return _error("permission_denied", str(exc))
    except ValueError as exc:
        return _error("invalid_review", str(exc))
    except OSError as exc:
        return _error("experience_io_error", str(exc))


def try_handle_experience_op(op: str, args: Dict[str, Any]) -> Optional[DaemonResponse]:
    actions = {
        "experience_read": "read",
        "experience_append": "append",
        "experience_replace": "replace",
        "experience_review_status": "review_status",
        "experience_candidate_submit": "candidate_submit",
        "experience_review_complete": "review_complete",
    }
    action = actions.get(str(op or ""))
    if action is None:
        return None
    return _handle(action, args)
