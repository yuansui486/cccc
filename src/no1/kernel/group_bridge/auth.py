"""Group-scoped authorization helpers for Group Bridge management."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..access_tokens import lookup_access_token


def is_admin_entry(entry: Dict[str, Any]) -> bool:
    return bool(entry.get("is_admin", False)) if isinstance(entry, dict) else False


def allowed_group_ids(entry: Dict[str, Any]) -> List[str]:
    if not isinstance(entry, dict) or not isinstance(entry.get("allowed_groups"), list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in entry["allowed_groups"]:
        group_id = str(item or "").strip()
        if group_id and group_id not in seen:
            seen.add(group_id)
            out.append(group_id)
    return out


def can_access_group(entry: Dict[str, Any], group_id: str) -> bool:
    if is_admin_entry(entry):
        return True
    normalized = str(group_id or "").strip()
    return bool(normalized) and normalized in set(allowed_group_ids(entry))


def authorize_token_group(token: str, group_id: str, home: Optional[Path] = None) -> Dict[str, Any]:
    entry = lookup_access_token(str(token or "").strip(), home)
    if not isinstance(entry, dict):
        return {"allowed": False, "is_admin": False, "reason": "unknown_token"}
    admin = is_admin_entry(entry)
    allowed = can_access_group(entry, group_id)
    return {
        "allowed": allowed,
        "is_admin": admin,
        "reason": "admin" if admin else "group_allowed" if allowed else "group_denied",
    }
