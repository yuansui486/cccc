from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping, Optional, TYPE_CHECKING

from ..kernel.group import load_group
from ..util.fs import atomic_write_text
from .audit import audit
from .elements import normalize_snapshot, resolve_locator
from .lease import LeaseConflict
from .models import WorkflowTrigger
from .observation import desktop_session_available
from .triggers import (
    advance_element_edge,
    element_poll_seconds,
    element_required_hits,
    next_cron_time,
    next_schedule_time,
    parse_at_timestamp,
    validate_trigger,
)

if TYPE_CHECKING:
    from .services import ComputerControlServices


class ComputerControlScheduler:
    """Persistent unattended scheduler with one coalesced pending occurrence."""

    LOOP_SECONDS = 0.5
    STATE_VERSION = 1
    EXECUTED_TYPES = frozenset({"interval", "schedule", "at", "cron", "element"})

    def __init__(self, service: "ComputerControlServices"):
        self.service = service
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._daemon_thread: threading.Thread | None = None
        self._daemon_stop = threading.Event()
        self._daemon_lock = threading.Lock()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="onecolleague-computer-trigger-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def start_daemon(self, owner: Any) -> None:
        from .services import _daemon_owner_state

        _daemon_owner_state(owner, home=self.service.home)
        if getattr(self.service, "role", "passive") != "daemon":
            raise PermissionError("daemon scheduler requires a daemon service")
        with self._daemon_lock:
            if self._daemon_thread is not None:
                if self._daemon_thread.is_alive():
                    return
                self._daemon_thread = None
            self._daemon_stop.clear()
            thread = threading.Thread(
                target=self._run_daemon_thread,
                args=(owner,),
                name="onecolleague-computer-trigger-scheduler",
                daemon=True,
            )
            self._daemon_thread = thread
            thread.start()

    def stop_daemon(self) -> None:
        self._daemon_stop.set()
        with self._daemon_lock:
            thread = self._daemon_thread
            self._daemon_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join()

    def _run_daemon_thread(self, owner: Any) -> None:
        try:
            asyncio.run(self._daemon_loop(owner))
        finally:
            with self._daemon_lock:
                if self._daemon_thread is threading.current_thread():
                    self._daemon_thread = None

    async def _daemon_loop(self, owner: Any) -> None:
        from .services import _daemon_owner_state

        while not self._daemon_stop.is_set():
            try:
                _daemon_owner_state(owner, home=self.service.home)
            except PermissionError:
                return
            try:
                await self._tick()
            except Exception:
                pass
            await asyncio.to_thread(self._daemon_stop.wait, self.LOOP_SECONDS)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                # One malformed group or temporarily unavailable desktop must
                # not terminate scheduling for every other group.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.LOOP_SECONDS)
            except asyncio.TimeoutError:
                continue

    @staticmethod
    async def _await_if_needed(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    def _state_path(self, group_id: str) -> Path:
        return self.service.store.state_root(group_id) / "scheduler" / "trigger-states.json"

    async def _load_states(self, group_id: str) -> Dict[str, Dict[str, Any]]:
        for name in ("load_trigger_states", "get_trigger_states"):
            method = getattr(self.service.store, name, None)
            if not callable(method):
                continue
            try:
                value = await self._await_if_needed(method(group_id))
            except (TypeError, NotImplementedError):
                continue
            if isinstance(value, dict):
                states = value.get("triggers") if isinstance(value.get("triggers"), dict) else value
                return {str(key): dict(item) for key, item in states.items() if isinstance(item, dict)}
        try:
            value = json.loads(self._state_path(group_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        states = value.get("triggers") if isinstance(value, dict) else None
        return {str(key): dict(item) for key, item in states.items() if isinstance(item, dict)} if isinstance(states, dict) else {}

    async def _save_states(self, group_id: str, states: Mapping[str, Mapping[str, Any]]) -> None:
        payload = {
            "version": self.STATE_VERSION,
            "updated_at": time.time(),
            "triggers": {str(key): dict(value) for key, value in states.items()},
        }
        for name in ("save_trigger_states", "set_trigger_states"):
            method = getattr(self.service.store, name, None)
            if not callable(method):
                continue
            try:
                await self._await_if_needed(method(group_id, payload))
                return
            except (TypeError, NotImplementedError):
                continue
        path = self._state_path(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    async def _desktop_available(self) -> bool:
        method = getattr(getattr(self.service, "observation", None), "desktop_session_available", None)
        try:
            value = await self._await_if_needed(method()) if callable(method) else desktop_session_available()
        except Exception:
            return False
        return bool(value)

    @staticmethod
    def _fingerprint(trigger: Mapping[str, Any], version: int) -> str:
        encoded = json.dumps({"version": int(version), "trigger": trigger}, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _state_key(workflow_id: str, trigger_id: str) -> str:
        return f"{workflow_id}:{trigger_id}"

    @staticmethod
    def _activation_allows(activation: Mapping[str, Any], trigger_id: str, published_version: int) -> bool:
        gate = activation.get(trigger_id)
        if not isinstance(gate, dict):
            return True
        return gate.get("enabled") is not False and int(gate.get("version") or 0) == int(published_version)

    @staticmethod
    def _time_next(trigger: Mapping[str, Any], after: float) -> Optional[float]:
        config = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
        trigger_type = str(trigger.get("type") or "")
        if trigger_type == "interval":
            return float(after) + float(config.get("seconds") or 0)
        if trigger_type == "schedule":
            return next_schedule_time(config, float(after))
        if trigger_type == "at":
            return parse_at_timestamp(config.get("at", config.get("datetime", config.get("timestamp"))), timezone=config.get("timezone"))
        if trigger_type == "cron":
            return next_cron_time(
                str(config.get("expression") or config.get("cron") or ""),
                float(after),
                timezone=config.get("timezone"),
            )
        return None

    @staticmethod
    def _trigger_inputs(trigger: Mapping[str, Any]) -> Dict[str, Any]:
        direct = trigger.get("inputs")
        if isinstance(direct, dict):
            return dict(direct)
        config = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
        return dict(config.get("inputs")) if isinstance(config.get("inputs"), dict) else {}

    @staticmethod
    def _context(
        trigger: Mapping[str, Any],
        fingerprint: str,
        *,
        scheduled_for: float,
        detected_at: float,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context: Dict[str, Any] = {
            "trigger_id": str(trigger.get("id") or ""),
            "name": str(trigger.get("name") or trigger.get("id") or ""),
            "type": str(trigger.get("type") or ""),
            "scheduled_for": float(scheduled_for),
            "detected_at": float(detected_at),
            "definition_fingerprint": fingerprint,
            "source": "element_observer" if trigger.get("type") == "element" else "scheduler",
        }
        if isinstance(evidence, dict):
            context["evidence"] = evidence
        return context

    def _audit_trigger(self, kind: str, group_id: str, workflow_id: str, trigger: Mapping[str, Any], **details: Any) -> None:
        audit(
            self.service.home,
            kind,
            group_id=group_id,
            actor_id=str(trigger.get("actor_id") or "foreman"),
            details={"workflow_id": workflow_id, "trigger_id": str(trigger.get("id") or ""), "type": trigger.get("type"), **details},
        )

    async def _observe_element(self, group_id: str, trigger: Mapping[str, Any]) -> tuple[str, Dict[str, Any]]:
        config = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
        locator = config.get("locator") if isinstance(config.get("locator"), dict) else {}
        actor_id = str(trigger.get("actor_id") or "foreman")
        observation_run_id = "trigger_observe_" + uuid.uuid4().hex[:16]

        def take_snapshot() -> Any:
            with self.service.lease.hold(
                group_id=group_id,
                actor_id=actor_id,
                run_id=observation_run_id,
                observe_only=True,
            ):
                return self.service.session.call_tool_sync("Snapshot", {}, timeout=None)

        try:
            raw = await asyncio.to_thread(take_snapshot)
            snapshot = normalize_snapshot(raw)
            enhance = getattr(getattr(self.service, "observation", None), "enhance", None)
            if callable(enhance):
                enhanced = await self._await_if_needed(enhance(snapshot, locator))
                if isinstance(enhanced, dict):
                    snapshot = enhanced
        except LeaseConflict:
            return "unknown", {"status": "computer_control_busy", "message": "电脑控制正在使用，元素观察保持原布防状态"}
        except Exception as exc:
            return "unknown", {"status": "snapshot_unavailable", "message": str(exc)[:300]}
        match = resolve_locator(snapshot, locator)
        evidence = {
            "observation_id": snapshot.get("observation_id"),
            "provider": snapshot.get("provider"),
            "status": match.get("status"),
            "match_count": int(match.get("match_count") or 0),
            "focused_window": snapshot.get("focused_window"),
        }
        if match.get("status") == "ambiguous":
            return "unknown", evidence
        if match.get("status") == "unique":
            element = match.get("matches", [])[0]
            present = bool(element.get("visible", True)) and bool(element.get("enabled", True))
            evidence["element"] = {
                "window_name": element.get("window_name"),
                "control_type": element.get("control_type"),
                "name": element.get("name"),
                "automation_id": element.get("automation_id"),
            }
            return ("present" if present else "absent"), evidence
        health = str(snapshot.get("snapshot_health") or "").casefold()
        diagnostics = snapshot.get("diagnostics") if isinstance(snapshot.get("diagnostics"), dict) else {}
        diagnostic_code = str(diagnostics.get("code") or "").casefold()
        target_window = str(locator.get("window_name") or "").strip()
        target_count = snapshot.get("target_window_element_count")
        unavailable = health in {"target_elements_unavailable", "snapshot_unavailable"} or diagnostic_code in {
            "native_uia_unavailable",
            "snapshot_unavailable",
            "target_window_not_found",
            "target_window_elements_unavailable",
        }
        if target_window and isinstance(target_count, int) and target_count == 0:
            unavailable = True
        return ("unknown" if unavailable else "absent"), evidence

    async def _launch_pending(
        self,
        group_id: str,
        workflow_id: str,
        version: int,
        trigger: Mapping[str, Any],
        state: MutableMapping[str, Any],
        now: float,
    ) -> bool:
        if not state.get("pending"):
            return False
        if float(state.get("retry_after") or 0) > now:
            return False
        previous_run_id = str(state.get("last_run_id") or "")
        get_run = getattr(self.service.runner, "get", None)
        if previous_run_id and callable(get_run):
            try:
                previous_run = await self._await_if_needed(get_run(group_id, previous_run_id))
            except Exception:
                previous_run = None
            previous_status = str((previous_run or {}).get("status") or "") if isinstance(previous_run, dict) else ""
            if previous_status in {"running", "recovering", "waiting_approval", "awaiting_verification", "external_blocked"}:
                state.update({"waiting_on": "previous_trigger_run", "retry_after": now + 1.0, "updated_at": now})
                return True
        if not await self._desktop_available():
            state.update({"waiting_on": "desktop", "desktop_available": False, "updated_at": now})
            return True
        context = state.get("pending_context") if isinstance(state.get("pending_context"), dict) else {}
        previous_waiting = str(state.get("waiting_on") or "")
        previous_error = str(state.get("last_error") or "")
        state.update({"waiting_on": "runner", "desktop_available": True, "updated_at": now})
        try:
            run = await self.service.runner.start(
                group_id,
                workflow_id,
                actor_id=str(trigger.get("actor_id") or "foreman"),
                version=int(version),
                inputs=self._trigger_inputs(trigger),
                trigger_context=context,
            )
        except LeaseConflict:
            if previous_waiting != "computer_control_busy":
                self._audit_trigger("trigger.pending", group_id, workflow_id, trigger, reason="computer_control_busy")
            state.update({"waiting_on": "computer_control_busy", "retry_after": now + 1.0, "updated_at": now})
            return True
        except Exception as exc:
            if previous_waiting != "runner_error" or previous_error != str(exc)[:500]:
                self._audit_trigger("trigger.error", group_id, workflow_id, trigger, message=str(exc)[:500])
            cooldown = max(2.0, min(float(trigger.get("cooldown_seconds") or 0), 60.0))
            state.update({"waiting_on": "runner_error", "retry_after": now + cooldown, "last_error": str(exc)[:500], "updated_at": now})
            return True
        state.update({
            "pending": False,
            "pending_context": None,
            "waiting_on": "",
            "retry_after": None,
            "last_error": "",
            "last_fired_at": now,
            "last_run_id": run.get("run_id") if isinstance(run, dict) else None,
            "updated_at": now,
        })
        self._audit_trigger("trigger.run_started", group_id, workflow_id, trigger, run_id=state.get("last_run_id"))
        trigger_type = str(trigger.get("type") or "")
        if trigger_type == "at":
            state["completed"] = True
            state["next_due_at"] = None
        elif trigger_type in {"interval", "schedule", "cron"}:
            state["next_due_at"] = self._time_next(trigger, now)
        return True

    async def _process_trigger(
        self,
        group_id: str,
        workflow_id: str,
        version: int,
        trigger: Mapping[str, Any],
        states: MutableMapping[str, Dict[str, Any]],
        *,
        now: Optional[float] = None,
    ) -> bool:
        current = float(time.time() if now is None else now)
        trigger_id = str(trigger.get("id") or "")
        key = self._state_key(workflow_id, trigger_id)
        fingerprint = self._fingerprint(trigger, version)
        state = states.get(key)
        dirty = False
        if not isinstance(state, dict) or state.get("definition_fingerprint") != fingerprint:
            state = {
                "definition_fingerprint": fingerprint,
                "pending": False,
                "armed": True,
                "hit_count": 0,
                "created_at": current,
                "updated_at": current,
            }
            states[key] = state
            dirty = True
        if state.get("pending"):
            return await self._launch_pending(group_id, workflow_id, version, trigger, state, current) or dirty

        trigger_type = str(trigger.get("type") or "")
        if trigger_type == "element":
            config = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
            poll_seconds = element_poll_seconds(config)
            if current < float(state.get("next_poll_at") or 0):
                return dirty
            state.update({"next_poll_at": current + poll_seconds, "last_checked_at": current, "updated_at": current})
            dirty = True
            if not await self._desktop_available():
                state.update({"waiting_on": "desktop", "desktop_available": False, "last_observation": "unknown"})
                return True
            observation, evidence = await self._observe_element(group_id, trigger)
            fire, advanced = advance_element_edge(state, observation, required_hits=element_required_hits(config))
            state.clear()
            state.update(advanced)
            state.update({"waiting_on": "" if observation != "unknown" else "element_observation", "desktop_available": True, "observation_evidence": evidence, "updated_at": current})
            if fire:
                cooldown = float(trigger.get("cooldown_seconds") or 0)
                last_fired = float(state.get("last_fired_at") or 0)
                not_before = max(current, last_fired + cooldown)
                state.update({
                    "pending": True,
                    "retry_after": not_before if not_before > current else None,
                    "pending_context": self._context(trigger, fingerprint, scheduled_for=current, detected_at=current, evidence=evidence),
                })
                self._audit_trigger("trigger.detected", group_id, workflow_id, trigger, detected_at=current, evidence=evidence)
                self._audit_trigger("trigger.pending", group_id, workflow_id, trigger, scheduled_for=current)
                await self._save_states(group_id, states)
                await self._launch_pending(group_id, workflow_id, version, trigger, state, current)
            return True

        if trigger_type not in {"interval", "schedule", "at", "cron"} or state.get("completed"):
            return dirty
        if state.get("next_due_at") is None:
            state["next_due_at"] = self._time_next(trigger, current)
            state["updated_at"] = current
            dirty = True
        next_due = state.get("next_due_at")
        if next_due is None or current < float(next_due):
            return dirty
        state.update({
            "pending": True,
            "retry_after": None,
            "pending_context": self._context(trigger, fingerprint, scheduled_for=float(next_due), detected_at=current),
            "updated_at": current,
        })
        self._audit_trigger("trigger.detected", group_id, workflow_id, trigger, scheduled_for=float(next_due), detected_at=current)
        self._audit_trigger("trigger.pending", group_id, workflow_id, trigger, scheduled_for=float(next_due))
        await self._save_states(group_id, states)
        await self._launch_pending(group_id, workflow_id, version, trigger, state, current)
        return True

    async def _tick(self) -> None:
        for group_dir in (self.service.home / "groups").glob("*"):
            group_id = group_dir.name
            group = load_group(group_id)
            if group is None or str(group.doc.get("state") or "active") not in {"active", "idle"}:
                continue
            states = await self._load_states(group_id)
            dirty = False
            setup_status = self.service.setup.status()
            fingerprint = str(setup_status.get("fingerprint") or "")
            if str(setup_status.get("phase") or "") != "ready" or not fingerprint:
                continue
            for manifest in self.service.store.list(group_id):
                workflow_id = str(manifest.get("workflow_id") or "")
                published = manifest.get("published_version")
                if not workflow_id or not published:
                    continue
                trusted = manifest.get("trusted") if isinstance(manifest.get("trusted"), dict) else {}
                trust = trusted.get(str(published))
                if not isinstance(trust, dict) or trust.get("fingerprint") != fingerprint:
                    continue
                try:
                    workflow = self.service.store.get(group_id, workflow_id, version=int(published))
                except Exception:
                    continue
                triggers = workflow["definition"].get("triggers") if isinstance(workflow.get("definition"), dict) else []
                activation = manifest.get("trigger_activation") if isinstance(manifest.get("trigger_activation"), dict) else {}
                for trigger in triggers if isinstance(triggers, list) else []:
                    if not isinstance(trigger, dict) or not trigger.get("enabled") or trigger.get("type") not in self.EXECUTED_TYPES:
                        continue
                    if not self._activation_allows(activation, str(trigger.get("id") or ""), int(published)):
                        continue
                    try:
                        dirty = await self._process_trigger(group_id, workflow_id, int(published), trigger, states) or dirty
                    except Exception as exc:
                        key = self._state_key(workflow_id, str(trigger.get("id") or ""))
                        state = states.setdefault(key, {})
                        state.update({"last_error": str(exc)[:500], "updated_at": time.time()})
                        dirty = True
            if dirty:
                await self._save_states(group_id, states)

    async def status(self, group_id: str, workflow_id: str) -> Dict[str, Any]:
        states = await self._load_states(group_id)
        selected = self.service.store.get(group_id, workflow_id)
        manifest = selected.get("manifest") if isinstance(selected.get("manifest"), dict) else {}
        published = int(manifest.get("published_version") or 0)
        if published and int(selected.get("version") or 0) != published:
            selected = self.service.store.get(group_id, workflow_id, version=published)
        definition = selected.get("definition") if isinstance(selected.get("definition"), dict) else {}
        activation = manifest.get("trigger_activation") if isinstance(manifest.get("trigger_activation"), dict) else {}
        result: Dict[str, Dict[str, Any]] = {}
        for trigger in definition.get("triggers", []) if isinstance(definition.get("triggers"), list) else []:
            if not isinstance(trigger, dict):
                continue
            trigger_id = str(trigger.get("id") or "")
            gate = activation.get(trigger_id) if isinstance(activation.get(trigger_id), dict) else None
            runtime_enabled = bool(trigger.get("enabled")) and self._activation_allows(activation, trigger_id, published)
            state = dict(states.get(self._state_key(workflow_id, trigger_id), {}))
            result[trigger_id] = {
                "trigger_id": trigger_id,
                "type": trigger.get("type"),
                "definition_enabled": bool(trigger.get("enabled")),
                "runtime_enabled": runtime_enabled,
                "activation": dict(gate) if isinstance(gate, dict) else None,
                **state,
            }
        return {
            "available": True,
            "running": bool(
                (self._task is not None and not self._task.done())
                or (self._daemon_thread is not None and self._daemon_thread.is_alive())
            ),
            "supported_types": sorted(self.EXECUTED_TYPES),
            "workflow_id": workflow_id,
            "published_version": published or None,
            "triggers": result,
        }

    async def test_trigger(self, group_id: str, workflow_id: str, trigger: Mapping[str, Any]) -> Dict[str, Any]:
        parsed = WorkflowTrigger.model_validate(dict(trigger))
        try:
            group_root = self.service.store._group(group_id).path  # type: ignore[attr-defined]
        except Exception:
            group_root = None
        validate_trigger(parsed, group_root=group_root)
        value = parsed.model_dump(mode="json")
        now = time.time()
        preview: Dict[str, Any] = {"type": parsed.type, "inputs": dict(parsed.inputs)}
        if parsed.type in {"interval", "schedule", "at", "cron"}:
            preview["next_fire_at"] = self._time_next(value, now)
        elif parsed.type == "element":
            preview.update({
                "poll_seconds": element_poll_seconds(parsed.config),
                "required_hits": element_required_hits(parsed.config),
            })
            if not await self._desktop_available():
                preview.update({"observation": "unknown", "waiting_on": "desktop"})
            else:
                observation, evidence = await self._observe_element(group_id, value)
                preview.update({"observation": observation, "evidence": evidence})
        else:
            preview["supported"] = False
        return {
            "available": True,
            "valid": True,
            "read_only": True,
            "workflow_id": workflow_id,
            "trigger_id": parsed.id,
            "preview": preview,
        }
