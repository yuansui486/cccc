from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ...contracts.v1 import DaemonError, DaemonResponse
from ...kernel.group import load_group
from ...kernel.ledger import append_event
from ...kernel.pet_actor import PET_ACTOR_ID, get_pet_actor
from ...kernel.pet_decisions import clear_pet_decisions, load_pet_decisions, pet_decisions_path, replace_pet_decisions
from ...kernel.pet_outcomes import append_expired_pet_decision_outcomes
from .assistive_jobs import JOB_KIND_PET_REVIEW, mark_job_completed
from ...util.file_lock import acquire_lockfile, release_lockfile
from ...util.fs import atomic_write_json, read_json


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _require_pet_actor(group: Any, actor_id: str) -> None:
    pet_actor = get_pet_actor(group)
    if not isinstance(pet_actor, dict) or str(actor_id or "").strip() != PET_ACTOR_ID:
        raise ValueError("pet decisions are restricted to pet actor")


def _pet_decisions_lock_path(group: Any) -> Path:
    return Path(group.path) / "state" / "pet_decisions.lock"


def _capture_pet_decisions_document(group: Any) -> tuple[bool, Dict[str, Any]]:
    path = pet_decisions_path(group)
    existed = path.exists()
    payload = read_json(path) if existed else {}
    return existed, payload if isinstance(payload, dict) else {}


def _restore_pet_decisions_document(group: Any, *, existed: bool, payload: Dict[str, Any]) -> None:
    path = pet_decisions_path(group)
    if not existed:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        return
    atomic_write_json(path, payload, indent=2)


def _try_mark_pet_review_completed(group_id: str) -> None:
    try:
        mark_job_completed(group_id, JOB_KIND_PET_REVIEW)
    except Exception:
        pass


def handle_pet_decisions_get(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    return DaemonResponse(ok=True, result={"decisions": load_pet_decisions(group)})


def handle_pet_decisions_replace(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    actor_id = str(args.get("actor_id") or "").strip()
    decisions = args.get("decisions") if isinstance(args.get("decisions"), list) else []
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    lk = acquire_lockfile(_pet_decisions_lock_path(group), blocking=True)
    try:
        _require_pet_actor(group, actor_id)
        previous_exists, previous_payload = _capture_pet_decisions_document(group)
        previous_decisions = load_pet_decisions(group)
        normalized = replace_pet_decisions(group, decisions=list(decisions), actor_id=actor_id)
        event = append_event(
            group.ledger_path,
            kind="pet.decisions.replace",
            group_id=group.group_id,
            scope_key="",
            by=actor_id or PET_ACTOR_ID,
            data={
                "count": len(normalized),
                "fingerprints": [
                    str(item.get("fingerprint") or "").strip()
                    for item in normalized
                    if isinstance(item, dict) and str(item.get("fingerprint") or "").strip()
                ],
            },
        )
        append_expired_pet_decision_outcomes(
            group,
            by=actor_id or PET_ACTOR_ID,
            previous_decisions=previous_decisions,
            current_decisions=normalized,
        )
        _try_mark_pet_review_completed(group.group_id)
        return DaemonResponse(ok=True, result={"decisions": normalized, "event": event})
    except Exception as e:
        if "previous_exists" in locals():
            try:
                _restore_pet_decisions_document(group, existed=previous_exists, payload=previous_payload)
            except Exception:
                pass
        return _error("pet_decisions_replace_failed", str(e))
    finally:
        release_lockfile(lk)


def handle_pet_decisions_clear(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    actor_id = str(args.get("actor_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    lk = acquire_lockfile(_pet_decisions_lock_path(group), blocking=True)
    try:
        _require_pet_actor(group, actor_id)
        previous_exists, previous_payload = _capture_pet_decisions_document(group)
        previous_decisions = load_pet_decisions(group)
        clear_pet_decisions(group, actor_id=actor_id)
        event = append_event(
            group.ledger_path,
            kind="pet.decisions.clear",
            group_id=group.group_id,
            scope_key="",
            by=actor_id or PET_ACTOR_ID,
            data={},
        )
        append_expired_pet_decision_outcomes(
            group,
            by=actor_id or PET_ACTOR_ID,
            previous_decisions=previous_decisions,
            current_decisions=[],
        )
        _try_mark_pet_review_completed(group.group_id)
        return DaemonResponse(ok=True, result={"cleared": True, "event": event})
    except Exception as e:
        if "previous_exists" in locals():
            try:
                _restore_pet_decisions_document(group, existed=previous_exists, payload=previous_payload)
            except Exception:
                pass
        return _error("pet_decisions_clear_failed", str(e))
    finally:
        release_lockfile(lk)


def try_handle_pet_decision_op(op: str, args: Dict[str, Any]) -> Optional[DaemonResponse]:
    if op == "pet_decisions_get":
        return handle_pet_decisions_get(args)
    if op == "pet_decisions_replace":
        return handle_pet_decisions_replace(args)
    if op == "pet_decisions_clear":
        return handle_pet_decisions_clear(args)
    return None
