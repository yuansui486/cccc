"""Hermes runtime setup helpers.

Hermes follows the same integration model as Claude/Codex: OneColleague uses the
selected user Hermes home/profile and injects per-actor runtime context at process
launch. OneColleague does not create or select a separate Hermes profile.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml  # type: ignore

from ..paths import ensure_home
from ..util.file_lock import acquire_lockfile, release_lockfile
from ..util.process import find_subprocess_executable, resolve_subprocess_argv
from .runtime import get_onecolleague_mcp_stdio_command

HERMES_PROVIDER_ID = "xai-oauth"
HERMES_ONECOLLEAGUE_PROVIDER_ID = "onecolleague"
HERMES_ONECOLLEAGUE_PROVIDER_RUNTIME_ID = "custom:onecolleague"
HERMES_ONECOLLEAGUE_BASE_URL = "https://peer.shierkeji.com/v1"
HERMES_ONECOLLEAGUE_API_KEY_ENV = "ONECOLLEAGUE_API_KEY"
HERMES_MCP_SERVER_NAME = "onecolleague"
HERMES_MCP_REQUIREMENTS = ("mcp==1.28.1", "starlette==1.3.1")

HERMES_MCP_ENV_PLACEHOLDERS: Dict[str, str] = {
    "ONECOLLEAGUE_HOME": "${ONECOLLEAGUE_HOME}",
    "ONECOLLEAGUE_GROUP_ID": "${ONECOLLEAGUE_GROUP_ID}",
    "ONECOLLEAGUE_ACTOR_ID": "${ONECOLLEAGUE_ACTOR_ID}",
    "CCCC_HOME": "${CCCC_HOME}",
    "CCCC_GROUP_ID": "${CCCC_GROUP_ID}",
    "CCCC_ACTOR_ID": "${CCCC_ACTOR_ID}",
}
HERMES_DISCOVERY_GROUP_ID = "g_probe"
HERMES_DISCOVERY_ACTOR_ID = "hermes-probe"


def user_hermes_home() -> Path:
    if os.name == "nt":
        local_appdata = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local_appdata:
            # Keep tests and explicitly mocked homes deterministic while using
            # the official native Windows location for the real user profile.
            try:
                if Path.home().expanduser() in Path(local_appdata).expanduser().parents:
                    return (Path(local_appdata) / "hermes").expanduser()
            except Exception:
                return (Path(local_appdata) / "hermes").expanduser()
    return (Path.home() / ".hermes").expanduser()


def hermes_home(*, hermes_home_override: Optional[Path] = None) -> Path:
    if hermes_home_override is not None:
        return Path(hermes_home_override).expanduser()
    return user_hermes_home()


def hermes_profile_dir(*, hermes_home_override: Optional[Path] = None) -> Path:
    return hermes_home(hermes_home_override=hermes_home_override)


def hermes_profile_config_path(*, hermes_home_override: Optional[Path] = None) -> Path:
    return hermes_profile_dir(hermes_home_override=hermes_home_override) / "config.yaml"


def hermes_configured_model(*, hermes_home_override: Optional[Path] = None) -> str:
    try:
        data = yaml.safe_load(
            hermes_profile_config_path(hermes_home_override=hermes_home_override).read_text(encoding="utf-8")
        ) or {}
    except Exception:
        data = {}
    config = data if isinstance(data, dict) else {}
    model = config.get("model")
    if isinstance(model, dict):
        return str(model.get("default") or model.get("name") or "").strip()
    return str(model or "").strip()


def build_hermes_auth_add_command(*, no_browser: bool = False) -> list[str]:
    cmd = ["hermes", "auth", "add", HERMES_PROVIDER_ID]
    if no_browser:
        cmd.append("--no-browser")
    return cmd


def build_hermes_mcp_test_command() -> list[str]:
    return ["hermes", "mcp", "test", HERMES_MCP_SERVER_NAME]


def build_hermes_launch_command(*, model: Optional[str] = None) -> list[str]:
    cmd = ["hermes", "--tui", "--yolo"]
    selected = str(model or "").strip()
    if selected:
        cmd.extend(["--provider", HERMES_ONECOLLEAGUE_PROVIDER_RUNTIME_ID, "--model", selected])
    return cmd


def normalize_hermes_launch_command(command: Iterable[str], *, selected_model: Optional[str] = None) -> list[str]:
    """Add a per-Actor Hermes provider/model override without mutating storage."""
    cmd = [str(item) for item in (command or []) if str(item).strip()]
    if not cmd:
        cmd = build_hermes_launch_command(model=selected_model)
    selected = str(selected_model or "").strip()
    has_model = any(item == "--model" or item.startswith("--model=") for item in cmd)
    has_provider = any(item == "--provider" or item.startswith("--provider=") for item in cmd)
    if not has_provider:
        cmd.extend(["--provider", HERMES_ONECOLLEAGUE_PROVIDER_RUNTIME_ID])
    if selected and not has_model:
        cmd.extend(["--model", selected])
    if cmd and Path(str(cmd[0])).name.lower() in {"hermes", "hermes.exe", "hermes.cmd", "hermes.bat"}:
        cmd[0] = find_subprocess_executable("hermes") or cmd[0]
    return cmd


def hermes_prebuilt_tui_dir(command: Iterable[str]) -> Optional[Path]:
    """Return Hermes' ready-to-run TUI bundle for a managed launch."""
    cmd = [str(item) for item in (command or []) if str(item).strip()]
    if "--tui" not in cmd:
        return None

    executable = str(cmd[0] if cmd else "").strip()
    resolved = find_subprocess_executable(executable or "hermes")
    if not resolved:
        return None

    executable_path = Path(resolved).expanduser()
    candidates: list[Path] = []
    for parent in executable_path.parents:
        candidates.append(parent / "ui-tui")
        if len(candidates) >= 5:
            break

    for candidate in candidates:
        node_modules = candidate / "node_modules"
        if not (candidate / "dist" / "entry.js").is_file() or not node_modules.is_dir():
            continue
        try:
            if any(node_modules.iterdir()):
                return candidate
        except OSError:
            continue
    return None


def _effective_hermes_home_override(hermes_home_override: Optional[Path]) -> Optional[Path]:
    if hermes_home_override is not None:
        return Path(hermes_home_override).expanduser()
    raw = str(os.environ.get("HERMES_HOME") or "").strip()
    return Path(raw).expanduser() if raw else None


def build_hermes_mcp_add_command(
    cccc_cmd: Optional[list[str]] = None,
    *,
    env_values: Optional[Dict[str, str]] = None,
) -> list[str]:
    cmd = list(cccc_cmd or get_onecolleague_mcp_stdio_command())
    if not cmd:
        cmd = ["onecolleague", "mcp"]
    out = ["hermes", "mcp", "add", HERMES_MCP_SERVER_NAME, "--command", str(cmd[0])]
    args = [str(part) for part in cmd[1:] if str(part).strip()]
    # Hermes argparse defines --args with REMAINDER semantics; it must be the
    # final option or every following --env token becomes an MCP server arg.
    out.append("--env")
    env = dict(env_values or HERMES_MCP_ENV_PLACEHOLDERS)
    out.extend(f"{key}={env.get(key, value)}" for key, value in HERMES_MCP_ENV_PLACEHOLDERS.items())
    if args:
        out.append("--args")
        out.extend(args)
    return out


def _shell_join(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def hermes_onecolleague_base_url(env: Optional[Dict[str, Any]] = None) -> str:
    values = env if isinstance(env, dict) else os.environ
    raw = str(values.get("ONECOLLEAGUE_OPENCODE_BASE_URL") or HERMES_ONECOLLEAGUE_BASE_URL).strip()
    return raw.rstrip("/") or HERMES_ONECOLLEAGUE_BASE_URL


def merge_hermes_onecolleague_provider(
    config: Dict[str, Any], *, env: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Merge the managed OpenAI-compatible OneColleague provider.

    Hermes resolves ``key_env`` at process launch, so the API key never needs
    to be persisted in the shared Hermes config.
    """
    result = dict(config or {})
    providers = result.get("providers")
    providers = dict(providers) if isinstance(providers, dict) else {}
    current = providers.get(HERMES_ONECOLLEAGUE_PROVIDER_ID)
    current = dict(current) if isinstance(current, dict) else {}
    current.update(
        {
            "name": "OneColleague",
            "base_url": hermes_onecolleague_base_url(env),
            "key_env": HERMES_ONECOLLEAGUE_API_KEY_ENV,
            "api_mode": "chat_completions",
        }
    )
    providers[HERMES_ONECOLLEAGUE_PROVIDER_ID] = current
    result["providers"] = providers
    return result


def _inspect_onecolleague_provider(config: Dict[str, Any]) -> Dict[str, Any]:
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    entry = providers.get(HERMES_ONECOLLEAGUE_PROVIDER_ID) if isinstance(providers, dict) else None
    entry = entry if isinstance(entry, dict) else {}
    base_url = str(entry.get("base_url") or entry.get("url") or entry.get("api") or "").strip()
    key_env = str(entry.get("key_env") or entry.get("api_key_env") or "").strip()
    ready = bool(
        base_url.rstrip("/") == HERMES_ONECOLLEAGUE_BASE_URL
        and key_env == HERMES_ONECOLLEAGUE_API_KEY_ENV
        and str(entry.get("api_mode") or entry.get("transport") or "chat_completions").strip().lower()
        in {"chat_completions", "chat-completions", "openai"}
    )
    return {
        "status": "ready" if ready else "missing",
        "configured": bool(entry),
        "provider": HERMES_ONECOLLEAGUE_PROVIDER_ID,
        "runtime_provider": HERMES_ONECOLLEAGUE_PROVIDER_RUNTIME_ID,
        "base_url": base_url,
        "key_env": key_env,
        "expected_base_url": HERMES_ONECOLLEAGUE_BASE_URL,
        "expected_key_env": HERMES_ONECOLLEAGUE_API_KEY_ENV,
        "api_mode": str(entry.get("api_mode") or entry.get("transport") or "").strip(),
        "expected_api_mode": "chat_completions",
    }


def _normalize_args(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    return [str(part) for part in str(value or "").split() if str(part).strip()]


def _onecolleague_mcp_entrypoint(command: str, args: list[str]) -> bool:
    if args != ["mcp"]:
        return False
    path = Path(str(command or "").strip()).expanduser()
    if path.name.lower() not in {
        "onecolleague",
        "onecolleague.exe",
        "onecolleague.cmd",
        "onecolleague.bat",
        "onecolleague-script.py",
    }:
        return False
    return path.is_file()


def _auth_doc_mentions_provider(value: Any) -> bool:
    needle = HERMES_PROVIDER_ID.lower()
    if isinstance(value, dict):
        for key, item in value.items():
            if needle in str(key or "").lower():
                return True
            if _auth_doc_mentions_provider(item):
                return True
        return False
    if isinstance(value, list):
        return any(_auth_doc_mentions_provider(item) for item in value)
    return needle in str(value or "").lower()


def _inspect_mcp_config(config: Dict[str, Any], *, expected_cmd: list[str]) -> Dict[str, Any]:
    servers = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
    server_name = HERMES_MCP_SERVER_NAME
    entry = None
    if isinstance(servers, dict):
        candidate_entry = servers.get(HERMES_MCP_SERVER_NAME)
        if isinstance(candidate_entry, dict):
            entry = candidate_entry
    if not isinstance(entry, dict):
        return {
            "status": "missing",
            "configured": False,
            "server_name": HERMES_MCP_SERVER_NAME,
            "accepted_server_names": [HERMES_MCP_SERVER_NAME],
            "expected_command": list(expected_cmd),
            "env_placeholders": dict(HERMES_MCP_ENV_PLACEHOLDERS),
        }

    command = str(entry.get("command") or "").strip()
    args = _normalize_args(entry.get("args"))
    env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    expected_command = str(expected_cmd[0] if expected_cmd else "").strip()
    expected_args = [str(part) for part in expected_cmd[1:]]
    command_ok = bool(command and expected_command and Path(command).expanduser() == Path(expected_command).expanduser())
    if not command_ok:
        command_ok = bool(
            _onecolleague_mcp_entrypoint(command, args)
            and _onecolleague_mcp_entrypoint(expected_command, expected_args)
        )
    args_ok = args == expected_args
    env_ok = all(str(env.get(key) or "").strip() == value for key, value in HERMES_MCP_ENV_PLACEHOLDERS.items())
    enabled_ok = str(entry.get("enabled", True)).strip().lower() not in {"0", "false", "no"}
    status = "ready" if command_ok and args_ok and env_ok and enabled_ok else "stale"
    return {
        "status": status,
        "configured": True,
        "server_name": server_name,
        "accepted_server_names": [HERMES_MCP_SERVER_NAME],
        "command": command,
        "args": args,
        "enabled": enabled_ok,
        "expected_command": list(expected_cmd),
        "command_matches": command_ok,
        "args_match": args_ok,
        "env_placeholders": {key: str(env.get(key) or "") for key in HERMES_MCP_ENV_PLACEHOLDERS},
        "env_placeholders_match": env_ok,
    }


def _inspect_auth(profile_dir: Path) -> Dict[str, Any]:
    auth_path = profile_dir / "auth.json"
    exists = auth_path.exists()
    provider_present = False
    if exists:
        provider_present = _auth_doc_mentions_provider(_read_json(auth_path))
    return {
        "provider": HERMES_PROVIDER_ID,
        "auth_path": str(auth_path),
        "auth_file_exists": bool(exists),
        "status": "present" if provider_present else "missing",
    }


def _normalize_mcp_config_placeholders(config_path: Path) -> None:
    """Replace discovery-time runtime env values with runtime placeholders.

    Hermes' official `mcp add` discovers tools by launching the server
    immediately, so setup must pass concrete runtime env values to avoid creating a
    literal `${ONECOLLEAGUE_HOME}` directory during discovery. The persisted shared
    profile, however, must retain placeholders so each actor process resolves
    its own OneColleague identity at launch time.
    """
    def _find_entry(servers: Any) -> tuple[str, Any]:
        if not isinstance(servers, dict):
            return HERMES_MCP_SERVER_NAME, None
        entry = servers.get(HERMES_MCP_SERVER_NAME)
        if isinstance(entry, dict):
            return HERMES_MCP_SERVER_NAME, entry
        return HERMES_MCP_SERVER_NAME, None

    def _matches() -> bool:
        doc_now = _read_yaml(config_path)
        servers_now = doc_now.get("mcp_servers") if isinstance(doc_now.get("mcp_servers"), dict) else None
        _server_name_now, entry_now = _find_entry(servers_now)
        env_now = entry_now.get("env") if isinstance(entry_now, dict) and isinstance(entry_now.get("env"), dict) else {}
        return all(str(env_now.get(key) or "") == value for key, value in HERMES_MCP_ENV_PLACEHOLDERS.items())

    try:
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except FileNotFoundError:
        return
    except Exception:
        lines = []

    if lines:
        changed = False
        in_mcp = False
        in_server = False
        in_env = False
        mcp_indent = 0
        server_indent = 0
        env_indent = 0
        for idx, line in enumerate(lines):
            stripped = line.lstrip(" ")
            content = stripped.strip()
            if not content or content.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if in_env and indent <= env_indent:
                in_env = False
            if in_server and indent <= server_indent and not (
                indent == server_indent and content.startswith(f"{HERMES_MCP_SERVER_NAME}:")
            ):
                in_server = False
            if in_mcp and indent <= mcp_indent and not (indent == mcp_indent and content.startswith("mcp_servers:")):
                in_mcp = False
            if not in_mcp and content.startswith("mcp_servers:"):
                in_mcp = True
                mcp_indent = indent
                continue
            if in_mcp and not in_server and indent > mcp_indent and content.startswith(f"{HERMES_MCP_SERVER_NAME}:"):
                in_server = True
                server_indent = indent
                continue
            if in_server and not in_env and indent > server_indent and content.startswith("env:"):
                in_env = True
                env_indent = indent
                continue
            if in_env and indent > env_indent:
                for key, value in HERMES_MCP_ENV_PLACEHOLDERS.items():
                    if content.startswith(f"{key}:"):
                        newline = "\n" if line.endswith("\n") else ""
                        lines[idx] = f"{line[:indent]}{key}: {value}{newline}"
                        changed = True
                        break
        if changed:
            config_path.write_text("".join(lines), encoding="utf-8")
            if _matches():
                return

    doc = _read_yaml(config_path)
    servers = doc.get("mcp_servers") if isinstance(doc.get("mcp_servers"), dict) else None
    _server_name, entry = _find_entry(servers)
    if not isinstance(entry, dict):
        return
    env = entry.get("env") if isinstance(entry.get("env"), dict) else {}
    changed = False
    for key, value in HERMES_MCP_ENV_PLACEHOLDERS.items():
        if str(env.get(key) or "") != value:
            env[key] = value
            changed = True
    if not changed:
        return
    entry["env"] = env
    config_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _hermes_version(path: str) -> str:
    if not path:
        return ""
    try:
        result = subprocess.run(
            resolve_subprocess_argv([path, "--version"]),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip().splitlines()[0] if str(result.stdout or "").strip() else ""


def hermes_runtime_status(
    *,
    home: Optional[Path] = None,
    include_version: bool = True,
    hermes_home_override: Optional[Path] = None,
) -> Dict[str, Any]:
    hermes_home_override = _effective_hermes_home_override(hermes_home_override)
    root = Path(home).expanduser().resolve() if home is not None else ensure_home()
    selected_home = hermes_home(hermes_home_override=hermes_home_override)
    profile_dir = hermes_profile_dir(hermes_home_override=hermes_home_override)
    config_path = hermes_profile_config_path(hermes_home_override=hermes_home_override)
    normal_home = user_hermes_home()
    expected_cmd = get_onecolleague_mcp_stdio_command()
    config = _read_yaml(config_path)
    hermes_path = find_subprocess_executable("hermes")

    mcp = _inspect_mcp_config(config, expected_cmd=expected_cmd)
    onecolleague_provider = _inspect_onecolleague_provider(config)
    auth = _inspect_auth(profile_dir)
    issues: list[str] = []
    if not hermes_path:
        issues.append("hermes_cli_missing")
    if not profile_dir.exists():
        issues.append("profile_missing")
    if not config_path.exists():
        issues.append("config_missing")
    if mcp.get("status") != "ready":
        issues.append("onecolleague_mcp_config_not_ready")
    if onecolleague_provider.get("status") != "ready":
        issues.append("onecolleague_provider_not_ready")

    gates = [
        {
            "id": "uses_selected_user_hermes_home",
            "status": "pass",
            "evidence": str(selected_home),
        },
        {
            "id": "selected_profile_exists",
            "status": "pass" if profile_dir.exists() else "fail",
            "evidence": str(profile_dir),
        },
        {
            "id": "mcp_config_uses_actor_placeholders",
            "status": "pass" if bool(mcp.get("env_placeholders_match")) else "fail",
            "evidence": mcp.get("env_placeholders", {}),
        },
        {
            "id": "xai_oauth_present",
            "status": "pass" if auth.get("status") == "present" else "pending",
            "evidence": auth.get("auth_path"),
        },
        {
            "id": "onecolleague_provider_ready",
            "status": "pass" if onecolleague_provider.get("status") == "ready" else "fail",
            "evidence": onecolleague_provider,
        },
        {
            "id": "concurrent_actor_attribution",
            "status": "pending",
            "evidence": "requires manual two-actor Hermes smoke after OAuth",
        },
        {
            "id": "ignore_rules_safe",
            "status": "pending",
            "evidence": "not enabled by default until Hermes smoke proves MCP still works",
        },
    ]

    env_prefix = f"HERMES_HOME={shlex.quote(str(selected_home))} " if hermes_home_override is not None else ""
    mcp_add_shape = _shell_join(
        build_hermes_mcp_add_command(
            expected_cmd,
            env_values={
                "ONECOLLEAGUE_HOME": str(root),
                "ONECOLLEAGUE_GROUP_ID": HERMES_DISCOVERY_GROUP_ID,
                "ONECOLLEAGUE_ACTOR_ID": HERMES_DISCOVERY_ACTOR_ID,
                "CCCC_HOME": str(root),
                "CCCC_GROUP_ID": HERMES_DISCOVERY_GROUP_ID,
                "CCCC_ACTOR_ID": HERMES_DISCOVERY_ACTOR_ID,
            },
        )
    )
    commands = {
        "prepare": f"{env_prefix}onecolleague runtime hermes prepare --yes",
        "mcp_test": f"{env_prefix}onecolleague runtime hermes mcp-test",
        "auth_add": f"{env_prefix}{_shell_join(build_hermes_auth_add_command())}",
        "auth_add_no_browser": f"{env_prefix}{_shell_join(build_hermes_auth_add_command(no_browser=True))}",
        "launch": f"{env_prefix}{_shell_join(build_hermes_launch_command())}",
        "official_mcp_add_shape": f"{env_prefix}{mcp_add_shape}",
    }

    return {
        "runtime": "hermes",
        "phase": "phase1_pty_runtime_mvp",
        "user_facing_actor_runtime_enabled": True,
        "setup_ready": bool(profile_dir.exists() and mcp.get("status") == "ready" and onecolleague_provider.get("status") == "ready"),
        "auth_ready": bool(auth.get("status") == "present"),
        "launch_ready": bool(hermes_path and profile_dir.exists() and mcp.get("status") == "ready" and onecolleague_provider.get("status") == "ready"),
        "hermes_cli": {
            "available": bool(hermes_path),
            "path": hermes_path,
            "version": _hermes_version(hermes_path or "") if include_version else "",
        },
        "hermes_home": str(selected_home),
        "profile": {
            "name": "default",
            "dir": str(profile_dir),
            "exists": bool(profile_dir.exists()),
            "config_path": str(config_path),
            "config_exists": bool(config_path.exists()),
        },
        "user_hermes_home": str(normal_home),
        "mcp": mcp,
        "onecolleague_provider": onecolleague_provider,
        "auth": auth,
        "commands": commands,
        "phase0_gates": gates,
        "issues": issues,
    }


def _run_hermes_cli(
    argv: list[str],
    *,
    hermes_home_path: Optional[Path] = None,
    cwd: Optional[Path] = None,
    timeout: int = 60,
    input_text: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if hermes_home_path is not None:
        env["HERMES_HOME"] = str(hermes_home_path)
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items() if isinstance(k, str)})
    kwargs: Dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "env": env,
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if input_text is not None:
        kwargs["input"] = input_text
    return subprocess.run(resolve_subprocess_argv(argv), **kwargs)


def _hermes_python(hermes_executable: str) -> Optional[Path]:
    executable = Path(str(hermes_executable or "")).expanduser()
    if not executable.is_absolute():
        return None
    scripts_dir = executable.parent
    candidates = (
        scripts_dir / "python.exe",
        scripts_dir / "python",
        scripts_dir.parent / "bin" / "python",
        scripts_dir.parent / "bin" / "python3",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _hermes_mcp_sdk_status(hermes_executable: str, *, timeout: int = 30) -> Dict[str, Any]:
    python = _hermes_python(hermes_executable)
    if python is None:
        return {
            "available": False,
            "python": "",
            "message": "Hermes Python environment could not be located",
        }
    probe = subprocess.run(
        resolve_subprocess_argv(
            [
                str(python),
                "-c",
                "import mcp; print(getattr(mcp, '__version__', 'installed'))",
            ]
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return {
        "available": probe.returncode == 0,
        "python": str(python),
        "returncode": int(probe.returncode),
        "stdout": str(probe.stdout or ""),
        "stderr": str(probe.stderr or ""),
        "message": "ready" if probe.returncode == 0 else "Hermes MCP SDK is not installed",
    }


def _install_hermes_mcp_sdk(
    hermes_executable: str,
    *,
    cwd: Optional[Path] = None,
    timeout: int = 600,
) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    python = _hermes_python(hermes_executable)
    if python is None:
        raise RuntimeError("Hermes Python environment could not be located")
    uv = find_subprocess_executable("uv")
    if uv:
        command = [uv, "pip", "install", "--python", str(python), *HERMES_MCP_REQUIREMENTS]
    else:
        command = [str(python), "-m", "pip", "install", *HERMES_MCP_REQUIREMENTS]
    result = subprocess.run(
        resolve_subprocess_argv(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(cwd) if cwd is not None else None,
    )
    return command, result


def _merge_hermes_provider_config(
    *,
    hermes_home_override: Optional[Path],
    provider_env: Optional[Dict[str, Any]],
) -> None:
    config_path = hermes_profile_config_path(hermes_home_override=hermes_home_override)
    current_config = _read_yaml(config_path)
    merged_config = merge_hermes_onecolleague_provider(current_config, env=provider_env)
    if merged_config != current_config:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump(merged_config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


def _completed_summary(result: subprocess.CompletedProcess[str]) -> Dict[str, Any]:
    return {
        "returncode": int(result.returncode),
        "stdout": str(result.stdout or ""),
        "stderr": str(result.stderr or ""),
    }


def _completed_failure_detail(result: subprocess.CompletedProcess[str], *, limit: int = 800) -> str:
    text = str(result.stderr or "").strip() or str(result.stdout or "").strip()
    if len(text) > limit:
        text = text[-limit:]
    return " ".join(text.split())


def prepare_hermes_runtime(
    *,
    home: Optional[Path] = None,
    cwd: Optional[Path] = None,
    auto_enable_tools: bool = False,
    force_mcp: bool = False,
    hermes_home_override: Optional[Path] = None,
    provider_env: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    hermes_home_override = _effective_hermes_home_override(hermes_home_override)
    root = Path(home).expanduser().resolve() if home is not None else ensure_home()
    lock = None
    commands_run: list[Dict[str, Any]] = []
    try:
        hermes_executable = find_subprocess_executable("hermes")
        if not hermes_executable:
            return {
                "ok": False,
                "error": {"code": "hermes_cli_missing", "message": "Hermes CLI is not installed or not in PATH"},
                "status": hermes_runtime_status(
                    home=root,
                    include_version=False,
                    hermes_home_override=hermes_home_override,
                ),
            }
        lock_dir = root / "daemon"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock = acquire_lockfile(lock_dir / "hermes-runtime-setup.lock", blocking=True)

        # Provider configuration is independent from MCP discovery. Persist it
        # first so a missing optional MCP dependency cannot leave Hermes with
        # neither the selected endpoint nor the OneColleague provider.
        _merge_hermes_provider_config(
            hermes_home_override=hermes_home_override,
            provider_env=provider_env,
        )

        status = hermes_runtime_status(
            home=root,
            include_version=False,
            hermes_home_override=hermes_home_override,
        )
        needs_mcp_config = bool(force_mcp or ((status.get("mcp") or {}).get("status") != "ready"))
        if needs_mcp_config and not auto_enable_tools:
            return {
                "ok": False,
                "error": {
                    "code": "hermes_mcp_setup_requires_confirmation",
                    "message": "Hermes MCP setup is discovery-first; rerun with auto_enable_tools/--yes to enable discovered OneColleague tools.",
                },
                "commands_run": commands_run,
                "status": status,
            }

        if auto_enable_tools:
            sdk_status = _hermes_mcp_sdk_status(hermes_executable)
            if not sdk_status.get("available"):
                install_cmd, install_result = _install_hermes_mcp_sdk(
                    hermes_executable,
                    cwd=cwd,
                )
                commands_run.append(
                    {
                        "name": "mcp_sdk_install",
                        "argv": install_cmd,
                        "result": _completed_summary(install_result),
                    }
                )
                sdk_status = _hermes_mcp_sdk_status(hermes_executable)
                if install_result.returncode != 0 or not sdk_status.get("available"):
                    failure_detail = _completed_failure_detail(install_result)
                    message = (
                        "Hermes is installed without its optional MCP SDK and "
                        "OneColleague could not install the official MCP dependencies"
                    )
                    if failure_detail:
                        message = f"{message}: {failure_detail}"
                    return {
                        "ok": False,
                        "error": {
                            "code": "hermes_mcp_sdk_install_failed",
                            "message": message,
                            "details": sdk_status,
                        },
                        "commands_run": commands_run,
                        "status": hermes_runtime_status(
                            home=root,
                            include_version=False,
                            hermes_home_override=hermes_home_override,
                        ),
                    }

        if needs_mcp_config:
            mcp_status = status.get("mcp") if isinstance(status.get("mcp"), dict) else {}
            if bool(mcp_status.get("configured")):
                remove_cmd = ["hermes", "mcp", "remove", HERMES_MCP_SERVER_NAME]
                remove_result = _run_hermes_cli(
                    remove_cmd,
                    hermes_home_path=hermes_home_override,
                    cwd=cwd,
                    timeout=60,
                )
                commands_run.append({"name": "mcp_remove", "argv": remove_cmd, "result": _completed_summary(remove_result)})
                if remove_result.returncode != 0:
                    return {
                        "ok": False,
                        "error": {
                            "code": "hermes_mcp_remove_failed",
                            "message": "failed to remove stale Hermes OneColleague MCP server",
                        },
                        "commands_run": commands_run,
                        "status": status,
                    }
            cmd = build_hermes_mcp_add_command(
                env_values={
                    "ONECOLLEAGUE_HOME": str(root),
                    "ONECOLLEAGUE_GROUP_ID": HERMES_DISCOVERY_GROUP_ID,
                    "ONECOLLEAGUE_ACTOR_ID": HERMES_DISCOVERY_ACTOR_ID,
                    "CCCC_HOME": str(root),
                    "CCCC_GROUP_ID": HERMES_DISCOVERY_GROUP_ID,
                    "CCCC_ACTOR_ID": HERMES_DISCOVERY_ACTOR_ID,
                },
            )
            result = _run_hermes_cli(cmd, hermes_home_path=hermes_home_override, cwd=cwd, timeout=120, input_text="Y\n")
            commands_run.append({"name": "mcp_add", "argv": cmd, "result": _completed_summary(result)})
            if result.returncode == 0:
                _normalize_mcp_config_placeholders(
                    hermes_profile_config_path(hermes_home_override=hermes_home_override)
                )
            status = hermes_runtime_status(
                home=root,
                include_version=False,
                hermes_home_override=hermes_home_override,
            )
            if result.returncode != 0 or ((status.get("mcp") or {}).get("status") != "ready"):
                failure_detail = _completed_failure_detail(result)
                message = "failed to configure Hermes OneColleague MCP server"
                if failure_detail:
                    message = f"{message}: {failure_detail}"
                return {
                    "ok": False,
                    "error": {"code": "hermes_mcp_add_failed", "message": message},
                    "commands_run": commands_run,
                    "status": status,
                }

        return {
            "ok": True,
            "commands_run": commands_run,
            "status": hermes_runtime_status(
                home=root,
                include_version=False,
                hermes_home_override=hermes_home_override,
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {"code": "hermes_prepare_failed", "message": str(exc)},
            "commands_run": commands_run,
            "status": hermes_runtime_status(
                home=root,
                include_version=False,
                hermes_home_override=hermes_home_override,
            ),
        }
    finally:
        if lock is not None:
            release_lockfile(lock)


def run_hermes_mcp_test(
    *,
    home: Optional[Path] = None,
    cwd: Optional[Path] = None,
    group_id: str = "g_probe",
    actor_id: str = "hermes-probe",
    hermes_home_override: Optional[Path] = None,
) -> Dict[str, Any]:
    hermes_home_override = _effective_hermes_home_override(hermes_home_override)
    root = Path(home).expanduser().resolve() if home is not None else ensure_home()
    cmd = build_hermes_mcp_test_command()
    env = {
        "ONECOLLEAGUE_HOME": str(root),
        "ONECOLLEAGUE_GROUP_ID": str(group_id or "g_probe"),
        "ONECOLLEAGUE_ACTOR_ID": str(actor_id or "hermes-probe"),
        "CCCC_HOME": str(root),
        "CCCC_GROUP_ID": str(group_id or "g_probe"),
        "CCCC_ACTOR_ID": str(actor_id or "hermes-probe"),
    }
    try:
        result = _run_hermes_cli(
            cmd,
            hermes_home_path=hermes_home_override,
            cwd=cwd,
            timeout=60,
            extra_env=env,
        )
    except Exception as exc:
        return {"ok": False, "argv": cmd, "error": {"code": "hermes_mcp_test_failed", "message": str(exc)}}
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return {
        "ok": result.returncode == 0 and "connection failed" not in output,
        "argv": cmd,
        "result": _completed_summary(result),
    }
