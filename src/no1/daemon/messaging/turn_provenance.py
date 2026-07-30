"""Daemon-owned message provenance and current turn-grant projection."""

from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ...contracts.v1.message import TurnProvenance
from ...kernel.group import Group, load_group
from ...kernel.inbox import find_event
from ...util.fs import atomic_write_json, read_json


INGRESS_WEB_USER = "web_user"
INGRESS_CLI_USER = "cli_user"
INGRESS_ACTOR_MCP = "actor_mcp"
INGRESS_IM = "im"
INGRESS_CROSS_GROUP = "cross_group"
INGRESS_GROUP_BRIDGE = "group_bridge"
TRUSTED_INGRESS_ARG = "__turn_ingress"

DEFAULT_TURN_GRANT_TTL_SECONDS = 120.0
_DAEMON_ISSUER_EPOCH = f"daemon_{uuid.uuid4().hex}"

_STATE_LOCK = threading.Lock()
_ACTOR_LOCKS: dict[tuple[str, str], threading.RLock] = {}


class TurnDeliveryBusyError(RuntimeError):
    code = "turn_delivery_busy"


def _actor_lock(group_id: str, actor_id: str) -> threading.RLock:
    key = (str(group_id or "").strip(), str(actor_id or "").strip())
    with _STATE_LOCK:
        lock = _ACTOR_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ACTOR_LOCKS[key] = lock
        return lock


def _external_source_claimed(args: dict[str, Any]) -> bool:
    for key in (
        "source_platform",
        "source_user_name",
        "source_user_id",
        "src_group_id",
        "src_event_id",
    ):
        value = args.get(key)
        if isinstance(value, list):
            if value:
                return True
        elif str(value or "").strip():
            return True
    return False


def _source_identity(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        return None
    return normalized


def _source_transport(value: Any) -> Optional[str]:
    normalized = _source_identity(value)
    if normalized is None:
        return None
    canonical = normalized.lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", canonical) is None:
        return None
    return canonical


def build_send_turn_provenance(args: dict[str, Any]) -> TurnProvenance:
    """Map a trusted port marker to provenance without trusting display fields."""

    ingress = str(args.get(TRUSTED_INGRESS_ARG) or "").strip()
    by = str(args.get("by") or "user").strip() or "user"
    if ingress in {INGRESS_WEB_USER, INGRESS_CLI_USER}:
        if by == "user" and not _external_source_claimed(args):
            return TurnProvenance(
                origin="local_user",
                ingress=ingress,
                fresh_local_request=True,
                local_request_id=f"localreq_{uuid.uuid4().hex}",
                source_transport=ingress,
            )
        return TurnProvenance(origin="untrusted", ingress="untrusted")
    if ingress == INGRESS_ACTOR_MCP:
        return TurnProvenance(origin="local_actor", ingress=ingress, source_transport=ingress)
    if ingress == INGRESS_IM:
        source_transport = _source_transport(args.get("source_platform"))
        source_peer_id = _source_identity(args.get("source_user_id"))
        if not source_transport or not source_peer_id:
            return TurnProvenance(origin="untrusted", ingress="untrusted")
        return TurnProvenance(
            origin="im",
            ingress=ingress,
            source_transport=source_transport,
            source_peer_id=source_peer_id,
        )
    if ingress == INGRESS_CROSS_GROUP:
        source_group_id = _source_identity(args.get("src_group_id"))
        source_event_id = _source_identity(args.get("src_event_id"))
        source_peer_id = _source_identity(args.get("src_by"))
        if not source_group_id or not source_event_id or not source_peer_id:
            return TurnProvenance(origin="untrusted", ingress="untrusted")
        return TurnProvenance(
            origin="cross_group",
            ingress=ingress,
            source_transport=ingress,
            source_group_id=source_group_id,
            source_event_id=source_event_id,
            source_peer_id=source_peer_id,
        )
    if ingress == INGRESS_GROUP_BRIDGE:
        source_transport = _source_transport(args.get("source_platform"))
        source_group_id = _source_identity(args.get("src_group_id"))
        source_event_id = _source_identity(args.get("src_event_id"))
        source_peer_id = _source_identity(args.get("source_user_id")) or _source_identity(args.get("src_by"))
        if not source_transport or not source_group_id or not source_peer_id:
            return TurnProvenance(origin="untrusted", ingress="untrusted")
        return TurnProvenance(
            origin="group_bridge",
            ingress=ingress,
            source_transport=source_transport,
            source_group_id=source_group_id,
            source_event_id=source_event_id or None,
            source_peer_id=source_peer_id,
        )
    return TurnProvenance(origin="untrusted", ingress="untrusted")


def _legacy_event_provenance(event: dict[str, Any]) -> TurnProvenance:
    # Legacy display/routing fields are caller-authored and cannot establish
    # trusted origin after reload.
    return TurnProvenance(origin="untrusted", ingress="reply")


def event_turn_provenance(event: dict[str, Any]) -> TurnProvenance:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    raw = data.get("turn_provenance")
    if isinstance(raw, dict):
        try:
            return TurnProvenance.model_validate(raw)
        except Exception:
            pass
    return _legacy_event_provenance(event)


def build_reply_turn_provenance(original: dict[str, Any], args: dict[str, Any]) -> TurnProvenance:
    parent = str(original.get("id") or "").strip()
    inherited = event_turn_provenance(original)
    root = str(inherited.root_event_id or "").strip() or parent
    current = build_send_turn_provenance(args)
    correlation = {"parent_event_id": parent or None, "root_event_id": root or None}
    if current.origin in {"local_user", "local_actor"}:
        return current.model_copy(update=correlation)

    remote_origins = {"im", "cross_group", "group_bridge"}
    if current.origin in remote_origins:
        if inherited.origin in remote_origins:
            same_identity = (
                current.origin == inherited.origin
                and current.source_transport == inherited.source_transport
                and current.source_group_id == inherited.source_group_id
                and current.source_peer_id == inherited.source_peer_id
            )
            if current.origin != "group_bridge":
                same_identity = same_identity and current.source_event_id == inherited.source_event_id
            elif current.source_event_id and inherited.source_event_id:
                same_identity = same_identity and current.source_event_id == inherited.source_event_id
            if not same_identity:
                return TurnProvenance(origin="untrusted", ingress="reply", **correlation)
        return current.model_copy(update=correlation)

    return TurnProvenance(origin="untrusted", ingress="reply", **correlation)


def load_event_turn_provenance(group: Group, event_id: str) -> Optional[TurnProvenance]:
    event = find_event(group, str(event_id or "").strip())
    if not isinstance(event, dict) or str(event.get("kind") or "") != "chat.message":
        return None
    return event_turn_provenance(event)


def _state_path(group: Group, actor_id: str) -> Optional[Path]:
    group_path = getattr(group, "path", None)
    if not isinstance(group_path, Path):
        return None
    return group_path / "state" / "turn-grants" / f"{str(actor_id or '').strip()}.json"


def _utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _normalized_event_ids(event_ids: Iterable[str]) -> list[str]:
    out = [str(item or "").strip() for item in event_ids]
    if any(not item for item in out) or len(set(out)) != len(out):
        return []
    return out


def _load_state(group: Group, actor_id: str) -> dict[str, Any]:
    path = _state_path(group, actor_id)
    if path is None:
        return {}
    state = read_json(path)
    return state if isinstance(state, dict) else {}


def _abandon_stale_issuer_state(
    group: Group,
    actor_id: str,
    state: dict[str, Any],
    *,
    now: Optional[float] = None,
) -> dict[str, Any]:
    if str(state.get("issuer_epoch") or "") == _DAEMON_ISSUER_EPOCH:
        return state
    if not isinstance(state.get("pending_attempt"), dict) and not isinstance(state.get("current_grant"), dict):
        return state
    path = _state_path(group, actor_id)
    if path is None:
        return {}
    at = float(time.time() if now is None else now)
    abandoned = {
        "v": 1,
        "issuer_epoch": _DAEMON_ISSUER_EPOCH,
        "group_id": group.group_id,
        "actor_id": actor_id,
        "generation": max(0, int(state.get("generation") or 0)) + 1,
        "pending_attempt": None,
        "current_grant": None,
        "invalidated_reason": "daemon_restart_abandoned",
        "updated_at": _utc_iso(at),
    }
    atomic_write_json(path, abandoned, indent=2)
    return abandoned


def _normalized_binding(binding: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(binding, dict):
        return {}
    return {
        str(key): value
        for key, value in binding.items()
        if str(key).strip() and value not in (None, "")
    }


def begin_turn_delivery_attempt(
    group_or_id: Group | str,
    *,
    actor_id: str,
    event_ids: Iterable[str],
    binding: Optional[dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Persist one pending delivery generation without issuing authority."""

    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid:
        return None
    path = _state_path(group, aid)
    if path is None:
        return None
    ids = _normalized_event_ids(event_ids)
    at = float(time.time() if now is None else now)
    with _actor_lock(group.group_id, aid):
        previous = _load_state(group, aid)
        previous = _abandon_stale_issuer_state(group, aid, previous, now=at)
        if isinstance(previous.get("pending_attempt"), dict):
            raise TurnDeliveryBusyError(f"delivery attempt already pending for {group.group_id}/{aid}")
        generation = max(0, int(previous.get("generation") or 0)) + 1
        attempt = {
            "v": 1,
            "issuer_epoch": _DAEMON_ISSUER_EPOCH,
            "attempt_id": f"turnattempt_{uuid.uuid4().hex}",
            "group_id": group.group_id,
            "actor_id": aid,
            "event_ids": ids,
            "generation": generation,
            "binding": _normalized_binding(binding),
            "started_at": _utc_iso(at),
            "started_at_epoch": at,
        }
        atomic_write_json(
            path,
            {
                "v": 1,
                "issuer_epoch": _DAEMON_ISSUER_EPOCH,
                "group_id": group.group_id,
                "actor_id": aid,
                "generation": generation,
                "pending_attempt": attempt,
                "current_grant": None,
                "invalidated_reason": "delivery_attempt",
                "updated_at": _utc_iso(at),
            },
            indent=2,
        )
        return dict(attempt)


def _attempt_matches(current: Any, expected: dict[str, Any]) -> bool:
    if not isinstance(current, dict):
        return False
    return bool(
        str(current.get("attempt_id") or "") == str(expected.get("attempt_id") or "")
        and str(current.get("issuer_epoch") or "") == str(expected.get("issuer_epoch") or "")
        and int(current.get("generation") or -1) == int(expected.get("generation") or -2)
        and _normalized_event_ids(current.get("event_ids") or [])
        == _normalized_event_ids(expected.get("event_ids") or [])
    )


def finalize_turn_delivery_attempt(
    group_or_id: Group | str,
    *,
    actor_id: str,
    attempt: dict[str, Any],
    binding: Optional[dict[str, Any]] = None,
    now: Optional[float] = None,
    ttl_seconds: float = DEFAULT_TURN_GRANT_TTL_SECONDS,
) -> Optional[dict[str, Any]]:
    """Publish a grant only while the exact pending attempt still owns the actor."""

    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid or not isinstance(attempt, dict):
        return None
    path = _state_path(group, aid)
    if path is None:
        return None
    at = float(time.time() if now is None else now)
    if str(attempt.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH:
        return None
    with _actor_lock(group.group_id, aid):
        state = _load_state(group, aid)
        if str(state.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH:
            return None
        current_grant = state.get("current_grant")
        if (
            int(state.get("generation") or -1) == int(attempt.get("generation") or -2)
            and isinstance(current_grant, dict)
            and str(current_grant.get("attempt_id") or "") == str(attempt.get("attempt_id") or "")
            and _normalized_event_ids(current_grant.get("event_ids") or [])
            == _normalized_event_ids(attempt.get("event_ids") or [])
        ):
            return dict(current_grant)
        pending = state.get("pending_attempt")
        if int(state.get("generation") or -1) != int(attempt.get("generation") or -2):
            return None
        if not _attempt_matches(pending, attempt):
            return None
        ids = _normalized_event_ids(attempt.get("event_ids") or [])
        provenances = [load_event_turn_provenance(group, event_id) for event_id in ids]
        pure_local = bool(ids) and all(
            item is not None
            and item.origin == "local_user"
            and bool(item.fresh_local_request)
            and bool(str(item.local_request_id or "").strip())
            for item in provenances
        )
        merged_binding = {
            **_normalized_binding(pending.get("binding") if isinstance(pending, dict) else None),
            **_normalized_binding(binding),
        }
        generation = int(attempt.get("generation") or 0)
        grant: Optional[dict[str, Any]] = None
        if pure_local:
            expires_at = at + max(0.001, float(ttl_seconds))
            grant = {
                "v": 1,
                "issuer_epoch": _DAEMON_ISSUER_EPOCH,
                "group_id": group.group_id,
                "actor_id": aid,
                "event_ids": ids,
                "local_request_ids": [str(item.local_request_id) for item in provenances if item is not None],
                "attempt_id": str(attempt.get("attempt_id") or ""),
                "generation": generation,
                "binding": merged_binding,
                "issued_at": _utc_iso(at),
                "issued_at_epoch": at,
                "expires_at": _utc_iso(expires_at),
                "expires_at_epoch": expires_at,
            }
        atomic_write_json(
            path,
            {
                "v": 1,
                "issuer_epoch": _DAEMON_ISSUER_EPOCH,
                "group_id": group.group_id,
                "actor_id": aid,
                "generation": generation,
                "pending_attempt": None,
                "current_grant": grant,
                "invalidated_reason": "" if grant is not None else "delivery_not_fresh_local_user",
                "updated_at": _utc_iso(at),
            },
            indent=2,
        )
        return dict(grant) if grant is not None else None


def fail_turn_delivery_attempt(
    group_or_id: Group | str,
    *,
    actor_id: str,
    attempt: dict[str, Any],
    reason: str,
    now: Optional[float] = None,
) -> bool:
    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid or not isinstance(attempt, dict):
        return False
    path = _state_path(group, aid)
    if path is None:
        return False
    with _actor_lock(group.group_id, aid):
        state = _load_state(group, aid)
        if (
            str(attempt.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH
            or str(state.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH
        ):
            return False
        if int(state.get("generation") or -1) != int(attempt.get("generation") or -2):
            return False
        if not _attempt_matches(state.get("pending_attempt"), attempt):
            return False
        at = float(time.time() if now is None else now)
        atomic_write_json(
            path,
            {
                "v": 1,
                "issuer_epoch": _DAEMON_ISSUER_EPOCH,
                "group_id": group.group_id,
                "actor_id": aid,
                "generation": int(attempt.get("generation") or 0),
                "pending_attempt": None,
                "current_grant": None,
                "invalidated_reason": str(reason or "delivery_failed").strip() or "delivery_failed",
                "updated_at": _utc_iso(at),
            },
            indent=2,
        )
        return True


def terminalize_uncertain_delivery_attempt(
    group_or_id: Group | str,
    *,
    actor_id: str,
    attempt: dict[str, Any],
    reason: str,
    now: Optional[float] = None,
) -> bool:
    """Clear an exact attempt after transport acceptance has become uncertain."""

    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid or not isinstance(attempt, dict):
        return False
    path = _state_path(group, aid)
    if path is None or str(attempt.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH:
        return False
    with _actor_lock(group.group_id, aid):
        state = _load_state(group, aid)
        if (
            str(state.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH
            or int(state.get("generation") or -1) != int(attempt.get("generation") or -2)
        ):
            return False
        candidate = state.get("pending_attempt")
        if not _attempt_matches(candidate, attempt):
            candidate = state.get("current_grant")
        if not _attempt_matches(candidate, attempt):
            return False
        at = float(time.time() if now is None else now)
        atomic_write_json(
            path,
            {
                "v": 1,
                "issuer_epoch": _DAEMON_ISSUER_EPOCH,
                "group_id": group.group_id,
                "actor_id": aid,
                "generation": int(attempt.get("generation") or 0) + 1,
                "pending_attempt": None,
                "current_grant": None,
                "invalidated_reason": str(reason or "accepted_delivery_uncertain").strip()
                or "accepted_delivery_uncertain",
                "updated_at": _utc_iso(at),
            },
            indent=2,
        )
        return True


def turn_delivery_attempt_receipt(
    group_or_id: Group | str,
    *,
    actor_id: str,
    attempt: dict[str, Any],
) -> dict[str, Any]:
    """Read whether an attempt finalized without being superseded."""

    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid or not isinstance(attempt, dict):
        return {"finalized": False, "grant": None}
    with _actor_lock(group.group_id, aid):
        state = _load_state(group, aid)
        if (
            str(attempt.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH
            or str(state.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH
        ):
            return {"finalized": False, "grant": None}
        if int(state.get("generation") or -1) != int(attempt.get("generation") or -2):
            return {"finalized": False, "grant": None}
        if isinstance(state.get("pending_attempt"), dict):
            return {"finalized": False, "grant": None}
        grant = state.get("current_grant") if isinstance(state.get("current_grant"), dict) else None
        if grant is not None and str(grant.get("attempt_id") or "") != str(attempt.get("attempt_id") or ""):
            return {"finalized": False, "grant": None}
        finalized_without_grant = str(state.get("invalidated_reason") or "") == "delivery_not_fresh_local_user"
        return {
            "finalized": bool(grant is not None or finalized_without_grant),
            "grant": dict(grant) if grant is not None else None,
        }


def turn_delivery_completion_receipt(attempt: Any) -> Optional[dict[str, Any]]:
    if not isinstance(attempt, dict):
        return None
    issuer_epoch = str(attempt.get("issuer_epoch") or "").strip()
    group_id = str(attempt.get("group_id") or "").strip()
    actor_id = str(attempt.get("actor_id") or "").strip()
    attempt_id = str(attempt.get("attempt_id") or "").strip()
    generation = int(attempt.get("generation") or 0)
    event_ids = _normalized_event_ids(attempt.get("event_ids") or [])
    if (
        issuer_epoch != _DAEMON_ISSUER_EPOCH
        or not group_id
        or not actor_id
        or not attempt_id
        or generation <= 0
        or not event_ids
    ):
        return None
    return {
        "v": 1,
        "issuer_epoch": issuer_epoch,
        "group_id": group_id,
        "actor_id": actor_id,
        "attempt_id": attempt_id,
        "generation": generation,
        "event_ids": event_ids,
        "binding": _normalized_binding(attempt.get("binding")),
    }


def invalidate_turn_grant(
    group_or_id: Group | str,
    actor_id: str,
    *,
    reason: str,
    now: Optional[float] = None,
) -> dict[str, Any]:
    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid:
        return {}
    path = _state_path(group, aid)
    if path is None:
        return {}
    with _actor_lock(group.group_id, aid):
        previous = _load_state(group, aid)
        generation = max(0, int(previous.get("generation") or 0)) + 1
        at = float(time.time() if now is None else now)
        state = {
            "v": 1,
            "issuer_epoch": _DAEMON_ISSUER_EPOCH,
            "group_id": group.group_id,
            "actor_id": aid,
            "generation": generation,
            "pending_attempt": None,
            "current_grant": None,
            "invalidated_reason": str(reason or "invalidated").strip() or "invalidated",
            "updated_at": _utc_iso(at),
        }
        atomic_write_json(path, state, indent=2)
        return state


def finalize_turn_delivery_success(
    group: Group | str,
    *,
    actor_id: str,
    event_ids: Iterable[str],
    now: Optional[float] = None,
    ttl_seconds: float = DEFAULT_TURN_GRANT_TTL_SECONDS,
) -> Optional[dict[str, Any]]:
    """Issue a grant where no external transport action separates begin/finalize."""

    attempt = begin_turn_delivery_attempt(
        group,
        actor_id=actor_id,
        event_ids=event_ids,
        now=now,
    )
    if attempt is None:
        return None
    return finalize_turn_delivery_attempt(
        group,
        actor_id=actor_id,
        attempt=attempt,
        now=now,
        ttl_seconds=ttl_seconds,
    )


def invalidate_turn_grant_if_event_ids(
    group_or_id: Group | str,
    actor_id: str,
    *,
    event_ids: Iterable[str],
    reason: str,
    now: Optional[float] = None,
) -> bool:
    """Invalidate only the grant issued for the exact completed delivery."""

    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    ids = _normalized_event_ids(event_ids)
    if group is None or not aid or not ids:
        return False
    path = _state_path(group, aid)
    if path is None:
        return False
    with _actor_lock(group.group_id, aid):
        previous = _load_state(group, aid)
        candidate = previous.get("pending_attempt")
        if not isinstance(candidate, dict):
            candidate = previous.get("current_grant")
        candidate_ids = _normalized_event_ids(candidate.get("event_ids") or []) if isinstance(candidate, dict) else []
        if candidate_ids != ids:
            return False
        generation = max(0, int(previous.get("generation") or 0)) + 1
        at = float(time.time() if now is None else now)
        state = {
            "v": 1,
            "issuer_epoch": _DAEMON_ISSUER_EPOCH,
            "group_id": group.group_id,
            "actor_id": aid,
            "generation": generation,
            "pending_attempt": None,
            "current_grant": None,
            "invalidated_reason": str(reason or "completed").strip() or "completed",
            "updated_at": _utc_iso(at),
        }
        atomic_write_json(path, state, indent=2)
        return True


def invalidate_turn_grant_if_identity(
    group_or_id: Group | str,
    actor_id: str,
    *,
    attempt_id: str,
    generation: int,
    event_ids: Iterable[str],
    binding: Optional[dict[str, Any]] = None,
    exact_binding: bool = False,
    reason: str,
    now: Optional[float] = None,
) -> bool:
    """Terminalize only the exact pending/issued delivery generation."""

    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    expected_attempt_id = str(attempt_id or "").strip()
    ids = _normalized_event_ids(event_ids)
    expected_binding = _normalized_binding(binding)
    if group is None or not aid or not expected_attempt_id or generation <= 0:
        return False
    path = _state_path(group, aid)
    if path is None:
        return False
    with _actor_lock(group.group_id, aid):
        state = _load_state(group, aid)
        if str(state.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH:
            return False
        if int(state.get("generation") or -1) != int(generation):
            return False
        candidate = state.get("pending_attempt")
        if not isinstance(candidate, dict):
            candidate = state.get("current_grant")
        if not isinstance(candidate, dict):
            return False
        if str(candidate.get("attempt_id") or "") != expected_attempt_id:
            return False
        if int(candidate.get("generation") or -1) != int(generation):
            return False
        if _normalized_event_ids(candidate.get("event_ids") or []) != ids:
            return False
        candidate_binding = _normalized_binding(candidate.get("binding"))
        if exact_binding and candidate_binding != expected_binding:
            return False
        if not exact_binding and any(candidate_binding.get(key) != value for key, value in expected_binding.items()):
            return False
        at = float(time.time() if now is None else now)
        next_generation = int(generation) + 1
        atomic_write_json(
            path,
            {
                "v": 1,
                "issuer_epoch": _DAEMON_ISSUER_EPOCH,
                "group_id": group.group_id,
                "actor_id": aid,
                "generation": next_generation,
                "pending_attempt": None,
                "current_grant": None,
                "invalidated_reason": str(reason or "completed").strip() or "completed",
                "updated_at": _utc_iso(at),
            },
            indent=2,
        )
        return True


def invalidate_turn_grant_from_completion_receipt(
    group_or_id: Group | str,
    actor_id: str,
    *,
    completion_receipt: Any,
    reason: str,
    event_ids: Optional[Iterable[str]] = None,
    binding: Optional[dict[str, Any]] = None,
    now: Optional[float] = None,
) -> bool:
    """Terminalize a delivery only when its daemon receipt fully matches."""

    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid or not isinstance(completion_receipt, dict):
        return False
    try:
        generation = int(completion_receipt.get("generation") or 0)
    except Exception:
        return False
    receipt_ids = _normalized_event_ids(completion_receipt.get("event_ids") or [])
    receipt_binding = _normalized_binding(completion_receipt.get("binding"))
    expected_ids = _normalized_event_ids(event_ids) if event_ids is not None else receipt_ids
    expected_binding = _normalized_binding(binding) if binding is not None else receipt_binding
    if (
        int(completion_receipt.get("v") or 0) != 1
        or str(completion_receipt.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH
        or str(completion_receipt.get("group_id") or "").strip() != group.group_id
        or str(completion_receipt.get("actor_id") or "").strip() != aid
        or not receipt_ids
        or receipt_ids != expected_ids
        or receipt_binding != expected_binding
    ):
        return False
    return invalidate_turn_grant_if_identity(
        group,
        aid,
        attempt_id=str(completion_receipt.get("attempt_id") or "").strip(),
        generation=generation,
        event_ids=receipt_ids,
        binding=receipt_binding,
        exact_binding=True,
        reason=reason,
        now=now,
    )


def get_current_turn_grant(
    group_or_id: Group | str,
    actor_id: str,
    *,
    now: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    group = group_or_id if isinstance(group_or_id, Group) else load_group(str(group_or_id or "").strip())
    aid = str(actor_id or "").strip()
    if group is None or not aid:
        return None
    at = float(time.time() if now is None else now)
    with _actor_lock(group.group_id, aid):
        state = _load_state(group, aid)
        state = _abandon_stale_issuer_state(group, aid, state, now=at)
        grant = state.get("current_grant") if isinstance(state.get("current_grant"), dict) else None
        if grant is None:
            return None
        try:
            expires_at = float(grant.get("expires_at_epoch") or 0.0)
        except Exception:
            expires_at = 0.0
        if expires_at <= at:
            invalidate_turn_grant(group, aid, reason="ttl_expired", now=at)
            return None
        if (
            str(grant.get("group_id") or "") != group.group_id
            or str(grant.get("actor_id") or "") != aid
            or str(grant.get("issuer_epoch") or "") != _DAEMON_ISSUER_EPOCH
            or int(grant.get("generation") or -1) != int(state.get("generation") or -2)
        ):
            invalidate_turn_grant(group, aid, reason="invalid_grant_state", now=at)
            return None
        return dict(grant)


def invalidate_group_turn_grants(group: Group, actor_ids: Iterable[str], *, reason: str) -> None:
    for actor_id in actor_ids:
        aid = str(actor_id or "").strip()
        if aid:
            invalidate_turn_grant(group, aid, reason=reason)
