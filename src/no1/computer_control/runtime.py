from __future__ import annotations

import asyncio
import base64
import json
import inspect
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util.fs import atomic_write_text
from .lease import ComputerControlLease, LeaseConflict
from .mcp import (
    MCPOutcomeUnknown,
    MCPUnavailable,
    WindowsMCPSession,
    normalize_tool_result,
    validate_arguments_against_schema,
    validate_workflow_tools,
)
from .models import WorkflowDefinition
from .storage import WorkflowStore
from .elements import element_center, locator_from_element, normalize_snapshot, resolve_locator

REF_RE = re.compile(r"^\$\{(inputs|steps)\.([A-Za-z0-9_.-]+)\}$")
SECRET_RE = re.compile(r"^\$\{secret:([A-Za-z_][A-Za-z0-9_]*)\}$")


class WorkflowRunner:
    def __init__(self, home: Path, store: WorkflowStore, lease: ComputerControlLease, session: WindowsMCPSession, fingerprint_provider: Optional[Any] = None):
        self.home = home
        self.store = store
        self.lease = lease
        self.session = session
        self.fingerprint_provider = fingerprint_provider or (lambda: "")
        self._tasks: Dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self._sync_loop: asyncio.AbstractEventLoop | None = None
        self._sync_thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()

    class _ExternalLeaseLost(RuntimeError):
        def __init__(self, lease: Optional[Dict[str, Any]] = None):
            super().__init__("computer control is now occupied by another group")
            self.lease = lease or {}

    async def _heartbeat_loop(self, run: Dict[str, Any], lost: asyncio.Event) -> None:
        group_id, actor_id, run_id = str(run["group_id"]), str(run["actor_id"]), str(run["run_id"])
        while not lost.is_set():
            try:
                await asyncio.sleep(max(1.0, float(self.lease.HEARTBEAT_SECONDS) / 2))
                if lost.is_set():
                    return
                self.lease.heartbeat(group_id=group_id, actor_id=actor_id, run_id=run_id)
                run["lease"] = {"last_heartbeat_at": time.time(), "status": "owned"}
            except asyncio.CancelledError:
                raise
            except PermissionError:
                current = self.lease.status().get("lease")
                if not isinstance(current, dict) or str(current.get("run_id") or "") == run_id:
                    try:
                        self.lease.acquire(group_id=group_id, actor_id=actor_id, run_id=run_id)
                        metrics = run.setdefault("metrics", {})
                        metrics["lease_recoveries"] = int(metrics.get("lease_recoveries") or 0) + 1
                        run["lease"] = {"last_recovered_at": time.time(), "status": "recovered"}
                        self._emit("lease.recovered", group_id=group_id, run_id=run_id)
                        continue
                    except LeaseConflict:
                        current = self.lease.status().get("lease")
                run["lease"] = {"status": "lost", "owner": current or {}}
                run["status"] = "external_blocked"
                run["external_blocked"] = {"reason": "computer_control_busy", "lease": current or {}, "at": time.time()}
                self._emit("run.external_blocked", group_id=group_id, run_id=run_id, lease=current or {})
                lost.set()
                return

    def _ensure_sync_loop(self) -> asyncio.AbstractEventLoop:
        with self._sync_lock:
            if self._sync_loop is not None and self._sync_loop.is_running():
                return self._sync_loop
            loop = asyncio.new_event_loop()

            def run_loop() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(target=run_loop, name="onecolleague-computer-runner", daemon=True)
            thread.start()
            self._sync_loop = loop
            self._sync_thread = thread
            return loop

    async def _catalog(self) -> List[Dict[str, Any]]:
        method = getattr(self.session, "catalog", None)
        if not callable(method):
            return []
        value = method()
        if inspect.isawaitable(value):
            value = await value
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    async def _resolve_element_action(
        self,
        node: Any,
        arguments: Dict[str, Any],
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Resolve an action target from a fresh UI tree before mutating input."""
        tool = str(getattr(node, "tool", "") or "").lower()
        if tool not in {"click", "type"}:
            return arguments
        if getattr(node, "target", None) is None and not any(key in arguments for key in ("label", "loc", "x", "y")):
            # Preserve compatibility with synthetic/test tools that do not
            # expose a UI target (real Windows-MCP Click/Type schemas reject
            # such calls during the normal workflow validation).
            return arguments
        snapshot_result = await self.session.call_tool("Snapshot", {}, timeout=None)
        snapshot = normalize_snapshot(snapshot_result)
        target = getattr(node, "target", None)
        locator = target.model_dump(mode="json") if target is not None else None
        match: Dict[str, Any] = {"status": "not_found", "matches": [], "match_count": 0, "confidence": "none"}
        if locator:
            match = resolve_locator(snapshot, locator)
        elif arguments.get("label"):
            label = str(arguments.get("label"))
            match = resolve_locator(snapshot, {"name": label, "text": label, "match": "exact"})
        selected = match.get("matches", [])[0] if match.get("status") == "unique" else None
        resolution: Dict[str, Any] = {
            "strategy": "element" if selected is not None else "unresolved",
            "status": match.get("status"),
            "confidence": match.get("confidence"),
            "candidate_count": int(match.get("match_count") or 0),
        }
        if selected is None:
            fallback_policy = str((locator or {}).get("fallback_policy") or "controlled")
            has_coordinate = any(key in arguments for key in ("loc", "x", "y"))
            anchor = (locator or {}).get("position_anchor") if isinstance((locator or {}).get("position_anchor"), dict) else {}
            if fallback_policy == "controlled" and not has_coordinate and isinstance(anchor, dict) and isinstance(anchor.get("x"), (int, float)) and isinstance(anchor.get("y"), (int, float)):
                arguments = {**arguments, "loc": [round(float(anchor["x"])), round(float(anchor["y"]))]}
                resolution.update({"strategy": "controlled_coordinate_fallback", "stability": "low", "reason": "元素暂时未找到，使用已验证位置锚点一次"})
                event["target_resolution"] = resolution
                return {key: value for key, value in arguments.items() if key != "coordinate_fallback"}
            if fallback_policy == "never" or not has_coordinate:
                event["target_resolution"] = resolution
                raise ValueError("未能在最新桌面快照中唯一找到操作元素；已停止，避免误点")
            resolution.update({"strategy": "controlled_coordinate_fallback", "stability": "low", "reason": "元素未找到，使用原始坐标一次"})
            event["target_resolution"] = resolution
            return {key: value for key, value in arguments.items() if key != "coordinate_fallback"}

        if not bool(selected.get("visible", True)) or not bool(selected.get("enabled", True)):
            event["target_resolution"] = {**resolution, "reason": "元素不可见或不可用"}
            raise ValueError("目标元素当前不可见或不可用")
        event["target_resolution"] = {**resolution, "element": locator_from_element(selected)}
        output = dict(arguments)
        catalog = await self._catalog()
        item = next((item for item in catalog if str(item.get("name") or "") == str(getattr(node, "tool", ""))), {})
        schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        label = str(selected.get("name") or selected.get("text") or "").strip()
        center = element_center(selected)
        # Prefer semantic arguments. Coordinates are only generated for a
        # matched element when the live tool has no usable label parameter.
        if label and (not props or "label" in props):
            output["label"] = label
            output.pop("loc", None)
            output.pop("x", None)
            output.pop("y", None)
        elif center is not None and (not props or "loc" in props):
            output["loc"] = center
            output.pop("x", None)
            output.pop("y", None)
        elif center is not None and "x" in props and "y" in props:
            output["x"], output["y"] = center
        return output

    def start_sync(
        self,
        group_id: str,
        workflow_id: str,
        *,
        actor_id: str,
        version: Optional[int],
        inputs: Dict[str, Any],
        authorization: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Submit a run from the synchronous MCP tool server."""
        loop = self._ensure_sync_loop()
        future = asyncio.run_coroutine_threadsafe(
            self.start(
                group_id,
                workflow_id,
                actor_id=actor_id,
                version=version,
                inputs=inputs,
                authorization=authorization,
            ),
            loop,
        )
        return future.result(timeout=15)

    def _run_path(self, group_id: str, run_id: str) -> Path:
        return self.store.state_root(group_id) / "runs" / f"{run_id}.json"

    def _approval_path(self, group_id: str, run_id: str, node_id: str) -> Path:
        return self.store.state_root(group_id) / "approvals" / run_id / f"{node_id}.json"

    def _recovery_path(self, group_id: str, run_id: str, recovery_id: str) -> Path:
        return self.store.state_root(group_id) / "recoveries" / run_id / f"{recovery_id}.json"

    def _write(self, group_id: str, run: Dict[str, Any]) -> None:
        path = self._run_path(group_id, str(run["run_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(run, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    @staticmethod
    def _emit(kind: str, **data: Any) -> None:
        try:
            from ..kernel.events import publish_event

            publish_event(f"computer_control.{kind}", data)
        except Exception:
            pass

    def get(self, group_id: str, run_id: str) -> Dict[str, Any]:
        error: BaseException | None = None
        value: Any = None
        for attempt in range(3):
            try:
                value = json.loads(self._run_path(group_id, run_id).read_text(encoding="utf-8"))
                break
            except PermissionError as exc:
                error = exc
                if attempt < 2:
                    time.sleep(0.01)
            except (OSError, ValueError) as exc:
                error = exc
                break
        else:
            value = None
        if error is not None and value is None:
            raise KeyError(run_id) from error
        if not isinstance(value, dict):
            raise KeyError(run_id)
        return value

    def list(self, group_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        root = self.store.state_root(group_id) / "runs"
        if not root.exists():
            return []
        result = []
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: max(1, min(limit, 200))]:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict):
                result.append(value)
        return result

    def decide_approval(self, group_id: str, run_id: str, node_id: str, *, approved: bool) -> Dict[str, Any]:
        run = self.get(group_id, run_id)
        if str(run.get("status") or "") != "waiting_approval" or str(run.get("current_node_id") or "") != node_id:
            raise ValueError("run is not waiting for approval on this node")
        path = self._approval_path(group_id, run_id, node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"approved": bool(approved), "decided_at": time.time()}, ensure_ascii=False) + "\n")
        return {"run_id": run_id, "node_id": node_id, "approved": bool(approved)}

    def submit_recovery(
        self,
        group_id: str,
        run_id: str,
        recovery_id: str,
        *,
        actor_id: str,
        tool: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        run = self.get(group_id, run_id)
        recovery = run.get("recovery") if isinstance(run.get("recovery"), dict) else {}
        if str(run.get("status") or "") != "recovering" or str(recovery.get("recovery_id") or "") != recovery_id:
            raise ValueError("run is not waiting for this recovery")
        if str(run.get("actor_id") or "") != actor_id:
            raise PermissionError("recovery belongs to another actor")
        path = self._recovery_path(group_id, run_id, recovery_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {"tool": str(tool or "").strip(), "arguments": arguments, "actor_id": actor_id, "submitted_at": time.time()}
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return {"run_id": run_id, "recovery_id": recovery_id, "accepted": True}

    async def _wait_for_recovery(self, run: Dict[str, Any], event: Dict[str, Any], *, error: Exception, attempt: int) -> Dict[str, Any]:
        group_id, run_id = str(run["group_id"]), str(run["run_id"])
        recovery_id = "recovery_" + uuid.uuid4().hex[:12]
        observation: Any = None
        try:
            observation = normalize_tool_result("Snapshot", await self.session.call_tool("Snapshot", {}, timeout=None))
            observation = self._redact_result(observation)
        except Exception as observation_error:
            observation = {"error": str(observation_error)[:1000]}
        catalog = await self._catalog()
        active_tool = str(event.get("tool") or "")
        tool_schema = next(
            (item.get("inputSchema") for item in catalog if isinstance(item, dict) and str(item.get("name") or "") == active_tool),
            {},
        )
        recovery = {
            "recovery_id": recovery_id,
            "node_id": event.get("node_id"),
            "attempt": attempt,
            "error": {"code": "tool_call_failed", "message": str(error)[:1000]},
            "tool": active_tool,
            "tool_schema": tool_schema if isinstance(tool_schema, dict) else {},
            "observation": observation,
            "delivery_status": "pending",
        }
        run["status"] = "recovering"
        run["recovery"] = recovery
        event["status"] = "recovering"
        event["recovery_id"] = recovery_id
        self._write(group_id, run)
        self._emit("recovery.required", group_id=group_id, run_id=run_id, actor_id=run.get("actor_id"), **recovery)
        delivered = False
        try:
            from ..contracts.v1 import SystemNotifyData
            from ..daemon.messaging.delivery import dispatch_system_notify_event_to_actor
            from ..kernel.group import load_group
            from ..kernel.ledger import append_event

            group = load_group(group_id)
            if group is None:
                raise RuntimeError("工作组不存在")
            notify = SystemNotifyData(
                kind="info",
                priority="high",
                title="电脑控制需要自适应恢复",
                message=(
                    f"运行 {run_id} 的步骤 {event.get('node_id')} 执行失败。"
                    f"请根据通知 context 中的 recovery_id、工具 Schema 和桌面观察结果，"
                    "通过 onecolleague_computer_run action=recover 提交修正参数。"
                ),
                target_actor_id=str(run.get("actor_id") or ""),
                context={"kind": "computer_control_recovery", "group_id": group_id, "run_id": run_id, **recovery},
            )
            notify_event = append_event(
                group.ledger_path,
                kind="system.notify",
                group_id=group.group_id,
                scope_key="",
                by="system",
                data=notify.model_dump(mode="json"),
            )
            while not delivered:
                delivered = bool(dispatch_system_notify_event_to_actor(group, event=notify_event, actor_id=str(run.get("actor_id") or ""), async_flush=True))
                if not delivered:
                    await asyncio.sleep(2)
        except Exception as delivery_error:
            recovery["delivery_error"] = str(delivery_error)[:1000]
        recovery["delivery_status"] = "delivered" if delivered else "failed"
        run["recovery"] = recovery
        self._write(group_id, run)
        self._emit("recovery.delivery", group_id=group_id, run_id=run_id, recovery_id=recovery_id, status=recovery["delivery_status"])
        if not delivered:
            raise RuntimeError("AI 自适应恢复通知无法投递：执行智能体未运行或当前不可接收消息")
        path = self._recovery_path(group_id, run_id, recovery_id)
        while True:
            if run_id in self._cancelled:
                raise asyncio.CancelledError()
            if path.exists():
                try:
                    patch = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    patch = {}
                if isinstance(patch, dict) and isinstance(patch.get("arguments"), dict):
                    run["status"] = "running"
                    run.pop("recovery", None)
                    event["status"] = "running"
                    self._write(group_id, run)
                    return patch
            await asyncio.sleep(0.5)

    async def _wait_for_approval(self, run: Dict[str, Any], node_id: str, *, timeout: Optional[float] = None) -> None:
        group_id, run_id = str(run["group_id"]), str(run["run_id"])
        path = self._approval_path(group_id, run_id, node_id)
        run["status"] = "waiting_approval"
        self._write(group_id, run)
        self._emit("run.approval_required", group_id=group_id, run_id=run_id, node_id=node_id)
        while True:
            if run_id in self._cancelled:
                raise asyncio.CancelledError()
            if path.exists():
                try:
                    decision = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    decision = {}
                if decision.get("approved") is True:
                    run["status"] = "running"
                    self._write(group_id, run)
                    return
                raise PermissionError("用户已拒绝该电脑操作")
            await asyncio.sleep(0.5)

    async def start(
        self,
        group_id: str,
        workflow_id: str,
        *,
        actor_id: str,
        version: Optional[int],
        inputs: Dict[str, Any],
        authorization: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selected = self.store.get(group_id, workflow_id, version=version)
        definition = WorkflowDefinition.model_validate({k: v for k, v in selected["definition"].items() if k != "change_note"})
        start_catalog = await self._catalog()
        if start_catalog:
            validate_workflow_tools(definition, start_catalog)
        effective_inputs = self._effective_inputs(definition, inputs)
        run_id = "run_" + uuid.uuid4().hex[:16]
        self.lease.acquire(group_id=group_id, actor_id=actor_id, run_id=run_id)
        now = time.time()
        run = {
            "run_id": run_id,
            "group_id": group_id,
            "workflow_id": workflow_id,
            "version": selected["version"],
            "actor_id": actor_id,
            "status": "running",
            "current_node_id": None,
            "started_at": now,
            "updated_at": now,
            "events": [],
            "metrics": {
                "replay_success": False,
                "transport_restarts": 0,
                "transport_restarts_at_start": int(getattr(self.session, "transport_restarts", 0)),
            },
            "authorization": {
                key: authorization.get(key)
                for key in (
                    "request_id",
                    "allow_publish",
                    "allow_trust",
                    "allow_unattended_triggers",
                    "allow_high_risk",
                    "created_ts",
                )
            } if isinstance(authorization, dict) else {},
        }
        self._write(group_id, run)
        task = asyncio.create_task(self._execute(run, definition, effective_inputs))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(run_id, None))
        return run

    @staticmethod
    def _effective_inputs(definition: WorkflowDefinition, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {**WorkflowRunner._effective_input_values(definition.inputs), **inputs}

    @staticmethod
    def _effective_input_values(inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: value.get("default") if isinstance(value, dict) and "default" in value else value
            for key, value in inputs.items()
        }

    @staticmethod
    def _resolve(value: Any, inputs: Dict[str, Any], steps: Dict[str, Any]) -> Any:
        if isinstance(value, str):
            secret = SECRET_RE.fullmatch(value)
            if secret:
                import os

                return os.environ.get(secret.group(1), "")
            ref = REF_RE.fullmatch(value)
            if ref:
                root: Any = inputs if ref.group(1) == "inputs" else steps
                for part in ref.group(2).split("."):
                    root = root.get(part) if isinstance(root, dict) else None
                return root
            return value
        if isinstance(value, dict):
            return {key: WorkflowRunner._resolve(child, inputs, steps) for key, child in value.items()}
        if isinstance(value, list):
            return [WorkflowRunner._resolve(child, inputs, steps) for child in value]
        return value

    @staticmethod
    def _path(value: Any, path: str) -> tuple[bool, Any]:
        current = value
        if not path:
            return True, current
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                return False, None
        return True, current

    @classmethod
    def _assert_success(cls, condition: Any, result: Any, steps: Dict[str, Any]) -> None:
        if not condition:
            return
        if isinstance(condition, str):
            resolved = cls._resolve(condition, {}, steps)
            if not bool(resolved):
                raise AssertionError(f"success condition was not satisfied: {condition}")
            return
        payload = condition.model_dump(mode="json") if hasattr(condition, "model_dump") else condition
        if not isinstance(payload, dict):
            raise AssertionError("invalid success condition")
        source = str(payload.get("source") or "result")
        root = result
        if source.startswith("steps."):
            exists, root = cls._path(steps, source[6:])
            if not exists:
                raise AssertionError(f"success condition source was not found: {source}")
        exists, actual = cls._path(root, str(payload.get("path") or ""))
        operator = str(payload.get("operator") or "truthy")
        expected = payload.get("expected")
        passed = exists if operator == "exists" else (
            exists and bool(actual) if operator == "truthy" else
            exists and actual == expected if operator == "equals" else
            exists and (
                expected in actual if isinstance(actual, (str, list, dict)) else False
            )
        )
        if not passed:
            raise AssertionError(f"success assertion failed ({operator})")

    def _store_artifacts(self, group_id: str, run_id: str, node_id: str, value: Any) -> Any:
        counter = 0

        def visit(child: Any) -> Any:
            nonlocal counter
            if isinstance(child, dict):
                if str(child.get("type") or "").lower() == "image" and isinstance(child.get("data"), str):
                    try:
                        raw = base64.b64decode(child["data"], validate=True)
                    except (ValueError, TypeError):
                        return {key: visit(item) for key, item in child.items()}
                    counter += 1
                    mime = str(child.get("mimeType") or child.get("mime_type") or "image/png")
                    suffix = ".jpg" if "jpeg" in mime else ".webp" if "webp" in mime else ".png"
                    relative = Path("artifacts") / run_id / f"{node_id}_{counter}{suffix}"
                    path = self.store.state_root(group_id) / "runs" / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                    return {"type": "image_artifact", "artifact_id": f"{node_id}_{counter}", "path": str(relative), "mime_type": mime, "size": len(raw)}
                return {key: visit(item) for key, item in child.items()}
            if isinstance(child, list):
                return [visit(item) for item in child]
            return child

        return visit(value)

    async def _execute(self, run: Dict[str, Any], definition: WorkflowDefinition, inputs: Dict[str, Any]) -> None:
        group_id, run_id, actor_id = run["group_id"], run["run_id"], run["actor_id"]
        nodes = {node.id: node for node in definition.nodes}
        outgoing: Dict[str, List[Any]] = {node_id: [] for node_id in nodes}
        for edge in definition.edges:
            outgoing[edge.source].append(edge)
        current = next(node.id for node in definition.nodes if node.type == "start")
        steps: Dict[str, Any] = {}
        loop_counts: Dict[str, int] = {}
        deadline = (
            time.monotonic() + float(definition.max_run_seconds)
            if definition.max_run_seconds is not None
            else None
        )
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(run, lease_lost))
        try:
            while True:
                if run_id in self._cancelled:
                    raise asyncio.CancelledError()
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError("workflow run exceeded its configured safety limit")
                if lease_lost.is_set():
                    raise self._ExternalLeaseLost(run.get("lease", {}).get("owner"))
                node = nodes[current]
                run["current_node_id"] = current
                event = {"node_id": current, "type": node.type, "status": "running", "started_at": time.time()}
                run["events"].append(event)
                run["updated_at"] = time.time()
                self._write(group_id, run)
                self._emit("node.started", group_id=group_id, run_id=run_id, workflow_id=run.get("workflow_id"), node_id=current, node_type=node.type)
                result: Any = None
                branch = "next"
                if node.type == "action":
                    self.lease.require(group_id=group_id, actor_id=actor_id, run_id=run_id, allow_observe=False)
                    arguments = self._resolve(node.arguments, inputs, steps)
                    arguments = await self._resolve_element_action(node, arguments, event)
                    live_catalog = await self._catalog()
                    catalog_item = next((item for item in live_catalog if str(item.get("name") or "") == node.tool), {})
                    if catalog_item:
                        validate_arguments_against_schema(
                            node.tool,
                            arguments,
                            catalog_item.get("inputSchema") if isinstance(catalog_item.get("inputSchema"), dict) else {},
                        )
                    last_error: Optional[Exception] = None
                    active_tool = node.tool
                    event["tool"] = active_tool
                    recovered_arguments: Optional[Dict[str, Any]] = None
                    configured_attempts = node.retries + 1
                    for attempt in range(configured_attempts):
                        try:
                            result = normalize_tool_result(active_tool, await self.session.call_tool(active_tool, arguments, timeout=node.timeout_seconds))
                            last_error = None
                            break
                        except Exception as exc:
                            last_error = exc
                            event["attempt"] = attempt + 1
                            if isinstance(exc, (MCPUnavailable, MCPOutcomeUnknown)):
                                raise
                    recovery_attempt = 0
                    while last_error is not None and node.adaptive and recovery_attempt < 3:
                        recovery_attempt += 1
                        patch = await self._wait_for_recovery(run, event, error=last_error, attempt=recovery_attempt)
                        patched_tool = str(patch.get("tool") or active_tool).strip() or active_tool
                        catalog_names = {str(item.get("name") or "") for item in await self._catalog() if isinstance(item, dict)}
                        if catalog_names and patched_tool not in catalog_names:
                            last_error = ValueError(f"unknown Windows-MCP tool in recovery: {patched_tool}")
                            continue
                        from .risk import classify_tool

                        risk_rank = {"low": 0, "medium": 1, "high": 2}
                        if risk_rank[classify_tool(patched_tool, patch.get("arguments"))] > risk_rank[classify_tool(node.tool, node.arguments)]:
                            last_error = PermissionError("adaptive recovery cannot increase the tool risk level")
                            continue
                        active_tool = patched_tool
                        arguments = self._resolve(patch.get("arguments") or {}, inputs, steps)
                        event["tool"] = active_tool
                        try:
                            result = normalize_tool_result(active_tool, await self.session.call_tool(active_tool, arguments, timeout=node.timeout_seconds))
                            recovered_arguments = patch.get("arguments")
                            last_error = None
                        except Exception as exc:
                            last_error = exc
                    if last_error is not None:
                        raise last_error
                    if lease_lost.is_set():
                        raise self._ExternalLeaseLost(run.get("lease", {}).get("owner"))
                    if node.tool.lower() in {"click", "type"}:
                        try:
                            after_snapshot = normalize_snapshot(await self.session.call_tool("Snapshot", {}, timeout=None))
                            event["post_action_observation"] = {
                                "element_count": after_snapshot.get("count", 0),
                                "warnings": after_snapshot.get("warnings", []),
                            }
                        except Exception as exc:
                            event["post_action_observation"] = {"status": "unavailable", "message": str(exc)[:300]}
                    steps[current] = result
                    self._assert_success(node.success_condition, result, steps)
                    if recovered_arguments is not None:
                        event["adaptive_recovery"] = {"attempts": recovery_attempt, "tool": active_tool}
                        try:
                            proposal_payload = definition.model_dump(mode="json")
                            for candidate in proposal_payload.get("nodes", []):
                                if isinstance(candidate, dict) and str(candidate.get("id") or "") == current:
                                    candidate["tool"] = active_tool
                                    candidate["arguments"] = recovered_arguments
                                    break
                            proposal_definition = WorkflowDefinition.model_validate(proposal_payload)
                            proposal = self.store.create_proposal(
                                group_id,
                                str(run["workflow_id"]),
                                proposal_definition,
                                base_version=int(run["version"]),
                                created_by=actor_id,
                                summary=f"运行时自适应修复步骤“{node.title or node.id}”",
                            )
                            event["optimization_proposal_id"] = proposal.get("proposal_id")
                            self._emit("optimization.proposed", group_id=group_id, run_id=run_id, workflow_id=run.get("workflow_id"), proposal_id=proposal.get("proposal_id"))
                        except Exception:
                            pass
                elif node.type == "wait":
                    duration = node.duration_seconds
                    if duration is None:
                        await asyncio.Event().wait()
                    else:
                        await asyncio.sleep(float(duration))
                elif node.type == "condition":
                    resolved = self._resolve(node.condition, inputs, steps)
                    branch = "true" if bool(resolved) else "false"
                elif node.type == "loop":
                    count = loop_counts.get(current, 0)
                    if count < int(node.max_iterations or 1):
                        loop_counts[current] = count + 1
                        branch = "body"
                    else:
                        branch = "done"
                elif node.type == "approval":
                    event["status"] = "waiting_approval"
                    event["message"] = "等待用户确认后继续"
                    self._write(group_id, run)
                    await self._wait_for_approval(run, current)
                stored_result = self._store_artifacts(group_id, run_id, current, result) if definition.save_screenshots else result
                event.update({"status": "completed", "finished_at": time.time(), "result": self._redact_result(stored_result)})
                self._emit("node.completed", group_id=group_id, run_id=run_id, workflow_id=run.get("workflow_id"), node_id=current, node_type=node.type)
                if node.type == "end":
                    break
                choices = [edge for edge in outgoing[current] if edge.branch == branch]
                if not choices and branch != "next":
                    choices = [edge for edge in outgoing[current] if edge.branch == "next"]
                if not choices:
                    raise RuntimeError(f"node {current} has no {branch} transition")
                current = choices[0].target
            run.update({"current_node_id": None, "finished_at": time.time(), "updated_at": time.time()})
            run["metrics"]["replay_success"] = True
            final_evidence_ok = True
            if definition.save_screenshots:
                try:
                    screenshot = normalize_tool_result(
                        "Screenshot",
                        await self.session.call_tool(
                            "Screenshot",
                            {"display": [0], "use_annotation": False},
                            timeout=None,
                        ),
                    )
                    stored_screenshot = self._store_artifacts(group_id, run_id, "final_evidence", screenshot)
                    run["evidence"] = {
                        "tool": "Screenshot",
                        "captured_at": time.time(),
                        "result": self._redact_result(stored_screenshot),
                    }
                    self._emit("final_evidence_captured", group_id=group_id, run_id=run_id)
                except Exception as evidence_error:
                    final_evidence_ok = False
                    run["evidence_error"] = {"code": "final_evidence_failed", "message": str(evidence_error)[:1000]}
            if not final_evidence_ok:
                run["status"] = "awaiting_verification"
                return
            if definition.auto_verify:
                authorization = run.get("authorization") if isinstance(run.get("authorization"), dict) else {}
                has_request_authorization = bool(str(authorization.get("request_id") or "").strip())
                finalized: Dict[str, Any] = {}
                if has_request_authorization and authorization.get("allow_publish") is not False:
                    finalized["published"] = self.store.publish(group_id, str(run["workflow_id"]), int(run["version"]))
                fingerprint = str(self.fingerprint_provider() or "")
                if has_request_authorization and authorization.get("allow_trust") is not False and fingerprint:
                    finalized["trusted"] = self.store.trust(
                        group_id,
                        str(run["workflow_id"]),
                        int(run["version"]),
                        fingerprint=fingerprint,
                        permissions=["all_windows_mcp_tools"],
                    )
                run["verification"] = {
                    "passed": True,
                    "summary": "所有步骤和最终证据已完成",
                    "verified_by": actor_id,
                    "verified_at": time.time(),
                    "automatic": True,
                }
                run["finalization"] = {
                    "published": "published" in finalized,
                    "trusted": "trusted" in finalized,
                    "unattended_triggers_authorized": has_request_authorization and authorization.get("allow_unattended_triggers") is not False,
                }
                run["status"] = "published" if finalized else "verified"
            else:
                run["status"] = "awaiting_verification"
        except self._ExternalLeaseLost as exc:
            run.update({"status": "external_blocked", "error": {"code": "computer_control_busy", "message": str(exc)}, "finished_at": time.time(), "updated_at": time.time()})
        except asyncio.CancelledError:
            run.update({"status": "cancelled", "finished_at": time.time(), "updated_at": time.time()})
        except Exception as exc:
            if run.get("status") == "external_blocked" or (
                isinstance(exc, PermissionError) and "computer_control_lease_required" in str(exc)
            ):
                run.update(
                    {
                        "status": "external_blocked",
                        "error": {"code": "computer_control_busy", "message": str(exc)[:2000]},
                        "finished_at": time.time(),
                        "updated_at": time.time(),
                    }
                )
                return
            if run.get("events") and isinstance(run["events"][-1], dict):
                failed_event = run["events"][-1]
                if str(failed_event.get("status") or "") in {"running", "recovering"}:
                    failed_event.update({
                        "status": "failed",
                        "finished_at": time.time(),
                        "error": {"code": str(getattr(exc, "code", "run_failed")), "message": str(exc)[:2000]},
                    })
            run.update({"status": "failed", "error": {"code": "run_failed", "message": str(exc)}, "finished_at": time.time(), "updated_at": time.time()})
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except BaseException:
                pass
            metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
            metrics["elapsed_seconds"] = round(time.time() - float(run.get("started_at") or time.time()), 3)
            metrics["transport_restarts"] = max(
                0,
                int(getattr(self.session, "transport_restarts", 0)) - int(metrics.get("transport_restarts_at_start") or 0),
            )
            metrics.pop("transport_restarts_at_start", None)
            run["metrics"] = metrics
            self._write(group_id, run)
            try:
                from ..kernel.events import publish_event

                publish_event(
                    f"computer_control.run.{run.get('status', 'finished')}",
                    {"group_id": group_id, "run_id": run_id, "workflow_id": run.get("workflow_id"), "status": run.get("status")},
                )
            except Exception:
                pass
            self._cancelled.discard(run_id)
            try:
                self.lease.release(run_id=run_id)
            except PermissionError:
                pass

    @staticmethod
    def _redact_result(value: Any) -> Any:
        if value is None:
            return None
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) > 10000:
            return {"truncated": True, "preview": encoded[:10000]}
        return value

    @staticmethod
    def _contains_failure_marker(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("isError") is True or value.get("is_error") is True:
                return True
            return any(WorkflowRunner._contains_failure_marker(child) for child in value.values())
        if isinstance(value, list):
            return any(WorkflowRunner._contains_failure_marker(child) for child in value)
        if isinstance(value, str):
            return any(int(code) != 0 for code in re.findall(r"(?im)^\s*Status\s+Code\s*:\s*(-?\d+)\s*$", value))
        return False

    async def cancel(self, group_id: str, run_id: str, *, emergency: bool = False) -> Dict[str, Any]:
        run = self.get(group_id, run_id)
        self._cancelled.add(run_id)
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
        self.lease.release(run_id=run_id, force=emergency)
        if emergency:
            await self.session.stop()
        return run

    def cancel_sync(self, group_id: str, run_id: str, *, emergency: bool = False) -> Dict[str, Any]:
        loop = self._ensure_sync_loop()
        future = asyncio.run_coroutine_threadsafe(self.cancel(group_id, run_id, emergency=emergency), loop)
        return future.result(timeout=15)

    def verify(
        self,
        group_id: str,
        run_id: str,
        *,
        actor_id: str,
        passed: bool,
        summary: str,
        evidence_ids: List[str],
        fingerprint: str,
    ) -> Dict[str, Any]:
        run = self.get(group_id, run_id)
        if str(run.get("actor_id") or "") != actor_id:
            raise PermissionError("run belongs to another actor")
        if str(run.get("status") or "") != "awaiting_verification":
            raise ValueError("run is not awaiting verification")
        failure_markers = [
            item for item in (run.get("events") if isinstance(run.get("events"), list) else [])
            if isinstance(item, dict) and (
                str(item.get("status") or "") in {"failed", "recovering"}
                or self._contains_failure_marker(item.get("result"))
                or self._contains_failure_marker(item.get("error"))
            )
        ]
        if passed and (failure_markers or run.get("error") or not bool((run.get("metrics") or {}).get("replay_success"))):
            raise ValueError("该运行包含失败步骤，不能验证为成功")
        run["verification"] = {
            "passed": bool(passed),
            "summary": str(summary or "")[:2000],
            "evidence_ids": [str(item)[:200] for item in evidence_ids[:100]],
            "verified_by": actor_id,
            "verified_at": time.time(),
        }
        if not passed:
            run["status"] = "verification_failed"
        else:
            authorization = run.get("authorization") if isinstance(run.get("authorization"), dict) else {}
            workflow_id, version = str(run["workflow_id"]), int(run["version"])
            finalized: Dict[str, Any] = {}
            has_request_authorization = bool(str(authorization.get("request_id") or "").strip())
            if has_request_authorization and authorization.get("allow_publish") is not False:
                finalized["published"] = self.store.publish(group_id, workflow_id, version)
            if has_request_authorization and authorization.get("allow_trust") is not False:
                if not fingerprint:
                    raise ValueError("Windows-MCP fingerprint is unavailable")
                finalized["trusted"] = self.store.trust(
                    group_id,
                    workflow_id,
                    version,
                    fingerprint=fingerprint,
                    permissions=["all_windows_mcp_tools"],
                )
            run["finalization"] = {
                "published": "published" in finalized,
                "trusted": "trusted" in finalized,
                "unattended_triggers_authorized": has_request_authorization and authorization.get("allow_unattended_triggers") is not False,
            }
            run["status"] = "published" if finalized else "verified"
        run["updated_at"] = time.time()
        self._write(group_id, run)
        self._emit("run.verified", group_id=group_id, run_id=run_id, workflow_id=run.get("workflow_id"), passed=bool(passed), status=run["status"])
        return run
