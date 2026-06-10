from __future__ import annotations

import json
import re
from pathlib import Path

CODEX_PROVIDER_BASE_URL = "https://peer.shierkeji.com/v1"
CODEX_PROVIDER_ENV_KEY = "ONECOLLEAGUE_API_KEY"

_MANAGED_TOP_LEVEL = {
    "model_provider": json.dumps("custom"),
    "model_reasoning_effort": json.dumps("high"),
    "disable_response_storage": "true",
}
_REMOVED_TOP_LEVEL = {"openai_base_url"}
_CUSTOM_PROVIDER_HEADER = "[model_providers.custom]"


def _managed_custom_provider_block() -> list[str]:
    return [
        _CUSTOM_PROVIDER_HEADER,
        'name = "custom"',
        'wire_api = "responses"',
        f'base_url = "{CODEX_PROVIDER_BASE_URL}"',
        f'env_key = "{CODEX_PROVIDER_ENV_KEY}"',
    ]


def _section_name(line: str) -> str:
    stripped = str(line or "").strip()
    if not stripped.startswith("[") or stripped.startswith("[["):
        return ""
    match = re.match(r"^\[([^\]]+)\]\s*(?:#.*)?$", stripped)
    return str(match.group(1) or "").strip() if match else ""


def _split_codex_config_sections(existing: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    top_level: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_name = ""
    current_lines: list[str] | None = None

    for line in str(existing or "").splitlines():
        name = _section_name(line)
        if name:
            if current_lines is not None:
                sections.append((current_name, current_lines))
            current_name = name
            current_lines = [line]
            continue
        if current_lines is None:
            top_level.append(line)
        else:
            current_lines.append(line)

    if current_lines is not None:
        sections.append((current_name, current_lines))
    return top_level, sections


def merge_codex_custom_provider_config(existing: str) -> str:
    top_level, sections = _split_codex_config_sections(existing)
    out_top: list[str] = []

    for line in top_level:
        match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = str(match.group(2) or "") if match else ""
        if key in _REMOVED_TOP_LEVEL:
            continue
        if key in _MANAGED_TOP_LEVEL:
            continue
        out_top.append(line)

    insert_at = 0
    while insert_at < len(out_top) and not str(out_top[insert_at] or "").strip():
        insert_at += 1
    managed = [f"{key} = {value}" for key, value in _MANAGED_TOP_LEVEL.items()]
    out_top = [*out_top[:insert_at], *managed, *out_top[insert_at:]]

    out_sections: list[str] = []
    provider_written = False
    for name, lines in sections:
        if name == "model_providers.custom":
            if not provider_written:
                out_sections.extend(_managed_custom_provider_block())
                provider_written = True
            continue
        out_sections.extend(lines)

    if not provider_written:
        out_sections = [*_managed_custom_provider_block(), *out_sections]

    parts: list[str] = []
    parts.extend(out_top)
    while parts and not str(parts[-1] or "").strip():
        parts.pop()
    if parts:
        parts.append("")
    parts.extend(out_sections)
    return "\n".join(parts).rstrip() + "\n"


def sync_codex_custom_provider_config() -> None:
    config_path = Path.home() / ".codex" / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    next_content = merge_codex_custom_provider_config(existing)
    if next_content == existing:
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_name(f".{config_path.name}.tmp")
    tmp_path.write_text(next_content, encoding="utf-8", newline="\n")
    tmp_path.replace(config_path)
