"""Actor runtime startup helpers shared by daemon actor lifecycle flows."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypedDict

from ...kernel.actors import find_actor
from ...kernel.context import ContextStorage
from ...kernel.ledger import append_event
from ...kernel.runtime import runtime_start_preflight_error
from ...kernel.runtime_state_source import actor_uses_codex_app_server_state
from ..claude_app_sessions import SUPERVISOR as claude_app_supervisor
from ..codex_app_sessions import SUPERVISOR as codex_app_supervisor
from ..mcp_install import prepare_runtime_mcp_env
from ..runtime_session_ops import start_pty_actor_with_runtime_resume
from ..terminal_theme import TERMINAL_COLOR_SCHEME_ENV, with_terminal_color_scheme
from ...runners import headless as headless_runner
from ...runners import pty as pty_runner
from ...runners.platform_support import pty_support_error_message


def _prepare_codex_skill_overlay(group: Any, actor_id: str, env: Dict[str, Any]) -> Dict[str, str]:
    try:
        from ..ops.capability_ops import prepare_codex_skill_package_overlay_for_actor
    except Exception:
        return {}
    overlay_env = prepare_codex_skill_package_overlay_for_actor(group, actor_id, env)
    if not isinstance(overlay_env, dict):
        return {}
    return {str(k): str(v) for k, v in overlay_env.items() if str(k).strip() and str(v).strip()}


class ActorLaunchConfig(TypedDict):
    actor: Dict[str, Any]
    runtime: str
    runner: str
    effective_runner: str
    command: List[str]
    public_env: Dict[str, str]
    private_env: Dict[str, str]
    merged_env: Dict[str, Any]
    default_scope_key: str
    submit: str


class ActorLaunchSpec(ActorLaunchConfig):
    scope_key: str
    cwd: Path
    effective_command: List[str]


def model_from_runtime_command(command: List[str], env: Optional[Dict[str, Any]] = None) -> str:
    """Return an explicitly configured model from a runtime command."""
    items = [str(item or "").strip() for item in list(command or [])]
    for idx, item in enumerate(items):
        if not item:
            continue
        if item in {"-m", "--model"}:
            if idx + 1 < len(items):
                return str(items[idx + 1] or "").strip()
            return ""
        if item.startswith("--model="):
            return item.split("=", 1)[1].strip()
    env_map = env if isinstance(env, dict) else {}
    for key in ("ANTHROPIC_MODEL", "OPENAI_MODEL", "CODEX_MODEL", "KIMI_MODEL_NAME"):
        value = str(env_map.get(key) or "").strip()
        if value:
            return value
    return ""


def _coerce_string_env(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str):
            out[str(key)] = str(value)
    return out


def _coerce_command(raw: Any, fallback: List[str]) -> List[str]:
    source = raw if isinstance(raw, list) else fallback
    return [str(item) for item in source if isinstance(item, str) and str(item).strip()]


def _command_normalizer_env(actor_env: Dict[str, Any]) -> Dict[str, Any]:
    env = dict(os.environ)
    env.update(dict(actor_env or {}))
    return env


def _normalize_runtime_command_for_launch(
    normalize_runtime_command: Callable[..., List[str]],
    runtime: str,
    command: List[str],
    env: Dict[str, Any],
) -> List[str]:
    try:
        signature = inspect.signature(normalize_runtime_command)
    except (TypeError, ValueError):
        return normalize_runtime_command(runtime, command)
    params = signature.parameters
    if "env" in params or any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return normalize_runtime_command(runtime, command, env=env)
    positional = [
        param
        for param in params.values()
        if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params.values()) or len(positional) >= 3:
        return normalize_runtime_command(runtime, command, env)
    return normalize_runtime_command(runtime, command)


def resolve_actor_launch_config(
    group: Any,
    actor_id: str,
    *,
    command: List[str],
    env: Dict[str, str],
    runner: str,
    runtime: str,
    effective_runner_kind: Callable[[str], str],
    caller_id: str = "",
    is_admin: bool = False,
    resolve_linked_actor_before_start: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
    load_actor_private_env: Optional[Callable[[str, str], Dict[str, str]]] = None,
    merge_actor_env_with_private: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> ActorLaunchConfig:
    actor = find_actor(group, actor_id)
    if actor is None:
        raise ValueError(f"actor not found: {actor_id}")
    if callable(resolve_linked_actor_before_start):
        actor = resolve_linked_actor_before_start(
            group,
            actor_id,
            caller_id=str(caller_id or "").strip(),
            is_admin=bool(is_admin),
        )

    resolved_command = _coerce_command(actor.get("command"), list(command or []))
    public_env = _coerce_string_env(actor.get("env")) if isinstance(actor.get("env"), dict) else _coerce_string_env(env)
    resolved_runner = str(actor.get("runner") or runner or "pty").strip() or "pty"
    resolved_runtime = str(actor.get("runtime") or runtime or "codex").strip() or "codex"
    if resolved_runtime == "web_model":
        resolved_runner = "headless"
        resolved_command = []
    effective_runner = effective_runner_kind(resolved_runner)
    if resolved_runtime == "custom" and effective_runner != "headless" and not resolved_command:
        raise ValueError("custom runtime requires a command (PTY runner)")

    private_env = (
        _coerce_string_env(load_actor_private_env(group.group_id, actor_id))
        if callable(load_actor_private_env)
        else {}
    )
    if callable(merge_actor_env_with_private):
        merged_env = dict(merge_actor_env_with_private(group.group_id, actor_id, public_env))
    elif private_env:
        merged_env = dict(public_env)
        merged_env.update(private_env)
    else:
        merged_env = dict(public_env)
    if resolved_runtime == "hermes":
        selected_model = str((actor.get("runtime_options") or {}).get("selected_model") or "").strip()
        if selected_model:
            # Runtime-only hint consumed by the command normalizer; never
            # persisted as a public environment variable.
            merged_env["HERMES_SELECTED_MODEL"] = selected_model
    return {
        "actor": dict(actor),
        "runtime": resolved_runtime,
        "runner": resolved_runner,
        "effective_runner": effective_runner,
        "command": resolved_command,
        "public_env": public_env,
        "private_env": private_env,
        "merged_env": merged_env,
        "default_scope_key": str(actor.get("default_scope_key") or "").strip(),
        "submit": str(actor.get("submit") or "enter").strip() or "enter",
    }


def resolve_actor_launch_spec(
    group: Any,
    actor_id: str,
    *,
    command: List[str],
    env: Dict[str, str],
    runner: str,
    runtime: str,
    find_scope_url: Callable[[Any, str], str],
    effective_runner_kind: Callable[[str], str],
    normalize_runtime_command: Callable[..., List[str]],
    supported_runtimes: tuple[str, ...] | list[str],
    caller_id: str = "",
    is_admin: bool = False,
    resolve_linked_actor_before_start: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
    load_actor_private_env: Optional[Callable[[str, str], Dict[str, str]]] = None,
    merge_actor_env_with_private: Optional[Callable[[str, str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> ActorLaunchSpec:
    launch_config = resolve_actor_launch_config(
        group,
        actor_id,
        command=command,
        env=env,
        runner=runner,
        runtime=runtime,
        effective_runner_kind=effective_runner_kind,
        caller_id=caller_id,
        is_admin=is_admin,
        resolve_linked_actor_before_start=resolve_linked_actor_before_start,
        load_actor_private_env=load_actor_private_env,
        merge_actor_env_with_private=merge_actor_env_with_private,
    )

    group_scope_key = str(group.doc.get("active_scope_key") or "").strip()
    if not group_scope_key:
        raise ValueError("no active scope for group")

    scope_key = str(launch_config["default_scope_key"] or group_scope_key).strip()
    url = find_scope_url(group, scope_key)
    if not url:
        raise ValueError(f"scope not attached: {scope_key}")
    cwd = Path(url).expanduser().resolve()
    if not cwd.exists():
        raise ValueError(f"project root path does not exist: {cwd}")

    if launch_config["runtime"] not in supported_runtimes:
        raise ValueError(f"unsupported runtime: {launch_config['runtime']}")

    runtime = str(launch_config["runtime"] or "").strip()
    effective_command = _normalize_runtime_command_for_launch(
        normalize_runtime_command,
        runtime,
        list(launch_config["command"] or []),
        _command_normalizer_env(dict(launch_config["merged_env"] or {})),
    )
    return {
        **launch_config,
        "scope_key": scope_key,
        "cwd": cwd,
        "effective_command": effective_command,
    }


def start_actor_process(
    group: Any,
    actor_id: str,
    *,
    command: List[str],
    env: Dict[str, str],
    runner: str,
    runtime: str,
    by: str,
    caller_id: str = "",
    is_admin: bool = False,
    terminal_color_scheme: str = "dark",
    find_scope_url: Callable[[Any, str], str],
    effective_runner_kind: Callable[[str], str],
    merge_actor_env_with_private: Callable[[str, str, Dict[str, Any]], Dict[str, Any]],
    normalize_runtime_command: Callable[..., List[str]],
    ensure_mcp_installed: Callable[..., bool],
    inject_actor_context_env: Callable[[Dict[str, Any], str, str], Dict[str, Any]],
    prepare_pty_env: Callable[[Dict[str, Any]], Dict[str, str]],
    pty_backlog_bytes: Callable[[], int],
    write_headless_state: Callable[[str, str], None],
    write_pty_state: Callable[[str, str, int], None],
    clear_preamble_sent: Callable[[Any, str], None],
    throttle_reset_actor: Callable[[str, str], None],
    supported_runtimes: tuple[str, ...],
    resolve_linked_actor_before_start: Optional[Callable[[Any, str], Dict[str, Any]]] = None,
    load_actor_private_env: Optional[Callable[[str, str], Dict[str, str]]] = None,
) -> Dict[str, Any]:
    try:
        launch_spec = resolve_actor_launch_spec(
            group,
            actor_id,
            command=command,
            env=env,
            runner=runner,
            runtime=runtime,
            find_scope_url=find_scope_url,
            effective_runner_kind=effective_runner_kind,
            normalize_runtime_command=normalize_runtime_command,
            supported_runtimes=supported_runtimes,
            caller_id=caller_id,
            is_admin=is_admin,
            resolve_linked_actor_before_start=resolve_linked_actor_before_start,
            load_actor_private_env=load_actor_private_env,
            merge_actor_env_with_private=merge_actor_env_with_private,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    actor = launch_spec["actor"]
    effective_runner = launch_spec["effective_runner"]
    effective_env = dict(launch_spec["merged_env"])
    effective_cmd = launch_spec["effective_command"]
    cwd = launch_spec["cwd"]
    runtime = launch_spec["runtime"]
    runner = launch_spec["runner"]
    terminal_color_scheme = str(env.get(TERMINAL_COLOR_SCHEME_ENV) or terminal_color_scheme)

    if runtime == "codex":
        try:
            effective_env.update(_prepare_codex_skill_overlay(group, actor_id, effective_env))
            from ...computer_control.isolation import codex_windows_mcp_disable_args

            disable_args = codex_windows_mcp_disable_args(effective_env)
            if effective_cmd and disable_args:
                additions: List[str] = []
                for index in range(0, len(disable_args), 2):
                    pair = disable_args[index : index + 2]
                    if len(pair) == 2 and pair[1] not in effective_cmd:
                        additions.extend(pair)
                effective_cmd = [effective_cmd[0], *additions, *effective_cmd[1:]]
            effective_env["ONECOLLEAGUE_CODEX_WINDOWS_MCP_DISABLED"] = "1"
        except Exception as e:
            return {"success": False, "error": f"failed to prepare Codex computer-control isolation: {e}"}

    launch_env = prepare_runtime_mcp_env(
        runtime,
        inject_actor_context_env(effective_env, group.group_id, actor_id),
        command=list(effective_cmd),
        runtime_options=dict(actor.get("runtime_options") or {}),
    )
    launch_env = with_terminal_color_scheme(launch_env, terminal_color_scheme)

    runtime_error = runtime_start_preflight_error(runtime, effective_cmd, runner=effective_runner)
    if runtime_error:
        return {"success": False, "error": runtime_error}

    if effective_runner != "headless":
        if not bool(getattr(pty_runner, "PTY_SUPPORTED", False)):
            error_message = pty_support_error_message() or "PTY runner is not supported in this environment."
            return {"success": False, "error": error_message}
        try:
            mcp_ready = bool(
                ensure_mcp_installed(
                    runtime,
                    cwd,
                    env=dict(launch_env),
                )
            )
        except Exception as e:
            return {"success": False, "error": f"failed to install MCP: {e}"}
        if not mcp_ready:
            return {"success": False, "error": f"failed to install MCP for runtime: {runtime}"}

    try:
        if runtime == "web_model" and effective_runner == "headless":
            try:
                write_headless_state(group.group_id, actor_id)
            except Exception:
                pass
            try:
                from .web_model_browser_delivery import web_model_browser_delivery_enabled
                from .web_model_browser_session import schedule_web_model_chatgpt_browser_session_warmup

                if web_model_browser_delivery_enabled(group.group_id, actor):
                    schedule_web_model_chatgpt_browser_session_warmup(
                        group_id=group.group_id,
                        actor_id=actor_id,
                        reason="actor_start",
                        retry_seconds=0.0,
                    )
            except Exception:
                pass
        elif actor_uses_codex_app_server_state(actor):
            session = codex_app_supervisor.start_pty_app_actor(
                group_id=group.group_id,
                actor_id=actor_id,
                cwd=cwd,
                env=dict(launch_env),
                model=model_from_runtime_command(effective_cmd, effective_env),
                remote_tui_base_command=list(effective_cmd),
                max_backlog_bytes=pty_backlog_bytes(),
            )
            try:
                write_pty_state(group.group_id, actor_id, session.remote_tui_pid())
            except Exception:
                pass
        elif runtime == "codex" and effective_runner == "headless":
            codex_app_supervisor.start_actor(
                group_id=group.group_id,
                actor_id=actor_id,
                cwd=cwd,
                env=dict(launch_env),
                model=model_from_runtime_command(effective_cmd, effective_env),
            )
        elif runtime == "claude" and effective_runner == "headless":
            claude_app_supervisor.start_actor(
                group_id=group.group_id,
                actor_id=actor_id,
                cwd=cwd,
                env=dict(launch_env),
                model=model_from_runtime_command(effective_cmd, effective_env),
            )
        elif effective_runner == "headless":
            headless_runner.SUPERVISOR.start_actor(
                group_id=group.group_id,
                actor_id=actor_id,
                cwd=cwd,
                env=dict(launch_env),
            )
            try:
                write_headless_state(group.group_id, actor_id)
            except Exception:
                pass
        else:
            session = start_pty_actor_with_runtime_resume(
                group_id=group.group_id,
                actor_id=actor_id,
                cwd=cwd,
                base_command=effective_cmd,
                env=prepare_pty_env(dict(launch_env)),
                runtime=runtime,
                model=model_from_runtime_command(effective_cmd, effective_env),
                max_backlog_bytes=pty_backlog_bytes(),
                runtime_start_preflight_error=runtime_start_preflight_error,
            )
            try:
                write_pty_state(group.group_id, actor_id, session.pid)
            except Exception:
                pass
    except Exception as e:
        return {"success": False, "error": f"failed to start session: {e}"}

    clear_preamble_sent(group, actor_id)
    throttle_reset_actor(group.group_id, actor_id)
    try:
        ContextStorage(group).clear_agent_status_if_present(actor_id)
    except Exception:
        pass

    try:
        if str(group.doc.get("state") or "").strip() == "stopped":
            group.doc["state"] = "active"
        group.doc["running"] = True
        group.save()
    except Exception:
        pass

    start_data: Dict[str, Any] = {"actor_id": actor_id, "runner": runner}
    if effective_runner != runner:
        start_data["runner_effective"] = effective_runner
    start_event = append_event(
        group.ledger_path,
        kind="actor.start",
        group_id=group.group_id,
        scope_key="",
        by=by,
        data=start_data,
    )

    from ...kernel.events import publish_event
    publish_event("actor.start", {"group_id": group.group_id, "actor_id": actor_id})
    return {
        "success": True,
        "actor": actor,
        "event": start_event,
        "effective_runner": effective_runner,
        "error": None,
    }
