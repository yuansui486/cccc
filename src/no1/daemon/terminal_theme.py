from __future__ import annotations

from typing import Any, Dict


TERMINAL_COLOR_SCHEME_ENV = "ONECOLLEAGUE_TERMINAL_COLOR_SCHEME"


def normalize_terminal_color_scheme(value: Any, *, default: str = "dark") -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"light", "dark"}:
        return candidate
    fallback = str(default or "dark").strip().lower()
    return fallback if fallback in {"light", "dark"} else "dark"


def with_terminal_color_scheme(env: Dict[str, str], value: Any) -> Dict[str, str]:
    result = dict(env or {})
    result[TERMINAL_COLOR_SCHEME_ENV] = normalize_terminal_color_scheme(value)
    return result
