"""Group settings operations for daemon."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ...contracts.v1 import DaemonError, DaemonResponse
from ...kernel.group import load_group, normalize_group_capability_defaults
from ...kernel.ledger import append_event
from ...kernel.messaging import get_default_send_to
from ...kernel.permissions import require_group_permission
from ...kernel.terminal_transcript import apply_terminal_transcript_patch, get_terminal_transcript_settings
from ...util.conv import coerce_bool


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _group_settings_error_details(exc: Exception) -> Optional[Dict[str, Any]]:
    message = str(exc or "").strip()
    if not message:
        return None
    return None


def _safe_int(value: Any, *, default: int, min_value: int = 0, max_value: Optional[int] = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = int(default)
    if out < int(min_value):
        out = int(min_value)
    if max_value is not None and out > int(max_value):
        out = int(max_value)
    return out


def _safe_int_list(value: Any, *, default: List[int], min_value: int = 1) -> List[int]:
    raw = value
    if isinstance(raw, str):
        items: Any = [p.strip() for p in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = default
    out: List[int] = []
    for item in items:
        try:
            n = int(item)
        except Exception:
            continue
        if n >= min_value:
            out.append(n)
    return sorted(set(out)) or list(default)


def _settings_payload(group: Any) -> Dict[str, Any]:
    automation = group.doc.get("automation") if isinstance(group.doc.get("automation"), dict) else {}
    delivery = group.doc.get("delivery") if isinstance(group.doc.get("delivery"), dict) else {}
    features = group.doc.get("features") if isinstance(group.doc.get("features"), dict) else {}
    tt = get_terminal_transcript_settings(group.doc)
    experience = group.doc.get("experience") if isinstance(group.doc.get("experience"), dict) else {}
    return {
        "default_send_to": get_default_send_to(group.doc),
        "nudge_after_seconds": _safe_int(automation.get("nudge_after_seconds", 300), default=300, min_value=0),
        "reply_required_nudge_after_seconds": _safe_int(
            automation.get("reply_required_nudge_after_seconds", 300),
            default=300,
            min_value=0,
        ),
        "attention_ack_nudge_after_seconds": _safe_int(
            automation.get("attention_ack_nudge_after_seconds", 600),
            default=600,
            min_value=0,
        ),
        "unread_nudge_after_seconds": _safe_int(automation.get("unread_nudge_after_seconds", 900), default=900, min_value=0),
        "nudge_digest_min_interval_seconds": _safe_int(
            automation.get("nudge_digest_min_interval_seconds", 120),
            default=120,
            min_value=0,
        ),
        "nudge_max_repeats_per_obligation": _safe_int(
            automation.get("nudge_max_repeats_per_obligation", 3),
            default=3,
            min_value=0,
        ),
        "nudge_escalate_after_repeats": _safe_int(
            automation.get("nudge_escalate_after_repeats", 2),
            default=2,
            min_value=0,
        ),
        "actor_idle_timeout_seconds": _safe_int(
            automation.get("actor_idle_timeout_seconds", 0),
            default=0,
            min_value=0,
        ),
        "keepalive_delay_seconds": _safe_int(automation.get("keepalive_delay_seconds", 120), default=120, min_value=0),
        "keepalive_max_per_actor": _safe_int(automation.get("keepalive_max_per_actor", 3), default=3, min_value=0),
        "silence_timeout_seconds": _safe_int(automation.get("silence_timeout_seconds", 0), default=0, min_value=0),
        "help_nudge_interval_seconds": _safe_int(
            automation.get("help_nudge_interval_seconds", 600),
            default=600,
            min_value=0,
        ),
        "help_nudge_min_messages": _safe_int(automation.get("help_nudge_min_messages", 10), default=10, min_value=0),
        "task_reminder_enabled": coerce_bool(automation.get("task_reminder_enabled"), default=True),
        "task_empty_cooldown_seconds": _safe_int(
            automation.get("task_empty_cooldown_seconds", 900),
            default=900,
            min_value=0,
        ),
        "task_active_overdue_milestones_seconds": _safe_int_list(
            automation.get("task_active_overdue_milestones_seconds"),
            default=[1800, 3000, 3600, 5400],
        ),
        "task_planned_unassigned_milestones_seconds": _safe_int_list(
            automation.get("task_planned_unassigned_milestones_seconds"),
            default=[900, 1800, 3600, 7200, 10800, 21600],
        ),
        "min_interval_seconds": _safe_int(delivery.get("min_interval_seconds", 0), default=0, min_value=0),
        "auto_mark_on_delivery": coerce_bool(delivery.get("auto_mark_on_delivery"), default=False),
        "experience_reminder_enabled": coerce_bool(experience.get("reminder_enabled"), default=True),
        "experience_reminder_every_user_messages": _safe_int(
            experience.get("reminder_every_user_messages", 10),
            default=10,
            min_value=1,
            max_value=1000,
        ),
        "experience_force_review_after_unwritten_reminders": _safe_int(
            experience.get("force_review_after_unwritten_reminders", 5),
            default=5,
            min_value=1,
            max_value=100,
        ),
        "terminal_transcript_visibility": str(tt.get("visibility") or "foreman"),
        "terminal_transcript_notify_tail": coerce_bool(tt.get("notify_tail"), default=False),
        "terminal_transcript_notify_lines": _safe_int(
            tt.get("notify_lines", 20),
            default=20,
            min_value=1,
            max_value=80,
        ),
        "panorama_enabled": coerce_bool(features.get("panorama_enabled"), default=False),
        "capability_defaults": normalize_group_capability_defaults(group.doc.get("capability_defaults")),
    }


def handle_group_settings_update(
    args: Dict[str, Any],
    *,
    effective_runner_kind: Callable[[str], str],
    start_actor_process: Callable[..., dict[str, Any]],
    load_actor_private_env: Callable[[str, str], Dict[str, str]],
    update_actor_private_env: Callable[..., Dict[str, str]],
    delete_actor_private_env: Callable[[str, str], None],
    get_actor_profile: Callable[[str], Optional[Dict[str, Any]]],
    load_actor_profile_secrets: Callable[[str], Dict[str, str]],
    remove_headless_state: Callable[[str, str], None],
    remove_pty_state_if_pid: Callable[..., None],
) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip()
    patch = args.get("patch") if isinstance(args.get("patch"), dict) else {}
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")

    messaging_keys = {"default_send_to"}
    delivery_keys = {"min_interval_seconds", "auto_mark_on_delivery"}
    experience_keys = {
        "experience_reminder_enabled",
        "experience_reminder_every_user_messages",
        "experience_force_review_after_unwritten_reminders",
    }
    automation_int_keys = {
        "nudge_after_seconds",
        "reply_required_nudge_after_seconds",
        "attention_ack_nudge_after_seconds",
        "unread_nudge_after_seconds",
        "nudge_digest_min_interval_seconds",
        "nudge_max_repeats_per_obligation",
        "nudge_escalate_after_repeats",
        "actor_idle_timeout_seconds",
        "keepalive_delay_seconds",
        "keepalive_max_per_actor",
        "silence_timeout_seconds",
        "help_nudge_interval_seconds",
        "help_nudge_min_messages",
        "task_empty_cooldown_seconds",
    }
    automation_bool_keys = {"task_reminder_enabled"}
    automation_list_keys = {"task_active_overdue_milestones_seconds", "task_planned_unassigned_milestones_seconds"}
    automation_keys = automation_int_keys | automation_bool_keys | automation_list_keys
    terminal_transcript_keys = {
        "terminal_transcript_visibility",
        "terminal_transcript_notify_tail",
        "terminal_transcript_notify_lines",
    }
    feature_keys = {"panorama_enabled"}
    capability_keys = {"capability_defaults"}
    allowed = messaging_keys | delivery_keys | experience_keys | automation_keys | terminal_transcript_keys | feature_keys | capability_keys

    unknown = set(patch.keys()) - allowed
    if unknown:
        return _error("invalid_patch", "invalid patch keys", details={"unknown_keys": sorted(unknown)})
    if not patch:
        return _error("invalid_patch", "empty patch")
    if "default_send_to" in patch:
        value = str(patch.get("default_send_to") or "").strip()
        if value not in ("foreman", "broadcast"):
            return _error(
                "invalid_patch",
                "default_send_to must be 'foreman' or 'broadcast'",
                details={"default_send_to": value},
            )
    try:
        require_group_permission(group, by=by, action="group.settings_update")
        messaging_patch = {k: v for k, v in patch.items() if k in messaging_keys}
        if messaging_patch:
            messaging = group.doc.get("messaging") if isinstance(group.doc.get("messaging"), dict) else {}
            messaging["default_send_to"] = str(messaging_patch.get("default_send_to") or "foreman").strip()
            group.doc["messaging"] = messaging

        delivery_patch = {k: v for k, v in patch.items() if k in delivery_keys}
        if delivery_patch:
            delivery = group.doc.get("delivery") if isinstance(group.doc.get("delivery"), dict) else {}
            for key, value in delivery_patch.items():
                if key == "auto_mark_on_delivery":
                    delivery[key] = coerce_bool(value, default=False)
                else:
                    delivery[key] = int(value)
            group.doc["delivery"] = delivery

        experience_patch = {k: v for k, v in patch.items() if k in experience_keys}
        if experience_patch:
            experience = group.doc.get("experience") if isinstance(group.doc.get("experience"), dict) else {}
            if "experience_reminder_enabled" in experience_patch:
                experience["reminder_enabled"] = coerce_bool(
                    experience_patch.get("experience_reminder_enabled"),
                    default=True,
                )
            if "experience_reminder_every_user_messages" in experience_patch:
                experience["reminder_every_user_messages"] = _safe_int(
                    experience_patch.get("experience_reminder_every_user_messages"),
                    default=10,
                    min_value=1,
                    max_value=1000,
                )
            if "experience_force_review_after_unwritten_reminders" in experience_patch:
                experience["force_review_after_unwritten_reminders"] = _safe_int(
                    experience_patch.get("experience_force_review_after_unwritten_reminders"),
                    default=5,
                    min_value=1,
                    max_value=100,
                )
            group.doc["experience"] = experience

        automation_patch = {k: v for k, v in patch.items() if k in automation_keys}
        if automation_patch:
            automation = group.doc.get("automation") if isinstance(group.doc.get("automation"), dict) else {}
            for key, value in automation_patch.items():
                if key in automation_bool_keys:
                    automation[key] = coerce_bool(value, default=True)
                elif key in automation_list_keys:
                    default = [1800, 3000, 3600, 5400] if key == "task_active_overdue_milestones_seconds" else [900, 1800, 3600, 7200, 10800, 21600]
                    automation[key] = _safe_int_list(value, default=default)
                else:
                    automation[key] = int(value)
            group.doc["automation"] = automation

        tt_patch: Dict[str, Any] = {}
        if "terminal_transcript_visibility" in patch:
            tt_patch["visibility"] = patch.get("terminal_transcript_visibility")
        if "terminal_transcript_notify_tail" in patch:
            tt_patch["notify_tail"] = patch.get("terminal_transcript_notify_tail")
        if "terminal_transcript_notify_lines" in patch:
            tt_patch["notify_lines"] = patch.get("terminal_transcript_notify_lines")
        if tt_patch:
            apply_terminal_transcript_patch(group.doc, tt_patch)

        capability_patch = {k: v for k, v in patch.items() if k in capability_keys}
        if capability_patch:
            defaults = normalize_group_capability_defaults(capability_patch.get("capability_defaults"))
            group.doc["capability_defaults"] = defaults

        features_patch = {k: v for k, v in patch.items() if k in feature_keys}
        if features_patch:
            features = group.doc.get("features") if isinstance(group.doc.get("features"), dict) else {}
            if "panorama_enabled" in features_patch:
                features["panorama_enabled"] = coerce_bool(features_patch["panorama_enabled"], default=False)
            group.doc["features"] = features

        group.save()
    except Exception as e:
        return _error("group_settings_update_failed", str(e), details=_group_settings_error_details(e))

    settings = _settings_payload(group)

    event = append_event(
        group.ledger_path,
        kind="group.settings_update",
        group_id=group.group_id,
        scope_key="",
        by=by,
        data={"patch": dict(patch)},
    )
    return DaemonResponse(ok=True, result={"group_id": group.group_id, "settings": settings, "event": event})


def try_handle_group_settings_op(
    op: str,
    args: Dict[str, Any],
    *,
    effective_runner_kind: Callable[[str], str],
    start_actor_process: Callable[..., dict[str, Any]],
    load_actor_private_env: Callable[[str, str], Dict[str, str]],
    update_actor_private_env: Callable[..., Dict[str, str]],
    delete_actor_private_env: Callable[[str, str], None],
    get_actor_profile: Callable[[str], Optional[Dict[str, Any]]],
    load_actor_profile_secrets: Callable[[str], Dict[str, str]],
    remove_headless_state: Callable[[str, str], None],
    remove_pty_state_if_pid: Callable[..., None],
) -> Optional[DaemonResponse]:
    if op == "group_settings_update":
        return handle_group_settings_update(
            args,
            effective_runner_kind=effective_runner_kind,
            start_actor_process=start_actor_process,
            load_actor_private_env=load_actor_private_env,
            update_actor_private_env=update_actor_private_env,
            delete_actor_private_env=delete_actor_private_env,
            get_actor_profile=get_actor_profile,
            load_actor_profile_secrets=load_actor_profile_secrets,
            remove_headless_state=remove_headless_state,
            remove_pty_state_if_pid=remove_pty_state_if_pid,
        )
    return None
