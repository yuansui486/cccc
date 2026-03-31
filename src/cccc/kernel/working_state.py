from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional


EffectiveWorkingState = Literal["stopped", "idle", "working", "waiting", "stuck"]

DEFAULT_PTY_STUCK_IDLE_SECONDS = 300.0
DEFAULT_PTY_WORKING_IDLE_SECONDS = 5.0
DEFAULT_PTY_TERMINAL_SIGNAL_TAIL_BYTES = 12_000
DEFAULT_CODEX_TERMINAL_SIGNAL_WINDOW_CHARS = 1_600


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))", "", str(text or "")).replace("\r", "")


def _recent_non_empty_lines(text: str, *, max_lines: int = 4) -> list[str]:
    lines: list[str] = []
    for raw_line in reversed(str(text or "").split("\n")):
        line = raw_line.strip()
        if not line:
            continue
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return lines


def _last_non_empty_line(text: str) -> str:
    lines = _recent_non_empty_lines(text, max_lines=1)
    return lines[0] if lines else ""


def _is_terminal_footer_line(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return False
    return bool(re.match(r"^gpt-[\w.-]+\s+default\b", value, re.IGNORECASE)) or (
        "/Desktop/" in value and bool(re.search(r"\bleft\b", value, re.IGNORECASE))
    )


def _is_terminal_prompt_line(line: str) -> bool:
    value = str(line or "").strip()
    if not value:
        return False
    if re.match(r"^(?:>|›)\s?.*", value):
        return True
    if re.match(r"^(?:\$|%|#|❯|➜|›)\s+.*$", value):
        return True
    if re.match(r"^[\w.@:/~-]+\s*(?:\$|%|#)\s*$", value):
        return True
    return False


def _has_terminal_prompt_visible(text: str) -> bool:
    for line in _recent_non_empty_lines(text):
        if _is_terminal_footer_line(line):
            continue
        return _is_terminal_prompt_line(line)
    return False


def _tail_window_has_codex_working_banner(text: str) -> bool:
    value = str(text or "")
    if not value:
        return False
    compact = re.sub(r"\s+", " ", value)
    return bool(re.search(r"\bworking\s*\(", compact, re.IGNORECASE))


def _tail_window(text: str, *, max_chars: int = DEFAULT_CODEX_TERMINAL_SIGNAL_WINDOW_CHARS) -> str:
    value = str(text or "")
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[-max_chars:]


def _has_visible_terminal_output(text: str) -> bool:
    cleaned = _strip_ansi(str(text or ""))
    for ch in cleaned:
        code = ord(ch)
        if code in (9, 10):
            continue
        if 32 <= code <= 126 or code > 159:
            return True
    return False


def _derive_pty_terminal_override(*, runtime: str, terminal_text: str) -> Optional[Dict[str, Any]]:
    runtime_id = _clean_text(runtime).lower()
    cleaned = _strip_ansi(terminal_text)
    if runtime_id != "codex":
        return None

    if _has_terminal_prompt_visible(cleaned):
        return {
            "effective_working_state": "idle",
            "effective_working_reason": "pty_terminal_prompt_visible",
        }

    tail_text = _tail_window(cleaned)
    if _tail_window_has_codex_working_banner(tail_text):
        return {
            "effective_working_state": "working",
            "effective_working_reason": "pty_terminal_codex_working_banner",
        }

    return None


def derive_effective_working_state(
    *,
    running: bool,
    effective_runner: str,
    runtime: str = "",
    idle_seconds: Optional[float] = None,
    pty_terminal_text: str = "",
    agent_state: Optional[Dict[str, Any]] = None,
    headless_state: Optional[Dict[str, Any]] = None,
    pty_stuck_idle_seconds: float = DEFAULT_PTY_STUCK_IDLE_SECONDS,
) -> Dict[str, Any]:
    hot = agent_state.get("hot") if isinstance(agent_state, dict) and isinstance(agent_state.get("hot"), dict) else {}
    active_task_id = _clean_text((headless_state or {}).get("current_task_id")) or _clean_text(hot.get("active_task_id"))
    updated_at = _clean_text((headless_state or {}).get("updated_at")) or _clean_text((agent_state or {}).get("updated_at"))

    if not running:
        return {
            "effective_working_state": "stopped",
            "effective_working_reason": "runner_not_running",
            "effective_working_updated_at": updated_at or None,
            "effective_active_task_id": active_task_id or None,
        }

    if effective_runner == "headless":
        status = _clean_text((headless_state or {}).get("status")).lower() or "idle"
        if status not in {"idle", "working", "waiting", "stopped"}:
            status = "idle"
        return {
            "effective_working_state": "stopped" if status == "stopped" else status,
            "effective_working_reason": f"headless_{status}",
            "effective_working_updated_at": updated_at or None,
            "effective_active_task_id": active_task_id or None,
        }

    terminal_override = _derive_pty_terminal_override(runtime=runtime, terminal_text=pty_terminal_text)
    if terminal_override is not None:
        return {
            **terminal_override,
            "effective_working_updated_at": updated_at or None,
            "effective_active_task_id": active_task_id or None,
        }

    idle = _safe_float(idle_seconds)
    has_visible_output = _has_visible_terminal_output(pty_terminal_text)
    if idle is not None:
        if has_visible_output and idle < DEFAULT_PTY_WORKING_IDLE_SECONDS:
            return {
                "effective_working_state": "working",
                "effective_working_reason": "pty_no_prompt_recent_output",
                "effective_working_updated_at": updated_at or None,
                "effective_active_task_id": active_task_id or None,
            }
        if idle < float(pty_stuck_idle_seconds):
            return {
                "effective_working_state": "waiting",
                "effective_working_reason": "pty_no_prompt_waiting",
                "effective_working_updated_at": updated_at or None,
                "effective_active_task_id": active_task_id or None,
            }
        return {
            "effective_working_state": "stuck",
            "effective_working_reason": "pty_no_prompt_stuck",
            "effective_working_updated_at": updated_at or None,
            "effective_active_task_id": active_task_id or None,
        }

    return {
        "effective_working_state": "waiting",
        "effective_working_reason": "pty_running_state_unknown",
        "effective_working_updated_at": updated_at or None,
        "effective_active_task_id": active_task_id or None,
    }
