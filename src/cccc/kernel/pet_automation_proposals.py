from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from ..contracts.v1 import AutomationRuleSet
from ..util.time import parse_utc_iso, utc_now_iso
from .group import Group


def _future_iso(*, minutes: int) -> str:
    base = parse_utc_iso(utc_now_iso())
    if base is None:
        return utc_now_iso()
    return (base + timedelta(minutes=max(1, int(minutes or 0)))).isoformat().replace("+00:00", "Z")


def _find_rule_id(ruleset: AutomationRuleSet, rule_id: str) -> Dict[str, Any] | None:
    wanted = str(rule_id or "").strip()
    if not wanted:
        return None
    for rule in list(ruleset.rules or []):
        rid = str(getattr(rule, "id", "") or "").strip()
        if rid == wanted:
            return rule.model_dump(exclude_none=True)
    return None


def _notify_rule(
    *,
    rule_id: str,
    title: str,
    message: str,
    fire_at: str,
    to: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "id": rule_id,
        "enabled": True,
        "scope": "group",
        "to": list(to or ["@foreman"]),
        "trigger": {
            "kind": "at",
            "at": fire_at,
        },
        "action": {
            "kind": "notify",
            "title": title,
            "message": message,
            "priority": "normal",
            "requires_ack": False,
        },
    }


def _proposal_for_rule(
    *,
    group_id: str,
    title: str,
    summary: str,
    rule: Dict[str, Any],
    existing_rule: Dict[str, Any] | None,
) -> Dict[str, Any]:
    action_type = "update_rule" if isinstance(existing_rule, dict) else "create_rule"
    return {
        "priority": 70,
        "summary": summary,
        "action": {
            "type": "automation_proposal",
            "group_id": group_id,
            "title": title,
            "summary": summary,
            "actions": [
                {
                    "type": action_type,
                    "rule": rule,
                }
            ],
        },
    }


def build_pet_automation_proposal_candidates(
    group: Group,
    *,
    signal_payload: Dict[str, Any],
    ruleset: AutomationRuleSet,
) -> List[Dict[str, Any]]:
    proposal_ready = signal_payload.get("proposal_ready") if isinstance(signal_payload.get("proposal_ready"), dict) else {}
    if not bool(proposal_ready.get("ready")):
        return []

    focus = str(proposal_ready.get("focus") or "none").strip().lower()
    group_id = str(group.group_id or "").strip()
    candidates: List[Dict[str, Any]] = []

    if focus == "reply_pressure":
        rule_id = "pet-reply-followup-once"
        fire_at = _future_iso(minutes=15)
        rule = _notify_rule(
            rule_id=rule_id,
            title="Reply follow-up check",
            message="Check the overdue reply loop and close the waiting thread if still pending.",
            fire_at=fire_at,
        )
        candidates.append(
            _proposal_for_rule(
                group_id=group_id,
                title="One-shot reply follow-up",
                summary="Create a one-shot reminder rule to recheck whether the overdue reply loop has closed.",
                rule=rule,
                existing_rule=_find_rule_id(ruleset, rule_id),
            )
        )
    elif focus == "waiting_user":
        rule_id = "pet-user-dependency-followup-once"
        fire_at = _future_iso(minutes=20)
        rule = _notify_rule(
            rule_id=rule_id,
            title="User dependency follow-up",
            message="Check whether the user dependency has been resolved or needs a foreman follow-up.",
            fire_at=fire_at,
        )
        candidates.append(
            _proposal_for_rule(
                group_id=group_id,
                title="One-shot user dependency follow-up",
                summary="Create a one-shot reminder rule to recheck whether the waiting_user task has moved.",
                rule=rule,
                existing_rule=_find_rule_id(ruleset, rule_id),
            )
        )
    elif focus == "handoff":
        rule_id = "pet-handoff-followup-once"
        fire_at = _future_iso(minutes=20)
        rule = _notify_rule(
            rule_id=rule_id,
            title="Handoff follow-up",
            message="Check whether the pending handoff has an owner and a next action.",
            fire_at=fire_at,
        )
        candidates.append(
            _proposal_for_rule(
                group_id=group_id,
                title="One-shot handoff follow-up",
                summary="Create a one-shot reminder rule to recheck whether the handoff has been accepted.",
                rule=rule,
                existing_rule=_find_rule_id(ruleset, rule_id),
            )
        )
    elif focus == "blocked":
        rule_id = "pet-blocked-work-followup-once"
        fire_at = _future_iso(minutes=20)
        rule = _notify_rule(
            rule_id=rule_id,
            title="Blocked work follow-up",
            message="Check whether the blocked path has been unblocked or needs direct coordination.",
            fire_at=fire_at,
        )
        candidates.append(
            _proposal_for_rule(
                group_id=group_id,
                title="One-shot blocked-work follow-up",
                summary="Create a one-shot reminder rule to recheck whether the blocked work is still stuck.",
                rule=rule,
                existing_rule=_find_rule_id(ruleset, rule_id),
            )
        )

    candidates.sort(key=lambda item: -int(item.get("priority") or 0))
    return candidates[:1]


def build_pet_automation_proposal_summary_lines(
    group: Group,
    *,
    signal_payload: Dict[str, Any],
    ruleset: AutomationRuleSet,
    limit: int = 1,
) -> List[str]:
    lines: List[str] = []
    for item in build_pet_automation_proposal_candidates(group, signal_payload=signal_payload, ruleset=ruleset)[:limit]:
        summary = str(item.get("summary") or "").strip()
        if summary:
            lines.append(summary)
    return lines
