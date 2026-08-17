"""OpenClaw runtime adapter for CCCC-managed actors."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

import json5
import psutil

from ..kernel.runtime import get_onecolleague_mcp_stdio_command
from ..paths import ensure_home
from ..util.fs import atomic_write_json, read_json
from ..util.process import (
    pid_is_alive,
    resolve_subprocess_argv,
    supervised_process_popen_kwargs,
    terminate_pid,
    windowless_subprocess_popen_kwargs,
)
from .opencode_provider import (
    OPENCODE_API_KEY_ENV,
    OPENCODE_PROVIDER_ID,
    get_opencode_model_catalog,
    load_opencode_model_catalog,
    opencode_base_url,
)


_MANAGED_AGENT_PREFIX = "onecolleague-"
_MANAGED_MCP_PREFIX = "onecolleague-"
_LEGACY_MANAGED_AGENT_PREFIXES: tuple[str, ...] = ()
_LEGACY_MANAGED_MCP_PREFIXES: tuple[str, ...] = ()
_TUI_VALUE_FLAGS = {"--history-limit", "--thinking", "--timeout-ms"}
_PREPARE_CACHE_SECONDS = 10.0
_PREPARED_SCHEMA_VERSION = 1
_MODEL_CACHE_SECONDS = 30.0
_GATEWAY_PORT_BASE = 22000
_GATEWAY_PORT_STRIDE = 128
_GATEWAY_PORT_SLOTS = 300
_MAX_INCLUDE_FILE_BYTES = 2 * 1024 * 1024
_BLOCKED_CONFIG_OBJECT_KEYS = {"__proto__", "prototype", "constructor"}
_CONTEXT_ENV_KEYS = (
    "OPENCLAW_CONFIG_PATH",
    "OPENCLAW_STATE_DIR",
    "OPENCLAW_GATEWAY_PORT",
    "OPENCLAW_GATEWAY_URL",
    "OPENCLAW_PROFILE",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONFIG_ENV_REF_RE = re.compile(r"(?<!\$)\$\{([A-Z_][A-Z0-9_]*)\}")
_PATH_ENV_REF_RE = re.compile(
    r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}|(?<!\$)\$([A-Za-z_][A-Za-z0-9_]*)|%([^%]+)%"
)
_LOCK = threading.RLock()
_GATEWAY_PROCESSES: Dict[tuple[str, ...], subprocess.Popen[Any]] = {}
_GATEWAY_ACTORS: Dict[tuple[str, str], tuple[str, ...]] = {}
_GATEWAY_ACTOR_ENVS: Dict[tuple[str, str], Dict[str, str]] = {}
_GATEWAY_IDLE_TIMERS: Dict[tuple[str, ...], threading.Timer] = {}
_GATEWAY_PORT_RESERVATIONS: Dict[str, int] = {}
_PREPARED: Dict[str, tuple[float, list[str]]] = {}
_MODEL_CACHE: Dict[tuple[str, ...], tuple[float, list[Dict[str, Any]]]] = {}
_INITIALIZED_CONTEXTS: Dict[str, tuple[Any, ...]] = {}
_CONTEXT_LOCKS: Dict[str, threading.RLock] = {}

_GATEWAY_RPC_SCRIPT = r"""
import { pathToFileURL } from "node:url";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const { callGatewayFromCli } = await import(pathToFileURL(request.sdkPath).href);

try {
  const result = await callGatewayFromCli(
    request.method,
    {
      url: request.url,
      token: request.token,
      timeout: String(request.timeoutMs),
      json: true,
    },
    request.params,
    {
      clientName: "gateway-client",
      mode: "backend",
      deviceIdentity: null,
      scopes: ["operator.admin"],
      progress: false,
    },
  );
  if (result !== undefined) process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stderr.write(error?.stack || error?.message || String(error));
  process.exitCode = 1;
}
""".strip()


@dataclass(frozen=True)
class _ManagedContext:
    context_id: str
    root: Path
    config_path: Path
    state_dir: Path
    gateway_port: int
    gateway_token: str
    source_config_path: Optional[Path] = None
    source_state_dir: Optional[Path] = None
    group_id: str = ""
    actor_id: str = ""
    agent_id: str = ""


def _stable_digest(group_id: str, actor_id: str, *, length: int = 16) -> str:
    raw = f"{str(group_id).strip()}\0{str(actor_id).strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def openclaw_agent_id(group_id: str, actor_id: str) -> str:
    return f"{_MANAGED_AGENT_PREFIX}{_stable_digest(group_id, actor_id)}"


def openclaw_mcp_server_name(group_id: str, actor_id: str) -> str:
    return openclaw_agent_id(group_id, actor_id)


def openclaw_session_key(group_id: str, actor_id: str, workspace: Path | str = "") -> str:
    normalized = os.path.normcase(os.path.abspath(str(workspace or Path.cwd())))
    workspace_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"agent:{openclaw_agent_id(group_id, actor_id)}:onecolleague-{workspace_hash}"


def _is_managed_agent_id(agent_id: str) -> bool:
    normalized = str(agent_id or "").strip()
    return normalized.startswith((_MANAGED_AGENT_PREFIX, *_LEGACY_MANAGED_AGENT_PREFIXES))


def _is_managed_mcp_server_name(server_name: str) -> bool:
    normalized = str(server_name or "").strip()
    return normalized.startswith((_MANAGED_MCP_PREFIX, *_LEGACY_MANAGED_MCP_PREFIXES))


def _command_stem(command: str) -> str:
    return str(Path(ntpath.basename(str(command or ""))).stem or "").strip().lower()


def openclaw_cli_prefix(command: Iterable[str] | None = None) -> list[str]:
    """Return the executable from a managed OpenClaw launch command."""
    items = [str(item) for item in list(command or []) if str(item).strip()]
    if not items:
        return ["openclaw"]
    if _command_stem(items[0]) != "openclaw":
        raise ValueError("OpenClaw runtime command must start with the openclaw executable")
    return [items[0]]


def _openclaw_profile(prefix: Iterable[str], env: Optional[Dict[str, str]]) -> str:
    del prefix
    return str((env or {}).get("OPENCLAW_PROFILE") or "").strip()


def build_openclaw_tui_command(
    command: Iterable[str] | None,
    *,
    group_id: str,
    actor_id: str,
    workspace: Path | str = "",
) -> list[str]:
    """Normalize a user command into a Gateway-backed actor-specific TUI."""
    items = [str(item) for item in list(command or []) if str(item).strip()]
    prefix = openclaw_cli_prefix(items)
    tail = items[len(prefix) :]
    if tail and tail[0].strip().lower() in {"tui", "terminal"}:
        tail = tail[1:]
    elif tail and not tail[0].startswith("-"):
        raise ValueError("OpenClaw runtime command must use the tui or terminal surface")

    options: list[str] = []
    index = 0
    while index < len(tail):
        item = tail[index]
        normalized = item.strip().lower()
        value_flag = next(
            (flag for flag in _TUI_VALUE_FLAGS if normalized == flag or normalized.startswith(f"{flag}=")),
            None,
        )
        if value_flag is None:
            raise ValueError(f"unsupported OpenClaw TUI option: {item}")
        if normalized == value_flag:
            if index + 1 >= len(tail) or str(tail[index + 1]).startswith("-"):
                raise ValueError(f"missing value for OpenClaw option: {item}")
            options.extend((item, tail[index + 1]))
            index += 2
        else:
            options.append(item)
            index += 1
    has_log_level = any(
        str(item).strip().lower() == "--log-level"
        or str(item).strip().lower().startswith("--log-level=")
        for item in prefix[1:]
    )
    if not has_log_level:
        prefix.extend(("--log-level", "error"))
    result = [*prefix, "tui", *options]
    result.extend(("--session", openclaw_session_key(group_id, actor_id, workspace)))
    return result


def _openclaw_env(env: Optional[Dict[str, str]]) -> Dict[str, str]:
    merged = dict(os.environ)
    if not isinstance(env, dict):
        return merged
    for key, value in env.items():
        normalized = str(key or "").strip()
        if normalized:
            merged[normalized] = str(value)
    return merged


def _resolve_openclaw_process_argv(
    argv: Iterable[str],
    *,
    env: Optional[Dict[str, str]] = None,
) -> list[str]:
    """Resolve OpenClaw without the npm CMD shim when a direct Node entry exists."""
    parts = [str(item) for item in argv if str(item).strip()]
    if not parts:
        return []
    resolved = resolve_subprocess_argv(parts)
    if os.name != "nt" or not resolved:
        return resolved
    command_path = Path(resolved[0]).expanduser()
    if command_path.suffix.lower() not in {".cmd", ".bat"} or command_path.stem.lower() != "openclaw":
        return resolved
    merged_env = _openclaw_env(env)
    node_path = command_path.with_name("node.exe")
    entry_path = command_path.parent / "node_modules" / "openclaw" / "openclaw.mjs"
    if not node_path.is_file():
        resolved_node = shutil.which("node", path=str(merged_env.get("PATH") or ""))
        node_path = Path(resolved_node) if resolved_node else node_path
    if not node_path.is_file() or not entry_path.is_file():
        return resolved
    return [str(node_path.resolve()), str(entry_path.resolve()), *resolved[1:]]


def _run_cli(
    prefix: list[str],
    args: Iterable[str],
    *,
    env: Optional[Dict[str, str]] = None,
    input_text: str = "",
    timeout: float = 20.0,
) -> subprocess.CompletedProcess[str]:
    argv = _resolve_openclaw_process_argv([*prefix, *[str(item) for item in args]], env=env)
    return subprocess.run(
        argv,
        input=input_text or None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=_openclaw_env(env),
        **windowless_subprocess_popen_kwargs(),
    )


def _gateway_popen_kwargs() -> Dict[str, Any]:
    """Keep the managed Gateway background process invisible on Windows."""
    windowless = windowless_subprocess_popen_kwargs()
    return windowless or supervised_process_popen_kwargs()


def _result_error(result: subprocess.CompletedProcess[str]) -> str:
    return str(result.stderr or result.stdout or "").strip()


def _context_lock(root: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(root)))
    with _LOCK:
        lock = _CONTEXT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CONTEXT_LOCKS[key] = lock
        return lock


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_json_output(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        starts = [idx for idx in (raw.find("{"), raw.find("[")) if idx >= 0]
        if not starts:
            return None
        try:
            return json.loads(raw[min(starts) :])
        except json.JSONDecodeError:
            return None


def _expand_context_vars(raw: str, env: Optional[Dict[str, str]]) -> str:
    value = str(raw or "")
    merged = _openclaw_env(env)

    def replace_env(match: re.Match[str]) -> str:
        name = next((item for item in match.groups() if item), "")
        return str(merged.get(name, match.group(0)))

    return _PATH_ENV_REF_RE.sub(replace_env, value)


def _normalize_context_path(raw: str, env: Optional[Dict[str, str]]) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    merged = _openclaw_env(env)
    value = _expand_context_vars(value, env)
    if value.startswith("~"):
        home = str(merged.get("HOME") or merged.get("USERPROFILE") or Path.home()).strip()
        value = str(Path(home) / value[1:].lstrip("/\\"))
    try:
        return os.path.normcase(str(Path(value).expanduser().resolve()))
    except (OSError, RuntimeError):
        return os.path.normcase(os.path.abspath(value))


def _path_is_within(path: str, root: str) -> bool:
    if not str(path or "").strip() or not str(root or "").strip():
        return False
    normalized_path = os.path.normcase(os.path.abspath(str(path or "")))
    normalized_root = os.path.normcase(os.path.abspath(str(root or "")))
    try:
        return os.path.commonpath((normalized_path, normalized_root)) == normalized_root
    except ValueError:
        return False


def _context_cache_key(prefix: Iterable[str], env: Optional[Dict[str, str]]) -> tuple[str, ...]:
    merged = _openclaw_env(env)
    values: list[str] = [str(item) for item in prefix]
    for key in _CONTEXT_ENV_KEYS:
        value = str(merged.get(key) or "").strip()
        if key.endswith("_PATH") or key.endswith("_DIR") or key in {"HOME", "USERPROFILE"}:
            value = _normalize_context_path(value, env)
        values.extend((key, value))
    config_path = str(merged.get("OPENCLAW_CONFIG_PATH") or "").strip()
    if config_path:
        path = Path(_normalize_context_path(config_path, env))
        try:
            stat = path.stat()
            values.extend(("config_mtime_ns", str(stat.st_mtime_ns), "config_size", str(stat.st_size)))
        except OSError:
            values.extend(("config_mtime_ns", "", "config_size", ""))
    source_config = _active_config_path(list(prefix), env=env)
    state_dir = _source_state_dir(env, source_config, profile=_openclaw_profile(prefix, env))
    for relative in (
        Path("agents") / "main" / "agent" / "auth-profiles.json",
        Path("agents") / "main" / "agent" / "openclaw-agent.sqlite",
        Path("credentials"),
        Path("auth"),
    ):
        path = state_dir / relative
        try:
            stat = path.stat()
            values.extend((str(relative), str(stat.st_mtime_ns), str(stat.st_size)))
        except OSError:
            values.extend((str(relative), "", ""))
    values.extend(("environment_fingerprint", _environment_fingerprint(env)))
    return tuple(values)


def _gateway_context_key(prefix: Iterable[str], env: Optional[Dict[str, str]]) -> tuple[str, ...]:
    merged = _openclaw_env(env)
    values: list[str] = [str(item) for item in prefix]
    for key in _CONTEXT_ENV_KEYS:
        value = str(merged.get(key) or "").strip()
        if key.endswith("_PATH") or key.endswith("_DIR") or key in {"HOME", "USERPROFILE"}:
            value = _normalize_context_path(value, env)
        values.extend((key, value))
    return tuple(values)


def _runtime_home(env: Optional[Dict[str, str]]) -> Path:
    source = env if isinstance(env, dict) else {}
    raw = str(source.get("ONECOLLEAGUE_HOME") or source.get("CCCC_HOME") or "").strip()
    if raw:
        home = Path(raw).expanduser().resolve()
        home.mkdir(parents=True, exist_ok=True)
        return home
    return ensure_home()


def _managed_context_id(
    prefix: list[str],
    env: Optional[Dict[str, str]],
    *,
    group_id: str = "",
    actor_id: str = "",
    source_config: Optional[Path] = None,
    source_state: Optional[Path] = None,
) -> str:
    source_config = source_config if source_config is not None else _active_config_path(prefix, env=env)
    source_state = source_state if source_state is not None else _source_state_dir(
        env,
        source_config,
        profile=_openclaw_profile(prefix, env),
    )
    payload = {
        "prefix": prefix,
        "profile": _openclaw_profile(prefix, env),
        "config": str(source_config or ""),
        "state": str(source_state),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _read_or_create_gateway_token(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    token_path = root / "gateway-token"
    for _ in range(20):
        try:
            token = token_path.read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            try:
                token_path.chmod(0o600)
            except OSError:
                pass
            return token

        candidate = secrets.token_urlsafe(32)
        try:
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            time.sleep(0.01)
            continue
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(candidate)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                token_path.chmod(0o600)
            except OSError:
                pass
            return candidate
        except Exception:
            token_path.unlink(missing_ok=True)
            raise
    raise RuntimeError(f"failed to create OpenClaw Gateway token: {token_path}")


def _managed_context(
    prefix: list[str],
    env: Optional[Dict[str, str]],
    *,
    group_id: str,
    actor_id: str,
) -> _ManagedContext:
    agent_id = openclaw_agent_id(group_id, actor_id)
    source_config = _active_config_path(prefix, env=env)
    source_state = _source_state_dir(env, source_config, profile=_openclaw_profile(prefix, env))
    context_id = _managed_context_id(
        prefix,
        env,
        group_id=group_id,
        actor_id=actor_id,
        source_config=source_config,
        source_state=source_state,
    )
    root = _runtime_home(env) / "runtime" / "openclaw" / "contexts" / context_id
    metadata = read_json(root / "context.json")
    requested = str(
        (env or {}).get("ONECOLLEAGUE_OPENCLAW_GATEWAY_PORT")
        or (env or {}).get("CCCC_OPENCLAW_GATEWAY_PORT")
        or ""
    ).strip()
    try:
        persisted_port = int(metadata.get("gateway_port") or 0)
    except (TypeError, ValueError):
        persisted_port = 0
    try:
        requested_port = int(requested or 0)
    except (TypeError, ValueError):
        requested_port = 0
    gateway_token = _read_or_create_gateway_token(root)
    reservation_key = os.path.normcase(os.path.abspath(str(root)))

    def reserved_by_other(port: int) -> bool:
        requested_range = set(range(port, port + 3))
        return any(
            key != reservation_key and requested_range.intersection(range(other_port, other_port + 3))
            for key, other_port in _GATEWAY_PORT_RESERVATIONS.items()
        )

    def owned_by_context(port: int) -> bool:
        return _owned_gateway_pid(
            {
                "OPENCLAW_CONFIG_PATH": str(root / "openclaw.json"),
                "OPENCLAW_GATEWAY_PORT": str(port),
                "OPENCLAW_GATEWAY_TOKEN": gateway_token,
            }
        ) > 0

    def port_available(port: int, *, allow_owned: bool = False) -> bool:
        if reserved_by_other(port):
            return False
        if _ports_available(port, port + 1, port + 2):
            return True
        return allow_owned and owned_by_context(port)

    with _LOCK:
        gateway_port = (
            persisted_port
            if 1024 <= persisted_port <= 65400 and port_available(persisted_port, allow_owned=True)
            else 0
        )
        if gateway_port <= 0 and 1024 <= requested_port <= 65400 and port_available(requested_port):
            gateway_port = requested_port
        if not (1024 <= gateway_port <= 65400):
            slot = int(context_id[:8], 16) % _GATEWAY_PORT_SLOTS
            candidates = [
                _GATEWAY_PORT_BASE + ((slot + offset) % _GATEWAY_PORT_SLOTS) * _GATEWAY_PORT_STRIDE
                for offset in range(_GATEWAY_PORT_SLOTS)
            ]
            gateway_port = next((port for port in candidates if port_available(port)), 0)
        if not (1024 <= gateway_port <= 65400):
            raise RuntimeError("no available OpenClaw Gateway port range")
        _GATEWAY_PORT_RESERVATIONS[reservation_key] = gateway_port
    return _ManagedContext(
        context_id=context_id,
        root=root,
        config_path=root / "openclaw.json",
        state_dir=root / "state",
        gateway_port=gateway_port,
        gateway_token=gateway_token,
        source_config_path=source_config,
        source_state_dir=source_state,
        group_id=str(group_id),
        actor_id=str(actor_id),
        agent_id=agent_id,
    )


def _source_state_dir(
    env: Optional[Dict[str, str]],
    source_config: Optional[Path],
    *,
    profile: str = "",
) -> Path:
    raw = str((env or {}).get("OPENCLAW_STATE_DIR") or "").strip()
    if raw:
        return Path(_normalize_context_path(raw, env))
    merged = _openclaw_env(env)
    home = Path(str(merged.get("HOME") or merged.get("USERPROFILE") or Path.home())).expanduser()
    selected_profile = str(profile or merged.get("OPENCLAW_PROFILE") or "").strip()
    suffix = "" if not selected_profile or selected_profile.lower() == "default" else f"-{selected_profile}"
    return (home / f".openclaw{suffix}").resolve()


def _ports_available(*ports: int) -> bool:
    sockets: list[socket.socket] = []
    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", int(port)))
            sockets.append(sock)
        return True
    except OSError:
        return False
    finally:
        for sock in sockets:
            sock.close()


def _loopback_port_open(port: int, *, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=max(0.05, float(timeout))):
            return True
    except OSError:
        return False


def _managed_environment(source: Optional[Dict[str, str]], context: _ManagedContext) -> Dict[str, str]:
    managed = {str(key): str(value) for key, value in dict(source or {}).items() if str(key).strip()}
    managed.update(
        {
            "OPENCLAW_CONFIG_PATH": str(context.config_path),
            "OPENCLAW_STATE_DIR": str(context.state_dir),
            "OPENCLAW_GATEWAY_PORT": str(context.gateway_port),
            "OPENCLAW_GATEWAY_URL": f"ws://127.0.0.1:{context.gateway_port}",
            "OPENCLAW_GATEWAY_PASSWORD": "",
            "OPENCLAW_GATEWAY_TOKEN": context.gateway_token,
            "OPENCLAW_SKIP_CHANNELS": "1",
        }
    )
    return managed


def _activate_managed_environment(env: Dict[str, str], context: _ManagedContext) -> None:
    env.update(_managed_environment(env, context))


def _active_config_path(
    prefix: list[str],
    *,
    env: Optional[Dict[str, str]],
) -> Optional[Path]:
    configured = str((env or {}).get("OPENCLAW_CONFIG_PATH") or "").strip()
    if configured:
        return Path(_normalize_context_path(configured, env))
    state_dir = _source_state_dir(env, None, profile=_openclaw_profile(prefix, env))
    default_path = state_dir / "openclaw.json"
    # OpenClaw's profile layout is stable and can be resolved without paying
    # for a full CLI cold start. The file may not exist for an unconfigured
    # installation; that is still the correct source context.
    if state_dir:
        return default_path.resolve()
    try:
        result = _run_cli(prefix, ["config", "file"], env=env, timeout=15.0)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in str(result.stdout or "").splitlines():
        candidate = _ANSI_ESCAPE_RE.sub("", line).strip()
        if candidate:
            return Path(_normalize_context_path(candidate, env))
    return None


def _config_json(
    prefix: list[str],
    path: str,
    *,
    env: Optional[Dict[str, str]],
    default: Any,
) -> Any:
    result = _run_cli(prefix, ["config", "get", path, "--json"], env=env, timeout=15.0)
    if result.returncode != 0:
        return default
    parsed = _parse_json_output(result.stdout)
    return default if parsed is None else parsed


def _read_managed_config_strict(env: Optional[Dict[str, str]]) -> Dict[str, Any]:
    config_path = _required_config_path(env)
    if not config_path.exists():
        return {}
    try:
        parsed = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to read managed OpenClaw config: {config_path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"managed OpenClaw config is not an object: {config_path}")
    return dict(parsed)


def _configured_state(config: Dict[str, Any]) -> tuple[list[Dict[str, Any]], Dict[str, Any], list[str]]:
    agents_config = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    raw_agents = agents_config.get("list") if isinstance(agents_config.get("list"), list) else []
    agents = [dict(item) for item in raw_agents if isinstance(item, dict)]
    mcp_config = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
    raw_servers = mcp_config.get("servers") if isinstance(mcp_config.get("servers"), dict) else {}
    servers = dict(raw_servers)
    skills_config = config.get("skills") if isinstance(config.get("skills"), dict) else {}
    load_config = skills_config.get("load") if isinstance(skills_config.get("load"), dict) else {}
    raw_extra_dirs = load_config.get("extraDirs") if isinstance(load_config.get("extraDirs"), list) else []
    extra_dirs = [str(item) for item in raw_extra_dirs if str(item).strip()]
    return agents, servers, extra_dirs


def _configured_agents(prefix: list[str], *, env: Optional[Dict[str, str]]) -> list[Dict[str, Any]]:
    configured = _config_json(prefix, "agents.list", env=env, default=None)
    if isinstance(configured, list):
        return [dict(item) for item in configured if isinstance(item, dict)]

    result = _run_cli(prefix, ["agents", "list", "--json"], env=env, timeout=20.0)
    parsed = _parse_json_output(result.stdout) if result.returncode == 0 else None
    if not isinstance(parsed, list):
        return []
    agents: list[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            continue
        entry: Dict[str, Any] = {
            "id": str(item.get("id") or "").strip(),
            "workspace": str(item.get("workspace") or "").strip(),
            "agentDir": str(item.get("agentDir") or "").strip(),
        }
        if bool(item.get("isDefault")):
            entry["default"] = True
        agents.append({key: value for key, value in entry.items() if value != ""})
    return agents


def _configured_mcp_servers(prefix: list[str], *, env: Optional[Dict[str, str]]) -> Dict[str, Any]:
    raw = _config_json(prefix, "mcp.servers", env=env, default={})
    return dict(raw) if isinstance(raw, dict) else {}


def _configured_extra_skill_dirs(prefix: list[str], *, env: Optional[Dict[str, str]]) -> list[str]:
    raw = _config_json(prefix, "skills.load.extraDirs", env=env, default=[])
    return [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []


def _managed_agent_dir(context: _ManagedContext, agent_id: str = "") -> Path:
    return context.root / "agents" / (str(agent_id).strip() or "unknown")


def _mcp_environment(group_id: str, actor_id: str, env: Optional[Dict[str, str]]) -> Dict[str, str]:
    result = {
        "CCCC_GROUP_ID": str(group_id),
        "ONECOLLEAGUE_GROUP_ID": str(group_id),
        "CCCC_ACTOR_ID": str(actor_id),
        "ONECOLLEAGUE_ACTOR_ID": str(actor_id),
    }
    source = env if isinstance(env, dict) else {}
    home = str(source.get("ONECOLLEAGUE_HOME") or source.get("CCCC_HOME") or ensure_home()).strip()
    if home:
        result["CCCC_HOME"] = home
        result["ONECOLLEAGUE_HOME"] = home
    return result


def _mcp_server_config(
    *,
    group_id: str,
    actor_id: str,
    cwd: Path,
    env: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    command = list(get_onecolleague_mcp_stdio_command())
    if not command:
        raise RuntimeError("OneColleague MCP command is unavailable")
    return {
        "command": command[0],
        "args": command[1:],
        "env": _mcp_environment(group_id, actor_id, env),
        "cwd": str(cwd),
    }


def _managed_server_names(servers: Dict[str, Any], current: str) -> list[str]:
    names = {str(name).strip() for name in servers if _is_managed_mcp_server_name(str(name))}
    names.add(current)
    return sorted(names)


def _reconcile_managed_agent_policies(
    agents: list[Dict[str, Any]],
    managed_servers: list[str],
) -> list[Dict[str, Any]]:
    reconciled: list[Dict[str, Any]] = []
    for raw in agents:
        entry = dict(raw)
        agent_id = str(entry.get("id") or "").strip()
        if not _is_managed_agent_id(agent_id):
            reconciled.append(entry)
            continue
        own_server = agent_id if agent_id.startswith(_MANAGED_AGENT_PREFIX) else f"onecolleague-{agent_id}"
        tools = dict(entry.get("tools") or {}) if isinstance(entry.get("tools"), dict) else {}
        existing_deny = tools.get("deny") if isinstance(tools.get("deny"), list) else []
        preserved = [
            str(item)
            for item in existing_deny
            if str(item).strip()
            and not any(
                str(item).strip().startswith(prefix)
                for prefix in (_MANAGED_MCP_PREFIX, *_LEGACY_MANAGED_MCP_PREFIXES)
            )
        ]
        managed_deny = [f"{name}__*" for name in managed_servers if name != own_server]
        tools["deny"] = list(dict.fromkeys([*preserved, *managed_deny]))
        entry["tools"] = tools
        reconciled.append(entry)
    return reconciled


def _replace_managed_agent(
    agents: list[Dict[str, Any]],
    *,
    agent_id: str,
    actor_id: str,
    cwd: Path,
    agent_dir: Path,
    model: str,
    skills: Optional[list[str]],
) -> list[Dict[str, Any]]:
    existing = next((dict(item) for item in agents if str(item.get("id") or "") == agent_id), {})
    existing.update(
        {
            "id": agent_id,
            "name": str(actor_id),
            "description": f"OneColleague actor {actor_id}",
            "workspace": str(cwd),
            "agentDir": str(agent_dir),
        }
    )
    if model:
        existing["model"] = model
    else:
        existing.pop("model", None)
    if skills is None:
        existing.pop("skills", None)
    else:
        existing["skills"] = skills
    out = [dict(item) for item in agents if str(item.get("id") or "") != agent_id]
    out.append(existing)
    return out


def _patch_config(
    prefix: list[str],
    payload: Dict[str, Any],
    *,
    env: Optional[Dict[str, str]],
) -> None:
    config_path = _required_config_path(env)
    current = read_json(config_path)
    candidate = _deep_merge(current if isinstance(current, dict) else {}, payload)
    _publish_config(prefix, candidate, env=env)


def _publish_config(
    prefix: list[str],
    candidate: Dict[str, Any],
    *,
    env: Optional[Dict[str, str]],
) -> None:
    config_path = _required_config_path(env)
    temp_path = _write_validated_config_candidate(prefix, candidate, env=env)
    previous_raw: Optional[bytes]
    try:
        previous_raw = config_path.read_bytes()
    except OSError:
        previous_raw = None
    os.replace(temp_path, config_path)
    expected_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    if _owned_gateway_pid(env) <= 0:
        return
    try:
        _wait_for_gateway_config_hash(prefix, expected_hash, env=env)
    except Exception:
        rollback_path = config_path.with_name(f".{config_path.name}.{secrets.token_hex(6)}.rollback")
        if previous_raw is None:
            config_path.unlink(missing_ok=True)
        else:
            rollback_path.write_bytes(previous_raw)
            os.replace(rollback_path, config_path)
        if previous_raw is not None and _owned_gateway_pid(env) > 0:
            previous_hash = hashlib.sha256(previous_raw).hexdigest()
            try:
                _wait_for_gateway_config_hash(prefix, previous_hash, env=env, timeout=5.0)
            except Exception:
                pass
        raise


def _required_config_path(env: Optional[Dict[str, str]]) -> Path:
    raw = str((env or {}).get("OPENCLAW_CONFIG_PATH") or "").strip()
    if not raw:
        raise ValueError("OPENCLAW_CONFIG_PATH is required for the managed OpenClaw runtime")
    normalized = _normalize_context_path(raw, env)
    if not normalized:
        raise ValueError("OPENCLAW_CONFIG_PATH is empty after path normalization")
    return Path(normalized)


def _wait_for_gateway_config_hash(
    prefix: list[str],
    expected_hash: str,
    *,
    env: Optional[Dict[str, str]],
    timeout: float = 10.0,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            snapshot = _gateway_rpc(
                prefix,
                "config.get",
                {},
                env=env,
                timeout=min(3.0, timeout),
            )
            if isinstance(snapshot, dict) and str(snapshot.get("hash") or "") == expected_hash:
                return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.2)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"OpenClaw Gateway did not load the published config{detail}")


def _write_validated_config_candidate(
    prefix: list[str],
    candidate: Dict[str, Any],
    *,
    env: Optional[Dict[str, str]],
) -> Path:
    temp_path = _write_config_candidate(candidate, env=env)
    validate_env = dict(env or {})
    validate_env["OPENCLAW_CONFIG_PATH"] = str(temp_path)
    result = _run_cli(prefix, ["config", "validate", "--json"], env=validate_env, timeout=30.0)
    if result.returncode != 0:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"failed to validate OpenClaw config: {_result_error(result)}")
    return temp_path


def _write_config_candidate(
    candidate: Dict[str, Any],
    *,
    env: Optional[Dict[str, str]],
) -> Path:
    config_path = Path(str((env or {}).get("OPENCLAW_CONFIG_PATH") or "")).resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = config_path.with_name(f".{config_path.name}.{secrets.token_hex(6)}.tmp")
    atomic_write_json(temp_path, candidate, indent=2)
    return temp_path


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _include_deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, list) and isinstance(patch, list):
        return [*base, *patch]
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            if key in _BLOCKED_CONFIG_OBJECT_KEYS:
                continue
            merged[key] = _include_deep_merge(merged[key], value) if key in merged else value
        return merged
    return patch


_PROJECTED_CONFIG_KEYS = ("env", "models", "auth", "secrets", "plugins", "skills", "mcp", "tools", "commands")
_PROJECTED_PATH_PATTERNS = {
    ("mcp", "servers", "*", "clientcert"),
    ("mcp", "servers", "*", "client_cert"),
    ("mcp", "servers", "*", "clientkey"),
    ("mcp", "servers", "*", "client_key"),
    ("mcp", "servers", "*", "cwd"),
    ("mcp", "servers", "*", "workingdirectory"),
    ("plugins", "load", "paths"),
    ("skills", "load", "allowsymlinktargets"),
    ("skills", "load", "extradirs"),
}
_DEFAULTS_EXCLUDED_KEYS = {
    "workspace",
    "agentDir",
    "heartbeat",
    "channels",
    "bindings",
    "broadcast",
    "cron",
    "hooks",
    "webhooks",
    "nodes",
}


def _resolve_projected_paths(
    value: Any,
    source_dir: Path,
    *,
    env: Optional[Dict[str, str]],
    path: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _resolve_projected_paths(v, source_dir, env=env, path=(*path, str(k).lower()))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_resolve_projected_paths(item, source_dir, env=env, path=path) for item in value]
    matches_path = any(
        len(pattern) == len(path)
        and all(expected == "*" or expected == actual for expected, actual in zip(pattern, path))
        for pattern in _PROJECTED_PATH_PATTERNS
    )
    if isinstance(value, str) and value and matches_path:
        expanded = _expand_context_vars(value, env)
        if _PATH_ENV_REF_RE.search(expanded):
            return value
        if value.startswith("~") or os.path.isabs(expanded):
            return _normalize_context_path(expanded, env)
        return str((source_dir / expanded).resolve())
    return value


def _source_config_signature(source_config: Optional[Path]) -> tuple[str, int, int]:
    if source_config is None:
        return ("", 0, 0)
    try:
        stat = source_config.stat()
    except OSError:
        return (str(source_config.resolve()), 0, 0)
    return (str(source_config.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _include_roots(source_config: Path, env: Optional[Dict[str, str]]) -> list[Path]:
    roots = [source_config.parent.resolve()]
    raw = str((env or {}).get("OPENCLAW_INCLUDE_ROOTS") or "").strip()
    for item in raw.split(os.pathsep) if raw else []:
        normalized = _normalize_context_path(item, env)
        if normalized:
            roots.append(Path(normalized).resolve())
    return roots


def _resolve_source_include_path(raw: str, *, including: Path, roots: list[Path]) -> Path:
    value = str(raw or "")
    if not value or "\0" in value or len(value) >= 4096:
        raise RuntimeError(f"invalid OpenClaw config include path in {including}: {raw!r}")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = including.parent / candidate
    resolved = candidate.resolve()
    if len(str(resolved)) >= 4096 or not any(_path_is_within(str(resolved), str(root)) for root in roots):
        raise RuntimeError(f"OpenClaw config include escapes the allowed roots: {resolved}")
    if not resolved.is_file():
        raise RuntimeError(f"OpenClaw config include does not exist: {resolved}")
    return resolved


def _load_json5_with_includes(
    path: Path,
    *,
    roots: list[Path],
    stack: tuple[Path, ...] = (),
    depth: int = 0,
) -> Any:
    resolved = path.resolve()
    if depth > 10:
        raise RuntimeError(f"OpenClaw config include depth exceeds 10 at {resolved}")
    if resolved in stack:
        chain = " -> ".join(str(item) for item in (*stack, resolved))
        raise RuntimeError(f"circular OpenClaw config include: {chain}")
    try:
        if stack:
            stat = resolved.stat()
            if stat.st_size > _MAX_INCLUDE_FILE_BYTES or int(getattr(stat, "st_nlink", 1)) > 1:
                raise RuntimeError(
                    "OpenClaw config include failed security checks "
                    f"(regular file, max {_MAX_INCLUDE_FILE_BYTES} bytes, no hardlinks): {resolved}"
                )
        parsed = json5.loads(resolved.read_text(encoding="utf-8"))
    except RuntimeError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"failed to parse OpenClaw source config: {resolved}: {exc}") from exc

    def expand(value: Any, current: Path, current_stack: tuple[Path, ...], current_depth: int) -> Any:
        if isinstance(value, list):
            return [expand(item, current, current_stack, current_depth) for item in value]
        if not isinstance(value, dict):
            return value
        has_include = "$include" in value
        include = value.get("$include")
        siblings = {str(key): item for key, item in value.items() if str(key) != "$include"}
        if not has_include:
            return {
                key: expand(item, current, current_stack, current_depth)
                for key, item in siblings.items()
            }
        specs = include if isinstance(include, list) else [include]
        if not all(isinstance(item, str) for item in specs):
            raise RuntimeError(f"invalid $include in OpenClaw config: {current}")
        merged: Any = {}
        for spec in specs:
            include_path = _resolve_source_include_path(spec, including=current, roots=roots)
            loaded = _load_json5_with_includes(
                include_path,
                roots=roots,
                stack=current_stack,
                depth=current_depth + 1,
            )
            merged = _include_deep_merge(merged, loaded)
        expanded_siblings = {
            key: expand(item, current, current_stack, current_depth)
            for key, item in siblings.items()
        }
        if not expanded_siblings:
            return merged
        if not isinstance(merged, dict):
            raise RuntimeError(f"OpenClaw config include with sibling keys must contain an object: {current}")
        return _include_deep_merge(merged, expanded_siblings)

    return expand(parsed, resolved, (*stack, resolved), depth)


def _load_source_config(
    prefix: list[str],
    *,
    source_config: Optional[Path],
    env: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    if source_config is None or not source_config.is_file():
        return {}
    parsed = _load_json5_with_includes(
        source_config,
        roots=_include_roots(source_config, env),
    )
    if not isinstance(parsed, dict):
        raise RuntimeError(f"OpenClaw source config is not an object: {source_config}")
    return dict(parsed)


def _project_source_config(
    raw: Dict[str, Any],
    *,
    source_dir: Path,
    env: Optional[Dict[str, str]],
) -> Dict[str, Any]:
    projected = {key: raw[key] for key in _PROJECTED_CONFIG_KEYS if key in raw}
    agents = raw.get("agents") if isinstance(raw.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    cleaned_defaults = {key: value for key, value in defaults.items() if key not in _DEFAULTS_EXCLUDED_KEYS}
    if cleaned_defaults:
        projected["agents"] = {"defaults": cleaned_defaults}
    return _resolve_projected_paths(projected, source_dir, env=env)


def _environment_fingerprint(
    env: Optional[Dict[str, str]],
    *,
    referenced_keys: Iterable[str] = (),
) -> str:
    relevant = {
        OPENCODE_API_KEY_ENV,
        "ONECOLLEAGUE_OPENCODE_BASE_URL",
        *(str(key).strip() for key in referenced_keys if str(key).strip()),
        *(
            str(key)
            for key in _openclaw_env(env)
            if str(key).startswith("OPENCLAW_")
            and str(key)
            not in {
                "OPENCLAW_CONFIG_PATH",
                "OPENCLAW_STATE_DIR",
                "OPENCLAW_GATEWAY_PORT",
                "OPENCLAW_GATEWAY_URL",
                "OPENCLAW_GATEWAY_PASSWORD",
                "OPENCLAW_GATEWAY_TOKEN",
                "OPENCLAW_SELECTED_MODEL",
            }
        ),
    }
    projected = {
        str(key): str(value)
        for key, value in _openclaw_env(env).items()
        if str(key) in relevant
    }
    return hashlib.sha256(
        json.dumps(projected, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _config_environment_keys(config: Dict[str, Any]) -> set[str]:
    serialized = json.dumps(config, ensure_ascii=True, separators=(",", ":"))
    return set(_CONFIG_ENV_REF_RE.findall(serialized))


def _onecolleague_model_id(value: Any) -> str:
    model = str(value or "").strip()
    provider_prefix = f"{OPENCODE_PROVIDER_ID}/"
    if model.lower().startswith(provider_prefix.lower()):
        model = model[len(provider_prefix) :].strip()
    return model


def _managed_openclaw_model_ids(
    env: Optional[Dict[str, str]],
    *,
    selected_model: str = "",
    catalog: Optional[Iterable[Dict[str, Any]]] = None,
) -> list[str]:
    rows = list(catalog) if catalog is not None else load_opencode_model_catalog(env)
    models: list[str] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        model = _onecolleague_model_id(item.get("model"))
        key = model.casefold()
        if not model or key in seen:
            continue
        seen.add(key)
        models.append(model)
    selected = _onecolleague_model_id(selected_model)
    if selected and selected.casefold() not in seen:
        models.append(selected)
    return models


def _merge_managed_openclaw_provider(
    config: Dict[str, Any],
    *,
    env: Optional[Dict[str, str]],
    selected_model: str = "",
    model_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    result = dict(config or {})
    models_config = result.get("models")
    models_config = dict(models_config) if isinstance(models_config, dict) else {}
    providers = models_config.get("providers")
    providers = dict(providers) if isinstance(providers, dict) else {}
    catalog_ids = (
        _managed_openclaw_model_ids(env, selected_model=selected_model)
        if model_ids is None
        else _managed_openclaw_model_ids(
            env,
            selected_model=selected_model,
            catalog=({"model": model_id} for model_id in model_ids),
        )
    )
    providers[OPENCODE_PROVIDER_ID] = {
        "baseUrl": opencode_base_url(env),
        "api": "openai-completions",
        "apiKey": {
            "source": "env",
            "provider": "default",
            "id": OPENCODE_API_KEY_ENV,
        },
        "models": [
            {
                "id": model_id,
                "name": model_id,
                "input": ["text"],
            }
            for model_id in catalog_ids
        ],
    }
    models_config.update({"mode": "merge", "providers": providers})
    result["models"] = models_config
    return result


def _initialize_managed_context(
    prefix: list[str],
    *,
    source_env: Dict[str, str],
    context: _ManagedContext,
) -> Dict[str, str]:
    managed_env = _managed_environment(source_env, context)
    source_config = context.source_config_path
    context.root.mkdir(parents=True, exist_ok=True)
    context.state_dir.mkdir(parents=True, exist_ok=True)
    source_raw = _load_source_config(prefix, source_config=source_config, env=source_env)
    environment_fingerprint = _environment_fingerprint(
        source_env,
        referenced_keys=_config_environment_keys(source_raw),
    )
    managed_model_ids = _managed_openclaw_model_ids(source_env)
    model_catalog_fingerprint = hashlib.sha256(
        json.dumps(managed_model_ids, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    source_digest = hashlib.sha256(
        json.dumps(source_raw, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    signature: tuple[Any, ...] = (
        *_source_config_signature(source_config),
        source_digest,
        environment_fingerprint,
        model_catalog_fingerprint,
    )
    metadata = read_json(context.root / "context.json")
    previous_environment = str(metadata.get("environment_fingerprint") or "")
    if previous_environment and previous_environment != environment_fingerprint:
        _terminate_owned_gateway(managed_env)
    initialization_key = os.path.normcase(os.path.abspath(str(context.root)))
    if _INITIALIZED_CONTEXTS.get(initialization_key) == signature and context.config_path.is_file():
        return managed_env
    persisted_matches = (
        context.config_path.is_file()
        and str(metadata.get("source_digest") or "") == source_digest
        and str(metadata.get("environment_fingerprint") or "") == environment_fingerprint
        and str(metadata.get("model_catalog_fingerprint") or "") == model_catalog_fingerprint
    )
    if persisted_matches:
        _INITIALIZED_CONTEXTS[initialization_key] = signature
        return managed_env

    source_dir = source_config.parent if source_config is not None else (
        context.source_state_dir
        or _source_state_dir(source_env, source_config, profile=_openclaw_profile(prefix, source_env))
    )
    payload: Dict[str, Any] = _project_source_config(source_raw, source_dir=source_dir, env=source_env)
    payload = _merge_managed_openclaw_provider(
        payload,
        env=source_env,
        model_ids=managed_model_ids,
    )
    source_state_dir = context.source_state_dir or context.state_dir
    previous = read_json(context.config_path)
    previous_agents = (
        previous.get("agents", {}).get("list", [])
        if isinstance(previous.get("agents"), dict)
        else []
    )
    managed_agents = [
        dict(item)
        for item in previous_agents
        if isinstance(item, dict) and _is_managed_agent_id(str(item.get("id") or ""))
    ]
    source_auth_agent = {
        "id": "main",
        "default": True,
        "agentDir": str(source_state_dir / "agents" / "main" / "agent"),
    }
    previous_servers = (
        previous.get("mcp", {}).get("servers", {})
        if isinstance(previous.get("mcp"), dict)
        else {}
    )
    managed_servers = {
        str(name): value
        for name, value in previous_servers.items()
        if _is_managed_mcp_server_name(str(name))
    } if isinstance(previous_servers, dict) else {}
    previous_extra_dirs = (
        previous.get("skills", {}).get("load", {}).get("extraDirs", [])
        if isinstance(previous.get("skills"), dict)
        and isinstance(previous.get("skills", {}).get("load"), dict)
        else []
    )
    managed_skill_root = _runtime_home(source_env) / "runtime" / "openclaw" / "skills" / "actors"
    managed_extra_dirs = [
        str(item)
        for item in previous_extra_dirs
        if str(item).strip() and _path_is_within(str(item), str(managed_skill_root))
    ] if isinstance(previous_extra_dirs, list) else []
    projected_defaults = dict((payload.get("agents") or {}).get("defaults") or {})
    projected_defaults.update({"skipBootstrap": True, "heartbeat": {"every": "0m"}})
    payload.update({
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "port": context.gateway_port,
            "auth": {
                "mode": "token",
                "token": "${OPENCLAW_GATEWAY_TOKEN}",
            },
            "tailscale": {"mode": "off"},
        },
        "agents": {
            "defaults": projected_defaults,
            "list": [source_auth_agent, *managed_agents],
        },
        "bindings": [],
        "cron": {"enabled": False},
        "hooks": {"enabled": False},
    })
    projected_servers = (
        payload.get("mcp", {}).get("servers", {})
        if isinstance(payload.get("mcp"), dict)
        else {}
    )
    if managed_servers:
        payload.setdefault("mcp", {})["servers"] = {
            **(projected_servers if isinstance(projected_servers, dict) else {}),
            **managed_servers,
        }
    projected_extra_dirs = (
        payload.get("skills", {}).get("load", {}).get("extraDirs", [])
        if isinstance(payload.get("skills"), dict)
        and isinstance(payload.get("skills", {}).get("load"), dict)
        else []
    )
    source_skill_root = source_state_dir / "skills"
    combined_extra_dirs = [
        *(projected_extra_dirs if isinstance(projected_extra_dirs, list) else []),
        *([str(source_skill_root.resolve())] if source_skill_root.is_dir() else []),
        *managed_extra_dirs,
    ]
    if combined_extra_dirs:
        unique_extra_dirs: list[str] = []
        seen_extra_dirs: set[str] = set()
        for item in combined_extra_dirs:
            value = str(item or "").strip()
            normalized = os.path.normcase(os.path.abspath(value)) if value else ""
            if not normalized or normalized in seen_extra_dirs:
                continue
            seen_extra_dirs.add(normalized)
            unique_extra_dirs.append(value)
        payload.setdefault("skills", {}).setdefault("load", {})["extraDirs"] = unique_extra_dirs
    _publish_config(prefix, payload, env=managed_env)
    atomic_write_json(
        context.root / "context.json",
        {
            "v": 2,
            "context_id": context.context_id,
            "group_id": context.group_id,
            "actor_id": context.actor_id,
            "agent_id": context.agent_id,
            "gateway_port": context.gateway_port,
            "command": prefix,
            "profile": _openclaw_profile(prefix, source_env),
            "source_config": str(source_config or ""),
            "source_state": str(source_state_dir),
            "source_digest": source_digest,
            "model_catalog_fingerprint": model_catalog_fingerprint,
            "state_dir": str(context.state_dir),
            "environment_fingerprint": environment_fingerprint,
        },
        indent=2,
    )
    _INITIALIZED_CONTEXTS[initialization_key] = signature
    return managed_env


def _list_openclaw_skill_names(
    prefix: list[str],
    *,
    agent_id: str,
    env: Optional[Dict[str, str]],
) -> Optional[list[str]]:
    result = _run_cli(prefix, ["skills", "list", "--agent", agent_id, "--json"], env=env, timeout=30.0)
    if result.returncode != 0:
        return None
    parsed = _parse_json_output(result.stdout)
    rows = parsed.get("skills") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return None
    return sorted(
        {
            str(item.get("name") or "").strip()
            for item in rows
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
    )


def _list_candidate_skill_names(
    prefix: list[str],
    candidate: Dict[str, Any],
    *,
    agent_id: str,
    env: Optional[Dict[str, str]],
) -> list[str]:
    temp_path = _write_config_candidate(candidate, env=env)
    candidate_env = dict(env or {})
    candidate_env["OPENCLAW_CONFIG_PATH"] = str(temp_path)
    try:
        names = _list_openclaw_skill_names(prefix, agent_id=agent_id, env=candidate_env)
    finally:
        temp_path.unlink(missing_ok=True)
    if names is None:
        raise RuntimeError("failed to enumerate OpenClaw skills for the managed actor")
    return names


def _skill_sources_fingerprint(extra_dirs: Iterable[str]) -> str:
    entries: list[tuple[str, str]] = []
    for raw_root in sorted({str(item).strip() for item in extra_dirs if str(item).strip()}):
        root = Path(raw_root).expanduser()
        if not root.exists():
            entries.append((os.path.normcase(os.path.abspath(str(root))), "missing"))
            continue
        candidates = [root] if root.is_file() and root.name.lower() == "skill.md" else sorted(root.rglob("SKILL.md"))
        for path in candidates:
            try:
                normalized = os.path.normcase(os.path.abspath(str(path)))
                entries.append((normalized, _file_sha256(path)))
            except OSError as exc:
                raise RuntimeError(f"failed to fingerprint OpenClaw skill source: {path}: {exc}") from exc
    return _sha256_json(entries)


def _managed_skill_names_from_dirs(extra_dirs: Iterable[str], managed_root: Path) -> set[str]:
    names: set[str] = set()
    for raw in extra_dirs:
        path = Path(str(raw or "")).expanduser()
        if not _path_is_within(str(path), str(managed_root)):
            continue
        metadata = read_json(path.parent / "managed-skills.json")
        skills = metadata.get("skills") if isinstance(metadata.get("skills"), dict) else {}
        for row in skills.values():
            if isinstance(row, dict) and str(row.get("name") or "").strip():
                names.add(str(row.get("name") or "").strip())
    return names


def _openclaw_install_signature(prefix: list[str], env: Optional[Dict[str, str]]) -> str:
    resolved = _resolve_openclaw_process_argv(prefix, env=env)
    paths: list[Path] = []
    for item in resolved[:2]:
        path = Path(str(item)).expanduser()
        if path.is_file():
            paths.append(path)
    if len(resolved) >= 2 and Path(resolved[1]).name.lower() == "openclaw.mjs":
        package_json = Path(resolved[1]).parent / "package.json"
        if package_json.is_file():
            paths.append(package_json)
    signature: list[tuple[str, int, int]] = []
    for path in paths:
        stat = path.stat()
        signature.append((os.path.normcase(os.path.abspath(str(path))), int(stat.st_mtime_ns), int(stat.st_size)))
    return _sha256_json(signature or resolved)


def _gateway_ready(prefix: list[str], *, env: Optional[Dict[str, str]]) -> bool:
    try:
        _gateway_rpc(prefix, "health", {}, env=env, timeout=3.0)
    except Exception:
        return False
    return True


def _openclaw_gateway_rpc_runtime(
    prefix: list[str],
    env: Optional[Dict[str, str]],
) -> tuple[str, Path]:
    merged_env = _openclaw_env(env)
    explicit_sdk = str(merged_env.get("OPENCLAW_PLUGIN_SDK_PATH") or "").strip()
    explicit_root = str(merged_env.get("OPENCLAW_PACKAGE_ROOT") or "").strip()
    command = str(prefix[0] if prefix else "openclaw").strip() or "openclaw"
    resolved_command = shutil.which(command, path=str(merged_env.get("PATH") or ""))
    if not resolved_command:
        resolved_command = resolve_subprocess_argv([command])[0]

    package_roots: list[Path] = []
    sdk_candidates: list[Path] = []
    if explicit_sdk:
        sdk_candidates.append(Path(explicit_sdk).expanduser())
    if explicit_root:
        package_roots.append(Path(explicit_root).expanduser())

    command_path = Path(resolved_command).expanduser()
    try:
        command_path = command_path.resolve()
    except OSError:
        pass
    if command_path.name.lower() == "openclaw.mjs":
        package_roots.append(command_path.parent)
    package_roots.extend(
        [
            command_path.parent / "node_modules" / "openclaw",
            command_path.parent / "lib" / "node_modules" / "openclaw",
        ]
    )
    for parent in list(command_path.parents)[:5]:
        if parent.name.lower() == "openclaw":
            package_roots.append(parent)
        package_roots.append(parent / "node_modules" / "openclaw")

    seen: set[str] = set()
    for root in package_roots:
        normalized = os.path.normcase(os.path.abspath(str(root)))
        if normalized in seen:
            continue
        seen.add(normalized)
        sdk_candidates.append(root / "dist" / "plugin-sdk" / "gateway-runtime.js")

    sdk_path = next((candidate for candidate in sdk_candidates if candidate.is_file()), None)
    if sdk_path is None:
        raise RuntimeError(
            "OpenClaw Plugin SDK gateway runtime was not found; update OpenClaw or set "
            "OPENCLAW_PACKAGE_ROOT"
        )

    explicit_node = str(merged_env.get("OPENCLAW_NODE_PATH") or "").strip()
    node_candidates = [Path(explicit_node).expanduser()] if explicit_node else []
    node_candidates.extend((command_path.parent / "node.exe", command_path.parent / "node"))
    node_path = next((candidate for candidate in node_candidates if candidate.is_file()), None)
    if node_path is None:
        resolved_node = shutil.which("node", path=str(merged_env.get("PATH") or ""))
        if not resolved_node:
            raise RuntimeError("Node.js is required to call the OpenClaw Plugin SDK")
        node_path = Path(resolved_node)
    return str(node_path), sdk_path.resolve()


def _gateway_rpc(
    prefix: list[str],
    method: str,
    params: Dict[str, Any],
    *,
    env: Optional[Dict[str, str]],
    timeout: float = 20.0,
) -> Any:
    port = int(str((env or {}).get("OPENCLAW_GATEWAY_PORT") or 0))
    token = str((env or {}).get("OPENCLAW_GATEWAY_TOKEN") or "")
    if port <= 0 or not token:
        raise RuntimeError("OpenClaw Gateway connection is missing port or token")
    url = str((env or {}).get("OPENCLAW_GATEWAY_URL") or f"ws://127.0.0.1:{port}").strip()
    node_path, sdk_path = _openclaw_gateway_rpc_runtime(prefix, env)
    timeout_ms = max(1, int(timeout * 1000))
    request = {
        "sdkPath": str(sdk_path),
        "method": str(method),
        "params": dict(params),
        "url": url,
        "token": token,
        "timeoutMs": timeout_ms,
    }
    result = subprocess.run(
        [node_path, "--input-type=module", "--eval", _GATEWAY_RPC_SCRIPT],
        input=json.dumps(request, ensure_ascii=True, separators=(",", ":")),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(timeout + 5.0, 10.0),
        check=False,
        env=_openclaw_env(env),
        **windowless_subprocess_popen_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(_result_error(result) or f"OpenClaw Gateway call failed: {method}")
    parsed = _parse_json_output(result.stdout)
    if parsed is None and str(result.stdout or "").strip():
        raise RuntimeError(f"OpenClaw Gateway returned invalid JSON for {method}")
    return parsed


def _process_created_at(pid: int) -> float:
    try:
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return 0.0


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _ownership_path(env: Optional[Dict[str, str]]) -> Path:
    config_path = _required_config_path(env)
    return config_path.parent / "gateway-ownership.json"


def _owned_gateway_pid(env: Optional[Dict[str, str]]) -> int:
    ownership = read_json(_ownership_path(env))
    pid = int(ownership.get("pid") or 0)
    if pid <= 0 or not pid_is_alive(pid):
        return 0
    expected_config = _normalize_context_path(str((env or {}).get("OPENCLAW_CONFIG_PATH") or ""), env)
    owned_config = _normalize_context_path(str(ownership.get("config_path") or ""), env)
    if not expected_config or owned_config != expected_config:
        return 0
    if str(ownership.get("token_fingerprint") or "") != _token_fingerprint(str((env or {}).get("OPENCLAW_GATEWAY_TOKEN") or "")):
        return 0
    if int(ownership.get("port") or 0) != int(str((env or {}).get("OPENCLAW_GATEWAY_PORT") or 0)):
        return 0
    expected_created = float(ownership.get("process_created_at") or 0.0)
    actual_created = _process_created_at(pid)
    if expected_created and actual_created and abs(expected_created - actual_created) > 0.01:
        return 0
    return pid


def _owned_gateway_ready(prefix: list[str], *, env: Optional[Dict[str, str]]) -> bool:
    return _owned_gateway_pid(env) > 0 and _gateway_ready(prefix, env=env)


def _terminate_owned_gateway(env: Optional[Dict[str, str]]) -> None:
    ownership_path = _ownership_path(env)
    pid = _owned_gateway_pid(env)
    if pid > 0:
        terminate_pid(pid, timeout_s=5.0, include_group=True, force=True)
    ownership_path.unlink(missing_ok=True)


def _discard_managed_gateway_context(context_root: Path) -> None:
    managed_env = _managed_env_from_context_root(context_root)
    key = _gateway_context_key(_context_command(context_root), managed_env)
    normalized_root = os.path.normcase(os.path.abspath(str(context_root)))
    with _LOCK:
        timer = _GATEWAY_IDLE_TIMERS.pop(key, None)
        process = _GATEWAY_PROCESSES.pop(key, None)
        actor_keys = [actor_key for actor_key, current in _GATEWAY_ACTORS.items() if current == key]
        for actor_key in actor_keys:
            _GATEWAY_ACTORS.pop(actor_key, None)
            _GATEWAY_ACTOR_ENVS.pop(actor_key, None)
        _INITIALIZED_CONTEXTS.pop(normalized_root, None)
        _GATEWAY_PORT_RESERVATIONS.pop(normalized_root, None)
        _CONTEXT_LOCKS.pop(normalized_root, None)
    if timer is not None:
        timer.cancel()
    if process is not None and process.poll() is None:
        _terminate_gateway_process(process)
    _terminate_owned_gateway(managed_env)


def _managed_env_from_context_root(root: Path) -> Dict[str, str]:
    token_path = root / "gateway-token"
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    metadata = read_json(root / "context.json")
    port = str(metadata.get("gateway_port") or "")
    result = {
        "OPENCLAW_CONFIG_PATH": str(root / "openclaw.json"),
        "OPENCLAW_GATEWAY_PORT": port,
        "OPENCLAW_GATEWAY_URL": f"ws://127.0.0.1:{port}",
        "OPENCLAW_GATEWAY_TOKEN": token,
    }
    state_dir = str(metadata.get("state_dir") or root / "state").strip()
    result["OPENCLAW_STATE_DIR"] = state_dir
    profile = str(metadata.get("profile") or "").strip()
    if profile:
        result["OPENCLAW_PROFILE"] = profile
    return result


def _context_command(root: Path) -> list[str]:
    raw = read_json(root / "context.json").get("command")
    if isinstance(raw, list):
        command = [str(item) for item in raw if str(item).strip()]
        if command:
            return command
    return ["openclaw"]


def refresh_openclaw_actor_skill_projection(group_id: str, actor_id: str) -> Dict[str, Any]:
    """Publish an already-materialized actor Skill overlay to a live Gateway."""
    key = (str(group_id or "").strip(), str(actor_id or "").strip())
    with _LOCK:
        env = dict(_GATEWAY_ACTOR_ENVS.get(key) or {})
    config_raw = str(env.get("OPENCLAW_CONFIG_PATH") or "").strip()
    if not config_raw:
        return {"refreshed": False, "reason": "actor_gateway_not_running"}
    config_path = Path(config_raw)
    root = config_path.parent
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise RuntimeError("managed OpenClaw config is invalid")
    from .ops.capability_ops import prepare_openclaw_skill_package_overlay_for_actor
    from ..kernel.group import load_group

    group = load_group(group_id)
    if group is None:
        raise RuntimeError("group not found")
    projection = prepare_openclaw_skill_package_overlay_for_actor(group, actor_id)
    agent_id = openclaw_agent_id(group_id, actor_id)
    agents_cfg = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    agents = agents_cfg.get("list") if isinstance(agents_cfg.get("list"), list) else []
    selected = sorted({str(item).strip() for item in projection.get("selected_names", []) if str(item).strip()})
    next_agents = []
    found = False
    for item in agents:
        if not isinstance(item, dict):
            next_agents.append(item)
            continue
        row = dict(item)
        if str(row.get("id") or "").strip() == agent_id:
            found = True
            existing_skills = row.get("skills") if isinstance(row.get("skills"), list) else []
            base_skills = [
                str(skill).strip()
                for skill in existing_skills
                if str(skill).strip() and not str(skill).strip().startswith("onecolleague-")
            ]
            row["skills"] = sorted(set(base_skills) | set(selected))
        next_agents.append(row)
    if not found:
        return {"refreshed": False, "reason": "agent_not_found"}
    skills_cfg = config.get("skills") if isinstance(config.get("skills"), dict) else {}
    load_cfg = dict(skills_cfg.get("load") or {}) if isinstance(skills_cfg.get("load"), dict) else {}
    extra_dirs = [str(item) for item in load_cfg.get("extraDirs", []) if str(item).strip()]
    managed_root = str(projection.get("managed_root") or "").strip()
    if managed_root:
        actor_overlay_id = hashlib.sha256(f"{key[0]}\0{key[1]}".encode("utf-8")).hexdigest()[:16]
        actor_overlay_root = str(Path(managed_root) / actor_overlay_id)
        extra_dirs = [item for item in extra_dirs if not _path_is_within(item, actor_overlay_root)]
    root_path = str(projection.get("root") or "").strip()
    if root_path and root_path not in extra_dirs:
        extra_dirs.append(root_path)
    load_cfg["extraDirs"] = extra_dirs
    load_cfg["watch"] = True
    candidate = _deep_merge(config, {"agents": {"list": next_agents}, "skills": {"load": load_cfg}})
    _publish_config(_context_command(root), candidate, env=env)
    return {"refreshed": True, "selected_names": selected, "root": root_path}


def _terminate_owned_gateways_under(root: Path) -> None:
    if not root.is_dir():
        return
    for ownership_path in root.rglob("gateway-ownership.json"):
        context_root = ownership_path.parent
        env = _managed_env_from_context_root(context_root)
        config_path = Path(env["OPENCLAW_CONFIG_PATH"])
        if config_path.parent != context_root or not _path_is_within(str(context_root), str(root)):
            continue
        _terminate_owned_gateway(env)


def _terminate_gateway_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    pid = int(getattr(process, "pid", 0) or 0)
    if pid > 0:
        terminate_pid(pid, timeout_s=5.0, include_group=True, force=True)
    try:
        process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            return
        try:
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _start_gateway(
    prefix: list[str],
    *,
    env: Optional[Dict[str, str]],
    cwd: Path,
    group_id: str,
    actor_id: str,
    authenticated_ready: bool = False,
) -> None:
    key = _gateway_context_key(prefix, env)
    actor_key = (str(group_id), str(actor_id))
    with _LOCK:
        idle_timer = _GATEWAY_IDLE_TIMERS.pop(key, None)
    if idle_timer is not None:
        idle_timer.cancel()
    if authenticated_ready and _owned_gateway_pid(env) > 0:
        _GATEWAY_ACTORS[actor_key] = key
        _GATEWAY_ACTOR_ENVS[actor_key] = dict(env or {})
        return
    if _owned_gateway_ready(prefix, env=env):
        _GATEWAY_ACTORS[actor_key] = key
        _GATEWAY_ACTOR_ENVS[actor_key] = dict(env or {})
        return
    process = _GATEWAY_PROCESSES.get(key)
    if process is None or process.poll() is not None:
        log_dir = _runtime_home(env) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        suffix = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()[:10]
        log_path = log_dir / f"openclaw-gateway-{suffix}.log"
        log_file = log_path.open("ab")
        try:
            process = subprocess.Popen(
                _resolve_openclaw_process_argv([
                    *prefix,
                    "gateway",
                    "run",
                    "--allow-unconfigured",
                    "--bind",
                    "loopback",
                    "--auth",
                    "token",
                    "--port",
                    str((env or {}).get("OPENCLAW_GATEWAY_PORT") or ""),
                ], env=env),
                cwd=str(cwd),
                env=_openclaw_env(env),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                **_gateway_popen_kwargs(),
            )
        finally:
            log_file.close()
        _GATEWAY_PROCESSES[key] = process
        atomic_write_json(
            _ownership_path(env),
            {
                "v": 1,
                "pid": int(process.pid),
                "process_created_at": _process_created_at(int(process.pid)) or time.time(),
                "port": int(str((env or {}).get("OPENCLAW_GATEWAY_PORT") or 0)),
                "config_path": str((env or {}).get("OPENCLAW_CONFIG_PATH") or ""),
                "token_fingerprint": _token_fingerprint(str((env or {}).get("OPENCLAW_GATEWAY_TOKEN") or "")),
            },
            indent=2,
        )
    _GATEWAY_ACTORS[actor_key] = key
    _GATEWAY_ACTOR_ENVS[actor_key] = dict(env or {})

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        port = int(str((env or {}).get("OPENCLAW_GATEWAY_PORT") or 0))
        if port > 0 and _loopback_port_open(port):
            if _owned_gateway_ready(prefix, env=env):
                return
        time.sleep(0.2)
    if _GATEWAY_PROCESSES.get(key) is process:
        _GATEWAY_PROCESSES.pop(key, None)
    if _GATEWAY_ACTORS.get(actor_key) == key:
        _GATEWAY_ACTORS.pop(actor_key, None)
    _GATEWAY_ACTOR_ENVS.pop(actor_key, None)
    _terminate_gateway_process(process)
    _ownership_path(env).unlink(missing_ok=True)
    raise RuntimeError(
        "OpenClaw Gateway did not become ready; run `openclaw gateway status` and `openclaw doctor` for details"
    )


def _known_runtime_homes(env: Optional[Dict[str, str]] = None) -> set[Path]:
    with _LOCK:
        homes = {_runtime_home(item) for item in _GATEWAY_ACTOR_ENVS.values()}
    if env is None:
        homes.add(ensure_home())
    else:
        homes.add(_runtime_home(env))
    return homes


def _iter_managed_context_roots(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    return tuple(
        path.parent
        for path in root.rglob("context.json")
        if path.is_file() and _path_is_within(str(path.parent), str(root))
    )


def _managed_context_search_roots(home: Path) -> tuple[Path, ...]:
    runtime_root = home / "runtime" / "openclaw"
    return runtime_root / "actors", runtime_root / "contexts"


def _iter_home_context_roots(home: Path) -> Iterable[Path]:
    seen: set[str] = set()
    roots: list[Path] = []
    for search_root in _managed_context_search_roots(home):
        for context_root in _iter_managed_context_roots(search_root):
            normalized = os.path.normcase(os.path.abspath(str(context_root)))
            if normalized in seen:
                continue
            seen.add(normalized)
            roots.append(context_root)
    return tuple(roots)


def stop_openclaw_actor_gateway(group_id: str, actor_id: str) -> None:
    """Release an actor's Gateway reference while preserving shared state."""
    actor_key = (str(group_id or "").strip(), str(actor_id or "").strip())
    if not all(actor_key):
        return
    with _LOCK:
        key = _GATEWAY_ACTORS.pop(actor_key, None)
        managed_env = _GATEWAY_ACTOR_ENVS.pop(actor_key, None)
        still_used = key is not None and any(current == key for current in _GATEWAY_ACTORS.values())
        for cache_key in list(_PREPARED):
            if f'"agent":"{openclaw_agent_id(*actor_key)}"' in cache_key:
                _PREPARED.pop(cache_key, None)
    if still_used:
        return
    if key is None or managed_env is None:
        agent_id = openclaw_agent_id(*actor_key)
        for home in _known_runtime_homes():
            for context_root in _iter_home_context_roots(home):
                config = read_json(context_root / "openclaw.json")
                agents = config.get("agents", {}).get("list", []) if isinstance(config.get("agents"), dict) else []
                if not any(
                    isinstance(item, dict) and str(item.get("id") or "").strip() == agent_id
                    for item in agents
                ):
                    continue
                managed_env = _managed_env_from_context_root(context_root)
                key = _gateway_context_key(_context_command(context_root), managed_env)
                break
            if key is not None and managed_env is not None:
                break
    if key is None or managed_env is None:
        return

    def _expire() -> None:
        with _LOCK:
            if any(current == key for current in _GATEWAY_ACTORS.values()):
                _GATEWAY_IDLE_TIMERS.pop(key, None)
                return
            _GATEWAY_IDLE_TIMERS.pop(key, None)
            process = _GATEWAY_PROCESSES.pop(key, None)
        if process is not None and process.poll() is None:
            _terminate_gateway_process(process)
        _terminate_owned_gateway(managed_env)

    timer = threading.Timer(120.0, _expire)
    timer.daemon = True
    with _LOCK:
        previous_timer = _GATEWAY_IDLE_TIMERS.pop(key, None)
        _GATEWAY_IDLE_TIMERS[key] = timer
    if previous_timer is not None:
        previous_timer.cancel()
    timer.start()


def stop_openclaw_group_gateways(group_id: str) -> None:
    normalized = str(group_id or "").strip()
    if not normalized:
        return
    with _LOCK:
        actor_ids = [actor_id for current_group, actor_id in _GATEWAY_ACTORS if current_group == normalized]
    for actor_id in actor_ids:
        stop_openclaw_actor_gateway(normalized, actor_id)


def stop_all_openclaw_gateways(*, env: Optional[Dict[str, str]] = None) -> None:
    with _LOCK:
        actor_keys = list(_GATEWAY_ACTORS)
        homes = {_runtime_home(actor_env) for actor_env in _GATEWAY_ACTOR_ENVS.values()}
        idle_timers = list(_GATEWAY_IDLE_TIMERS.values())
        _GATEWAY_IDLE_TIMERS.clear()
    for timer in idle_timers:
        timer.cancel()
    for group_id, actor_id in actor_keys:
        stop_openclaw_actor_gateway(group_id, actor_id)
    with _LOCK:
        delayed_timers = list(_GATEWAY_IDLE_TIMERS.values())
        _GATEWAY_IDLE_TIMERS.clear()
    for timer in delayed_timers:
        timer.cancel()
    homes.update(_known_runtime_homes(env))
    for home in homes:
        for search_root in _managed_context_search_roots(home):
            _terminate_owned_gateways_under(search_root)


def remove_openclaw_actor_runtime(
    group_id: str,
    actor_id: str,
    *,
    env: Optional[Dict[str, str]] = None,
) -> None:
    """Remove only OneColleague-managed OpenClaw state for a deleted actor."""
    group_id = str(group_id or "").strip()
    actor_id = str(actor_id or "").strip()
    if not group_id or not actor_id:
        return
    stop_openclaw_actor_gateway(group_id, actor_id)
    agent_id = openclaw_agent_id(group_id, actor_id)
    skill_digest = _stable_digest(group_id, actor_id)
    homes = _known_runtime_homes(env)
    for home in homes:
        managed_runtime_root = home / "runtime" / "openclaw"
        actor_runtime_root = managed_runtime_root / "actors" / agent_id
        for context_root in _iter_home_context_roots(home):
            with _context_lock(context_root):
                if _path_is_within(str(context_root), str(actor_runtime_root)):
                    _discard_managed_gateway_context(context_root)
                    shutil.rmtree(context_root, ignore_errors=True)
                    continue
                config = read_json(context_root / "openclaw.json")
                agents_config = config.get("agents") if isinstance(config.get("agents"), dict) else {}
                agents = agents_config.get("list") if isinstance(agents_config.get("list"), list) else []
                if not any(
                    isinstance(item, dict) and str(item.get("id") or "").strip() == agent_id
                    for item in agents
                ):
                    continue
                servers_config = config.get("mcp") if isinstance(config.get("mcp"), dict) else {}
                raw_servers = servers_config.get("servers") if isinstance(servers_config.get("servers"), dict) else {}
                servers = {str(name): value for name, value in raw_servers.items() if str(name) != agent_id}
                remaining_agents = [
                    dict(item)
                    for item in agents
                    if isinstance(item, dict) and str(item.get("id") or "").strip() != agent_id
                ]
                managed_names = sorted(str(name) for name in servers if _is_managed_mcp_server_name(str(name)))
                remaining_agents = _reconcile_managed_agent_policies(remaining_agents, managed_names)
                if not any(_is_managed_agent_id(str(item.get("id") or "")) for item in remaining_agents):
                    _discard_managed_gateway_context(context_root)
                    shutil.rmtree(context_root, ignore_errors=True)
                    continue
                load = config.get("skills", {}).get("load", {}) if isinstance(config.get("skills"), dict) else {}
                extra_dirs = load.get("extraDirs") if isinstance(load.get("extraDirs"), list) else []
                skill_root = home / "runtime" / "openclaw" / "skills" / "actors" / skill_digest
                extra_dirs = [item for item in extra_dirs if not _path_is_within(str(item), str(skill_root))]
                candidate = _deep_merge(
                    config,
                    {"agents": {"list": remaining_agents}, "skills": {"load": {"extraDirs": extra_dirs}}},
                )
                candidate.setdefault("mcp", {})["servers"] = servers
                context_env = _managed_env_from_context_root(context_root)
                _publish_config(_context_command(context_root), candidate, env=context_env)
                metadata = read_json(context_root / "context.json")
                actors = metadata.get("actors") if isinstance(metadata.get("actors"), dict) else {}
                actors.pop(agent_id, None)
                metadata["actors"] = actors
                prepared = metadata.get("prepared") if isinstance(metadata.get("prepared"), dict) else {}
                prepared.pop(agent_id, None)
                published_hash = _file_sha256(context_root / "openclaw.json")
                for prepared_agent_id, prepared_row in list(prepared.items()):
                    if isinstance(prepared_row, dict):
                        prepared[prepared_agent_id] = {**prepared_row, "published_config_hash": published_hash}
                metadata["prepared"] = prepared
                atomic_write_json(context_root / "context.json", metadata, indent=2)
                shutil.rmtree(context_root / "agents" / agent_id, ignore_errors=True)
                state_dir_raw = str(metadata.get("state_dir") or "").strip()
                if state_dir_raw and _path_is_within(state_dir_raw, str(managed_runtime_root)):
                    shutil.rmtree(Path(state_dir_raw) / "agents" / agent_id, ignore_errors=True)
        shutil.rmtree(actor_runtime_root, ignore_errors=True)
        skill_root = home / "runtime" / "openclaw" / "skills" / "actors" / skill_digest
        shutil.rmtree(skill_root, ignore_errors=True)

    with _LOCK:
        for cache_key in list(_INITIALIZED_CONTEXTS):
            if any(_path_is_within(cache_key, str(home / "runtime" / "openclaw" / "actors" / agent_id)) for home in homes):
                _INITIALIZED_CONTEXTS.pop(cache_key, None)
        for context_key in list(_GATEWAY_PORT_RESERVATIONS):
            if any(_path_is_within(context_key, str(home / "runtime" / "openclaw" / "actors" / agent_id)) for home in homes):
                _GATEWAY_PORT_RESERVATIONS.pop(context_key, None)
        for cache_key in list(_PREPARED):
            if f'"agent":"{agent_id}"' in cache_key:
                _PREPARED.pop(cache_key, None)


def list_openclaw_models(
    command: Iterable[str] | None = None,
    *,
    env: Optional[Dict[str, str]] = None,
    refresh: bool = False,
) -> list[Dict[str, Any]]:
    prefix = openclaw_cli_prefix(command)
    key = _context_cache_key(prefix, env)
    now = time.monotonic()
    with _LOCK:
        cached = _MODEL_CACHE.get(key)
        if not refresh and cached and now - cached[0] <= _MODEL_CACHE_SECONDS:
            return [dict(item) for item in cached[1]]
    rows = get_opencode_model_catalog(env)
    models = [
        {
            "key": model_id,
            "name": model_id,
            "input": "text",
            "contextWindow": 0,
            "tags": [],
            "available": True,
            **({"locked": bool(item.get("locked"))} if "locked" in item else {}),
        }
        for item in rows
        if isinstance(item, dict)
        and (model_id := _onecolleague_model_id(item.get("model")))
    ]
    with _LOCK:
        _MODEL_CACHE[key] = (now, models)
    return [dict(item) for item in models]


def list_openclaw_model_ids(
    command: Iterable[str] | None = None,
    *,
    env: Optional[Dict[str, str]] = None,
) -> list[str]:
    rows = list_openclaw_models(command, env=env)
    return list(dict.fromkeys(str(item.get("key") or "").strip() for item in rows if str(item.get("key") or "").strip()))


def _resolve_model(command: list[str], env: Dict[str, str], requested: str) -> str:
    del command
    model = str(requested or env.get("OPENCLAW_SELECTED_MODEL") or "").strip()
    if not model:
        return ""
    normalized = _onecolleague_model_id(model)
    return f"{OPENCODE_PROVIDER_ID}/{normalized}" if normalized else ""


def _patch_session_model(
    prefix: list[str],
    *,
    session_key: str,
    model: Optional[str],
    env: Dict[str, str],
) -> None:
    try:
        _gateway_rpc(
            prefix,
            "sessions.patch",
            {"key": str(session_key), "model": model},
            env=env,
            timeout=20.0,
        )
    except Exception as exc:
        raise RuntimeError(f"failed to select OpenClaw model for the actor session: {exc}") from exc


def _skill_projection(group_id: str, actor_id: str) -> Dict[str, Any]:
    from ..kernel.group import load_group
    from .ops.capability_ops import prepare_openclaw_skill_package_overlay_for_actor

    group = load_group(group_id)
    if group is None:
        return {}
    result = prepare_openclaw_skill_package_overlay_for_actor(group, actor_id)
    return dict(result) if isinstance(result, dict) else {}


def prepare_openclaw_actor_runtime(
    *,
    group_id: str,
    actor_id: str,
    cwd: Path,
    command: Iterable[str] | None,
    env: Dict[str, str],
    model: str = "",
    start_guard: Optional[Callable[[], bool]] = None,
    phase_callback: Optional[Callable[[str], None]] = None,
) -> list[str]:
    """Provision a OneColleague actor in OpenClaw and return its Gateway-backed TUI command."""
    group_id = str(group_id or "").strip()
    actor_id = str(actor_id or "").strip()
    if not group_id or not actor_id:
        raise ValueError("OpenClaw runtime requires OneColleague group and actor context")
    workspace = Path(cwd).expanduser().resolve()
    base_command = [str(item) for item in list(command or []) if str(item).strip()]
    prefix = openclaw_cli_prefix(base_command)
    source_env = dict(env or {})
    def guard() -> None:
        if start_guard is not None and not start_guard():
            raise RuntimeError("OpenClaw startup was cancelled")

    def phase(value: str) -> None:
        guard()
        if phase_callback is not None:
            phase_callback(value)

    phase("projecting")
    context = _managed_context(
        prefix,
        source_env,
        group_id=group_id,
        actor_id=actor_id,
    )
    context_lock = _context_lock(context.root)
    with context_lock:
        guard()
        managed_env = _initialize_managed_context(prefix, source_env=source_env, context=context)
        env.update(managed_env)
        launch_command = build_openclaw_tui_command(
            base_command,
            group_id=group_id,
            actor_id=actor_id,
            workspace=workspace,
        )
        launch_command = _resolve_openclaw_process_argv(launch_command, env=env)

        selected_model = _resolve_model(prefix, env, model)
        managed_model_ids = _managed_openclaw_model_ids(env, selected_model=selected_model)
        model_catalog_fingerprint = _sha256_json(managed_model_ids)
        phase("projecting_skills")
        projection = _skill_projection(group_id, actor_id)
        skill_root = str(projection.get("root") or "").strip()
        managed_skill_root = str(projection.get("managed_root") or "").strip()
        selected_skill_names = sorted(
            {str(item).strip() for item in projection.get("selected_names") or [] if str(item).strip()}
        )
        managed_skill_names = sorted(
            {str(item).strip() for item in projection.get("managed_names") or [] if str(item).strip()}
        )
        agent_id = openclaw_agent_id(group_id, actor_id)
        server_name = openclaw_mcp_server_name(group_id, actor_id)
        current_config = _read_managed_config_strict(env)
        agents, servers, extra_dirs = _configured_state(current_config)
        if managed_skill_root:
            extra_dirs = [item for item in extra_dirs if not _path_is_within(item, managed_skill_root)]
        server_config = _mcp_server_config(
            group_id=group_id,
            actor_id=actor_id,
            cwd=workspace,
            env=env,
        )
        servers[server_name] = server_config
        if skill_root:
            normalized_existing = {os.path.normcase(os.path.abspath(item)) for item in extra_dirs}
            if os.path.normcase(os.path.abspath(skill_root)) not in normalized_existing:
                extra_dirs.append(skill_root)

        context_metadata = read_json(context.root / "context.json")
        cache_payload = {
            "schema": _PREPARED_SCHEMA_VERSION,
            "prefix": prefix,
            "install": _openclaw_install_signature(prefix, env),
            "agent": agent_id,
            "context": context.context_id,
            "config": str(context.config_path),
            "source_digest": str(context_metadata.get("source_digest") or ""),
            "environment_fingerprint": str(context_metadata.get("environment_fingerprint") or ""),
            "workspace": str(workspace),
            "model": selected_model,
            "model_catalog_fingerprint": model_catalog_fingerprint,
            "mcp": server_config,
            "skills": selected_skill_names,
            "managed_skills": managed_skill_names,
            "skill_fingerprint": str(projection.get("fingerprint") or ""),
            "skill_sources": _skill_sources_fingerprint(extra_dirs),
        }
        cache_key = json.dumps(cache_payload, sort_keys=True, separators=(",", ":"))
        prepared_input_hash = _sha256_json(cache_payload)
        current_config_hash = _file_sha256(context.config_path) if context.config_path.is_file() else ""
        prepared_by_agent = (
            context_metadata.get("prepared") if isinstance(context_metadata.get("prepared"), dict) else {}
        )
        prepared_metadata = (
            prepared_by_agent.get(agent_id) if isinstance(prepared_by_agent.get(agent_id), dict) else {}
        )
        persistent_hit = (
            str(prepared_metadata.get("prepared_input_hash") or "") == prepared_input_hash
            and str(prepared_metadata.get("published_config_hash") or "") == current_config_hash
            and bool(current_config_hash)
        )
        if persistent_hit:
            phase("starting_gateway")
            _start_gateway(
                prefix,
                env=env,
                cwd=context.root,
                group_id=group_id,
                actor_id=actor_id,
            )
            try:
                phase("patching_session")
                _patch_session_model(
                    prefix,
                    session_key=openclaw_session_key(group_id, actor_id, workspace),
                    model=selected_model or None,
                    env=env,
                )
            except Exception:
                stop_openclaw_actor_gateway(group_id, actor_id)
                raise
            _PREPARED[cache_key] = (time.monotonic(), launch_command)
            return list(launch_command)

        agents = _replace_managed_agent(
            agents,
            agent_id=agent_id,
            actor_id=actor_id,
            cwd=workspace,
            agent_dir=_managed_agent_dir(context, agent_id),
            model=selected_model,
            skills=None,
        )
        agents = _reconcile_managed_agent_policies(agents, _managed_server_names(servers, server_name))
        payload: Dict[str, Any] = {
            "gateway": {"mode": "local"},
            "agents": {"list": agents},
            "mcp": {"servers": {server_name: server_config}},
            "skills": {"load": {"extraDirs": extra_dirs, "watch": True}},
        }
        _managed_agent_dir(context, agent_id).mkdir(parents=True, exist_ok=True)
        candidate = _deep_merge(current_config, payload)
        candidate = _merge_managed_openclaw_provider(
            candidate,
            env=env,
            selected_model=selected_model,
            model_ids=managed_model_ids,
        )
        base_extra_dirs = [item for item in extra_dirs if not _path_is_within(item, str(managed_skill_root))]
        managed_names_in_context = _managed_skill_names_from_dirs(extra_dirs, managed_skill_root)
        base_skill_config = dict(candidate.get("skills") or {}) if isinstance(candidate.get("skills"), dict) else {}
        base_skill_load = dict(base_skill_config.get("load") or {}) if isinstance(base_skill_config.get("load"), dict) else {}
        base_skill_load["extraDirs"] = base_extra_dirs
        base_skill_config["load"] = base_skill_load
        skill_catalog_key = _sha256_json(
            {
                "install": _openclaw_install_signature(prefix, env),
                "source_digest": str(context_metadata.get("source_digest") or ""),
                "base_sources": _skill_sources_fingerprint(base_extra_dirs),
                "skills": base_skill_config,
                "plugins": candidate.get("plugins"),
            }
        )
        catalog_metadata = (
            context_metadata.get("skill_catalog")
            if isinstance(context_metadata.get("skill_catalog"), dict)
            else {}
        )
        cached_base_skills = catalog_metadata.get("base_skills")
        catalog_cache_hit = (
            str(catalog_metadata.get("key") or "") == skill_catalog_key
            and isinstance(cached_base_skills, list)
        )
        if catalog_cache_hit:
            visible_skills = sorted(
                {str(item).strip() for item in cached_base_skills if str(item).strip()} | managed_names_in_context
            )
        else:
            phase("enumerating_skills")
            visible_skills = _list_candidate_skill_names(
                prefix,
                candidate,
                agent_id=agent_id,
                env=env,
            )
            cached_base_skills = sorted(set(visible_skills) - managed_names_in_context)
        allowed_skills = sorted((set(visible_skills) - set(managed_skill_names)) | set(selected_skill_names))
        agents = _replace_managed_agent(
            agents,
            agent_id=agent_id,
            actor_id=actor_id,
            cwd=workspace,
            agent_dir=_managed_agent_dir(context, agent_id),
            model=selected_model,
            skills=allowed_skills,
        )
        agents = _reconcile_managed_agent_policies(agents, _managed_server_names(servers, server_name))
        candidate = _deep_merge(candidate, {"agents": {"list": agents}})
        gateway_was_running = _owned_gateway_pid(env) > 0
        phase("validating")
        try:
            _publish_config(prefix, candidate, env=env)
        except RuntimeError:
            if not gateway_was_running:
                raise
            gateway_key = _gateway_context_key(prefix, env)
            stale_process = _GATEWAY_PROCESSES.pop(gateway_key, None)
            _terminate_owned_gateway(env)
            if stale_process is not None:
                _terminate_gateway_process(stale_process)
            _publish_config(prefix, candidate, env=env)
        gateway_ready_after_publish = gateway_was_running and _owned_gateway_pid(env) > 0
        phase("starting_gateway")
        _start_gateway(
            prefix,
            env=env,
            cwd=context.root,
            group_id=group_id,
            actor_id=actor_id,
            authenticated_ready=gateway_ready_after_publish,
        )
        try:
            phase("patching_session")
            _patch_session_model(
                prefix,
                session_key=openclaw_session_key(group_id, actor_id, workspace),
                model=selected_model or None,
                env=env,
            )
        except Exception:
            stop_openclaw_actor_gateway(group_id, actor_id)
            raise
        metadata = read_json(context.root / "context.json")
        actors = metadata.get("actors") if isinstance(metadata.get("actors"), dict) else {}
        actors[agent_id] = {"group_id": group_id, "actor_id": actor_id}
        metadata["actors"] = actors
        prepared = metadata.get("prepared") if isinstance(metadata.get("prepared"), dict) else {}
        published_config_hash = _file_sha256(context.config_path)
        for prepared_agent_id, prepared_row in list(prepared.items()):
            if isinstance(prepared_row, dict):
                prepared[prepared_agent_id] = {**prepared_row, "published_config_hash": published_config_hash}
        prepared[agent_id] = {
            "prepared_input_hash": prepared_input_hash,
            "published_config_hash": published_config_hash,
        }
        metadata["prepared"] = prepared
        metadata["prepared_schema"] = _PREPARED_SCHEMA_VERSION
        metadata["skill_catalog"] = {"key": skill_catalog_key, "base_skills": cached_base_skills}
        atomic_write_json(context.root / "context.json", metadata, indent=2)
        _PREPARED[cache_key] = (time.monotonic(), launch_command)
        return list(launch_command)


def ensure_openclaw_mcp_installed(
    cwd: Path,
    *,
    env: Optional[Dict[str, str]],
    command: Iterable[str] | None = None,
) -> bool:
    source = dict(env or {})
    group_id = str(source.get("ONECOLLEAGUE_GROUP_ID") or source.get("CCCC_GROUP_ID") or "").strip()
    actor_id = str(source.get("ONECOLLEAGUE_ACTOR_ID") or source.get("CCCC_ACTOR_ID") or "").strip()
    if not group_id or not actor_id:
        return False
    prefix = openclaw_cli_prefix(command)
    resolved = _resolve_openclaw_process_argv(prefix, env=source)
    if not resolved or not Path(str(resolved[0])).expanduser().exists():
        raise RuntimeError("OpenClaw CLI is unavailable")
    return True
