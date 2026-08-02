"""Maintenance and relay operation handlers for daemon."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Optional, Tuple

from ...contracts.v1 import DaemonError, DaemonResponse
from ...kernel.actors import list_actors, resolve_recipient_tokens
from ...kernel.group import load_group
from ...kernel.ledger_retention import compact as compact_ledger
from ...kernel.ledger_retention import snapshot as snapshot_ledger
from ...kernel.permissions import require_group_permission
from ...runners import pty as pty_runner
from ...util.conv import coerce_bool
from ..messaging.turn_provenance import INGRESS_CROSS_GROUP, TRUSTED_INGRESS_ARG
from ..messaging.message_admission import (
    ADMISSION_CLAIM_ARG,
    INGRESS_CLAIM_ARG,
    CommittedMessageAdmission,
    MessageAdmissionError,
    issue_admission_claim,
    issue_message_admission,
)
from ...kernel.messaging import get_default_send_to, recipient_actor_ids


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> DaemonResponse:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


def handle_term_resize(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    actor_id = str(args.get("actor_id") or "").strip()
    cols_raw = args.get("cols")
    rows_raw = args.get("rows")
    writer_lease = str(args.get("writer_lease") or "").strip()
    try:
        cols = int(cols_raw) if isinstance(cols_raw, int) else int(str(cols_raw or "0"))
    except Exception:
        cols = 0
    try:
        rows = int(rows_raw) if isinstance(rows_raw, int) else int(str(rows_raw or "0"))
    except Exception:
        rows = 0
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    if not actor_id:
        return _error("missing_actor_id", "missing actor_id")
    if cols < 10 or rows < 2:
        return _error("invalid_size", f"cols={cols} rows={rows} too small")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    if not writer_lease:
        return _error("terminal_writer_lease_required", "terminal writer lease is required")
    resized = pty_runner.SUPERVISOR.resize_if_writer(
        group_id=group_id,
        actor_id=actor_id,
        writer_lease=writer_lease,
        cols=cols,
        rows=rows,
    )
    if not resized:
        return _error("terminal_not_writable", "terminal writer lease is no longer active")
    return DaemonResponse(ok=True, result={"group_id": group_id, "actor_id": actor_id, "cols": cols, "rows": rows})


def handle_ledger_snapshot(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip()
    reason = str(args.get("reason") or "manual").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        require_group_permission(group, by=by, action="group.update")
        snapshot = snapshot_ledger(group, reason=reason)
    except Exception as e:
        return _error("ledger_snapshot_failed", str(e))
    return DaemonResponse(ok=True, result={"snapshot": snapshot})


def handle_ledger_compact(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    by = str(args.get("by") or "user").strip()
    reason = str(args.get("reason") or "auto").strip()
    force = coerce_bool(args.get("force"), default=False)
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    group = load_group(group_id)
    if group is None:
        return _error("group_not_found", f"group not found: {group_id}")
    try:
        require_group_permission(group, by=by, action="group.update")
        result = compact_ledger(group, reason=reason, force=force)
    except Exception as e:
        return _error("ledger_compact_failed", str(e))
    return DaemonResponse(ok=True, result=result)


def handle_send_cross_group(
    args: Dict[str, Any],
    *,
    dispatch_send: Callable[[str, Dict[str, Any]], Tuple[DaemonResponse, bool]],
) -> DaemonResponse:
    src_group_id = str(args.get("group_id") or "").strip()
    dst_group_id = str(args.get("dst_group_id") or "").strip()
    text = str(args.get("text") or "")
    by = str(args.get("by") or "user").strip() or "user"
    priority = str(args.get("priority") or "normal").strip() or "normal"
    reply_required = coerce_bool(args.get("reply_required"), default=False)
    collaboration_required = coerce_bool(args.get("collaboration_required"), default=False)
    source_ingress = str(args.get(TRUSTED_INGRESS_ARG) or "").strip()
    to_raw = args.get("to")
    dst_to_tokens: list[str] = []
    if isinstance(to_raw, list):
        dst_to_tokens = [str(item).strip() for item in to_raw if isinstance(item, str) and str(item).strip()]

    attachments_raw = args.get("attachments")
    if attachments_raw:
        return _error("attachments_not_supported", "attachments are not supported for cross-group messages yet")
    refs_raw = args.get("refs")
    if isinstance(refs_raw, list) and any(isinstance(item, dict) for item in refs_raw):
        return _error("refs_not_supported", "quoted refs are not supported for cross-group messages yet")
    if priority not in ("normal", "attention"):
        return _error("invalid_priority", "priority must be 'normal' or 'attention'")
    if not src_group_id:
        return _error("missing_group_id", "missing group_id")
    if not dst_group_id:
        return _error("missing_dst_group_id", "missing dst_group_id")
    if src_group_id == dst_group_id:
        return _error("invalid_dst_group_id", "dst_group_id must be different from group_id")

    src_group = load_group(src_group_id)
    if src_group is None:
        return _error("group_not_found", f"group not found: {src_group_id}")
    dst_group = load_group(dst_group_id)
    if dst_group is None:
        return _error("group_not_found", f"group not found: {dst_group_id}")

    dst_input_tokens = list(dst_to_tokens)
    dst_to_canon: list[str] = []
    if dst_to_tokens:
        try:
            dst_to_canon = resolve_recipient_tokens(dst_group, dst_to_tokens)
        except Exception as e:
            return _error("invalid_recipient", str(e))
    if not dst_to_canon and not dst_to_tokens:
        mentions = re.findall(r"@(\w[\w-]*)", text)
        actor_ids = {
            str(actor.get("id") or "").strip()
            for actor in list_actors(dst_group)
            if isinstance(actor, dict)
        }
        mention_tokens = [
            f"@{item}" if item in {"all", "peers", "foreman"} else item
            for item in mentions
            if item in actor_ids or item in {"all", "peers", "foreman"}
        ]
        if mention_tokens:
            dst_input_tokens = mention_tokens
            try:
                dst_to_canon = resolve_recipient_tokens(dst_group, mention_tokens)
            except Exception as e:
                return _error("invalid_recipient", str(e))
    if not dst_to_canon and not dst_to_tokens and get_default_send_to(dst_group.doc) == "foreman":
        dst_to_canon = ["@foreman"]

    source_admission_claim = None
    destination_admission_claim = None
    insight = args.get("insight")
    if INGRESS_CLAIM_ARG in args:
        try:
            peers = [actor_id for actor_id in recipient_actor_ids(dst_group, dst_to_canon) if actor_id != by]
            pending = issue_message_admission(
                args,
                expected_op="send_cross_group",
                destination_group=dst_group,
                input_tokens=dst_input_tokens,
                to=dst_to_canon,
                peer_actor_ids=peers,
            )
            destination_admission = pending.consume_current(lambda value: value)
            source_admission = CommittedMessageAdmission(
                group_id=src_group_id,
                sender_id=by,
                to=("user",),
                insight=destination_admission.insight,
                authority_kind=destination_admission.authority_kind,
                ingress=destination_admission.ingress,
            )
            destination_admission = CommittedMessageAdmission(
                group_id=destination_admission.group_id,
                sender_id=destination_admission.sender_id,
                to=destination_admission.to,
                insight=destination_admission.insight,
                authority_kind=destination_admission.authority_kind,
                ingress=INGRESS_CROSS_GROUP,
            )
            source_admission_claim = issue_admission_claim(source_admission)
            destination_admission_claim = issue_admission_claim(destination_admission)
            insight = destination_admission.insight
        except MessageAdmissionError as exc:
            return _error(exc.code, exc.message, details=exc.details)

    src_resp, _ = dispatch_send(
        "send",
        {
            "group_id": src_group_id,
            "text": text,
            "by": by,
            "to": ["user"],
            "priority": priority,
            "reply_required": reply_required,
            "collaboration_required": collaboration_required,
            "dst_group_id": dst_group_id,
            "dst_to": dst_to_canon,
            "insight": insight,
            **({ADMISSION_CLAIM_ARG: source_admission_claim} if source_admission_claim is not None else {TRUSTED_INGRESS_ARG: source_ingress}),
        },
    )
    if not src_resp.ok:
        return src_resp

    src_event = src_resp.result.get("event")
    src_event_id = str((src_event or {}).get("id") or "").strip() if isinstance(src_event, dict) else ""
    if not src_event_id:
        return _error("send_failed", "missing source event id")

    dst_resp, _ = dispatch_send(
        "send",
        {
            "group_id": dst_group_id,
            "text": text,
            "by": by,
            "to": dst_to_canon,
            "priority": priority,
            "reply_required": reply_required,
            "collaboration_required": collaboration_required,
            "src_group_id": src_group_id,
            "src_event_id": src_event_id,
            "src_by": by,
            "insight": insight,
            **({ADMISSION_CLAIM_ARG: destination_admission_claim} if destination_admission_claim is not None else {TRUSTED_INGRESS_ARG: INGRESS_CROSS_GROUP}),
        },
    )
    if not dst_resp.ok:
        return dst_resp

    return DaemonResponse(ok=True, result={"src_event": src_event, "dst_event": dst_resp.result.get("event")})


def try_handle_maintenance_op(
    op: str,
    args: Dict[str, Any],
    *,
    dispatch_send: Optional[Callable[[str, Dict[str, Any]], Tuple[DaemonResponse, bool]]] = None,
) -> Optional[DaemonResponse]:
    if op == "term_resize":
        return handle_term_resize(args)
    if op == "ledger_snapshot":
        return handle_ledger_snapshot(args)
    if op == "ledger_compact":
        return handle_ledger_compact(args)
    if op == "send_cross_group":
        if dispatch_send is None:
            return _error("internal_error", "dispatch_send callback not configured")
        return handle_send_cross_group(args, dispatch_send=dispatch_send)
    return None
