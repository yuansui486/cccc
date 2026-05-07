"""MCP handler functions for messaging tools (message_send/reply, blob_path, file_send)."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....kernel.actors import find_actor
from ....kernel.blobs import resolve_blob_attachment_path, store_blob_bytes
from ....kernel.group import load_group
from ....util.conv import coerce_bool
from ..common import MCPError, _call_daemon_or_raise


def _find_target_actor(*, group_id: str, actor_id: str) -> Optional[Dict[str, Any]]:
    group = load_group(str(group_id or "").strip())
    if group is None:
        return None
    actor = find_actor(group, str(actor_id or "").strip())
    return dict(actor) if isinstance(actor, dict) else None


def _normalize_runtime_escaped_text(*, group_id: str, actor_id: str, text: str) -> str:
    """Normalize double-escaped control characters only for codex actor runtimes."""
    raw = str(text or "")
    actor = _find_target_actor(group_id=group_id, actor_id=actor_id)
    if actor is None:
        return raw
    runtime = str(actor.get("runtime") or "").strip().lower()
    if runtime != "codex":
        return raw
    normalized = raw
    for pattern, replacement in (
        (r"(?<!\\)\\n", "\n"),
        (r"(?<!\\)\\r", "\r"),
        (r"(?<!\\)\\t", "\t"),
    ):
        normalized = re.sub(pattern, replacement, normalized)
    return normalized


def message_send(
    *,
    group_id: str,
    dst_group_id: Optional[str] = None,
    actor_id: str,
    text: str,
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
    refs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Send a message to the group (or cross-group)."""
    text = _normalize_runtime_escaped_text(group_id=group_id, actor_id=actor_id, text=text)
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)

    dst_gid = str(dst_group_id or "").strip()
    if dst_gid and dst_gid != str(group_id or "").strip():
        return _call_daemon_or_raise(
            {
                "op": "send_cross_group",
                "args": {
                    "group_id": group_id,
                    "dst_group_id": dst_gid,
                    "text": text,
                    "by": actor_id,
                    "to": to if to is not None else [],
                    "priority": prio,
                    "reply_required": reply_required_flag,
                    "refs": refs if refs is not None else [],
                },
            }
        )

    return _call_daemon_or_raise(
        {
            "op": "send",
            "args": {
                "group_id": group_id,
                "text": text,
                "by": actor_id,
                "to": to if to is not None else [],
                "path": "",
                "priority": prio,
                "reply_required": reply_required_flag,
                "refs": refs if refs is not None else [],
            },
        }
    )


def tracked_send(
    *,
    group_id: str,
    actor_id: str,
    title: str,
    text: str,
    to: Optional[List[str]] = None,
    outcome: str = "",
    checklist: Optional[List[Dict[str, Any]]] = None,
    assignee: str = "",
    waiting_on: str = "",
    handoff_to: str = "",
    notes: str = "",
    priority: str = "normal",
    reply_required: bool = True,
    idempotency_key: str = "",
    refs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a task and send one visible task-linked delegation message."""
    text = _normalize_runtime_escaped_text(group_id=group_id, actor_id=actor_id, text=text)
    title = str(title or "").strip()
    if not title:
        raise MCPError(code="missing_title", message="cccc_tracked_send requires title")
    if not text.strip():
        raise MCPError(code="empty_message", message="cccc_tracked_send message text cannot be empty")
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    return _call_daemon_or_raise(
        {
            "op": "tracked_send",
            "args": {
                "group_id": group_id,
                "by": actor_id,
                "title": title,
                "text": text,
                "to": to if to is not None else [],
                "outcome": str(outcome or "").strip(),
                "checklist": checklist if checklist is not None else [],
                "assignee": str(assignee or "").strip(),
                "waiting_on": str(waiting_on or "").strip(),
                "handoff_to": str(handoff_to or "").strip(),
                "notes": str(notes or "").strip(),
                "priority": prio,
                "reply_required": coerce_bool(reply_required, default=True),
                "idempotency_key": str(idempotency_key or "").strip(),
                "refs": refs if refs is not None else [],
            },
        }
    )


def message_reply(
    *,
    group_id: str,
    actor_id: str,
    reply_to: str,
    text: str,
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
    refs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Reply to a message."""
    if not str(reply_to or "").strip():
        raise MCPError(code="missing_event_id", message="missing event_id (reply target)")
    text = _normalize_runtime_escaped_text(group_id=group_id, actor_id=actor_id, text=text)
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)
    return _call_daemon_or_raise(
        {
            "op": "reply",
            "args": {
                "group_id": group_id,
                "text": text,
                "by": actor_id,
                "reply_to": reply_to,
                "to": to if to is not None else [],
                "priority": prio,
                "reply_required": reply_required_flag,
                "refs": refs if refs is not None else [],
            },
        }
    )


def blob_path(*, group_id: str, rel_path: str) -> Dict[str, Any]:
    """Resolve a blob attachment path."""
    gid = str(group_id or "").strip()
    group = load_group(gid)
    if group is None:
        raise MCPError(code="group_not_found", message=f"group not found: {group_id}")
    rp = str(rel_path or "").strip()
    if not rp:
        raise MCPError(code="missing_rel_path", message="missing rel_path")
    # Auto-prefix state/blobs/ when caller provides just the blob filename.
    if not rp.startswith("state/blobs/"):
        rp = f"state/blobs/{rp}"
    full = resolve_blob_attachment_path(group, rel_path=rp)
    return {"path": str(full)}


def file_send(
    *,
    group_id: str,
    actor_id: str,
    path: str,
    text: str = "",
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
) -> Dict[str, Any]:
    """Send a local file as a chat.message attachment.

    Security: only files under the group's active scope root are allowed.
    """
    gid = str(group_id or "").strip()
    group = load_group(gid)
    if group is None:
        raise MCPError(code="group_not_found", message=f"group not found: {group_id}")

    scope_key = str(group.doc.get("active_scope_key") or "").strip()
    if not scope_key:
        raise MCPError(code="missing_scope", message="group has no active scope")

    scopes = group.doc.get("scopes")
    scope_url = ""
    if isinstance(scopes, list):
        for sc in scopes:
            if isinstance(sc, dict) and str(sc.get("scope_key") or "").strip() == scope_key:
                scope_url = str(sc.get("url") or "").strip()
                break
    if not scope_url:
        raise MCPError(code="missing_scope", message="active scope url not found")

    root = Path(scope_url).expanduser().resolve()
    src = Path(str(path or "").strip())
    if not src.is_absolute():
        src = (root / src).resolve()
    else:
        src = src.expanduser().resolve()

    try:
        src.relative_to(root)
    except ValueError:
        raise MCPError(code="invalid_path", message="path must be under the group's active scope root")
    if not src.exists() or not src.is_file():
        raise MCPError(code="not_found", message=f"file not found: {src}")

    try:
        raw = src.read_bytes()
    except Exception as e:
        raise MCPError(code="read_failed", message=str(e))

    mt, _ = mimetypes.guess_type(src.name)
    att = store_blob_bytes(group, data=raw, filename=src.name, mime_type=str(mt or ""))
    msg = str(text or "").strip() or f"[file] {att.get('title') or src.name}"
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)
    return _call_daemon_or_raise(
        {
            "op": "send",
            "args": {
                "group_id": gid,
                "text": msg,
                "by": actor_id,
                "to": to if to is not None else [],
                "path": "",
                "attachments": [att],
                "priority": prio,
                "reply_required": reply_required_flag,
            },
        }
    )
