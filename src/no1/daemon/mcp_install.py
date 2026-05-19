"""Runtime MCP installation helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from ..kernel.hermes_runtime import hermes_runtime_status, prepare_hermes_runtime
from ..kernel.runtime import get_onecolleague_mcp_stdio_command
from ..util.conv import coerce_bool
from ..util.fs import read_json
from ..util.process import resolve_subprocess_argv

MCP_SERVER_NAME = "onecolleague"
MCP_SERVER_NAMES = (MCP_SERVER_NAME,)


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


def _runtime_expected_onecolleague_command(runtime: str) -> list[str]:
    cmd = list(get_onecolleague_mcp_stdio_command())
    if sys.platform.startswith("win") and runtime == "droid" and cmd:
        cmd[0] = str(cmd[0]).replace("\\", "/")
    return cmd


def _home_dir(env: Dict[str, str] | None) -> Path:
    raw = ""
    if isinstance(env, dict):
        raw = str(env.get("HOME") or env.get("USERPROFILE") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home()


def _cccc_home_dir(env: Dict[str, str] | None) -> Path | None:
    raw = ""
    if isinstance(env, dict):
        raw = str(env.get("ONECOLLEAGUE_HOME") or env.get("CCCC_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return None


def _hermes_home_override(env: Dict[str, str] | None) -> Path | None:
    raw = ""
    if isinstance(env, dict):
        raw = str(env.get("HERMES_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return None


def _kimi_share_dir(env: Dict[str, str] | None) -> Path:
    raw = ""
    if isinstance(env, dict):
        raw = str(env.get("KIMI_SHARE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _home_dir(env) / ".kimi"


def build_mcp_add_command(runtime: str) -> list[str] | None:
    onecolleague_cmd = _runtime_expected_onecolleague_command(runtime)
    if runtime == "claude":
        return ["claude", "mcp", "add", "-s", "user", MCP_SERVER_NAME, "--", *onecolleague_cmd]
    if runtime == "codex":
        return ["codex", "mcp", "add", MCP_SERVER_NAME, "--", *onecolleague_cmd]
    if runtime == "droid":
        return ["droid", "mcp", "add", "--type", "stdio", MCP_SERVER_NAME, *onecolleague_cmd]
    if runtime == "amp":
        return ["amp", "mcp", "add", MCP_SERVER_NAME, *onecolleague_cmd]
    if runtime == "auggie":
        return ["auggie", "mcp", "add", MCP_SERVER_NAME, "--", *onecolleague_cmd]
    if runtime == "neovate":
        return ["neovate", "mcp", "add", "-g", MCP_SERVER_NAME, *onecolleague_cmd]
    if runtime == "gemini":
        return ["gemini", "mcp", "add", "-s", "user", MCP_SERVER_NAME, *onecolleague_cmd]
    if runtime == "hermes":
        return ["onecolleague", "runtime", "hermes", "prepare", "--yes"]
    if runtime == "kimi":
        return ["kimi", "mcp", "add", "--transport", "stdio", MCP_SERVER_NAME, "--", *onecolleague_cmd]
    return None


def build_mcp_remove_command(runtime: str, server_name: str = MCP_SERVER_NAME) -> list[str] | None:
    name = str(server_name or MCP_SERVER_NAME).strip() or MCP_SERVER_NAME
    if runtime == "claude":
        return ["claude", "mcp", "remove", name, "-s", "user"]
    if runtime == "droid":
        return ["droid", "mcp", "remove", name]
    return None


def _run_cli(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int,
    text: bool = True,
    env: Dict[str, str] | None = None,
) -> subprocess.CompletedProcess[Any]:
    kwargs: dict[str, object] = {
        "capture_output": True,
        "timeout": timeout,
        "text": text,
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if env is not None:
        merged_env = dict(os.environ)
        merged_env.update({str(k): str(v) for k, v in env.items() if isinstance(k, str)})
        kwargs["env"] = merged_env
    return subprocess.run(resolve_subprocess_argv(argv), **kwargs)


def _json_mcp_state(paths: tuple[Path, ...], expected_cmd: list[str]) -> str:
    state = "missing"
    for cfg_path in paths:
        cfg = read_json(cfg_path)
        servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
        if not isinstance(servers, dict):
            continue
        for server_name in MCP_SERVER_NAMES:
            entry = servers.get(server_name)
            if entry is None:
                continue
            if _json_mcp_entry_matches_expected(entry, expected_cmd):
                return "ready"
            state = "stale"
    return state


def _probe_named_cli_mcp_state(
    argv_prefix: list[str],
    *,
    expected_cmd: list[str],
    matcher,
    text: bool,
    env: Dict[str, str] | None,
) -> str:
    state = "missing"
    for server_name in MCP_SERVER_NAMES:
        result = _run_cli([*argv_prefix, server_name], timeout=10, text=text, env=env)
        if result.returncode != 0:
            continue
        output = _coerce_output_text(result.stdout)
        if matcher(output, expected_cmd):
            return "ready"
        state = "stale"
    return state


def _runtime_mcp_state(runtime: str, *, env: Dict[str, str] | None = None) -> str:
    expected_cmd = _runtime_expected_onecolleague_command(runtime)

    if runtime == "claude":
        return _probe_named_cli_mcp_state(
            ["claude", "mcp", "get"],
            expected_cmd=expected_cmd,
            matcher=_claude_mcp_entry_matches_expected,
            text=False,
            env=env,
        )

    if runtime == "codex":
        return _probe_named_cli_mcp_state(
            ["codex", "mcp", "get"],
            expected_cmd=expected_cmd,
            matcher=_codex_mcp_entry_matches_expected,
            text=True,
            env=env,
        )

    if runtime == "droid":
        home = _home_dir(env)
        return _json_mcp_state(
            (
                home / ".factory" / "mcp.json",
                home / ".config" / "droid" / "mcp.json",
                home / ".droid" / "mcp.json",
            ),
            expected_cmd,
        )

    if runtime == "amp":
        settings_path = _home_dir(env) / ".config" / "amp" / "settings.json"
        if not settings_path.exists():
            return "missing"
        doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(doc, dict):
            return "missing"
        servers = doc.get("amp.mcpServers")
        if not isinstance(servers, dict):
            return "missing"
        state = "missing"
        for server_name in MCP_SERVER_NAMES:
            entry = servers.get(server_name)
            if entry is None:
                continue
            if _json_mcp_entry_matches_expected(entry, expected_cmd):
                return "ready"
            state = "stale"
        return state

    if runtime == "auggie":
        settings_path = _home_dir(env) / ".augment" / "settings.json"
        if not settings_path.exists():
            return "missing"
        doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(doc, dict):
            return "missing"
        servers = doc.get("mcpServers")
        if not isinstance(servers, dict):
            return "missing"
        state = "missing"
        for server_name in MCP_SERVER_NAMES:
            entry = servers.get(server_name)
            if entry is None:
                continue
            if _json_mcp_entry_matches_expected(entry, expected_cmd):
                return "ready"
            state = "stale"
        return state

    if runtime == "neovate":
        config_path = _home_dir(env) / ".neovate" / "config.json"
        if not config_path.exists():
            return "missing"
        doc = json.loads(config_path.read_text(encoding="utf-8") or "{}")
        if not isinstance(doc, dict):
            return "missing"
        servers = doc.get("mcpServers")
        if not isinstance(servers, dict):
            return "missing"
        state = "missing"
        for server_name in MCP_SERVER_NAMES:
            entry = servers.get(server_name)
            if entry is None:
                continue
            if _json_mcp_entry_matches_expected(entry, expected_cmd):
                return "ready"
            state = "stale"
        return state

    if runtime == "gemini":
        return _json_mcp_state((_home_dir(env) / ".gemini" / "settings.json",), expected_cmd)

    if runtime == "hermes":
        status = hermes_runtime_status(
            home=_cccc_home_dir(env),
            include_version=False,
            hermes_home_override=_hermes_home_override(env),
        )
        mcp = status.get("mcp") if isinstance(status.get("mcp"), dict) else {}
        return str(mcp.get("status") or "missing")

    if runtime == "kimi":
        return _json_mcp_state((_kimi_share_dir(env) / "mcp.json",), expected_cmd)

    return "missing"


def is_mcp_installed(runtime: str, *, env: Dict[str, str] | None = None) -> bool:
    try:
        return _runtime_mcp_state(runtime, env=env) == "ready"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception:
        pass
    return False


def ensure_mcp_installed(
    runtime: str,
    cwd: Path,
    *,
    auto_mcp_runtimes: tuple[str, ...],
    env: Dict[str, str] | None = None,
) -> bool:
    if runtime not in auto_mcp_runtimes:
        return True
    if runtime == "hermes":
        try:
            state = _runtime_mcp_state(runtime, env=env)
            if state == "ready":
                return True
            result = prepare_hermes_runtime(
                home=_cccc_home_dir(env),
                cwd=cwd,
                auto_enable_tools=True,
                force_mcp=(state == "stale"),
                hermes_home_override=_hermes_home_override(env),
            )
            return bool(result.get("ok")) and _runtime_mcp_state(runtime, env=env) == "ready"
        except Exception:
            return False
    try:
        state = _runtime_mcp_state(runtime, env=env)
        if state == "ready":
            return True
        add_cmd = build_mcp_add_command(runtime)
        if not add_cmd:
            return False

        if state == "stale":
            for server_name in MCP_SERVER_NAMES:
                remove_cmd = build_mcp_remove_command(runtime, server_name=server_name)
                if not remove_cmd:
                    continue
                remove_result = _run_cli(remove_cmd, cwd=cwd, timeout=30, env=env)
                if remove_result.returncode != 0:
                    return False

        result = _run_cli(add_cmd, cwd=cwd, timeout=30, env=env)
        return result.returncode == 0 and is_mcp_installed(runtime, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False
