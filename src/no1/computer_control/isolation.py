from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List


def _load_codex_config(env: Dict[str, Any]) -> Dict[str, Any]:
    root = Path(str(env.get("CODEX_HOME") or (Path.home() / ".codex"))).expanduser()
    path = root / "config.toml"
    try:
        try:
            import tomllib
        except ImportError:  # pragma: no cover - Python 3.9 compatibility
            import tomli as tomllib  # type: ignore[no-redef]
        value = tomllib.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (ImportError, OSError, ValueError):
        return {}


def codex_windows_mcp_server_names(env: Dict[str, Any]) -> List[str]:
    config = _load_codex_config(env)
    servers = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
    names: List[str] = []
    for name, value in servers.items():
        if not isinstance(value, dict):
            continue
        command = str(value.get("command") or "")
        args = value.get("args") if isinstance(value.get("args"), list) else []
        identity = " ".join([command, *(str(item) for item in args)]).lower()
        if "windows-mcp" in identity or "windows_mcp" in identity:
            names.append(str(name))
    return names


def _windows_mcp_supported_platform() -> bool:
    return os.name == "nt"


def codex_windows_mcp_disable_args(env: Dict[str, Any]) -> List[str]:
    if _windows_mcp_supported_platform():
        return []
    args: List[str] = []
    for name in codex_windows_mcp_server_names(env):
        key = name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else '"' + name.replace('"', '\\"') + '"'
        args.extend(["-c", f"mcp_servers.{key}.enabled=false"])
    return args
