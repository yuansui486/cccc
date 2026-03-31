from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...contracts.v1.notify import SystemNotifyData
from ...daemon.messaging.delivery import emit_system_notify
from ...kernel.group import get_group_state, load_group
from ...kernel.inbox import iter_events
from ...kernel.pet_actor import PET_ACTOR_ID, get_pet_actor, is_desktop_pet_enabled
from ...paths import ensure_home
from ...util.fs import atomic_write_json, read_json
from ...util.time import parse_utc_iso, utc_now_iso

LOGGER = logging.getLogger(__name__)

_SCHEMA = 1
_STATE_FILENAME = "pet_profile_state.json"
PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE = 10
PET_PROFILE_REFRESH_DELTA = 20
PET_PROFILE_REFRESH_COOLDOWN_SECONDS = 6 * 60 * 60
PET_PROFILE_SAMPLE_WINDOW = 50
PET_PROFILE_REFRESH_PACKET_SIZE = 16
PET_PROFILE_CLIP_CHARS = 140
PET_PROFILE_MIN_CLEAN_CHARS = 12
PET_PROFILE_MAX_RAW_CHARS = 2400
PET_PROFILE_MAX_RAW_LINES = 10

_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_WHITESPACE_RE = re.compile(r"\s+")
_PATHISH_RE = re.compile(r"(?:[A-Za-z]:\\|/)[^\s]{4,}")


def _state_path(group_id: str) -> Path:
    gid = str(group_id or "").strip()
    if not gid:
        return ensure_home() / "groups" / "_invalid" / "state" / _STATE_FILENAME
    return ensure_home() / "groups" / gid / "state" / _STATE_FILENAME


def _default_state(group_id: str) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    return {
        "schema": _SCHEMA,
        "group_id": gid,
        "eligible_total": 0,
        "samples": [],
        "last_eligible_event_id": "",
        "last_eligible_ts": "",
        "last_requested_eligible_total": 0,
        "last_requested_at": "",
        "last_refresh_eligible_total": 0,
        "last_refresh_at": "",
        "last_applied_user_model_hash": "",
    }


def _normalize_sample_entry(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    event_id = str(raw.get("event_id") or "").strip()
    ts = str(raw.get("ts") or "").strip()
    text = str(raw.get("text") or "").strip()
    if not event_id or not text:
        return None
    return {
        "event_id": event_id,
        "ts": ts,
        "text": text,
    }


def _normalize_state_payload(group_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base = _default_state(group_id)
    base["eligible_total"] = max(0, int(payload.get("eligible_total") or 0))
    base["last_eligible_event_id"] = str(payload.get("last_eligible_event_id") or "").strip()
    base["last_eligible_ts"] = str(payload.get("last_eligible_ts") or "").strip()
    base["last_requested_eligible_total"] = max(0, int(payload.get("last_requested_eligible_total") or 0))
    base["last_requested_at"] = str(payload.get("last_requested_at") or "").strip()
    base["last_refresh_eligible_total"] = max(0, int(payload.get("last_refresh_eligible_total") or 0))
    base["last_refresh_at"] = str(payload.get("last_refresh_at") or "").strip()
    base["last_applied_user_model_hash"] = str(payload.get("last_applied_user_model_hash") or "").strip()
    samples_raw = payload.get("samples") if isinstance(payload.get("samples"), list) else []
    samples: List[Dict[str, str]] = []
    for item in samples_raw[-PET_PROFILE_SAMPLE_WINDOW:]:
        normalized = _normalize_sample_entry(item)
        if normalized is not None:
            samples.append(normalized)
    base["samples"] = samples
    return base


def _load_state(group_id: str) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    payload = read_json(_state_path(gid))
    if isinstance(payload, dict) and int(payload.get("schema") or 0) == _SCHEMA:
        return _normalize_state_payload(gid, payload)
    return _default_state(gid)


def _save_state(group_id: str, state: Dict[str, Any]) -> None:
    gid = str(group_id or "").strip()
    path = _state_path(gid)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_state_payload(gid, state)
    atomic_write_json(path, normalized, indent=2)


def _clean_user_message_text(text: str) -> str:
    value = str(text or "")
    value = _CODE_FENCE_RE.sub(" ", value)
    value = re.sub(r"(?m)^\s{4,}.*$", " ", value)
    value = re.sub(r"(?m)^\s*>\s?.*$", " ", value)
    value = _MARKDOWN_LINK_RE.sub(r"\1", value)
    value = _URL_RE.sub(" ", value)
    value = _INLINE_CODE_RE.sub(" ", value)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


def _looks_like_paste_noise(raw_text: str, clean_text: str) -> bool:
    raw = str(raw_text or "")
    clean = str(clean_text or "")
    if not clean:
        return True
    if len(raw) > PET_PROFILE_MAX_RAW_CHARS:
        return True
    if raw.count("\n") >= PET_PROFILE_MAX_RAW_LINES:
        return True
    url_hits = len(_URL_RE.findall(raw))
    path_hits = len(_PATHISH_RE.findall(raw))
    if url_hits >= 3:
        return True
    if path_hits >= 5:
        return True
    non_word_count = sum(1 for ch in raw if not ch.isalnum() and not ch.isspace())
    if raw and (non_word_count / max(1, len(raw))) > 0.30 and len(clean) < 80:
        return True
    if len(clean) < PET_PROFILE_MIN_CLEAN_CHARS:
        return True
    return False


def _prepare_sample_text(text: str) -> str:
    clean = _clean_user_message_text(text)
    if _looks_like_paste_noise(text, clean):
        return ""
    return clean[:PET_PROFILE_CLIP_CHARS].strip()


def _sample_entry_from_message(*, event_id: str, ts: str, text: str) -> Optional[Dict[str, str]]:
    sample_text = _prepare_sample_text(text)
    if not sample_text:
        return None
    return {
        "event_id": str(event_id or "").strip(),
        "ts": str(ts or "").strip(),
        "text": sample_text,
    }


def _bootstrap_state_from_ledger(group_id: str) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    group = load_group(gid)
    state = _default_state(gid)
    if group is None:
        return state
    samples: List[Dict[str, str]] = []
    eligible_total = 0
    last_event_id = ""
    last_ts = ""
    try:
        for ev in iter_events(group.ledger_path):
            if not isinstance(ev, dict) or str(ev.get("kind") or "").strip() != "chat.message":
                continue
            if str(ev.get("by") or "").strip() != "user":
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            text = str(data.get("text") or "")
            entry = _sample_entry_from_message(
                event_id=str(ev.get("id") or "").strip(),
                ts=str(ev.get("ts") or "").strip(),
                text=text,
            )
            if entry is None:
                continue
            eligible_total += 1
            last_event_id = entry["event_id"]
            last_ts = entry["ts"]
            samples.append(entry)
            if len(samples) > PET_PROFILE_SAMPLE_WINDOW:
                samples = samples[-PET_PROFILE_SAMPLE_WINDOW:]
    except Exception:
        LOGGER.exception("pet_profile_bootstrap_failed group_id=%s", gid)
    state["eligible_total"] = eligible_total
    state["samples"] = samples
    state["last_eligible_event_id"] = last_event_id
    state["last_eligible_ts"] = last_ts
    return state


def _load_or_bootstrap_state(group_id: str) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    path = _state_path(gid)
    if path.exists():
        return _load_state(gid)
    state = _bootstrap_state_from_ledger(gid)
    _save_state(gid, state)
    return state


def _append_sample(state: Dict[str, Any], entry: Dict[str, str]) -> None:
    samples = state.get("samples") if isinstance(state.get("samples"), list) else []
    normalized = [_normalize_sample_entry(item) for item in samples]
    compact = [item for item in normalized if item is not None]
    compact.append(entry)
    state["samples"] = compact[-PET_PROFILE_SAMPLE_WINDOW:]


def _build_refresh_packet(samples: List[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized = [_normalize_sample_entry(item) for item in samples]
    compact = [item for item in normalized if item is not None]
    if len(compact) <= PET_PROFILE_REFRESH_PACKET_SIZE:
        return compact

    latest_keep = min(8, PET_PROFILE_REFRESH_PACKET_SIZE // 2)
    older_target = max(0, PET_PROFILE_REFRESH_PACKET_SIZE - latest_keep)
    older = compact[:-latest_keep]
    latest = compact[-latest_keep:]
    if older_target <= 0 or not older:
        return compact[-PET_PROFILE_REFRESH_PACKET_SIZE:]
    if len(older) <= older_target:
        return older + latest

    selected: List[Dict[str, str]] = []
    if older_target == 1:
        selected.append(older[0])
    else:
        span = len(older) - 1
        for idx in range(older_target):
            pos = round(idx * span / max(1, older_target - 1))
            selected.append(older[pos])
    return selected + latest


def _iso_age_seconds(value: str) -> Optional[float]:
    dt = parse_utc_iso(str(value or "").strip())
    if dt is None:
        return None
    try:
        return max(0.0, (parse_utc_iso(utc_now_iso()) - dt).total_seconds())  # type: ignore[operator]
    except Exception:
        return None


def _refresh_anchor_total(state: Dict[str, Any]) -> int:
    return max(
        int(state.get("last_refresh_eligible_total") or 0),
        int(state.get("last_requested_eligible_total") or 0),
    )


def _refresh_due_reason(state: Dict[str, Any]) -> str:
    eligible_total = int(state.get("eligible_total") or 0)
    if eligible_total <= 0:
        return ""
    if int(state.get("last_refresh_eligible_total") or 0) <= 0 and int(state.get("last_requested_eligible_total") or 0) <= 0:
        return "bootstrap" if eligible_total >= PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE else ""
    anchor = _refresh_anchor_total(state)
    return "eligible_delta" if (eligible_total - anchor) >= PET_PROFILE_REFRESH_DELTA else ""


def _cooldown_ready(state: Dict[str, Any]) -> bool:
    last_requested_at = _iso_age_seconds(str(state.get("last_requested_at") or ""))
    last_refresh_at = _iso_age_seconds(str(state.get("last_refresh_at") or ""))
    candidates = [value for value in (last_requested_at, last_refresh_at) if value is not None]
    if not candidates:
        return True
    return min(candidates) >= float(PET_PROFILE_REFRESH_COOLDOWN_SECONDS)


def _can_refresh_now(group_id: str) -> bool:
    group = load_group(group_id)
    if group is None:
        return False
    if not is_desktop_pet_enabled(group):
        return False
    if get_group_state(group) not in {"active", "idle"}:
        return False
    actor = get_pet_actor(group)
    if not isinstance(actor, dict):
        return False
    if not bool(actor.get("enabled", True)):
        return False
    return True


def _emit_profile_refresh(group_id: str, *, state: Dict[str, Any], source_event_id: str, reason: str) -> bool:
    group = load_group(group_id)
    if group is None:
        return False
    packet = _build_refresh_packet(state.get("samples") if isinstance(state.get("samples"), list) else [])
    packet_hash = hashlib.sha1(
        "\n".join(f"{item['event_id']}:{item['text']}" for item in packet).encode("utf-8", errors="replace")
    ).hexdigest()
    notify = SystemNotifyData(
        kind="info",
        priority="normal",
        title="Pet profile refresh requested",
        message="Refresh your user_model from the prepared sample packet in this unread notify context. Do not touch pet decisions.",
        target_actor_id=PET_ACTOR_ID,
        requires_ack=False,
        related_event_id=source_event_id or None,
        context={
            "kind": "pet_profile_refresh",
            "reason": reason,
            "eligible_total": int(state.get("eligible_total") or 0),
            "last_refresh_eligible_total": int(state.get("last_refresh_eligible_total") or 0),
            "sample_window_size": len(state.get("samples") if isinstance(state.get("samples"), list) else []),
            "sample_packet_size": len(packet),
            "sample_packet_hash": packet_hash,
            "sample_packet": packet,
        },
    )
    emit_system_notify(group, by="system", notify=notify)
    state["last_requested_eligible_total"] = int(state.get("eligible_total") or 0)
    state["last_requested_at"] = utc_now_iso()
    _save_state(group_id, state)
    return True


def maybe_request_pet_profile_refresh(
    group_id: str,
    *,
    source_event_id: str = "",
    reason: str = "",
) -> bool:
    gid = str(group_id or "").strip()
    if not gid:
        return False
    state = _load_or_bootstrap_state(gid)
    due_reason = _refresh_due_reason(state)
    if not due_reason:
        return False
    if not _cooldown_ready(state):
        return False
    if not _can_refresh_now(gid):
        _save_state(gid, state)
        return False
    resolved_reason = str(reason or "").strip() or due_reason
    return _emit_profile_refresh(gid, state=state, source_event_id=source_event_id, reason=resolved_reason)


def record_user_chat_message(
    group_id: str,
    *,
    event_id: str,
    ts: str,
    text: str,
) -> Dict[str, Any]:
    gid = str(group_id or "").strip()
    eid = str(event_id or "").strip()
    if not gid or not eid:
        return {"eligible": False, "requested": False}
    state = _load_or_bootstrap_state(gid)
    if str(state.get("last_eligible_event_id") or "").strip() != eid:
        entry = _sample_entry_from_message(event_id=eid, ts=ts, text=text)
        if entry is None:
            return {"eligible": False, "requested": False}
        state["eligible_total"] = int(state.get("eligible_total") or 0) + 1
        state["last_eligible_event_id"] = entry["event_id"]
        state["last_eligible_ts"] = entry["ts"]
        _append_sample(state, entry)
        _save_state(gid, state)
    requested = maybe_request_pet_profile_refresh(gid, source_event_id=eid, reason="new_user_messages")
    next_state = _load_state(gid)
    return {
        "eligible": True,
        "requested": requested,
        "eligible_total": int(next_state.get("eligible_total") or 0),
    }


def mark_pet_profile_refresh_applied(
    group_id: str,
    *,
    actor_id: str,
    user_model: str,
) -> None:
    gid = str(group_id or "").strip()
    aid = str(actor_id or "").strip()
    value = str(user_model or "").strip()
    if not gid or aid != PET_ACTOR_ID or not value:
        return
    state = _load_or_bootstrap_state(gid)
    requested_total = int(state.get("last_requested_eligible_total") or 0)
    if requested_total > 0:
        state["last_refresh_eligible_total"] = max(int(state.get("last_refresh_eligible_total") or 0), requested_total)
    elif int(state.get("eligible_total") or 0) > 0:
        state["last_refresh_eligible_total"] = int(state.get("eligible_total") or 0)
    state["last_refresh_at"] = utc_now_iso()
    state["last_applied_user_model_hash"] = hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()
    _save_state(gid, state)


def recover_due_pet_profile_refreshes() -> None:
    groups_root = ensure_home() / "groups"
    try:
        for group_dir in groups_root.iterdir():
            if not group_dir.is_dir():
                continue
            gid = str(group_dir.name or "").strip()
            if not gid or gid.startswith("_"):
                continue
            try:
                maybe_request_pet_profile_refresh(gid, reason="startup_recheck")
            except Exception as exc:
                LOGGER.warning("recover_due_pet_profile_refresh_failed group_id=%s err=%s", gid, exc)
    except FileNotFoundError:
        return
