"""Chat send/reply operation handlers for daemon."""

from __future__ import annotations

import logging
import hashlib
import json
import mimetypes
import os
import re
import stat
import threading
import time
import uuid
import weakref
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ...computer_control.models import computer_control_permissions
from ...contracts.v1 import ChatMessageData, ChatStreamData, DaemonError, DaemonResponse, SystemNotifyData
from ...kernel.actors import find_actor, list_actors, resolve_recipient_tokens
from ...kernel.group import get_group_state, load_group, set_group_state
from ...kernel.inbox import (
    find_event_with_chat_ack,
    get_quote_text_from_message_data,
    is_message_for_actor,
    iter_events_reverse,
)
from ...kernel.context import ContextStorage
from ...kernel.ledger import (
    MAX_CHAT_TEXT_BYTES,
    LedgerEventConflictError,
    append_event,
    append_event_once,
    read_last_lines,
)
from ...kernel.messaging import (
    default_reply_recipients,
    enabled_recipient_actor_ids,
    get_default_send_to,
    recipient_actor_ids,
    targets_any_agent,
)
from ...contracts.v1.message import normalize_insight
from ...kernel.message_sender_snapshot import build_sender_snapshot
from ...kernel.blobs import store_blob_bytes
from ...kernel.scope import detect_scope
from ...util.time import utc_now_iso
from ..claude_app_sessions import SUPERVISOR as claude_app_supervisor
from ..codex_app_sessions import SUPERVISOR as codex_app_supervisor
from .delivery import (
    append_mcp_reply_reminder,
    emit_system_notify,
    flush_pending_messages,
    get_headless_targets_for_message,
    queue_chat_message,
    request_flush_pending_messages,
)
from .experience_reminder import append_experience_reminder, commit_experience_reminder, plan_experience_reminder
from .actor_delivery_planner import (
    TRANSPORT_CLAUDE_HEADLESS,
    TRANSPORT_CODEX_APP_SERVER,
    TRANSPORT_CODEX_HEADLESS,
    TRANSPORT_PTY,
    TRANSPORT_WEB_MODEL_BROWSER,
    event_with_effective_to,
    plan_actor_chat_delivery,
)
from ..actors.web_model_browser_delivery import (
    schedule_web_model_browser_delivery,
    web_model_browser_delivery_enabled,
)
from .chat_support_ops import schedule_headless_post_wake_delivery
from .actor_turn_rendering import (
    build_actor_delivery_text as _build_delivery_text,
    build_actor_headless_delivery_text as _build_headless_delivery_text,
    compact_delivery_text as _compact_delivery_text,
)
from ..context.context_ops import handle_context_sync
from .install_slash_command import INSTALL_CAPABILITY_ID, parse_install_slash_command, render_install_command_task
from .turn_provenance import (
    INGRESS_GROUP_BRIDGE,
    TRUSTED_INGRESS_ARG,
    build_reply_turn_provenance,
    build_send_turn_provenance,
    invalidate_turn_grant_from_completion_receipt,
)
from .message_admission import (
    ADMISSION_CLAIM_ARG,
    INGRESS_CLAIM_ARG,
    MessageAdmissionError,
    bind_file_descriptor,
    consume_admission_claim,
    issue_admission_claim,
    issue_message_admission,
)

logger = logging.getLogger("no1.daemon.server")

_MAX_FILE_SEND_BYTES = 20 * 1024 * 1024

_GROUP_BRIDGE_DELIVERY_CLAIM_ARG = "__group_bridge_delivery_claim"
_GROUP_BRIDGE_DELIVERY_FIELDS = frozenset(
    {
        "group_id",
        "text",
        "insight",
        "format",
        "priority",
        "reply_required",
        "collaboration_required",
        "by",
        "to",
        "attachments",
        "refs",
        "path",
        "quote_text",
        "source_platform",
        "source_user_id",
        "src_group_id",
        "src_event_id",
        "src_by",
        "remote_reply_to",
        "client_id",
        TRUSTED_INGRESS_ARG,
        _GROUP_BRIDGE_DELIVERY_CLAIM_ARG,
    }
)
_GROUP_BRIDGE_DELIVERY_ID_PATTERN = re.compile(r"gbs_[0-9a-f]{32}")
_GROUP_BRIDGE_CLAIM_LOCK = threading.Lock()


class _GroupBridgeDeliveryClaim:
    __slots__ = ("__weakref__",)

    def __repr__(self) -> str:
        return "<sealed GroupBridgeDeliveryClaim>"

    def __copy__(self) -> object:
        raise TypeError("Group Bridge delivery claims cannot be copied")

    def __deepcopy__(self, _memo: object) -> object:
        raise TypeError("Group Bridge delivery claims cannot be copied")

    def __reduce__(self) -> object:
        raise TypeError("Group Bridge delivery claims cannot be serialized")


_GroupBridgeProjection = tuple[object, ...]
_GroupBridgeClaimState = tuple[int, _GroupBridgeProjection, bool]
_GROUP_BRIDGE_CLAIMS: weakref.WeakKeyDictionary[_GroupBridgeDeliveryClaim, _GroupBridgeClaimState] = (
    weakref.WeakKeyDictionary()
)


def _closed_identity(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and len(value) <= 512
        and not any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value)
    )


def _group_bridge_delivery_projection(
    args: object,
    *,
    includes_claim: bool,
) -> Optional[_GroupBridgeProjection]:
    if type(args) is not dict:
        return None
    expected_fields = (
        _GROUP_BRIDGE_DELIVERY_FIELDS
        if includes_claim
        else _GROUP_BRIDGE_DELIVERY_FIELDS - {_GROUP_BRIDGE_DELIVERY_CLAIM_ARG}
    )
    if set(args) != expected_fields:
        return None
    text = args.get("text")
    insight = args.get("insight")
    delivery_id = args.get("src_event_id")
    try:
        text_bytes = (
            len(text.encode("utf-8"))
            if type(text) is str and len(text) <= MAX_CHAT_TEXT_BYTES
            else 0
        )
    except UnicodeError:
        return None
    valid = (
        _closed_identity(args.get("group_id"))
        and type(text) is str
        and bool(text)
        and len(text) <= MAX_CHAT_TEXT_BYTES
        and text_bytes <= MAX_CHAT_TEXT_BYTES
        and (insight is None or type(insight) is str)
        and type(args.get("format")) is str
        and args.get("format") in ("plain", "markdown")
        and type(args.get("priority")) is str
        and args.get("priority") in ("normal", "attention")
        and type(args.get("reply_required")) is bool
        and args.get("collaboration_required") is False
        and type(args.get("by")) is str
        and args.get("by") == "system"
        and type(args.get("to")) is list
        and len(args["to"]) == 1
        and type(args["to"][0]) is str
        and args["to"][0] == "user"
        and type(args.get("attachments")) is list
        and not args["attachments"]
        and type(args.get("refs")) is list
        and not args["refs"]
        and type(args.get("path")) is str
        and args.get("path") == ""
        and type(args.get("quote_text")) is str
        and args.get("quote_text") == ""
        and type(args.get("source_platform")) is str
        and args.get("source_platform") == "group_bridge_session"
        and _closed_identity(args.get("source_user_id"))
        and _closed_identity(args.get("src_group_id"))
        and type(args.get("src_by")) is str
        and len(args["src_by"]) <= 256
        and type(args.get("remote_reply_to")) is list
        and len(args["remote_reply_to"]) <= 1
        and all(_closed_identity(item) for item in args["remote_reply_to"])
        and args["remote_reply_to"]
        == (
            [args["src_by"]]
            if args["src_by"] and not args["src_by"].startswith(("@", "#", "group_bridge:"))
            else []
        )
        and type(delivery_id) is str
        and _GROUP_BRIDGE_DELIVERY_ID_PATTERN.fullmatch(delivery_id) is not None
        and type(args.get("client_id")) is str
        and args.get("client_id") == delivery_id
        and type(args.get(TRUSTED_INGRESS_ARG)) is str
        and args.get(TRUSTED_INGRESS_ARG) == INGRESS_GROUP_BRIDGE
    )
    if not valid:
        return None
    return (
        args["group_id"],
        text,
        insight,
        args["format"],
        args["priority"],
        args["reply_required"],
        args["collaboration_required"],
        args["by"],
        tuple(args["to"]),
        tuple(args["attachments"]),
        tuple(args["refs"]),
        args["path"],
        args["quote_text"],
        args["source_platform"],
        args["source_user_id"],
        args["src_group_id"],
        delivery_id,
        args["src_by"],
        tuple(args["remote_reply_to"]),
        args["client_id"],
        args[TRUSTED_INGRESS_ARG],
    )


def _issue_group_bridge_delivery_claim(args: Dict[str, Any]) -> _GroupBridgeDeliveryClaim:
    projection = _group_bridge_delivery_projection(args, includes_claim=False)
    if projection is None:
        raise ValueError("Group Bridge delivery projection is invalid")
    claim = _GroupBridgeDeliveryClaim()
    with _GROUP_BRIDGE_CLAIM_LOCK:
        _GROUP_BRIDGE_CLAIMS[claim] = (os.getpid(), projection, False)
    return claim


def _consume_group_bridge_delivery_claim(value: object, args: Dict[str, Any]) -> bool:
    if type(value) is not _GroupBridgeDeliveryClaim:
        return False
    with _GROUP_BRIDGE_CLAIM_LOCK:
        state = _GROUP_BRIDGE_CLAIMS.get(value)
        if state is None:
            return False
        pid, expected, used = state
        if used or pid != os.getpid():
            return False
        _GROUP_BRIDGE_CLAIMS[value] = (pid, expected, True)
        actual = _group_bridge_delivery_projection(args, includes_claim=True)
        if actual is None or actual != expected:
            return False
        return True


def _consume_valid_group_bridge_delivery(args: Dict[str, Any]) -> tuple[bool, Optional[DaemonResponse]]:
    if _GROUP_BRIDGE_DELIVERY_CLAIM_ARG not in args:
        return False, None
    claim = args.get(_GROUP_BRIDGE_DELIVERY_CLAIM_ARG)
    if not _consume_group_bridge_delivery_claim(claim, args):
        return False, _error("invalid_group_bridge_delivery", "Group Bridge delivery claim is invalid")
    return True, None


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def _wake_group_on_human_message(
    group: Any,
    *,
    by: str,
    state_at_accept: str = "",
    automation_on_resume: Callable[[Any], None],
    clear_pending_system_notifies: Callable[[str, set[str]], None],
) -> Any:
    # Keep idle stable against agent chatter / throttled deliveries.
    try:
        accept_state = str(state_at_accept or "").strip().lower()
        if accept_state and accept_state != "idle":
            return group
        if get_group_state(group) != "idle":
            return group
        is_actor_sender = isinstance(find_actor(group, by), dict)
        if not by or by == "system" or is_actor_sender:
            return group
        group = set_group_state(group, state="active")
        try:
            automation_on_resume(group)
        except Exception:
            pass
        try:
            clear_pending_system_notifies(
                group.group_id,
                {"nudge", "keepalive", "help_nudge", "actor_idle", "silence_check", "auto_idle", "automation"},
            )
        except Exception:
            pass
        return group
    except Exception:
        return group


def _normalize_refs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    refs: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            refs.append(item)
    return refs


def _normalize_to_tokens(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if isinstance(item, str) and str(item).strip()]
    if isinstance(raw, str):
        token = raw.strip()
        return [token] if token else []
    return []


def _resolve_send_audience(group: Any, *, args: Dict[str, Any], by: str) -> tuple[list[str], list[str], list[str]]:
    input_tokens = _normalize_to_tokens(args.get("to"))
    explicitly_set = bool(input_tokens)
    try:
        to = resolve_recipient_tokens(group, input_tokens)
    except Exception as exc:
        raise MessageAdmissionError("invalid_recipient", str(exc)) from exc
    if not to:
        mentions = re.findall(r"@(\w[\w-]*)", str(args.get("text") or ""))
        if mentions:
            actor_ids = {
                str(actor.get("id") or "")
                for actor in list_actors(group)
                if isinstance(actor, dict)
            }
            mention_tokens = [
                f"@{item}" if item in {"all", "peers", "foreman"} else item
                for item in mentions
                if item in actor_ids or item in {"all", "peers", "foreman"}
            ]
            if mention_tokens:
                input_tokens = mention_tokens
                try:
                    to = resolve_recipient_tokens(group, mention_tokens)
                except Exception as exc:
                    raise MessageAdmissionError("invalid_recipient", str(exc)) from exc
    if not to and not explicitly_set and get_default_send_to(group.doc) == "foreman":
        to = ["@foreman"]
    peers = [actor_id for actor_id in recipient_actor_ids(group, to) if actor_id != by]
    return input_tokens, to, peers


def _admission_error_response(exc: MessageAdmissionError) -> DaemonResponse:
    return _error(exc.code, exc.message, details=exc.details)


def _tracked_send_client_id(*, group_id: str, by: str, idempotency_key: str) -> str:
    basis = "\0".join([str(group_id or ""), str(by or ""), str(idempotency_key or "")])
    digest = hashlib.sha256(basis.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"tracked-send:{digest}"


def _tracked_send_existing_result(group: Any, *, client_id: str, by: str = "") -> Optional[Dict[str, Any]]:
    if not client_id:
        return None
    sender = str(by or "").strip()
    try:
        lines = read_last_lines(group.ledger_path, 800)
    except Exception:
        return None
    for raw_line in reversed(lines):
        try:
            event = json.loads(raw_line)
        except Exception:
            continue
        if not isinstance(event, dict) or str(event.get("kind") or "") != "chat.message":
            continue
        if sender and str(event.get("by") or "").strip() != sender:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if str(data.get("client_id") or "").strip() != client_id:
            continue
        refs = data.get("refs") if isinstance(data.get("refs"), list) else []
        task_ref = next(
            (
                ref
                for ref in refs
                if isinstance(ref, dict)
                and str(ref.get("kind") or "").strip() == "task_ref"
                and str(ref.get("task_id") or "").strip()
            ),
            None,
        )
        task_id = str((task_ref or {}).get("task_id") or "").strip()
        return {
            "event": event,
            "event_id": str(event.get("id") or "").strip(),
            "task_id": task_id,
            "task_ref": task_ref,
            "replayed": True,
            "task_created": False,
            "message_sent": True,
            "partial_failure": False,
        }
    return None


def _tracked_send_existing_task(group: Any, *, client_request_id: str) -> Optional[Any]:
    if not client_request_id:
        return None
    try:
        storage = ContextStorage(group)
        tasks = storage.list_tasks()
    except Exception:
        return None
    matches = [
        task
        for task in tasks
        if str(getattr(task, "client_request_id", "") or "").strip() == client_request_id
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda task: (
            str(getattr(task, "updated_at", "") or getattr(task, "created_at", "") or ""),
            str(getattr(task, "id", "") or ""),
        ),
        reverse=True,
    )
    return matches[0]


def _reply_request_fingerprint(
    *,
    group_id: str,
    by: str,
    client_id: str,
    reply_to: str,
    text: str,
    insight: Optional[str],
    to: list[str],
    priority: str,
    reply_required: bool,
    collaboration_required: bool,
    refs: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
) -> str:
    facts = {
        "version": 1,
        "group_id": group_id,
        "by": by,
        "client_id": client_id,
        "reply_to": reply_to,
        "text": text,
        "insight": insight,
        "to": to,
        "priority": priority,
        "reply_required": reply_required,
        "collaboration_required": collaboration_required,
        "refs": refs,
        "attachments": attachments,
    }
    encoded = json.dumps(facts, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reply_existing_event(group: Any, *, client_id: str, by: str) -> Optional[dict[str, Any]]:
    if not client_id:
        return None
    try:
        for event in iter_events_reverse(group.ledger_path):
            if not isinstance(event, dict) or str(event.get("kind") or "") != "chat.message":
                continue
            if str(event.get("by") or "").strip() != by:
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if str(data.get("client_id") or "").strip() == client_id and str(data.get("reply_to") or "").strip():
                return event
    except Exception:
        return None
    return None


def _derive_tracked_send_assignee(args: Dict[str, Any]) -> str:
    explicit = str(args.get("assignee") or "").strip()
    if explicit:
        return explicit
    to_tokens = _normalize_to_tokens(args.get("to"))
    if len(to_tokens) != 1:
        return ""
    token = to_tokens[0].strip()
    if not token or token.startswith("@") or token == "user":
        return ""
    return token


def _normalize_tracked_checklist(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, list):
        out: list[Any] = []
        for item in raw:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    out.append({**item, "text": text})
            else:
                text = str(item or "").strip()
                if text:
                    out.append({"text": text})
        return out
    text = str(raw or "").strip()
    if not text:
        return None
    return [{"text": line.strip()} for line in text.splitlines() if line.strip()]


def _task_ref(
    *,
    task_id: str,
    title: str,
    status: str = "planned",
    waiting_on: str = "none",
    handoff_to: str = "",
) -> dict[str, Any]:
    ref = {
        "kind": "task_ref",
        "task_id": task_id,
        "title": str(title or "").strip(),
        "status": str(status or "planned").strip() or "planned",
    }
    waiting_value = str(waiting_on or "").strip()
    if waiting_value:
        ref["waiting_on"] = waiting_value
    handoff_value = str(handoff_to or "").strip()
    if handoff_value:
        ref["handoff_to"] = handoff_value
    return ref


def _quote_text_from_message_data(data: dict[str, Any], *, max_len: int = 100) -> Optional[str]:
    return get_quote_text_from_message_data(data, max_len=max_len)


def _notify_headless_targets(
    *,
    group: Any,
    by: str,
    event_id: str,
    priority: str,
    reply_required: bool,
    event: dict[str, Any],
    skip_actor_ids: Optional[set[str]] = None,
) -> None:
    try:
        headless_targets = get_headless_targets_for_message(group, event=event, by=by)
        skip_ids = {str(item).strip() for item in (skip_actor_ids or set()) if str(item).strip()}
        if reply_required:
            notify_title = "Need reply"
            notify_priority = "urgent" if priority == "attention" else "high"
        else:
            notify_title = "Needs acknowledgement" if priority == "attention" else "New message"
            notify_priority = "urgent" if priority == "attention" else "high"
        for actor_id in headless_targets:
            if actor_id in skip_ids:
                continue
            actor = find_actor(group, actor_id)
            if isinstance(actor, dict) and str(actor.get("runtime") or "").strip().lower() == "web_model":
                continue
            emit_system_notify(
                group,
                by="system",
                notify=SystemNotifyData(
                    kind="info",
                    priority=notify_priority,
                    title=notify_title,
                    message=f"New message from {by}. Check your inbox.",
                    target_actor_id=actor_id,
                    requires_ack=False,
                    context={"event_id": event_id, "from": by},
                ),
            )
    except Exception:
        pass


def _commit_headless_delivery_queued(
    group: Any,
    *,
    experience_decision: Any,
) -> None:
    commit_experience_reminder(group, experience_decision)


def _prepare_message_skill(
    group: Any,
    *,
    capability_id: str,
    recipient_ids: list[str],
) -> tuple[str, Optional[DaemonResponse]]:
    """Enable a composer-selected skill for this turn and project OpenClaw skills.

    The capability state machine owns installation/policy checks.  We invoke it
    before appending the chat event so a projection failure cannot leave a
    message which the target runtime cannot execute.
    """
    raw_id = str(capability_id or "").strip()
    if not raw_id:
        return "", None
    try:
        from ..ops.capability_ops import (
            _canonical_capability_id,
            handle_capability_enable,
            prepare_openclaw_skill_package_overlay_for_actor,
        )
        from ..ops.capability_ops._admission import resolve_current_admission
    except Exception as exc:
        return "", _error("skill_projection_failed", str(exc))
    try:
        canonical_id = str(_canonical_capability_id(raw_id) or "").strip()
    except Exception:
        canonical_id = raw_id
    if not canonical_id:
        return "", _error("invalid_skill_capability", "skill capability id is empty")

    stable_name = f"onecolleague-{hashlib.sha256(canonical_id.encode('utf-8')).hexdigest()[:16]}"
    touched: list[tuple[str, bool]] = []
    actor_ids = list(dict.fromkeys(str(item or "").strip() for item in recipient_ids if str(item or "").strip()))

    def rollback() -> None:
        for touched_actor, had_session in reversed(touched):
            if had_session:
                continue
            try:
                handle_capability_enable(
                    {
                        "group_id": str(getattr(group, "group_id", "") or ""),
                        "actor_id": touched_actor,
                        "by": "user",
                        "scope": "session",
                        "capability_id": canonical_id,
                        "enabled": False,
                        "ttl_seconds": 3600,
                        "reason": "composer_skill_projection_rollback",
                    }
                )
            except Exception:
                logger.exception("failed to roll back composer skill activation for %s", touched_actor)

    for actor_id in actor_ids:
        actor = find_actor(group, actor_id)
        if not isinstance(actor, dict):
            continue
        try:
            admission_before = resolve_current_admission(
                group_id=str(getattr(group, "group_id", "") or ""), actor_id=actor_id
            )
            sources_before = admission_before.get("activation_sources") if isinstance(admission_before, dict) else {}
            had_session = any(
                isinstance(item, dict) and str(item.get("scope") or "") == "session"
                for item in (sources_before.get(canonical_id) if isinstance(sources_before, dict) else [])
            )
        except Exception:
            had_session = False
        enabled = handle_capability_enable(
            {
                "group_id": str(getattr(group, "group_id", "") or ""),
                "actor_id": actor_id,
                "by": "user",
                "scope": "session",
                "capability_id": canonical_id,
                "enabled": True,
                "ttl_seconds": 3600,
                "reason": "composer_selected_skill",
            }
        )
        result = enabled.result if isinstance(enabled.result, dict) else {}
        if not enabled.ok or result.get("enabled") is False or str(result.get("state") or "").lower() in {"blocked", "failed", "denied"}:
            rollback()
            return "", _error(
                "skill_projection_failed",
                str((enabled.error.message if enabled.error is not None else result.get("reason")) or "skill activation failed"),
                details={"capability_id": canonical_id, "actor_id": actor_id},
            )
        touched.append((actor_id, had_session))
        if str(actor.get("runtime") or "").strip().lower() == "openclaw":
            try:
                from ..ops.capability_ops._skill_packages import openclaw_actor_skill_projection_lock

                with openclaw_actor_skill_projection_lock(
                    str(getattr(group, "group_id", "") or ""), actor_id
                ):
                    projection = prepare_openclaw_skill_package_overlay_for_actor(group, actor_id)
                    selected = projection.get("selected_names") if isinstance(projection, dict) else []
                    if stable_name not in {str(item or "").strip() for item in selected if str(item or "").strip()}:
                        rollback()
                        return "", _error(
                            "skill_unavailable",
                            "selected skill is not installed or is not an OpenClaw skill package",
                            details={"capability_id": canonical_id, "actor_id": actor_id},
                        )
                    try:
                        from ..openclaw_runtime import refresh_openclaw_actor_skill_projection

                        refresh_openclaw_actor_skill_projection(
                            str(getattr(group, "group_id", "") or ""),
                            actor_id,
                            projection=projection,
                        )
                    except Exception as exc:
                        rollback()
                        return "", _error(
                            "skill_projection_failed",
                            str(exc),
                            details={"capability_id": canonical_id, "actor_id": actor_id},
                        )
            except Exception as exc:
                rollback()
                return "", _error(
                    "skill_projection_failed",
                    str(exc),
                    details={"capability_id": canonical_id, "actor_id": actor_id},
                )
    return stable_name, None


def handle_send(
    args: Dict[str, Any],
    *,
    coerce_bool: Callable[[Any], bool],
    normalize_attachments: Callable[[Any, Any], list[dict[str, Any]]],
    effective_runner_kind: Callable[[str], str],
    auto_wake_recipients: Callable[[Any, list[str], str], list[str]],
    automation_on_resume: Callable[[Any], None],
    automation_on_new_message: Callable[[Any], None],
    clear_pending_system_notifies: Callable[[str, set[str]], None],
) -> DaemonResponse:
    group_bridge_delivery, group_bridge_error = _consume_valid_group_bridge_delivery(args)
    if group_bridge_error is not None:
        return group_bridge_error
    group_id = str(args.get("group_id") or "").strip()
    text = str(args.get("text") or "")
    by = str(args.get("by") or "user").strip()
    priority = str(args.get("priority") or "normal").strip() or "normal"
    reply_required = coerce_bool(args.get("reply_required"))
    collaboration_required = coerce_bool(args.get("collaboration_required"))
    message_format = args["format"] if group_bridge_delivery else "plain"
    computer_control_request_raw = args.get("computer_control_request")
    computer_control_request: Optional[Dict[str, Any]] = None
    if isinstance(computer_control_request_raw, dict):
        mode = str(computer_control_request_raw.get("mode") or "create_and_run").strip()
        actor_id = str(computer_control_request_raw.get("actor_id") or "").strip()
        workflow_id = str(computer_control_request_raw.get("workflow_id") or "").strip()
        inputs = computer_control_request_raw.get("inputs") if isinstance(computer_control_request_raw.get("inputs"), dict) else {}
        permissions = computer_control_permissions(computer_control_request_raw)
        if mode not in {"create_and_run", "run_existing"}:
            return _error("invalid_computer_control_request", "computer control mode must be create_and_run or run_existing")
        if not actor_id:
            return _error("invalid_computer_control_request", "computer control actor_id is required")
        if mode == "run_existing" and not workflow_id:
            return _error("invalid_computer_control_request", "workflow_id is required when running an existing workflow")
        try:
            from ...computer_control.models import WorkflowDefinition

            WorkflowDefinition._reject_plain_secrets(inputs)
        except ValueError as exc:
            return _error("sensitive_value_rejected", str(exc))
        computer_control_request = {
            "request_id": "ccreq_" + uuid.uuid4().hex[:14],
            "mode": mode,
            "workflow_id": workflow_id,
            "actor_id": actor_id,
            "inputs": inputs,
            **permissions,
            "status": "accepted",
        }
    quote_text = str(args.get("quote_text") or "").strip()
    src_group_id = str(args.get("src_group_id") or "").strip()
    src_event_id = str(args.get("src_event_id") or "").strip()
    dst_group_id = str(args.get("dst_group_id") or "").strip()
    client_id = str(args.get("client_id") or "").strip()
    source_platform = str(args.get("source_platform") or "").strip()
    source_user_name = str(args.get("source_user_name") or "").strip()
    source_user_id = str(args.get("source_user_id") or "").strip()
    mention_user_ids_raw = args.get("mention_user_ids")
    mention_user_ids = (
        [str(item).strip() for item in mention_user_ids_raw if str(item).strip()]
        if isinstance(mention_user_ids_raw, list)
        else []
    )
    dst_to_raw = args.get("dst_to")
    dst_to: list[str] = []
    if isinstance(dst_to_raw, list):
        dst_to = [str(x).strip() for x in dst_to_raw if isinstance(x, str) and str(x).strip()]
    if (src_group_id and not src_event_id) or (src_event_id and not src_group_id):
        src_group_id = ""
        src_event_id = ""
    to_raw = args.get("to")
    to_tokens: list[str] = []
    if isinstance(to_raw, list):
        to_tokens = [str(x).strip() for x in to_raw if isinstance(x, str) and str(x).strip()]
    elif isinstance(to_raw, str):
        token = to_raw.strip()
        if token:
            to_tokens = [token]
    if computer_control_request is not None:
        to_tokens = [str(computer_control_request["actor_id"])]
    to_explicitly_set = len(to_tokens) > 0
    install_slash_command = None if group_bridge_delivery else parse_install_slash_command(text)

    if priority not in ("normal", "attention"):
        return _error("invalid_priority", "priority must be 'normal' or 'attention'")
    if not group_id:
        return _error("missing_group_id", "missing group_id")

    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    if client_id and not group_bridge_delivery:
        existing = _tracked_send_existing_result(group, client_id=client_id, by=by)
        if existing is not None:
            return DaemonResponse(ok=True, result=existing)

    try:
        admission_ingress = ""
        if ADMISSION_CLAIM_ARG in args:
            committed = consume_admission_claim(args, group_id=group_id, sender_id=by)
            to = list(committed.to)
            insight = committed.insight
            admission_ingress = committed.ingress
        else:
            input_tokens, to, peers = _resolve_send_audience(group, args=args, by=by)
            if INGRESS_CLAIM_ARG in args:
                pending = issue_message_admission(
                    args,
                    expected_op="send",
                    destination_group=group,
                    input_tokens=input_tokens,
                    to=to,
                    peer_actor_ids=peers,
                )
                committed = pending.consume_current(lambda value: value)
                to = list(committed.to)
                insight = committed.insight
                admission_ingress = committed.ingress
            else:
                insight = normalize_insight(args.get("insight"))
    except MessageAdmissionError as exc:
        return _admission_error_response(exc)
    except ValueError as exc:
        return _error("invalid_insight", str(exc))

    if not text.strip() and not args.get("attachments"):
        return _error("empty_message", "message text cannot be empty")

    skill_capability_id = str(args.get("skill_capability_id") or "").strip()
    skill_delivery_name, skill_error = _prepare_message_skill(
        group,
        capability_id=skill_capability_id,
        recipient_ids=recipient_actor_ids(group, to),
    )
    if skill_error is not None:
        return skill_error

    group = _wake_group_on_human_message(
        group,
        by=by,
        state_at_accept=str(args.get("__group_state_at_accept") or ""),
        automation_on_resume=automation_on_resume,
        clear_pending_system_notifies=clear_pending_system_notifies,
    )

    woken: list[str] = []
    if targets_any_agent(to):
        matched_enabled = enabled_recipient_actor_ids(group, to)
        if by and by in matched_enabled:
            matched_enabled = [actor_id for actor_id in matched_enabled if actor_id != by]
        woken = auto_wake_recipients(group, to, by)
        if not matched_enabled:
            if not woken:
                wanted = " ".join(to) if to else "@all"
                return _error(
                    "no_enabled_recipients",
                    (
                        "No enabled recipients after excluding sender. "
                        "Please specify 'to' explicitly, e.g. to=['user'], to=['@all'], or to=['peer-reviewer']. "
                        f"Current resolved recipients: {wanted}"
                    ),
                    details={"to": list(to)},
                )

    path = str(args.get("path") or "").strip()
    if path:
        scope = detect_scope(Path(path))
        scope_key = scope.scope_key
        scopes = group.doc.get("scopes")
        attached = False
        if isinstance(scopes, list):
            attached = any(isinstance(item, dict) and item.get("scope_key") == scope_key for item in scopes)
        if not attached:
            return _error(
                "scope_not_attached",
                f"scope not attached: {scope_key}",
                details={"hint": "onecolleague attach <path> --group <id>"},
            )
    else:
        scope_key = str(group.doc.get("active_scope_key") or "").strip()
    if not scope_key:
        scope_key = ""

    try:
        attachments = normalize_attachments(group, args.get("attachments"))
    except Exception as e:
        return _error("invalid_attachments", str(e))
    refs = _normalize_refs(args.get("refs"))
    delivery_body_text = text
    if skill_delivery_name:
        delivery_body_text = f"Use the enabled OneColleague skill `/{skill_delivery_name}` for this task.\n\n{delivery_body_text}"
    if install_slash_command is not None:
        delivery_body_text = render_install_command_task(install_slash_command)
        refs = [
            *refs,
            {
                "kind": "text",
                "title": "slash_command",
                "command": "/install",
                "capability_id": INSTALL_CAPABILITY_ID,
                "args_text": install_slash_command.get("args_text", ""),
                "target": install_slash_command.get("target", ""),
                "target_kind": install_slash_command.get("target_kind", ""),
            },
        ]

    if not text.strip() and not attachments:
        return _error("empty_message", "message text cannot be empty")

    event_data = ChatMessageData(
        text=text,
        format=message_format,
        insight=insight,
        priority=priority,
        reply_required=reply_required,
        collaboration_required=collaboration_required,
        computer_control_request=computer_control_request,
        skill_capability_id=skill_capability_id or None,
        quote_text=quote_text or None,
        to=to,
        refs=refs,
        attachments=attachments,
        source_platform=source_platform or None,
        source_user_name=source_user_name or None,
        source_user_id=source_user_id or None,
        mention_user_ids=mention_user_ids or None,
        **build_sender_snapshot(group, by=by),
        src_group_id=src_group_id or None,
        src_event_id=src_event_id or None,
        src_by=str(args.get("src_by") or "").strip() or None,
        remote_reply_to=(
            [str(item).strip() for item in args.get("remote_reply_to", []) if str(item).strip()]
            if isinstance(args.get("remote_reply_to"), list)
            else None
        ),
        dst_group_id=dst_group_id or None,
        dst_to=dst_to if dst_group_id else None,
        client_id=client_id or None,
        turn_provenance=build_send_turn_provenance(
            {**args, **({TRUSTED_INGRESS_ARG: admission_ingress} if admission_ingress else {})}
        ),
    ).model_dump()
    group_bridge_replayed = False
    if group_bridge_delivery:
        try:
            event, replayed = append_event_once(
                group.ledger_path,
                event_id=client_id,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by=by,
                data=event_data,
            )
        except LedgerEventConflictError:
            return _error("group_bridge_delivery_conflict", "Group Bridge delivery identity conflicts with the ledger")
        group_bridge_replayed = replayed
    else:
        event = append_event(
            group.ledger_path,
            kind="chat.message",
            group_id=group.group_id,
            scope_key=scope_key,
            by=by,
            data=event_data,
        )
    if computer_control_request is not None:
        event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
        event_provenance = (
            event_data.get("turn_provenance")
            if isinstance(event_data.get("turn_provenance"), dict)
            else {}
        )
        request_record = {
            **computer_control_request,
            "group_id": group.group_id,
            "text": text,
            "event_id": str(event.get("id") or ""),
            "local_request_id": str(event_provenance.get("local_request_id") or ""),
            "created_at": utc_now_iso(),
            "created_ts": time.time(),
        }
        try:
            request_path = group.path / "state" / "computer-control" / "requests.jsonl"
            request_path.parent.mkdir(parents=True, exist_ok=True)
            with request_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(request_record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            logger.exception("failed to persist computer-control request %s", computer_control_request.get("request_id"))
    effective_to = to if to else ["@all"]
    event_id = str(event.get("id") or "").strip()
    event_ts = str(event.get("ts") or "").strip()
    delivery_text = _build_delivery_text(
        text=delivery_body_text,
        insight=insight,
        priority=priority,
        reply_required=reply_required,
        collaboration_required=collaboration_required,
        event_id=event_id,
        refs=refs,
        attachments=attachments,
        src_group_id=src_group_id,
        src_event_id=src_event_id,
        computer_control_request=computer_control_request,
    )
    headless_delivery_text = append_mcp_reply_reminder(
        _build_headless_delivery_text(
            by=by,
            to=effective_to,
            body=delivery_text,
            quote_text=quote_text,
            source_platform=source_platform,
            source_user_name=source_user_name,
            source_user_id=source_user_id,
        )
    )
    actors = list_actors(group)
    event_for_delivery = event_with_effective_to(event, effective_to)
    skip_headless_notify_actor_ids: set[str] = set()
    logger.debug(f"[SEND] group={group_id} text={text[:30]!r} actors={[a.get('id') for a in actors]} effective_to={effective_to}")
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        decision = plan_actor_chat_delivery(
            group=group,
            actor=actor,
            event=event,
            by=by,
            effective_to=effective_to,
            effective_runner_kind=effective_runner_kind,
            codex_headless_running=codex_app_supervisor.actor_running,
            claude_headless_running=claude_app_supervisor.actor_running,
            web_model_browser_delivery_enabled=web_model_browser_delivery_enabled,
        )
        actor_id = decision.actor_id
        experience_decision = plan_experience_reminder(
            group,
            actor_id=actor_id,
            messages=[{"id": event_id, "kind": "chat.message", "by": by, "scope_key": scope_key}],
            scope_key=scope_key,
        )
        actor_headless_delivery_text = append_experience_reminder(headless_delivery_text, experience_decision)
        if decision.transport in {TRANSPORT_CODEX_HEADLESS, TRANSPORT_CODEX_APP_SERVER}:
            delivered = bool(codex_app_supervisor.submit_user_message(
                group_id=group.group_id,
                actor_id=actor_id,
                text=actor_headless_delivery_text,
                event_id=event_id,
                ts=event_ts,
                attachments=attachments,
            ))
            if delivered:
                _commit_headless_delivery_queued(
                    group,
                    experience_decision=experience_decision,
                )
                skip_headless_notify_actor_ids.add(actor_id)
        elif decision.transport == TRANSPORT_CLAUDE_HEADLESS:
            delivered = bool(claude_app_supervisor.submit_user_message(
                group_id=group.group_id,
                actor_id=actor_id,
                text=actor_headless_delivery_text,
                event_id=event_id,
                ts=event_ts,
                attachments=attachments,
            ))
            if delivered:
                _commit_headless_delivery_queued(
                    group,
                    experience_decision=experience_decision,
                )
                skip_headless_notify_actor_ids.add(actor_id)
        elif decision.transport == TRANSPORT_PTY:
            queue_chat_message(
                group,
                actor_id=actor_id,
                event_id=event_id,
                by=by,
                to=effective_to,
                text=delivery_text,
                source_platform=source_platform or None,
                source_user_name=source_user_name or None,
                source_user_id=source_user_id or None,
                collaboration_required=collaboration_required,
                scope_key=scope_key,
                ts=event_ts,
            )
            request_flush_pending_messages(group, actor_id=actor_id)
        elif decision.transport == TRANSPORT_WEB_MODEL_BROWSER:
            if schedule_web_model_browser_delivery(
                group_id=group.group_id,
                actor_id=actor_id,
                trigger_event_id=event_id,
                logger=logger,
            ):
                skip_headless_notify_actor_ids.add(actor_id)
        else:
            if actor_id in woken and decision.reason in {"codex_headless_not_running", "claude_headless_not_running"}:
                if schedule_headless_post_wake_delivery(
                    group_id=group.group_id,
                    actor_id=actor_id,
                    runtime=decision.runtime,
                    text=actor_headless_delivery_text,
                    event_id=event_id,
                    ts=event_ts,
                    attachments=attachments,
                    codex_actor_running=codex_app_supervisor.actor_running,
                    claude_actor_running=claude_app_supervisor.actor_running,
                    codex_submit_user_message=codex_app_supervisor.submit_user_message,
                    claude_submit_user_message=claude_app_supervisor.submit_user_message,
                    logger=logger,
                    on_delivered=lambda g=group, d=experience_decision: _commit_headless_delivery_queued(
                        g,
                        experience_decision=d,
                    ),
                ):
                    skip_headless_notify_actor_ids.add(actor_id)
            logger.debug(f"[SEND] skip actor={actor_id} ({decision.reason})")

    _notify_headless_targets(
        group=group,
        by=by,
        event_id=event_id,
        priority=priority,
        reply_required=reply_required,
        event=event_for_delivery,
        skip_actor_ids=skip_headless_notify_actor_ids,
    )

    if not group_bridge_delivery:
        try:
            automation_on_new_message(group)
        except Exception:
            pass
    result: Dict[str, Any] = {"event": event}
    if group_bridge_delivery:
        result.update(
            {
                "event_id": client_id,
                "replayed": group_bridge_replayed,
                "message_sent": True,
            }
        )
    return DaemonResponse(ok=True, result=result)


def handle_tracked_send(
    args: Dict[str, Any],
    *,
    coerce_bool: Callable[[Any], bool],
    normalize_attachments: Callable[[Any, Any], list[dict[str, Any]]],
    effective_runner_kind: Callable[[str], str],
    auto_wake_recipients: Callable[[Any, list[str], str], list[str]],
    automation_on_resume: Callable[[Any], None],
    automation_on_new_message: Callable[[Any], None],
    clear_pending_system_notifies: Callable[[str, set[str]], None],
) -> DaemonResponse:
    """Create a task and send the linked chat message as one daemon-owned operation."""
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    title = str(args.get("title") or "").strip()
    text = str(args.get("text") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    if not title:
        title = _compact_delivery_text(text, limit=120)
    if not title:
        return _error("missing_title", "tracked_send requires a title or non-empty text")
    if not text:
        return _error("empty_message", "tracked_send message text cannot be empty")
    message_priority = str(args.get("message_priority") or args.get("priority") or "normal").strip() or "normal"
    if message_priority not in ("normal", "attention"):
        return _error("invalid_priority", "priority must be 'normal' or 'attention'")

    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")

    idempotency_key = str(args.get("idempotency_key") or args.get("client_request_id") or "").strip()
    client_id = _tracked_send_client_id(group_id=group_id, by=by, idempotency_key=idempotency_key) if idempotency_key else ""
    if client_id:
        existing = _tracked_send_existing_result(group, client_id=client_id)
        if existing is not None:
            return DaemonResponse(ok=True, result=existing)
        existing_task = _tracked_send_existing_task(group, client_request_id=client_id)
    else:
        existing_task = None

    assignee = _derive_tracked_send_assignee(args)
    outcome = str(args.get("outcome") or args.get("goal") or "").strip() or text
    status = str(args.get("status") or "planned").strip() or "planned"
    waiting_on = str(args.get("waiting_on") or ("actor" if assignee else "none")).strip() or "none"
    priority = str(args.get("task_priority") or message_priority).strip() or "normal"
    task_type = str(args.get("task_type") or "standard").strip() or "standard"
    checklist = _normalize_tracked_checklist(args.get("checklist"))
    notes = str(args.get("notes") or "").strip()
    blocked_by = args.get("blocked_by")
    handoff_to = str(args.get("handoff_to") or "").strip()
    base_refs = _normalize_refs(args.get("refs"))
    reply_required = coerce_bool(args.get("reply_required")) if "reply_required" in args else True
    message_args = {
        "group_id": group_id,
        "text": text,
        "by": by,
        "to": _normalize_to_tokens(args.get("to")),
        "path": str(args.get("path") or ""),
        "priority": message_priority,
        "reply_required": reply_required,
        "refs": base_refs,
        "skill_capability_id": str(args.get("skill_capability_id") or "").strip(),
    }
    if client_id:
        message_args["client_id"] = client_id
    if INGRESS_CLAIM_ARG in args:
        try:
            input_tokens, canonical_to, peers = _resolve_send_audience(group, args=message_args, by=by)
            pending_admission = issue_message_admission(
                args,
                expected_op="tracked_send",
                destination_group=group,
                input_tokens=input_tokens,
                to=canonical_to,
                peer_actor_ids=peers,
            )
            committed_admission = pending_admission.consume_current(lambda value: value)
        except MessageAdmissionError as exc:
            if existing_task is not None and exc.code == "peer_insight_required":
                exc.details.update(
                    existing_task_preserved=True,
                    existing_task_id=str(getattr(existing_task, "id", "") or "").strip(),
                )
            return _admission_error_response(exc)
        message_args["to"] = list(committed_admission.to)
        message_args["insight"] = committed_admission.insight
        message_args[ADMISSION_CLAIM_ARG] = issue_admission_claim(committed_admission)
    else:
        try:
            message_args["insight"] = normalize_insight(args.get("insight"))
        except ValueError as exc:
            return _error("invalid_insight", str(exc))

    if existing_task is not None:
        existing_task_id = str(getattr(existing_task, "id", "") or "").strip()
        existing_title = str(getattr(existing_task, "title", "") or "").strip() or title
        existing_status = str(getattr(getattr(existing_task, "status", ""), "value", getattr(existing_task, "status", "")) or "planned").strip() or "planned"
        existing_waiting_on = str(getattr(getattr(existing_task, "waiting_on", ""), "value", getattr(existing_task, "waiting_on", "")) or "none").strip() or "none"
        existing_handoff_to = str(getattr(existing_task, "handoff_to", "") or "").strip()
        resumed_ref = _task_ref(
            task_id=existing_task_id,
            title=existing_title,
            status=existing_status,
            waiting_on=existing_waiting_on,
            handoff_to=existing_handoff_to,
        )
        message_args["refs"] = [*base_refs, resumed_ref]
        send_resp = handle_send(
            message_args,
            coerce_bool=coerce_bool,
            normalize_attachments=normalize_attachments,
            effective_runner_kind=effective_runner_kind,
            auto_wake_recipients=auto_wake_recipients,
            automation_on_resume=automation_on_resume,
            automation_on_new_message=automation_on_new_message,
            clear_pending_system_notifies=clear_pending_system_notifies,
        )
        if not send_resp.ok:
            err = send_resp.error.model_dump() if send_resp.error is not None else None
            return DaemonResponse(
                ok=True,
                result={
                    "task_id": existing_task_id,
                    "task_ref": resumed_ref,
                    "task_created": False,
                    "message_sent": False,
                    "partial_failure": True,
                    "message_error": err,
                    "recovered_from_partial_failure": False,
                },
            )
        send_result = send_resp.result if isinstance(send_resp.result, dict) else {}
        event = send_result.get("event") if isinstance(send_result.get("event"), dict) else {}
        return DaemonResponse(
            ok=True,
            result={
                "task_id": existing_task_id,
                "task_ref": resumed_ref,
                "event": event,
                "event_id": str(event.get("id") or "").strip(),
                "task_created": False,
                "message_sent": True,
                "partial_failure": False,
                "replayed": False,
                "recovered_from_partial_failure": True,
            },
        )

    task_op: dict[str, Any] = {
        "op": "task.create",
        "title": title,
        "outcome": outcome,
        "status": status,
        "priority": priority,
        "waiting_on": waiting_on,
        "task_type": task_type,
    }
    if client_id:
        task_op["client_request_id"] = client_id
    if assignee:
        task_op["assignee"] = assignee
    if notes:
        task_op["notes"] = notes
    if blocked_by is not None:
        task_op["blocked_by"] = blocked_by
    if handoff_to:
        task_op["handoff_to"] = handoff_to
    if checklist is not None:
        task_op["checklist"] = checklist

    task_resp = handle_context_sync({"group_id": group_id, "by": by, "ops": [task_op]})
    if not task_resp.ok:
        return task_resp
    task_result = task_resp.result if isinstance(task_resp.result, dict) else {}
    changes = task_result.get("changes") if isinstance(task_result.get("changes"), list) else []
    task_id = ""
    for change in changes:
        if isinstance(change, dict) and str(change.get("op") or "") == "task.create":
            task_id = str(change.get("task_id") or "").strip()
            if task_id:
                break
    if not task_id:
        return _error("tracked_send_task_missing", "task.create succeeded but did not return a task_id")

    ref = _task_ref(
        task_id=task_id,
        title=title,
        status=status,
        waiting_on=waiting_on,
        handoff_to=handoff_to,
    )
    message_args["refs"] = [*base_refs, ref]

    send_resp = handle_send(
        message_args,
        coerce_bool=coerce_bool,
        normalize_attachments=normalize_attachments,
        effective_runner_kind=effective_runner_kind,
        auto_wake_recipients=auto_wake_recipients,
        automation_on_resume=automation_on_resume,
        automation_on_new_message=automation_on_new_message,
        clear_pending_system_notifies=clear_pending_system_notifies,
    )
    if not send_resp.ok:
        err = send_resp.error.model_dump() if send_resp.error is not None else None
        return DaemonResponse(
            ok=True,
            result={
                "task_id": task_id,
                "task_ref": ref,
                "context_result": task_result,
                "task_created": True,
                "message_sent": False,
                "partial_failure": True,
                "message_error": err,
            },
        )
    send_result = send_resp.result if isinstance(send_resp.result, dict) else {}
    event = send_result.get("event") if isinstance(send_result.get("event"), dict) else {}
    return DaemonResponse(
        ok=True,
        result={
            "task_id": task_id,
            "task_ref": ref,
            "context_result": task_result,
            "event": event,
            "event_id": str(event.get("id") or "").strip(),
            "task_created": True,
            "message_sent": True,
            "partial_failure": False,
            "replayed": False,
        },
    )


def handle_reply(
    args: Dict[str, Any],
    *,
    coerce_bool: Callable[[Any], bool],
    normalize_attachments: Callable[[Any, Any], list[dict[str, Any]]],
    effective_runner_kind: Callable[[str], str],
    auto_wake_recipients: Callable[[Any, list[str], str], list[str]],
    automation_on_resume: Callable[[Any], None],
    automation_on_new_message: Callable[[Any], None],
    clear_pending_system_notifies: Callable[[str, set[str]], None],
) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    text = str(args.get("text") or "")
    by = str(args.get("by") or "user").strip()
    reply_to = str(args.get("reply_to") or "").strip()
    priority = str(args.get("priority") or "normal").strip() or "normal"
    reply_required = coerce_bool(args.get("reply_required"))
    collaboration_required = coerce_bool(args.get("collaboration_required"))
    client_id = str(args.get("client_id") or "").strip()
    to_raw = args.get("to")
    to_tokens: list[str] = []
    if isinstance(to_raw, list):
        to_tokens = [str(x).strip() for x in to_raw if isinstance(x, str) and str(x).strip()]

    if priority not in ("normal", "attention"):
        return _error("invalid_priority", "priority must be 'normal' or 'attention'")
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    if not reply_to:
        return _error("missing_reply_to", "missing reply_to event_id")

    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")

    original, existing_ack = find_event_with_chat_ack(group, event_id=reply_to, actor_id=by)
    if original is None:
        return _error("event_not_found", f"event not found: {reply_to}")
    target_event_id = str(original.get("id") or "").strip()
    original_data = original.get("data") if isinstance(original.get("data"), dict) else {}
    quote_text = _quote_text_from_message_data(original_data, max_len=100)
    original_source_platform = str(original_data.get("source_platform") or "").strip()
    original_source_user_name = str(original_data.get("source_user_name") or "").strip()
    original_source_user_id = str(original_data.get("source_user_id") or "").strip()
    original_mention_user_ids_raw = original_data.get("mention_user_ids")
    original_mention_user_ids = (
        [str(item).strip() for item in original_mention_user_ids_raw if str(item).strip()]
        if isinstance(original_mention_user_ids_raw, list)
        else []
    )

    if not to_tokens:
        to_tokens = default_reply_recipients(group, by=by, original_event=original)
    try:
        to = resolve_recipient_tokens(group, to_tokens)
    except Exception as e:
        return _error("invalid_recipient", str(e))

    try:
        attachments = normalize_attachments(group, args.get("attachments"))
    except Exception as e:
        return _error("invalid_attachments", str(e))
    refs = _normalize_refs(args.get("refs"))
    if not text.strip() and not attachments:
        return _error("empty_message", "message text cannot be empty")
    try:
        insight = normalize_insight(args.get("insight"))
    except ValueError as exc:
        return _error("invalid_insight", str(exc))
    request_fingerprint = _reply_request_fingerprint(
        group_id=group_id,
        by=by,
        client_id=client_id,
        reply_to=target_event_id or reply_to,
        text=text,
        insight=insight,
        to=to,
        priority=priority,
        reply_required=reply_required,
        collaboration_required=collaboration_required,
        refs=refs,
        attachments=attachments,
    )
    existing_reply = _reply_existing_event(group, client_id=client_id, by=by)
    if existing_reply is not None:
        existing_data = existing_reply.get("data") if isinstance(existing_reply.get("data"), dict) else {}
        if str(existing_data.get("request_fingerprint") or "") != request_fingerprint:
            return _error("message_replay_conflict", "reply client_id conflicts with the original request")
        return DaemonResponse(ok=True, result={"event": existing_reply, "replayed": True})

    if INGRESS_CLAIM_ARG in args:
        try:
            peers = [actor_id for actor_id in recipient_actor_ids(group, to) if actor_id != by]
            pending_admission = issue_message_admission(
                args,
                expected_op="reply",
                destination_group=group,
                input_tokens=to_tokens,
                to=to,
                peer_actor_ids=peers,
            )
            committed_admission = pending_admission.consume_current(lambda value: value)
            to = list(committed_admission.to)
            insight = committed_admission.insight
            admission_ingress = committed_admission.ingress
        except MessageAdmissionError as exc:
            return _admission_error_response(exc)

    if not text.strip() and not args.get("attachments"):
        return _error("empty_message", "message text cannot be empty")

    skill_capability_id = str(args.get("skill_capability_id") or "").strip()
    skill_delivery_name, skill_error = _prepare_message_skill(
        group,
        capability_id=skill_capability_id,
        recipient_ids=recipient_actor_ids(group, to),
    )
    if skill_error is not None:
        return skill_error

    group = _wake_group_on_human_message(
        group,
        by=by,
        state_at_accept=str(args.get("__group_state_at_accept") or ""),
        automation_on_resume=automation_on_resume,
        clear_pending_system_notifies=clear_pending_system_notifies,
    )

    woken: list[str] = []
    if targets_any_agent(to):
        matched_enabled = enabled_recipient_actor_ids(group, to)
        if by and by in matched_enabled:
            matched_enabled = [actor_id for actor_id in matched_enabled if actor_id != by]
        woken = auto_wake_recipients(group, to, by)
        if not matched_enabled:
            if not woken:
                wanted = " ".join(to) if to else "@all"
                return _error(
                    "no_enabled_recipients",
                    (
                        "No enabled recipients after excluding sender. "
                        "Please specify 'to' explicitly, e.g. to=['user'], to=['@all'], or to=['peer-reviewer']. "
                        f"Current resolved recipients: {wanted}"
                    ),
                    details={"to": list(to)},
                )

    scope_key = str(group.doc.get("active_scope_key") or "").strip()

    event = append_event(
        group.ledger_path,
        kind="chat.message",
        group_id=group.group_id,
        scope_key=scope_key,
        by=by,
        data=ChatMessageData(
            text=text,
            format="plain",
            insight=insight,
            priority=priority,
            reply_required=reply_required,
            collaboration_required=collaboration_required,
            skill_capability_id=skill_capability_id or None,
            to=to,
            reply_to=target_event_id or reply_to,
            quote_text=quote_text,
            refs=refs,
            attachments=attachments,
            source_platform=original_source_platform or None,
            source_user_name=original_source_user_name or None,
            source_user_id=original_source_user_id or None,
            mention_user_ids=original_mention_user_ids or None,
            **build_sender_snapshot(group, by=by),
            client_id=client_id or None,
            request_fingerprint=request_fingerprint if client_id else None,
            turn_provenance=build_reply_turn_provenance(
                original,
                {**args, **({TRUSTED_INGRESS_ARG: admission_ingress} if INGRESS_CLAIM_ARG in args else {})},
            ),
        ).model_dump(),
    )

    if isinstance(find_actor(group, by), dict):
        invalidate_turn_grant_from_completion_receipt(
            group,
            by,
            completion_receipt=args.get("completion_receipt"),
            reason="actor_reply",
        )

    ack_event: Optional[dict[str, Any]] = None
    try:
        if str(original.get("kind") or "") == "chat.message":
            original_by = str(original.get("by") or "").strip()
            original_data = original.get("data") if isinstance(original.get("data"), dict) else {}
            original_priority = str(original_data.get("priority") or "normal").strip()
            if by and by != original_by and original_priority == "attention":
                if is_message_for_actor(group, actor_id=by, event=original):
                    if target_event_id and not existing_ack:
                        ack_event = append_event(
                            group.ledger_path,
                            kind="chat.ack",
                            group_id=group.group_id,
                            scope_key="",
                            by=by,
                            data={"actor_id": by, "event_id": target_event_id},
                        )
    except Exception:
        ack_event = None

    effective_to = to if to else ["@all"]
    event_for_delivery = event_with_effective_to(event, effective_to)

    event_id = str(event.get("id") or "").strip()
    event_ts = str(event.get("ts") or "").strip()
    delivery_text = _build_delivery_text(
        text=(
            f"Use the enabled OneColleague skill `/{skill_delivery_name}` for this task.\n\n{text}"
            if skill_delivery_name
            else text
        ),
        insight=insight,
        priority=priority,
        reply_required=reply_required,
        collaboration_required=collaboration_required,
        event_id=event_id,
        refs=refs,
        attachments=attachments,
    )
    headless_delivery_text = append_mcp_reply_reminder(
        _build_headless_delivery_text(
            by=by,
            to=effective_to,
            body=delivery_text,
            reply_to=target_event_id or reply_to,
            quote_text=quote_text,
        )
    )
    skip_headless_notify_actor_ids: set[str] = set()
    for actor in list_actors(group):
        if not isinstance(actor, dict):
            continue
        decision = plan_actor_chat_delivery(
            group=group,
            actor=actor,
            event=event,
            by=by,
            effective_to=effective_to,
            effective_runner_kind=effective_runner_kind,
            codex_headless_running=codex_app_supervisor.actor_running,
            claude_headless_running=claude_app_supervisor.actor_running,
            web_model_browser_delivery_enabled=web_model_browser_delivery_enabled,
        )
        actor_id = decision.actor_id
        experience_decision = plan_experience_reminder(
            group,
            actor_id=actor_id,
            messages=[{"id": event_id, "kind": "chat.message", "by": by, "scope_key": scope_key}],
            scope_key=scope_key,
        )
        actor_headless_delivery_text = append_experience_reminder(headless_delivery_text, experience_decision)
        if decision.transport in {TRANSPORT_CODEX_HEADLESS, TRANSPORT_CODEX_APP_SERVER}:
            delivered = bool(codex_app_supervisor.submit_user_message(
                group_id=group.group_id,
                actor_id=actor_id,
                text=actor_headless_delivery_text,
                event_id=event_id,
                ts=event_ts,
                reply_to=target_event_id or reply_to,
                attachments=attachments,
            ))
            if delivered:
                _commit_headless_delivery_queued(
                    group,
                    experience_decision=experience_decision,
                )
                skip_headless_notify_actor_ids.add(actor_id)
        elif decision.transport == TRANSPORT_CLAUDE_HEADLESS:
            delivered = bool(claude_app_supervisor.submit_user_message(
                group_id=group.group_id,
                actor_id=actor_id,
                text=actor_headless_delivery_text,
                event_id=event_id,
                ts=event_ts,
                reply_to=target_event_id or reply_to,
                attachments=attachments,
            ))
            if delivered:
                _commit_headless_delivery_queued(
                    group,
                    experience_decision=experience_decision,
                )
                skip_headless_notify_actor_ids.add(actor_id)
        elif decision.transport == TRANSPORT_PTY:
            queue_chat_message(
                group,
                actor_id=actor_id,
                event_id=event_id,
                by=by,
                to=effective_to,
                text=delivery_text,
                reply_to=target_event_id or reply_to,
                quote_text=quote_text,
                collaboration_required=collaboration_required,
                scope_key=scope_key,
                ts=event_ts,
            )
            request_flush_pending_messages(group, actor_id=actor_id)
        elif decision.transport == TRANSPORT_WEB_MODEL_BROWSER:
            if schedule_web_model_browser_delivery(
                group_id=group.group_id,
                actor_id=actor_id,
                trigger_event_id=event_id,
                logger=logger,
            ):
                skip_headless_notify_actor_ids.add(actor_id)
        elif actor_id in woken and decision.reason in {"codex_headless_not_running", "claude_headless_not_running"}:
            if schedule_headless_post_wake_delivery(
                group_id=group.group_id,
                actor_id=actor_id,
                runtime=decision.runtime,
                text=actor_headless_delivery_text,
                event_id=event_id,
                ts=event_ts,
                reply_to=target_event_id or reply_to,
                attachments=attachments,
                codex_actor_running=codex_app_supervisor.actor_running,
                claude_actor_running=claude_app_supervisor.actor_running,
                codex_submit_user_message=codex_app_supervisor.submit_user_message,
                claude_submit_user_message=claude_app_supervisor.submit_user_message,
                logger=logger,
                on_delivered=lambda g=group, d=experience_decision: _commit_headless_delivery_queued(
                    g,
                    experience_decision=d,
                ),
            ):
                skip_headless_notify_actor_ids.add(actor_id)

    _notify_headless_targets(
        group=group,
        by=by,
        event_id=event_id,
        priority=priority,
        reply_required=reply_required,
        event=event_for_delivery,
        skip_actor_ids=skip_headless_notify_actor_ids,
    )

    try:
        automation_on_new_message(group)
    except Exception:
        pass
    return DaemonResponse(ok=True, result={"event": event, "ack_event": ack_event})


def handle_stream_emit(args: Dict[str, Any]) -> DaemonResponse:
    """Handle chat.stream events (start/update/end)."""
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "").strip()
    op = str(args.get("op") or "").strip()

    if not group_id:
        return _error("missing_group_id", "missing group_id")
    if not by:
        return _error("missing_by", "missing by")
    if op not in ("start", "update", "end"):
        return _error("invalid_op", "op must be 'start', 'update', or 'end'")

    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")

    stream_id = str(args.get("stream_id") or "").strip()
    if op == "start":
        stream_id = uuid.uuid4().hex
    elif not stream_id:
        return _error("missing_stream_id", "stream_id is required for update/end")

    text = str(args.get("text") or "")
    fmt = str(args.get("format") or "plain").strip() or "plain"
    seq = int(args.get("seq") or 0)
    to_raw = args.get("to")
    to: list[str] = []
    if isinstance(to_raw, list):
        to = [str(x).strip() for x in to_raw if isinstance(x, str) and str(x).strip()]
    reply_to = str(args.get("reply_to") or "").strip() or None
    client_id = str(args.get("client_id") or "").strip() or None

    data = ChatStreamData(
        stream_id=stream_id,
        op=op,
        text=text,
        format=fmt,
        seq=seq,
        to=to,
        reply_to=reply_to,
        client_id=client_id,
    )

    scope_key = str(group.doc.get("active_scope_key") or "").strip()
    event = append_event(
        group.ledger_path,
        kind="chat.stream",
        group_id=group.group_id,
        scope_key=scope_key,
        by=by,
        data=data.model_dump(),
    )

    return DaemonResponse(ok=True, result={"event": event, "stream_id": stream_id})


def _open_active_scope_file(group: Any, raw_path: str) -> tuple[int, os.stat_result, str]:
    scope_key = str(group.doc.get("active_scope_key") or "").strip()
    scopes = group.doc.get("scopes") if isinstance(group.doc.get("scopes"), list) else []
    scope_url = next(
        (
            str(item.get("url") or "").strip()
            for item in scopes
            if isinstance(item, dict) and str(item.get("scope_key") or "").strip() == scope_key
        ),
        "",
    )
    if not scope_key or not scope_url:
        raise MessageAdmissionError("missing_scope", "group has no active scope")
    root = Path(scope_url).expanduser().resolve(strict=True)
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise MessageAdmissionError("invalid_path", "path must be under the active scope") from exc
    else:
        relative = candidate
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise MessageAdmissionError("invalid_path", "path must identify a file under the active scope")
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        before = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise MessageAdmissionError("not_found", "file is not a regular active-scope file")
        if before.st_size > _MAX_FILE_SEND_BYTES:
            raise MessageAdmissionError("file_too_large", "file is too large to send")
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        after = os.fstat(file_fd)
        if (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        ):
            os.close(file_fd)
            raise MessageAdmissionError("file_changed", "file changed while it was opened")
        return file_fd, after, parts[-1]
    except OSError as exc:
        raise MessageAdmissionError("read_failed", str(exc)) from exc
    finally:
        os.close(directory_fd)


def handle_file_send(
    args: Dict[str, Any],
    *,
    coerce_bool: Callable[[Any], bool],
    normalize_attachments: Callable[[Any, Any], list[dict[str, Any]]],
    effective_runner_kind: Callable[[str], str],
    auto_wake_recipients: Callable[[Any, list[str], str], list[str]],
    automation_on_resume: Callable[[Any], None],
    automation_on_new_message: Callable[[Any], None],
    clear_pending_system_notifies: Callable[[str, set[str]], None],
) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "").strip()
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    priority = str(args.get("priority") or "normal").strip() or "normal"
    if priority not in {"normal", "attention"}:
        return _error("invalid_priority", "priority must be 'normal' or 'attention'")
    try:
        input_tokens, to, peers = _resolve_send_audience(group, args=args, by=by)
        pending = issue_message_admission(
            args,
            expected_op="file_send",
            destination_group=group,
            input_tokens=input_tokens,
            to=to,
            peer_actor_ids=peers,
        )
    except MessageAdmissionError as exc:
        return _admission_error_response(exc)

    try:
        file_fd, opened_stat, filename = _open_active_scope_file(group, str(args.get("path") or ""))
    except MessageAdmissionError as exc:
        return _admission_error_response(exc)
    try:
        bound = bind_file_descriptor(pending, file_fd, opened_stat)

        def complete(committed: Any) -> DaemonResponse:
            chunks: list[bytes] = []
            remaining = opened_stat.st_size
            while remaining > 0:
                chunk = os.read(file_fd, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            final_stat = os.fstat(file_fd)
            if (
                len(raw) != opened_stat.st_size
                or (final_stat.st_dev, final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns)
                != (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_size, opened_stat.st_mtime_ns)
            ):
                return _error("file_changed", "file changed while it was read")
            mime_type, _ = mimetypes.guess_type(filename)
            attachment = store_blob_bytes(
                group,
                data=raw,
                filename=filename,
                mime_type=str(mime_type or ""),
            )
            text = str(args.get("text") or "").strip() or f"[file] {attachment.get('title') or filename}"
            send_args = {
                "group_id": group_id,
                "by": by,
                "text": text,
                "to": list(committed.to),
                "insight": committed.insight,
                "attachments": [attachment],
                "priority": priority,
                "reply_required": coerce_bool(args.get("reply_required")),
                "skill_capability_id": str(args.get("skill_capability_id") or "").strip(),
                ADMISSION_CLAIM_ARG: issue_admission_claim(committed),
            }
            return handle_send(
                send_args,
                coerce_bool=coerce_bool,
                normalize_attachments=normalize_attachments,
                effective_runner_kind=effective_runner_kind,
                auto_wake_recipients=auto_wake_recipients,
                automation_on_resume=automation_on_resume,
                automation_on_new_message=automation_on_new_message,
                clear_pending_system_notifies=clear_pending_system_notifies,
            )

        return bound.consume_descriptor(file_fd, complete)
    except MessageAdmissionError as exc:
        return _admission_error_response(exc)
    except OSError as exc:
        return _error("read_failed", str(exc))
    finally:
        os.close(file_fd)


def try_handle_chat_op(
    op: str,
    args: Dict[str, Any],
    *,
    coerce_bool: Callable[[Any], bool],
    normalize_attachments: Callable[[Any, Any], list[dict[str, Any]]],
    effective_runner_kind: Callable[[str], str],
    auto_wake_recipients: Callable[[Any, list[str], str], list[str]],
    automation_on_resume: Callable[[Any], None],
    automation_on_new_message: Callable[[Any], None],
    clear_pending_system_notifies: Callable[[str, set[str]], None],
) -> Optional[DaemonResponse]:
    if op == "stream_emit":
        return handle_stream_emit(args)
    if op == "file_send":
        return handle_file_send(
            args,
            coerce_bool=coerce_bool,
            normalize_attachments=normalize_attachments,
            effective_runner_kind=effective_runner_kind,
            auto_wake_recipients=auto_wake_recipients,
            automation_on_resume=automation_on_resume,
            automation_on_new_message=automation_on_new_message,
            clear_pending_system_notifies=clear_pending_system_notifies,
        )
    if op == "send":
        return handle_send(
            args,
            coerce_bool=coerce_bool,
            normalize_attachments=normalize_attachments,
            effective_runner_kind=effective_runner_kind,
            auto_wake_recipients=auto_wake_recipients,
            automation_on_resume=automation_on_resume,
            automation_on_new_message=automation_on_new_message,
            clear_pending_system_notifies=clear_pending_system_notifies,
        )
    if op == "tracked_send":
        return handle_tracked_send(
            args,
            coerce_bool=coerce_bool,
            normalize_attachments=normalize_attachments,
            effective_runner_kind=effective_runner_kind,
            auto_wake_recipients=auto_wake_recipients,
            automation_on_resume=automation_on_resume,
            automation_on_new_message=automation_on_new_message,
            clear_pending_system_notifies=clear_pending_system_notifies,
        )
    if op == "reply":
        return handle_reply(
            args,
            coerce_bool=coerce_bool,
            normalize_attachments=normalize_attachments,
            effective_runner_kind=effective_runner_kind,
            auto_wake_recipients=auto_wake_recipients,
            automation_on_resume=automation_on_resume,
            automation_on_new_message=automation_on_new_message,
            clear_pending_system_notifies=clear_pending_system_notifies,
        )
    return None
