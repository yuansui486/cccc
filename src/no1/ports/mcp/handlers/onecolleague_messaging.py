"""MCP handler functions for messaging tools (message_send/reply, blob_path, file_send)."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....kernel.actors import find_actor
from ....kernel.blobs import resolve_blob_attachment_path
from ....kernel.group import load_group
from ....kernel.peer_insight import POST_MESSAGE_NUDGE
from ....util.conv import coerce_bool
from ..common import MCPError, _call_daemon_or_raise
from ..actor_authority import ActorMessageAuthority, consume_actor_message_authority

_MAX_BLOB_READ_BYTES = 1_000_000
_DEFAULT_BLOB_READ_BYTES = 200_000


def _mcp_error(code: str, message: str, recommended_action: str = "") -> MCPError:
    details = {"recommended_action": recommended_action} if str(recommended_action or "").strip() else None
    return MCPError(code=code, message=message, details=details)


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


def _message_operation_succeeded(result: Dict[str, Any]) -> bool:
    payload = result if isinstance(result, dict) else {}
    if payload.get("partial_failure") is True or payload.get("message_sent") is False:
        return False
    return bool(
        isinstance(payload.get("event"), dict)
        or isinstance(payload.get("src_event"), dict) and isinstance(payload.get("dst_event"), dict)
        or payload.get("message_sent") is True
        or str(payload.get("event_id") or "").strip()
    )


def _with_post_message_nudge(result: Dict[str, Any]) -> Dict[str, Any]:
    if not _message_operation_succeeded(result):
        return result
    out = dict(result)
    out["post_message_nudge"] = {
        "kind": "whole_situation_reconstruction",
        "message": POST_MESSAGE_NUDGE,
    }
    return out


def message_send(
    *,
    group_id: str,
    dst_group_id: Optional[str] = None,
    actor_id: str,
    text: str,
    insight: Any = None,
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
    refs: Optional[List[Dict[str, Any]]] = None,
    actor_authority: ActorMessageAuthority,
) -> Dict[str, Any]:
    """Send a message to the group (or cross-group)."""
    consume_actor_message_authority(
        actor_authority,
        group_id=group_id,
        actor_id=actor_id,
        tool_names={"onecolleague_message_send"},
    )
    text = _normalize_runtime_escaped_text(group_id=group_id, actor_id=actor_id, text=text)
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)

    dst_gid = str(dst_group_id or "").strip()
    if dst_gid and dst_gid != str(group_id or "").strip():
        return _with_post_message_nudge(
            _call_daemon_or_raise(
                {
                    "op": "actor_send_cross_group",
                    "args": {
                        "group_id": group_id,
                        "dst_group_id": dst_gid,
                        "text": text,
                        "insight": insight,
                        "by": actor_id,
                        "to": to if to is not None else [],
                        "priority": prio,
                        "reply_required": reply_required_flag,
                        "refs": refs if refs is not None else [],
                    },
                }
            )
        )

    return _with_post_message_nudge(
        _call_daemon_or_raise(
            {
                "op": "actor_message_send",
                "args": {
                    "group_id": group_id,
                    "text": text,
                    "insight": insight,
                    "by": actor_id,
                    "to": to if to is not None else [],
                    "path": "",
                    "priority": prio,
                    "reply_required": reply_required_flag,
                    "refs": refs if refs is not None else [],
                },
            }
        )
    )


def tracked_send(
    *,
    group_id: str,
    actor_id: str,
    title: str,
    text: str,
    insight: Any = None,
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
    actor_authority: ActorMessageAuthority,
) -> Dict[str, Any]:
    """Create a task and send one visible task-linked delegation message."""
    consume_actor_message_authority(
        actor_authority,
        group_id=group_id,
        actor_id=actor_id,
        tool_names={"onecolleague_tracked_send"},
    )
    text = _normalize_runtime_escaped_text(group_id=group_id, actor_id=actor_id, text=text)
    title = str(title or "").strip()
    if not title:
        raise MCPError(code="missing_title", message="onecolleague_tracked_send requires title")
    if not text.strip():
        raise MCPError(code="empty_message", message="onecolleague_tracked_send message text cannot be empty")
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    return _with_post_message_nudge(
        _call_daemon_or_raise(
            {
                "op": "actor_tracked_send",
                "args": {
                    "group_id": group_id,
                    "by": actor_id,
                    "title": title,
                    "text": text,
                    "insight": insight,
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
    )


def message_reply(
    *,
    group_id: str,
    actor_id: str,
    reply_to: str,
    text: str,
    insight: Any = None,
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
    refs: Optional[List[Dict[str, Any]]] = None,
    completion_receipt: Optional[Dict[str, Any]] = None,
    client_id: str = "",
    actor_authority: ActorMessageAuthority,
) -> Dict[str, Any]:
    """Reply to a message."""
    consume_actor_message_authority(
        actor_authority,
        group_id=group_id,
        actor_id=actor_id,
        tool_names={"onecolleague_message_reply"},
    )
    if not str(reply_to or "").strip():
        raise _mcp_error(
            code="missing_event_id",
            message="missing event_id (reply target)",
            recommended_action="Use the event_id/reply_to from the message or delivered turn envelope; if unsure, inspect onecolleague_inbox_list or the current turn events.",
        )
    text = _normalize_runtime_escaped_text(group_id=group_id, actor_id=actor_id, text=text)
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)
    return _with_post_message_nudge(
        _call_daemon_or_raise(
            {
                "op": "actor_message_reply",
                "args": {
                    "group_id": group_id,
                    "text": text,
                    "insight": insight,
                    "by": actor_id,
                    "reply_to": reply_to,
                    "to": to if to is not None else [],
                    "priority": prio,
                    "reply_required": reply_required_flag,
                    "refs": refs if refs is not None else [],
                    "client_id": str(client_id or "").strip(),
                    "completion_receipt": completion_receipt if isinstance(completion_receipt, dict) else None,
                },
            }
        )
    )


def blob_path(*, group_id: str, rel_path: str) -> Dict[str, Any]:
    """Resolve a blob attachment path."""
    gid = str(group_id or "").strip()
    group = load_group(gid)
    if group is None:
        raise MCPError(code="group_not_found", message=f"group not found: {group_id}")
    rp = str(rel_path or "").strip()
    if not rp:
        raise _mcp_error(
            code="missing_rel_path",
            message="missing rel_path",
            recommended_action="Use the rel_path from the CCCC attachment, usually state/blobs/<sha256>_<name>.",
        )
    # Auto-prefix state/blobs/ when caller provides just the blob filename.
    if not rp.startswith("state/blobs/"):
        rp = f"state/blobs/{rp}"
    full = resolve_blob_attachment_path(group, rel_path=rp)
    return {"path": str(full)}


def blob_info(*, group_id: str, rel_path: str) -> Dict[str, Any]:
    """Return metadata for a blob attachment."""

    resolved = blob_path(group_id=group_id, rel_path=rel_path)
    path = Path(str(resolved.get("path") or ""))
    if not path.exists() or not path.is_file():
        raise _mcp_error(
            code="not_found",
            message=f"blob not found: {rel_path}",
            recommended_action="Check the exact attachment rel_path from the inbox/turn payload; pass the full state/blobs/... path or the blob filename.",
        )
    try:
        size = int(path.stat().st_size)
    except Exception:
        size = 0
    mt, _ = mimetypes.guess_type(path.name)
    return {
        "path": str(path),
        "rel_path": str(rel_path or "").strip(),
        "title": path.name,
        "mime_type": str(mt or ""),
        "bytes": size,
    }


def blob_read(*, group_id: str, rel_path: str, max_bytes: Any = _DEFAULT_BLOB_READ_BYTES) -> Dict[str, Any]:
    """Read a text blob attachment by relative path."""

    info = blob_info(group_id=group_id, rel_path=rel_path)
    path = Path(str(info.get("path") or ""))
    try:
        limit = int(max_bytes if max_bytes is not None else _DEFAULT_BLOB_READ_BYTES)
    except Exception:
        limit = _DEFAULT_BLOB_READ_BYTES
    limit = max(1, min(limit, _MAX_BLOB_READ_BYTES))
    try:
        with path.open("rb") as fh:
            data = fh.read(limit + 1)
    except Exception as exc:
        raise MCPError(code="read_failed", message=str(exc))
    truncated = len(data) > limit
    raw = data[:limit]
    text = raw.decode("utf-8", errors="replace")
    return {
        **info,
        "text": text,
        "truncated": truncated,
        "max_bytes": limit,
    }


def file_send(
    *,
    group_id: str,
    actor_id: str,
    path: str,
    text: str = "",
    insight: Any = None,
    to: Optional[List[str]] = None,
    priority: str = "normal",
    reply_required: bool = False,
    actor_authority: ActorMessageAuthority,
) -> Dict[str, Any]:
    """Send a local file as a chat.message attachment.

    Security: only files under the group's active scope root are allowed.
    """
    gid = str(group_id or "").strip()
    consume_actor_message_authority(
        actor_authority,
        group_id=gid,
        actor_id=actor_id,
        tool_names={"onecolleague_file"},
    )
    prio = str(priority or "normal").strip() or "normal"
    if prio not in ("normal", "attention"):
        raise MCPError(code="invalid_priority", message="priority must be 'normal' or 'attention'")
    reply_required_flag = coerce_bool(reply_required, default=False)
    return _with_post_message_nudge(
        _call_daemon_or_raise(
            {
                "op": "actor_file_send",
                "args": {
                    "group_id": gid,
                    "text": str(text or ""),
                    "insight": insight,
                    "by": actor_id,
                    "path": str(path or ""),
                    "to": to if to is not None else [],
                    "priority": prio,
                    "reply_required": reply_required_flag,
                },
            }
        )
    )
