from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from .pet_outcomes import load_suppressed_pet_fingerprints
from ..util.fs import atomic_write_json, read_json

if TYPE_CHECKING:
    from .group import Group

_SCHEMA = 1


def pet_decisions_path(group: Group) -> Path:
    return group.path / "state" / "pet_decisions.json"


_FOREMAN_ACTION_KEYWORDS = (
    "prioritize",
    "please",
    "follow up",
    "handle",
    "close",
    "move",
    "confirm",
    "restart",
    "apply",
    "check",
    "decide",
    "sync",
    "contact",
    "reply",
    "ask",
    "review",
)
_ACTION_PREFIX_RE = re.compile(r"^(please|prioritize|follow up|check|review|ask|decide|restart|apply)\b", re.IGNORECASE)
_STRUCTURED_TOKEN_RE = re.compile(r"\b[a-z0-9]+(?:[_/-][a-z0-9]+)+\b", re.IGNORECASE)
_LONG_ASCII_TOKEN_RE = re.compile(r"\b[a-z]{5,}\b", re.IGNORECASE)


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _is_foreman_target(raw: Any) -> bool:
    if not isinstance(raw, list):
        return False
    targets = [str(item or "").strip().lower() for item in raw]
    return "@foreman" in targets or "foreman" in targets


def _split_message_clauses(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    parts = re.split(r"[。！？；\n]+", normalized)
    clauses: list[str] = []
    for part in parts:
        for chunk in re.split(r"[，,]+", part):
            clause = _normalize_text(chunk)
            if clause:
                clauses.append(clause)
    return clauses


def _structured_noise_count(text: str) -> int:
    normalized = _normalize_text(text)
    if not normalized:
        return 0
    structured = len(_STRUCTURED_TOKEN_RE.findall(normalized))
    structured += normalized.count("/")
    structured += normalized.count("=")
    structured += normalized.count(":")
    ascii_tokens = _LONG_ASCII_TOKEN_RE.findall(normalized)
    if len(ascii_tokens) >= 3:
        structured += len(ascii_tokens) - 2
    return structured


def _score_foreman_clause(clause: str) -> int:
    text = _normalize_text(clause)
    if not text:
        return -999
    score = 0
    for keyword in _FOREMAN_ACTION_KEYWORDS:
        if keyword in text:
            score += 4
    if _ACTION_PREFIX_RE.search(text):
        score += 4
    if "?" in text or "？" in text:
        score += 2
    if len(text) <= 28:
        score += 1
    elif len(text) >= 44:
        score -= 2
    structured_noise = _structured_noise_count(text)
    score -= min(structured_noise * 2, 8)
    if text.count("，") + text.count(",") >= 2 and score < 4:
        score -= 2
    return score


def _compact_foreman_message(text: str) -> str:
    raw = _normalize_text(text)
    clauses = _split_message_clauses(raw)
    if len(clauses) <= 1:
        return raw
    best_index = 0
    best_score = -999
    for idx, clause in enumerate(clauses):
        score = _score_foreman_clause(clause)
        if score > best_score:
            best_score = score
            best_index = idx
    picked: list[str] = []
    first = clauses[best_index]
    if first:
        picked.append(first)
    if best_index + 1 < len(clauses):
        second = clauses[best_index + 1]
        if _score_foreman_clause(second) >= 0 and len(f"{first}，{second}") <= 40:
            picked.append(second)
    return _normalize_text("，".join(picked) or raw)


def _normalize_draft_message_text(raw_text: Any, *, raw_to: Any) -> str:
    text = _normalize_text(raw_text)
    if not text:
        return ""
    if _is_foreman_target(raw_to):
        return _compact_foreman_message(text)
    return text


def _normalize_task_proposal_summary(
    summary: Any,
    *,
    fingerprint: str,
    source: Dict[str, Any],
) -> str:
    return _normalize_text(summary)


def _normalize_task_proposal_text(
    text: Any,
    *,
    fingerprint: str,
    source: Dict[str, Any],
) -> str:
    return _normalize_text(text)


def _normalize_action(raw: Any, *, fingerprint: str = "", source: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    source_payload = source if isinstance(source, dict) else {}
    action_type = str(raw.get("type") or "").strip()
    out: Dict[str, Any] = {"type": action_type}
    if action_type == "restart_actor":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        out["actor_id"] = str(raw.get("actor_id") or "").strip()
        return out
    if action_type == "draft_message":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        normalized_to = [str(item or "").strip() for item in raw.get("to") if str(item or "").strip()] if isinstance(raw.get("to"), list) else []
        out["text"] = _normalize_draft_message_text(raw.get("text"), raw_to=normalized_to)
        if isinstance(raw.get("to"), list):
            out["to"] = normalized_to
        reply_to = str(raw.get("reply_to") or "").strip()
        if reply_to:
            out["reply_to"] = reply_to
        return out
    if action_type == "task_proposal":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        out["operation"] = str(raw.get("operation") or "").strip().lower()
        task_id = str(raw.get("task_id") or "").strip()
        if task_id:
            out["task_id"] = task_id
        title = str(raw.get("title") or "").strip()
        if title:
            out["title"] = title
        status = str(raw.get("status") or "").strip().lower()
        if status:
            out["status"] = status
        assignee = str(raw.get("assignee") or "").strip()
        if assignee:
            out["assignee"] = assignee
        text = _normalize_task_proposal_text(
            raw.get("text"),
            fingerprint=fingerprint,
            source=source_payload,
        )
        if text:
            out["text"] = text
        return out
    if action_type == "automation_proposal":
        out["group_id"] = str(raw.get("group_id") or "").strip()
        title = str(raw.get("title") or "").strip()
        if title:
            out["title"] = title
        summary = str(raw.get("summary") or "").strip()
        if summary:
            out["summary"] = summary
        actions_raw = raw.get("actions")
        if isinstance(actions_raw, list):
            normalized_actions: list[dict[str, Any]] = []
            for item in actions_raw:
                if not isinstance(item, dict):
                    continue
                action_item: Dict[str, Any] = {}
                action_kind = str(item.get("type") or "").strip()
                if not action_kind:
                    continue
                action_item["type"] = action_kind
                for key in ("rule_id", "enabled"):
                    if key in item:
                        action_item[key] = item.get(key)
                if isinstance(item.get("rule"), dict):
                    action_item["rule"] = dict(item.get("rule") or {})
                if isinstance(item.get("ruleset"), dict):
                    action_item["ruleset"] = dict(item.get("ruleset") or {})
                normalized_actions.append(action_item)
            if normalized_actions:
                out["actions"] = normalized_actions
        return out
    return {}


def _normalize_source(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ("event_id", "task_id", "actor_id", "actor_role", "error_reason", "suggestion_kind"):
        value = str(raw.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _normalize_decision(raw: Any) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    decision_id = str(raw.get("id") or "").strip()
    fingerprint = str(raw.get("fingerprint") or "").strip()
    kind = str(raw.get("kind") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    source = _normalize_source(raw.get("source"))
    action = _normalize_action(raw.get("action"), fingerprint=fingerprint, source=source)
    action_type = str(action.get("type") or "").strip()
    if not decision_id or not fingerprint or not kind or not summary or not action_type:
        return None
    if action_type == "draft_message":
        if not str(action.get("group_id") or "").strip() or not str(action.get("text") or "").strip():
            return None
    elif action_type == "restart_actor":
        if not str(action.get("group_id") or "").strip() or not str(action.get("actor_id") or "").strip():
            return None
    elif action_type == "task_proposal":
        if not str(action.get("group_id") or "").strip():
            return None
    elif action_type == "automation_proposal":
        if not str(action.get("group_id") or "").strip() or not isinstance(action.get("actions"), list) or len(action.get("actions") or []) == 0:
            return None
    if action_type == "task_proposal":
        summary = _normalize_task_proposal_summary(summary, fingerprint=fingerprint, source=source)
    out: Dict[str, Any] = {
        "id": decision_id,
        "kind": kind,
        "priority": int(raw.get("priority") or 0),
        "summary": summary,
        "agent": str(raw.get("agent") or "").strip(),
        "fingerprint": fingerprint,
        "action": action,
        "source": source,
        "updated_at": str(raw.get("updated_at") or "").strip(),
    }
    if bool(raw.get("ephemeral")):
        out["ephemeral"] = True
    return out


def load_pet_decisions(group: Group) -> List[Dict[str, Any]]:
    raw = read_json(pet_decisions_path(group))
    if not isinstance(raw, dict):
        return []
    items = raw.get("decisions")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items:
        normalized = _normalize_decision(item)
        if normalized is not None:
            out.append(normalized)
    return out


def _decision_signature(decision: Dict[str, Any]) -> str:
    source = decision.get("source") if isinstance(decision.get("source"), dict) else {}
    action = decision.get("action") if isinstance(decision.get("action"), dict) else {}
    payload = {
        "kind": str(decision.get("kind") or "").strip(),
        "summary": str(decision.get("summary") or "").strip(),
        "agent": str(decision.get("agent") or "").strip(),
        "source": source,
        "action": action,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _assign_unique_fingerprints(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen_signatures: set[tuple[str, str]] = set()
    seen_fingerprints: set[str] = set()
    for decision in decisions:
        fingerprint = str(decision.get("fingerprint") or "").strip()
        if not fingerprint:
            continue
        signature = _decision_signature(decision)
        signature_key = (fingerprint, signature)
        if signature_key in seen_signatures:
            continue
        candidate = fingerprint
        if candidate in seen_fingerprints:
            digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
            candidate = f"{fingerprint}:{digest}"
            suffix = 1
            while candidate in seen_fingerprints:
                candidate = f"{fingerprint}:{digest}:{suffix}"
                suffix += 1
        normalized = dict(decision)
        normalized["fingerprint"] = candidate
        out.append(normalized)
        seen_signatures.add(signature_key)
        seen_fingerprints.add(candidate)
    return out


def replace_pet_decisions(group: Group, *, decisions: List[Dict[str, Any]], actor_id: str) -> List[Dict[str, Any]]:
    suppressed = load_suppressed_pet_fingerprints(group)
    normalized: List[Dict[str, Any]] = []
    for item in decisions:
        normalized_item = _normalize_decision(item)
        if normalized_item is not None:
            fingerprint = str(normalized_item.get("fingerprint") or "").strip()
            if fingerprint and fingerprint in suppressed:
                continue
            normalized.append(normalized_item)
    normalized = _assign_unique_fingerprints(normalized)
    atomic_write_json(
        pet_decisions_path(group),
        {
            "schema": _SCHEMA,
            "by": str(actor_id or "").strip(),
            "decisions": normalized,
        },
        indent=2,
    )
    return normalized


def clear_pet_decisions(group: Group, *, actor_id: str) -> None:
    atomic_write_json(
        pet_decisions_path(group),
        {
            "schema": _SCHEMA,
            "by": str(actor_id or "").strip(),
            "decisions": [],
        },
        indent=2,
    )
