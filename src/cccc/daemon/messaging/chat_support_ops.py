"""Shared helpers for chat operation flows."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict


def auto_wake_recipients(
    group: Any,
    to: list[str],
    *,
    by: str,
    disabled_recipient_actor_ids: Callable[[Any, list[str]], list[str]],
    enabled_recipient_actor_ids: Callable[[Any, list[str]], list[str]],
    find_actor: Callable[[Any, str], Any],
    coerce_bool: Callable[..., bool],
    is_actor_running: Callable[[Any, str], bool],
    start_actor_process: Callable[..., Dict[str, Any]],
    update_actor: Callable[[Any, str, Dict[str, Any]], Any],
    runner_stop_actor: Callable[[str, str, str], Any],
    request_flush_pending_messages: Callable[..., bool],
    logger: logging.Logger,
    auto_wake_lock: threading.Lock,
    auto_wake_in_progress: set[tuple[str, str]],
) -> list[str]:
    """Best-effort background auto-start for recipients that are unavailable.

    Returns the actor IDs accepted for wake-up scheduling. Actual startup and
    enabling happen asynchronously so chat send/reply latency does not include
    runtime boot cost.
    """
    scheduled: list[str] = []
    candidate_ids: list[str] = []
    seen_candidates: set[str] = set()

    for actor_id in disabled_recipient_actor_ids(group, to):
        aid = str(actor_id or "").strip()
        if not aid or aid == str(by or "").strip() or aid in seen_candidates:
            continue
        seen_candidates.add(aid)
        candidate_ids.append(aid)

    for actor_id in enabled_recipient_actor_ids(group, to):
        aid = str(actor_id or "").strip()
        if not aid or aid == str(by or "").strip() or aid in seen_candidates:
            continue
        if is_actor_running(group, aid):
            continue
        seen_candidates.add(aid)
        candidate_ids.append(aid)

    for actor_id in candidate_ids:
        key = (str(group.group_id or "").strip(), str(actor_id or "").strip())
        if not key[0] or not key[1]:
            continue
        with auto_wake_lock:
            if key in auto_wake_in_progress:
                continue
            auto_wake_in_progress.add(key)
        actor = find_actor(group, actor_id)
        if actor is None:
            with auto_wake_lock:
                auto_wake_in_progress.discard(key)
            continue
        was_enabled = coerce_bool(actor.get("enabled"), default=True)
        if was_enabled and is_actor_running(group, actor_id):
            with auto_wake_lock:
                auto_wake_in_progress.discard(key)
            continue
        cmd = actor.get("command") if isinstance(actor.get("command"), list) else []
        env = actor.get("env") if isinstance(actor.get("env"), dict) else {}
        runner_kind = str(actor.get("runner") or "pty").strip()
        runtime = str(actor.get("runtime") or "codex").strip()
        scheduled.append(actor_id)

        def _run_wake(
            *,
            wake_actor_id: str,
            wake_runner_kind: str,
            wake_runtime: str,
            wake_cmd: list[str],
            wake_env: dict[str, Any],
            wake_was_enabled: bool,
            wake_key: tuple[str, str],
        ) -> None:
            try:
                result = start_actor_process(
                    group,
                    wake_actor_id,
                    command=list(wake_cmd or []),
                    env=dict(wake_env or {}),
                    runner=wake_runner_kind,
                    runtime=wake_runtime,
                    by=by,
                )
                if result["success"]:
                    try:
                        if not wake_was_enabled:
                            update_actor(group, wake_actor_id, {"enabled": True})
                        request_flush_pending_messages(group, actor_id=wake_actor_id)
                    except Exception as e:
                        try:
                            runner_stop_actor(group.group_id, wake_actor_id, wake_runner_kind)
                        except Exception:
                            pass
                        logger.warning(
                            "[auto-wake] failed to persist enabled actor=%s group=%s: %s",
                            wake_actor_id,
                            group.group_id,
                            e,
                        )
                else:
                    logger.info(
                        "[auto-wake] actor start failed actor=%s group=%s err=%s",
                        wake_actor_id,
                        group.group_id,
                        result.get("error"),
                    )
            except Exception:
                logger.exception("[auto-wake] unexpected error actor=%s group=%s", wake_actor_id, group.group_id)
            finally:
                with auto_wake_lock:
                    auto_wake_in_progress.discard(wake_key)

        thread = threading.Thread(
            target=_run_wake,
            kwargs={
                "wake_actor_id": actor_id,
                "wake_runner_kind": runner_kind,
                "wake_runtime": runtime,
                "wake_cmd": list(cmd or []),
                "wake_env": dict(env or {}),
                "wake_was_enabled": bool(was_enabled),
                "wake_key": key,
            },
            name=f"cccc-auto-wake-{key[0]}-{key[1]}",
            daemon=True,
        )
        thread.start()
    return scheduled


def normalize_attachments(
    group: Any,
    raw: Any,
    *,
    resolve_blob_attachment_path: Callable[..., Any],
) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("attachments must be a list")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("invalid attachment (must be object)")
        rel_path = str(item.get("path") or "").strip()
        if not rel_path:
            raise ValueError("attachment missing path")
        abs_path = resolve_blob_attachment_path(group, rel_path=rel_path)
        if not abs_path.exists() or not abs_path.is_file():
            raise ValueError(f"attachment not found: {rel_path}")
        try:
            size = int(abs_path.stat().st_size)
        except Exception:
            size = int(item.get("bytes") or 0)
        out.append(
            {
                "kind": str(item.get("kind") or "file"),
                "path": rel_path,
                "title": str(item.get("title") or ""),
                "mime_type": str(item.get("mime_type") or ""),
                "bytes": size,
                "sha256": str(item.get("sha256") or ""),
            }
        )
    return out
