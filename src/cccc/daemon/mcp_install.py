"""Runtime MCP installation helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from ..kernel.runtime import get_cccc_mcp_stdio_command
from ..util.conv import coerce_bool
from ..util.fs import read_json
from ..util.process import resolve_subprocess_argv


def _parse_mcp_get_output(output: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip().lower()] = value.strip()
    return parsed


def _normalize_mcp_command_value(value: str) -> str:
    normalized = str(value or "").strip().strip('"').strip("'")
    if sys.platform.startswith("win"):
        return normalized.replace("/", "\\").lower()
    return normalized


def _normalize_mcp_arg_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = str(value or "").split()
    return [str(part or "").strip().strip('"').strip("'") for part in parts if str(part or "").strip()]


def _entry_command_matches_expected(command: Any, args: Any, expected_cmd: list[str], *, strict: bool) -> bool:
    if not expected_cmd:
        return False
    actual_command = str(command or "").strip()
    if not actual_command:
        return not strict
    expected_command = _normalize_mcp_command_value(expected_cmd[0])
    if _normalize_mcp_command_value(actual_command) != expected_command:
        return False
    return _normalize_mcp_arg_values(args) == _normalize_mcp_arg_values(expected_cmd[1:])


def _mcp_transport_matches(entry: Dict[str, Any]) -> bool:
    transport = entry.get("transport", entry.get("type", "stdio"))
    value = str(transport or "stdio").strip().lower()
    return not value or value == "stdio"


def _coerce_output_text(output: Any) -> str:
    if isinstance(output, bytes):
        return output.decode(errors="ignore")
    return str(output or "")


def _codex_mcp_entry_matches_expected(output: str, expected_cmd: list[str]) -> bool:
    entry = _parse_mcp_get_output(output)
    if not entry:
        return False
    if str(entry.get("enabled", "true")).strip().lower() == "false":
        return False
    if not _mcp_transport_matches(entry):
        return False
    return _entry_command_matches_expected(
        entry.get("command", ""),
        entry.get("args", ""),
        expected_cmd,
        strict=sys.platform.startswith("win"),
    )


def _claude_mcp_entry_matches_expected(output: str, expected_cmd: list[str]) -> bool:
    entry = _parse_mcp_get_output(output)
    if not entry:
        return False
    if not _mcp_transport_matches(entry):
        return False
    return _entry_command_matches_expected(
        entry.get("command", ""),
        entry.get("args", ""),
        expected_cmd,
        strict=sys.platform.startswith("win"),
    )


def _json_mcp_entry_matches_expected(entry: Any, expected_cmd: list[str]) -> bool:
    if not isinstance(entry, dict):
        return bool(entry)
    if coerce_bool(entry.get("disabled"), default=False):
        return False
    if not _mcp_transport_matches(entry):
        return False
    return _entry_command_matches_expected(
        entry.get("command", ""),
        entry.get("args", []),
        expected_cmd,
        strict=sys.platform.startswith("win"),
    )


def _runtime_expected_cccc_command(runtime: str) -> list[str]:
    cmd = list(get_cccc_mcp_stdio_command())
    if sys.platform.startswith("win") and runtime == "droid" and cmd:
        cmd[0] = str(cmd[0]).replace("\\", "/")
    return cmd


def build_mcp_add_command(runtime: str) -> list[str] | None:
    cccc_cmd = _runtime_expected_cccc_command(runtime)
    if runtime == "claude":
        return ["claude", "mcp", "add", "-s", "user", "cccc", "--", *cccc_cmd]
    if runtime == "codex":
        return ["codex", "mcp", "add", "cccc", "--", *cccc_cmd]
    if runtime == "droid":
        return ["droid", "mcp", "add", "--type", "stdio", "cccc", *cccc_cmd]
    if runtime == "amp":
        return ["amp", "mcp", "add", "cccc", *cccc_cmd]
    if runtime == "auggie":
        return ["auggie", "mcp", "add", "cccc", "--", *cccc_cmd]
    if runtime == "neovate":
        return ["neovate", "mcp", "add", "-g", "cccc", *cccc_cmd]
    if runtime == "gemini":
        return ["gemini", "mcp", "add", "-s", "user", "cccc", *cccc_cmd]
    if runtime == "kimi":
        return ["kimi", "mcp", "add", "--transport", "stdio", "cccc", "--", *cccc_cmd]
    return None


def build_mcp_remove_command(runtime: str) -> list[str] | None:
    if runtime == "claude":
        return ["claude", "mcp", "remove", "cccc", "-s", "user"]
    if runtime == "droid":
        return ["droid", "mcp", "remove", "cccc"]
    return None


def _run_cli(argv: list[str], *, cwd: Path | None = None, timeout: int, text: bool = True) -> subprocess.CompletedProcess[Any]:
    kwargs: dict[str, object] = {
        "capture_output": True,
        "timeout": timeout,
        "text": text,
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    return subprocess.run(resolve_subprocess_argv(argv), **kwargs)


def _json_mcp_state(paths: tuple[Path, ...], expected_cmd: list[str]) -> str:
    state = "missing"
    for cfg_path in paths:
        cfg = read_json(cfg_path)
        servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
        if not isinstance(servers, dict):
            continue
        entry = servers.get("cccc")
        if entry is None:
            continue
        if _json_mcp_entry_matches_expected(entry, expected_cmd):
            return "ready"
        state = "stale"
    return state


def _runtime_mcp_state(runtime: str) -> str:
    expected_cmd = _runtime_expected_cccc_command(runtime)

    if runtime == "claude":
        result = _run_cli(["claude", "mcp", "get", "cccc"], timeout=10, text=False)
        if result.returncode != 0:
            return "missing"
        output = _coerce_output_text(result.stdout)
        return "ready" if _claude_mcp_entry_matches_expected(output, expected_cmd) else "stale"

    if runtime == "codex":
        result = _run_cli(["codex", "mcp", "get", "cccc"], timeout=10)
        if result.returncode != 0:
            return "missing"
        return "ready" if _codex_mcp_entry_matches_expected(result.stdout, expected_cmd) else "stale"

    if runtime == "droid":
        return _json_mcp_state(
            (
                Path.home() / ".factory" / "mcp.json",
                Path.home() / ".config" / "droid" / "mcp.json",
                Path.home() / ".droid" / "mcp.json",
            ),
            expected_cmd,
        )

    if runtime == "amp":
        settings_path = Path.home() / ".config" / "amp" / "settings.json"
        if not settings_path.exists():
            return "missing"
        doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(doc, dict):
            return "missing"
        servers = doc.get("amp.mcpServers")
        if not isinstance(servers, dict):
            return "missing"
        entry = servers.get("cccc")
        if entry is None:
            return "missing"
        return "ready" if _json_mcp_entry_matches_expected(entry, expected_cmd) else "stale"

    if runtime == "auggie":
        settings_path = Path.home() / ".augment" / "settings.json"
        if not settings_path.exists():
            return "missing"
        doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(doc, dict):
            return "missing"
        servers = doc.get("mcpServers")
        if not isinstance(servers, dict):
            return "missing"
        entry = servers.get("cccc")
        if entry is None:
            return "missing"
        return "ready" if _json_mcp_entry_matches_expected(entry, expected_cmd) else "stale"

    if runtime == "neovate":
        config_path = Path.home() / ".neovate" / "config.json"
        if not config_path.exists():
            return "missing"
        doc = json.loads(config_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(doc, dict):
            return "missing"
        servers = doc.get("mcpServers")
        if not isinstance(servers, dict):
            return "missing"
        entry = servers.get("cccc")
        if entry is None:
            return "missing"
        return "ready" if _json_mcp_entry_matches_expected(entry, expected_cmd) else "stale"

    if runtime == "gemini":
        return _json_mcp_state((Path.home() / ".gemini" / "settings.json",), expected_cmd)

    if runtime == "kimi":
        return _json_mcp_state((Path.home() / ".kimi" / "mcp.json",), expected_cmd)

    return "missing"


def is_mcp_installed(runtime: str) -> bool:
    try:
        return _runtime_mcp_state(runtime) == "ready"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception:
        pass
    return False


def ensure_mcp_installed(runtime: str, cwd: Path, *, auto_mcp_runtimes: tuple[str, ...]) -> bool:
    if runtime not in auto_mcp_runtimes:
        return True
    try:
        state = _runtime_mcp_state(runtime)
        if state == "ready":
            return True
        add_cmd = build_mcp_add_command(runtime)
        if not add_cmd:
            return False

        if state == "stale":
            remove_cmd = build_mcp_remove_command(runtime)
            if remove_cmd:
                remove_result = _run_cli(remove_cmd, cwd=cwd, timeout=30)
                if remove_result.returncode != 0:
                    return False

        result = _run_cli(add_cmd, cwd=cwd, timeout=30)
        return result.returncode == 0 and is_mcp_installed(runtime)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False
