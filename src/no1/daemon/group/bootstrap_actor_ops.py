"""Daemon startup helpers for persisted actor runtime state."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ...kernel.context import ContextStorage
from ...kernel.actors import is_internal_actor, is_supported_internal_actor, list_actors
from ...kernel.group import load_group
from ...kernel.runtime import runtime_start_preflight_error
from ...kernel.runtime_state_source import actor_uses_codex_app_server_state
from ...util.fs import read_json
from ...util.time import utc_now_iso
from ..claude_app_sessions import SUPERVISOR as claude_app_supervisor
from ..codex_app_sessions import SUPERVISOR as codex_app_supervisor
from ..mcp_install import prepare_runtime_mcp_env
from ..runtime_session_ops import start_pty_actor_with_runtime_resume
from ..runner_state_ops import web_model_group_running
from ...util.conv import coerce_bool
from ...runners import headless as headless_runner
from ...runners import pty as pty_runner
from ..actors.actor_runtime_ops import model_from_runtime_command, resolve_actor_launch_spec
from ..assistants.voice_secretary_runtime_ops import (
    capture_voice_secretary_actor_state,
    restore_voice_secretary_actor_state,
    sync_voice_secretary_actor_from_foreman,
)
from ..messaging.turn_provenance import invalidate_turn_grant
from ..openclaw_startup import cancel_all_openclaw_actor_starts

logger = logging.getLogger("no1.daemon.server")

ResolveLinkedActorBeforeStart = Callable[..., Dict[str, Any]]


def _clear_execution_state(group: Any) -> int:
    storage = ContextStorage(group)
    agents = storage.load_agents()
    changed = 0
    now = utc_now_iso()
    for agent in agents.agents:
        if not (
            agent.hot.active_task_id
            or agent.hot.focus
            or agent.hot.next_action
            or agent.hot.blockers
            or agent.warm.what_changed
        ):
            continue
        agent.hot.active_task_id = None
        agent.hot.focus = ""
        agent.hot.next_action = ""
        agent.hot.blockers = []
        agent.warm.what_changed = ""
        agent.updated_at = now
        changed += 1
    if changed:
        storage.save_agents(agents)
        storage.bump_version_state(agents_changed=True)
    return changed


def _invalidate_live_turn_grants(group: Any) -> int:
    state_dir = group.path / "state" / "turn-grants"
    if not state_dir.exists():
        return 0
    changed = 0
    for path in state_dir.glob("*.json"):
        state = read_json(path)
        if not isinstance(state, dict):
            continue
        if not isinstance(state.get("pending_attempt"), dict) and not isinstance(state.get("current_grant"), dict):
            continue
        invalidate_turn_grant(group, path.stem, reason="daemon_start_stopped")
        changed += 1
    return changed


def _remove_runner_markers(group: Any) -> int:
    removed = 0
    for runner_kind in ("pty", "headless"):
        state_dir = group.path / "state" / "runners" / runner_kind
        if not state_dir.exists():
            continue
        for path in state_dir.glob("*.json"):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def reset_groups_for_daemon_start(home: Path) -> Dict[str, int]:
    """Persist a stopped baseline before daemon services become reachable."""
    result = {
        "groups": 0,
        "groups_changed": 0,
        "actors_disabled": 0,
        "runner_markers_removed": 0,
        "openclaw_starts_cancelled": 0,
        "agent_states_cleared": 0,
        "turn_grants_invalidated": 0,
    }
    base = home / "groups"
    if not base.exists():
        return result

    for group_yaml in sorted(base.glob("*/group.yaml")):
        group = load_group(group_yaml.parent.name)
        if group is None:
            logger.warning("startup reset skipped unreadable group: %s", group_yaml.parent.name)
            continue
        result["groups"] += 1
        group_changed = False
        actors = group.doc.get("actors") if isinstance(group.doc.get("actors"), list) else []
        for actor in actors:
            if not isinstance(actor, dict):
                continue
            if actor.get("enabled") is not False:
                actor["enabled"] = False
                result["actors_disabled"] += 1
                group_changed = True
        if group.doc.get("running") is not False:
            group.doc["running"] = False
            group_changed = True
        if str(group.doc.get("state") or "").strip().lower() != "stopped":
            group.doc["state"] = "stopped"
            group_changed = True
        if group_changed:
            group.save()
            result["groups_changed"] += 1

        result["openclaw_starts_cancelled"] += cancel_all_openclaw_actor_starts(group.group_id)
        result["runner_markers_removed"] += _remove_runner_markers(group)
        result["agent_states_cleared"] += _clear_execution_state(group)
        result["turn_grants_invalidated"] += _invalidate_live_turn_grants(group)

    return result


def autostart_running_groups(
    home: Path,
    *,
    effective_runner_kind: Callable[[str], str],
    start_actor_process: Optional[Callable[..., Dict[str, Any]]] = None,
    find_scope_url: Callable[[Any, str], str],
    supported_runtimes: tuple[str, ...],
    ensure_mcp_installed: Callable[..., bool],
    auto_mcp_runtimes: tuple[str, ...],
    pty_supported: Optional[Callable[[], bool]] = None,
    merge_actor_env_with_private: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    inject_actor_context_env: Callable[[dict[str, Any], str, str], dict[str, Any]],
    prepare_pty_env: Callable[[dict[str, Any]], dict[str, str]],
    normalize_runtime_command: Callable[[str, list[str]], list[str]],
    pty_backlog_bytes: Callable[[], int],
    write_headless_state: Callable[[str, str], None],
    write_pty_state: Callable[[str, str, int], None],
    clear_preamble_sent: Callable[[Any, str], None],
    throttle_reset_actor: Callable[[str, str], None],
    automation_on_resume: Callable[[Any], None],
    get_group_state: Callable[[Any], str],
    load_actor_private_env: Callable[[str, str], dict[str, str]],
    update_actor_private_env: Callable[..., dict[str, str]],
    delete_actor_private_env: Callable[[str, str], None],
    resolve_linked_actor_before_start: Optional[ResolveLinkedActorBeforeStart] = None,
) -> None:
    base = home / "groups"
    if not base.exists():
        return

    for group_yaml in base.glob("*/group.yaml"):
        group_id = group_yaml.parent.name
        group = load_group(group_id)
        if group is None:
            continue
        if not coerce_bool(group.doc.get("running"), default=False):
            continue
        logger.info("autostart group=%s state=%s running=%s", group_id, str(group.doc.get("state") or "active"), True)

        group_scope_key = str(group.doc.get("active_scope_key") or "").strip()
        if not group_scope_key:
            group.doc["running"] = False
            try:
                group.save()
            except Exception:
                pass
            continue

        voice_state_before = capture_voice_secretary_actor_state(group, load_actor_private_env=load_actor_private_env)
        try:
            sync_voice_secretary_actor_from_foreman(
                group,
                effective_runner_kind=effective_runner_kind,
                load_actor_private_env=load_actor_private_env,
                update_actor_private_env=update_actor_private_env,
                delete_actor_private_env=delete_actor_private_env,
                resolve_linked_actor_before_start=resolve_linked_actor_before_start,
            )
        except Exception as e:
            logger.warning("Voice Secretary actor sync failed for %s: %s", group_id, e)
            try:
                restore_voice_secretary_actor_state(
                    group,
                    None if str(e).strip() == "voice secretary requires a foreman actor" else voice_state_before,
                    update_actor_private_env=update_actor_private_env,
                    delete_actor_private_env=delete_actor_private_env,
                )
            except Exception:
                pass

        for actor in list_actors(group):
            if not isinstance(actor, dict):
                continue
            actor_id = str(actor.get("id") or "").strip()
            if not actor_id:
                continue
            if is_internal_actor(actor) and not is_supported_internal_actor(actor):
                logger.info(
                    "autostart skipped unsupported internal actor group=%s actor=%s kind=%s",
                    group.group_id,
                    actor_id,
                    str(actor.get("internal_kind") or "").strip(),
                )
                continue
            if not coerce_bool(actor.get("enabled"), default=True):
                continue

            profile_scope = str(actor.get("profile_scope") or "").strip().lower()
            profile_owner = str(actor.get("profile_owner") or "").strip()
            caller_id = profile_owner if profile_scope == "user" and profile_owner else ""
            try:
                launch_spec = resolve_actor_launch_spec(
                    group,
                    actor_id,
                    command=list(actor.get("command") or []) if isinstance(actor.get("command"), list) else [],
                    env=dict(actor.get("env") or {}) if isinstance(actor.get("env"), dict) else {},
                    runner=str(actor.get("runner") or "pty"),
                    runtime=str(actor.get("runtime") or "codex"),
                    find_scope_url=find_scope_url,
                    effective_runner_kind=effective_runner_kind,
                    normalize_runtime_command=normalize_runtime_command,
                    supported_runtimes=supported_runtimes,
                    caller_id=caller_id,
                    is_admin=False,
                    resolve_linked_actor_before_start=resolve_linked_actor_before_start,
                    merge_actor_env_with_private=merge_actor_env_with_private,
                )
            except Exception as e:
                logger.warning("Autostart skipped for %s/%s: %s", group_id, actor_id, e)
                continue

            effective_runner = str(launch_spec["effective_runner"])
            cwd = launch_spec["cwd"]
            runtime = str(launch_spec["runtime"])
            effective_env = dict(launch_spec["merged_env"])
            if runtime == "openclaw" and start_actor_process is not None:
                from ..openclaw_startup import queue_openclaw_actor_start, read_openclaw_startup

                previous_startup = read_openclaw_startup(group.group_id, actor_id)
                if str(previous_startup.get("state") or "") == "failed":
                    logger.info(
                        "autostart skipped failed OpenClaw actor group=%s actor=%s",
                        group.group_id,
                        actor_id,
                    )
                    continue
                queue_openclaw_actor_start(
                    group.group_id,
                    actor_id,
                    by="daemon",
                    caller_id="",
                    is_admin=True,
                    start_actor_process=start_actor_process,
                )
                continue
            launch_env = prepare_runtime_mcp_env(
                runtime,
                inject_actor_context_env(effective_env, group.group_id, actor_id),
                command=list(launch_spec["effective_command"]),
                runtime_options=dict((launch_spec.get("actor") or {}).get("runtime_options") or {}),
            )

            ok_mcp = True
            effective_cmd = list(launch_spec["effective_command"])
            runtime_error = runtime_start_preflight_error(runtime, effective_cmd, runner=effective_runner)
            if runtime_error:
                logger.warning("Autostart skipped for %s/%s: %s", group_id, actor_id, runtime_error)
                continue
            if effective_runner != "headless":
                try:
                    ok_mcp = bool(
                        ensure_mcp_installed(
                            runtime,
                            cwd,
                            env=dict(launch_env),
                            command=list(effective_cmd),
                        )
                    )
                except Exception:
                    ok_mcp = False
            if not ok_mcp and runtime in auto_mcp_runtimes:
                logger.warning(
                    "MCP server 'onecolleague' is not installed for %s/%s (runtime=%s); actor will start but tools may not work.",
                    group_id,
                    actor_id,
                    runtime,
                )

            clear_preamble_sent(group, actor_id)

            try:
                logger.info(
                    "autostart start group=%s actor=%s runtime=%s runner=%s runner_effective=%s",
                    group.group_id,
                    actor_id,
                    runtime,
                    str(launch_spec["runner"]),
                    effective_runner,
                )
                if runtime == "web_model" and effective_runner == "headless":
                    write_headless_state(group.group_id, actor_id)
                    try:
                        from ..actors.web_model_browser_delivery import web_model_browser_delivery_enabled
                        from ..actors.web_model_browser_session import schedule_web_model_chatgpt_browser_session_warmup

                        if web_model_browser_delivery_enabled(group.group_id, actor):
                            schedule_web_model_chatgpt_browser_session_warmup(
                                group_id=group.group_id,
                                actor_id=actor_id,
                                reason="daemon_autostart",
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
                        model=model_from_runtime_command(launch_spec["effective_command"], effective_env),
                        remote_tui_base_command=list(launch_spec["effective_command"]),
                        max_backlog_bytes=pty_backlog_bytes(),
                    )
                elif runtime == "codex" and effective_runner == "headless":
                    codex_app_supervisor.start_actor(
                        group_id=group.group_id,
                        actor_id=actor_id,
                        cwd=cwd,
                        env=dict(launch_env),
                        model=model_from_runtime_command(launch_spec["effective_command"], effective_env),
                    )
                elif runtime == "claude" and effective_runner == "headless":
                    claude_app_supervisor.start_actor(
                        group_id=group.group_id,
                        actor_id=actor_id,
                        cwd=cwd,
                        env=dict(launch_env),
                        model=model_from_runtime_command(launch_spec["effective_command"], effective_env),
                    )
                elif effective_runner == "headless":
                    headless_runner.SUPERVISOR.start_actor(
                        group_id=group.group_id,
                        actor_id=actor_id,
                        cwd=cwd,
                        env=dict(launch_env),
                    )
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
                logger.info(
                    "autostart started group=%s actor=%s runtime=%s runner_effective=%s",
                    group.group_id,
                    actor_id,
                    runtime,
                    effective_runner,
                )
            except Exception as e:
                logger.warning("Autostart failed for %s/%s: %s", group_id, actor_id, e)
                continue

            try:
                if runtime == "web_model" and effective_runner == "headless":
                    pass
                elif actor_uses_codex_app_server_state(actor):
                    write_pty_state(group.group_id, actor_id, session.remote_tui_pid())
                elif runtime == "codex" and effective_runner == "headless":
                    pass
                elif runtime == "claude" and effective_runner == "headless":
                    pass
                elif effective_runner == "headless":
                    write_headless_state(group.group_id, actor_id)
                else:
                    write_pty_state(group.group_id, actor_id, session.pid)
            except Exception as e:
                logger.debug("State write failed for %s/%s: %s", group_id, actor_id, e)

            clear_preamble_sent(group, actor_id)
            throttle_reset_actor(group.group_id, actor_id)
            try:
                ContextStorage(group).clear_agent_status_if_present(actor_id)
            except Exception:
                pass

        try:
            if (
                get_group_state(group) == "active"
                and (
                    codex_app_supervisor.group_running(group.group_id)
                    or
                    claude_app_supervisor.group_running(group.group_id)
                    or
                    pty_runner.SUPERVISOR.group_running(group.group_id)
                    or headless_runner.SUPERVISOR.group_running(group.group_id)
                    or web_model_group_running(group.group_id)
                )
            ):
                automation_on_resume(group)
        except Exception:
            pass
