from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type


def _handle_experience_namespace(
    name: str,
    arguments: Dict[str, Any],
    *,
    resolve_group_id: Callable[[Dict[str, Any]], str],
    resolve_actor_id: Callable[[Dict[str, Any]], str],
    call_daemon_or_raise: Callable[..., Dict[str, Any]],
    mcp_error_cls: Type[Exception],
) -> Optional[Dict[str, Any]]:
    if name == "onecolleague_experience":
        action = str(arguments.get("action") or "read").strip().lower()
        if action not in {"read", "append", "replace"}:
            raise mcp_error_cls("invalid_request", "action must be one of: read, append, replace")

        args: Dict[str, Any] = {
            "group_id": resolve_group_id(arguments),
            "by": resolve_actor_id(arguments),
        }
        if action in {"append", "replace"}:
            content = str(arguments.get("content") or "")
            if not content.strip():
                raise mcp_error_cls("validation_error", "missing content")
            args["content"] = content
        if action == "replace":
            revision = str(arguments.get("expected_revision") or "").strip()
            if not revision:
                raise mcp_error_cls("validation_error", "expected_revision is required for replace")
            args["expected_revision"] = revision

        return call_daemon_or_raise({"op": f"experience_{action}", "args": args})
    return None
