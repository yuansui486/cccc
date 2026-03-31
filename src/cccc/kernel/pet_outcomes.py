from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from ..util.time import parse_utc_iso, utc_now_iso
from .inbox import iter_events_reverse
from .ledger import append_event
if TYPE_CHECKING:
    from .group import Group


PET_DECISION_OUTCOME_KIND = "pet.decision.outcome"
PET_USER_DECISION_OUTCOMES = {"executed", "dismissed"}
PET_INTERNAL_DECISION_OUTCOMES = {"expired"}
PET_DECISION_OUTCOMES = PET_USER_DECISION_OUTCOMES | PET_INTERNAL_DECISION_OUTCOMES
_EXECUTED_SUPPRESS_MS = 30 * 60 * 1000
_DISMISSED_SUPPRESS_MS = 60 * 1000


def normalize_pet_outcome(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in PET_DECISION_OUTCOMES else ""


def _default_outcome_cooldown_ms(outcome: str) -> int:
    normalized = normalize_pet_outcome(outcome)
    if normalized == "executed":
        return _EXECUTED_SUPPRESS_MS
    if normalized == "dismissed":
        return _DISMISSED_SUPPRESS_MS
    return 0


def _latest_outcomes_by_fingerprint(group: Group, *, limit: int = 256) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for event in iter_events_reverse(group.ledger_path):
        if str(event.get("kind") or "").strip() != PET_DECISION_OUTCOME_KIND:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        fingerprint = str(data.get("fingerprint") or "").strip()
        if not fingerprint or fingerprint in out:
            continue
        out[fingerprint] = event
        if len(out) >= max(1, int(limit or 256)):
            break
    return out


def load_suppressed_pet_fingerprints(group: Group) -> Dict[str, Dict[str, Any]]:
    now_dt = parse_utc_iso(utc_now_iso())
    if now_dt is None:
        return {}
    suppressed: Dict[str, Dict[str, Any]] = {}
    for fingerprint, event in _latest_outcomes_by_fingerprint(group).items():
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        outcome = normalize_pet_outcome(data.get("outcome"))
        if outcome not in PET_USER_DECISION_OUTCOMES:
            continue
        event_dt = parse_utc_iso(str(event.get("ts") or "").strip())
        if event_dt is None:
            continue
        cooldown_ms = int(data.get("cooldown_ms") or 0)
        if cooldown_ms <= 0:
            cooldown_ms = _default_outcome_cooldown_ms(outcome)
        if cooldown_ms <= 0:
            continue
        if event_dt + timedelta(milliseconds=cooldown_ms) <= now_dt:
            continue
        suppressed[fingerprint] = {
            "outcome": outcome,
            "cooldown_ms": cooldown_ms,
            "ts": str(event.get("ts") or "").strip(),
        }
    return suppressed


def append_pet_decision_outcome(
    group: Group,
    *,
    by: str,
    fingerprint: str,
    outcome: str,
    decision_id: str = "",
    action_type: str = "",
    cooldown_ms: int = 0,
    source_event_id: str = "",
) -> Dict[str, Any]:
    normalized_fingerprint = str(fingerprint or "").strip()
    normalized_outcome = normalize_pet_outcome(outcome)
    if not normalized_fingerprint:
        raise ValueError("missing fingerprint")
    if not normalized_outcome:
        raise ValueError(f"invalid pet decision outcome: {outcome}")
    payload: Dict[str, Any] = {
        "fingerprint": normalized_fingerprint,
        "outcome": normalized_outcome,
    }
    if str(decision_id or "").strip():
        payload["decision_id"] = str(decision_id or "").strip()
    if str(action_type or "").strip():
        payload["action_type"] = str(action_type or "").strip()
    if int(cooldown_ms or 0) > 0:
        payload["cooldown_ms"] = int(cooldown_ms)
    if str(source_event_id or "").strip():
        payload["source_event_id"] = str(source_event_id or "").strip()
    return append_event(
        group.ledger_path,
        kind=PET_DECISION_OUTCOME_KIND,
        group_id=group.group_id,
        scope_key="",
        by=str(by or "user").strip() or "user",
        data=payload,
    )


def append_expired_pet_decision_outcomes(
    group: Group,
    *,
    by: str,
    previous_decisions: Iterable[Dict[str, Any]],
    current_decisions: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    current_fingerprints = {
        str(item.get("fingerprint") or "").strip()
        for item in current_decisions
        if isinstance(item, dict) and str(item.get("fingerprint") or "").strip()
    }
    events: List[Dict[str, Any]] = []
    for item in previous_decisions:
        if not isinstance(item, dict):
            continue
        fingerprint = str(item.get("fingerprint") or "").strip()
        if not fingerprint or fingerprint in current_fingerprints:
            continue
        events.append(
            append_pet_decision_outcome(
                group,
                by=by,
                fingerprint=fingerprint,
                outcome="expired",
                decision_id=str(item.get("id") or "").strip(),
                action_type=str(((item.get("action") or {}) if isinstance(item.get("action"), dict) else {}).get("type") or "").strip(),
                source_event_id=str(((item.get("source") or {}) if isinstance(item.get("source"), dict) else {}).get("event_id") or "").strip(),
            )
        )
    return events
