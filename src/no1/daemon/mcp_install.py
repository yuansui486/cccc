"""Runtime MCP installation helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from ..kernel.hermes_runtime import hermes_prebuilt_tui_dir, hermes_runtime_status, prepare_hermes_runtime
from ..kernel.runtime import get_onecolleague_mcp_stdio_command
from ..util.conv import coerce_bool
from ..util.fs import read_json
from ..util.process import resolve_subprocess_argv
from .opencode_provider import merge_opencode_agent_default, merge_opencode_provider_config

MCP_SERVER_NAME = "onecolleague"
MCP_SERVER_NAMES = (MCP_SERVER_NAME,)
LEGACY_MCP_SERVER_NAMES = ("cccc",)


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


def _mcp_command_array_matches_expected(command: Any, expected_cmd: list[str]) -> bool:
    if not isinstance(command, list) or len(command) != len(expected_cmd) or not expected_cmd:
        return False
    actual = [str(part or "").strip().strip('"').strip("'") for part in command]
    expected = [str(part or "").strip().strip('"').strip("'") for part in expected_cmd]
    if _normalize_mcp_command_value(actual[0]) != _normalize_mcp_command_value(expected[0]):
        return False
    return actual[1:] == expected[1:]


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


_OPENCODE_CONTEXT_ENV_KEYS = ("ONECOLLEAGUE_HOME", "CCCC_HOME", "ONECOLLEAGUE_GROUP_ID", "CCCC_GROUP_ID", "ONECOLLEAGUE_ACTOR_ID", "CCCC_ACTOR_ID")


def _opencode_context_environment(env: Dict[str, str] | None) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not isinstance(env, dict):
        return result
    for key in _OPENCODE_CONTEXT_ENV_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def _opencode_onecolleague_entry(env: Dict[str, str] | None) -> Dict[str, Any]:
    return {
        "type": "local",
        "command": _runtime_expected_onecolleague_command("opencode"),
        "enabled": True,
        "environment": _opencode_context_environment(env),
    }


def _read_opencode_inline_config(env: Dict[str, str] | None) -> Dict[str, Any]:
    raw = str((env or {}).get("OPENCODE_CONFIG_CONTENT") or "").strip() if isinstance(env, dict) else ""
    if not raw:
        return {}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid OPENCODE_CONFIG_CONTENT: expected JSON object") from exc
    if not isinstance(doc, dict):
        raise ValueError("invalid OPENCODE_CONFIG_CONTENT: expected JSON object")
    return dict(doc)


def _opencode_mcp_entry_matches_expected(entry: Any, expected_cmd: list[str], env: Dict[str, str] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(entry.get("type") or "").strip().lower() != "local":
        return False
    if coerce_bool(entry.get("enabled"), default=True) is False:
        return False
    if not _mcp_command_array_matches_expected(entry.get("command"), expected_cmd):
        return False
    expected_env = _opencode_context_environment(env)
    actual_env = entry.get("environment")
    if not isinstance(actual_env, dict):
        return not expected_env
    return all(str(actual_env.get(key) or "").strip() in {value, f"{{env:{key}}}"} for key, value in expected_env.items())


def _opencode_mcp_state(env: Dict[str, str] | None) -> str:
    try:
        doc = _read_opencode_inline_config(env)
    except ValueError:
        return "stale"
    servers = doc.get("mcp") if isinstance(doc, dict) else None
    if not isinstance(servers, dict):
        return "missing"
    entry = servers.get(MCP_SERVER_NAME)
    if entry is None:
        return "missing"
    return "ready" if _opencode_mcp_entry_matches_expected(entry, _runtime_expected_onecolleague_command("opencode"), env) else "stale"


def prepare_runtime_mcp_env(
    runtime: str,
    env: Dict[str, Any] | None,
    *,
    command: list[str] | None = None,
    runtime_options: Dict[str, Any] | None = None,
) -> Dict[str, str]:
    """Prepare runtime-scoped MCP environment without changing user config."""
    result = {str(k): str(v) for k, v in (env or {}).items() if isinstance(k, str)}
    normalized_runtime = str(runtime or "").strip().lower()
    selected_model = str((runtime_options or {}).get("selected_model") or "").strip()
    # Claude Code normally lets settings.json env entries replace values
    # inherited from the host process.  OneColleague is the host for managed
    # actors, so ask Claude to honor the provider routing and credentials we
    # inject here (ANTHROPIC_BASE_URL, ANTHROPIC_*_KEY/TOKEN, model vars).
    if normalized_runtime == "claude":
        result["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] = "1"
    if normalized_runtime == "kimi" and selected_model:
        result["KIMI_MODEL_NAME"] = selected_model
    if normalized_runtime == "hermes" and selected_model:
        result["HERMES_SELECTED_MODEL"] = selected_model
    if normalized_runtime == "openclaw" and selected_model:
        result["OPENCLAW_SELECTED_MODEL"] = selected_model
    if normalized_runtime == "hermes" and not str(result.get("HERMES_TUI_DIR") or "").strip():
        tui_dir = hermes_prebuilt_tui_dir(command or [])
        if tui_dir is not None:
            result["HERMES_TUI_DIR"] = str(tui_dir)
    if normalized_runtime != "opencode":
        return result
    doc = _read_opencode_inline_config(result)
    doc = merge_opencode_provider_config(doc, result)
    doc = merge_opencode_agent_default(doc, command=command, runtime_options=runtime_options)
    mcp = doc.get("mcp")
    mcp = dict(mcp) if isinstance(mcp, dict) else {}
    mcp[MCP_SERVER_NAME] = _opencode_onecolleague_entry(result)
    doc["mcp"] = mcp
    result["OPENCODE_CONFIG_CONTENT"] = json.dumps(doc, ensure_ascii=True, separators=(",", ":"))
    return result


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
    if runtime == "codex":
        return ["codex", "mcp", "remove", name]
    if runtime == "droid":
        return ["droid", "mcp", "remove", name]
    return None


def build_mcp_get_command(runtime: str, server_name: str = MCP_SERVER_NAME) -> list[str] | None:
    name = str(server_name or MCP_SERVER_NAME).strip() or MCP_SERVER_NAME
    if runtime == "claude":
        return ["claude", "mcp", "get", name]
    if runtime == "codex":
        return ["codex", "mcp", "get", name]
    return None


def _mcp_subprocess_env(env: Dict[str, str] | None) -> dict[str, str]:
    merged_env = dict(os.environ)
    merged_env.setdefault("PYTHONUTF8", "1")
    merged_env.setdefault("PYTHONIOENCODING", "utf-8")
    if env is not None:
        merged_env.update({str(k): str(v) for k, v in env.items() if isinstance(k, str)})
    return merged_env


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
        "env": _mcp_subprocess_env(env),
    }
    if text:
        kwargs["encoding"] = "utf-8"
        kwargs["errors"] = "replace"
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    return subprocess.run(resolve_subprocess_argv(argv), **kwargs)


def _remove_legacy_mcp_servers(runtime: str, cwd: Path, *, env: Dict[str, str] | None = None) -> bool:
    for server_name in LEGACY_MCP_SERVER_NAMES:
        get_cmd = build_mcp_get_command(runtime, server_name=server_name)
        remove_cmd = build_mcp_remove_command(runtime, server_name=server_name)
        if not get_cmd or not remove_cmd:
            continue
        try:
            get_result = _run_cli(get_cmd, timeout=10, env=env)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if get_result.returncode != 0:
            continue
        try:
            remove_result = _run_cli(remove_cmd, cwd=cwd, timeout=30, env=env)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        if remove_result.returncode != 0:
            return False
    return True


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

    if runtime == "opencode":
        return _opencode_mcp_state(env)

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
    command: list[str] | None = None,
) -> bool:
    if runtime not in auto_mcp_runtimes:
        return True
    if runtime == "openclaw":
        try:
            from .openclaw_runtime import ensure_openclaw_mcp_installed

            return ensure_openclaw_mcp_installed(cwd, env=env, command=command)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"openclaw_runtime_setup_failed: {exc}") from exc
    if runtime == "hermes":
        try:
            state = _runtime_mcp_state(runtime, env=env)
            if state == "ready":
                status = hermes_runtime_status(
                    home=_cccc_home_dir(env),
                    include_version=False,
                    hermes_home_override=_hermes_home_override(env),
                )
                provider = status.get("onecolleague_provider") if isinstance(status.get("onecolleague_provider"), dict) else {}
                if provider.get("status") == "ready":
                    return True
            result = prepare_hermes_runtime(
                home=_cccc_home_dir(env),
                cwd=cwd,
                auto_enable_tools=True,
                force_mcp=(state == "stale"),
                hermes_home_override=_hermes_home_override(env),
                provider_env=env,
            )
            if not bool(result.get("ok")):
                error = result.get("error") if isinstance(result.get("error"), dict) else {}
                code = str(error.get("code") or "hermes_mcp_setup_failed").strip()
                message = str(error.get("message") or "failed to configure Hermes MCP").strip()
                raise RuntimeError(f"{code}: {message}")
            final_state = _runtime_mcp_state(runtime, env=env)
            if final_state != "ready":
                raise RuntimeError(f"hermes_mcp_setup_incomplete: final MCP state is {final_state}")
            return True
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"hermes_mcp_setup_failed: {exc}") from exc
    try:
        state = _runtime_mcp_state(runtime, env=env)
        if not _remove_legacy_mcp_servers(runtime, cwd, env=env):
            return False
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
