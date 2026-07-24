from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util.fs import atomic_write_text
from .lease import ComputerControlLease, LeaseConflict
from .mcp import WindowsMCPSession
from .models import WorkflowDefinition
from .storage import WorkflowStore

REF_RE = re.compile(r"^\$\{(inputs|steps)\.([A-Za-z0-9_.-]+)\}$")
SECRET_RE = re.compile(r"^\$\{secret:([A-Za-z_][A-Za-z0-9_]*)\}$")


class WorkflowRunner:
    def __init__(self, home: Path, store: WorkflowStore, lease: ComputerControlLease, session: WindowsMCPSession):
        self.home = home
        self.store = store
        self.lease = lease
        self.session = session
        self._tasks: Dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self._sync_loop: asyncio.AbstractEventLoop | None = None
        self._sync_thread: threading.Thread | None = None
        self._sync_lock = threading.Lock()

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

    def start_sync(self, group_id: str, workflow_id: str, *, actor_id: str, version: Optional[int], inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a run from the synchronous MCP tool server."""
        loop = self._ensure_sync_loop()
        future = asyncio.run_coroutine_threadsafe(
            self.start(group_id, workflow_id, actor_id=actor_id, version=version, inputs=inputs), loop
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
        try:
            value = json.loads(self._run_path(group_id, run_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyError(run_id) from exc
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
        recovery = {
            "recovery_id": recovery_id,
            "node_id": event.get("node_id"),
            "attempt": attempt,
            "error": {"code": "tool_call_failed", "message": str(error)[:1000]},
        }
        run["status"] = "recovering"
        run["recovery"] = recovery
        event["status"] = "recovering"
        event["recovery_id"] = recovery_id
        self._write(group_id, run)
        self._emit("recovery.required", group_id=group_id, run_id=run_id, actor_id=run.get("actor_id"), **recovery)
        path = self._recovery_path(group_id, run_id, recovery_id)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
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
            self.lease.heartbeat(group_id=group_id, actor_id=str(run["actor_id"]), run_id=run_id)
            await asyncio.sleep(0.5)
        raise TimeoutError("AI 自适应恢复等待超时")

    async def _wait_for_approval(self, run: Dict[str, Any], node_id: str, *, timeout: float = 600) -> None:
        group_id, run_id = str(run["group_id"]), str(run["run_id"])
        path = self._approval_path(group_id, run_id, node_id)
        deadline = time.monotonic() + timeout
        run["status"] = "waiting_approval"
        self._write(group_id, run)
        self._emit("run.approval_required", group_id=group_id, run_id=run_id, node_id=node_id)
        while time.monotonic() < deadline:
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
            self.lease.heartbeat(group_id=group_id, actor_id=str(run["actor_id"]), run_id=run_id)
            await asyncio.sleep(0.5)
        raise TimeoutError("等待用户确认超时")

    async def start(self, group_id: str, workflow_id: str, *, actor_id: str, version: Optional[int], inputs: Dict[str, Any]) -> Dict[str, Any]:
        selected = self.store.get(group_id, workflow_id, version=version)
        definition = WorkflowDefinition.model_validate({k: v for k, v in selected["definition"].items() if k != "change_note"})
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
        }
        self._write(group_id, run)
        task = asyncio.create_task(self._execute(run, definition, inputs))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(run_id, None))
        return run

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

    async def _execute(self, run: Dict[str, Any], definition: WorkflowDefinition, inputs: Dict[str, Any]) -> None:
        group_id, run_id, actor_id = run["group_id"], run["run_id"], run["actor_id"]
        nodes = {node.id: node for node in definition.nodes}
        outgoing: Dict[str, List[Any]] = {node_id: [] for node_id in nodes}
        for edge in definition.edges:
            outgoing[edge.source].append(edge)
        current = next(node.id for node in definition.nodes if node.type == "start")
        steps: Dict[str, Any] = {}
        loop_counts: Dict[str, int] = {}
        deadline = time.monotonic() + definition.max_run_seconds
        heartbeat_at = 0.0
        try:
            while True:
                if run_id in self._cancelled:
                    raise asyncio.CancelledError()
                if time.monotonic() > deadline:
                    raise TimeoutError("workflow run exceeded its time limit")
                if time.monotonic() - heartbeat_at >= self.lease.HEARTBEAT_SECONDS:
                    self.lease.heartbeat(group_id=group_id, actor_id=actor_id, run_id=run_id)
                    heartbeat_at = time.monotonic()
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
                    last_error: Optional[Exception] = None
                    active_tool = node.tool
                    recovered_arguments: Optional[Dict[str, Any]] = None
                    configured_attempts = node.retries + 1
                    for attempt in range(configured_attempts):
                        try:
                            result = await self.session.call_tool(active_tool, arguments, timeout=node.timeout_seconds)
                            last_error = None
                            break
                        except Exception as exc:
                            last_error = exc
                            event["attempt"] = attempt + 1
                    recovery_attempt = 0
                    while last_error is not None and node.adaptive and recovery_attempt < 3:
                        recovery_attempt += 1
                        patch = await self._wait_for_recovery(run, event, error=last_error, attempt=recovery_attempt)
                        patched_tool = str(patch.get("tool") or active_tool).strip() or active_tool
                        catalog_names = {str(item.get("name") or "") for item in await self.session.catalog() if isinstance(item, dict)}
                        if patched_tool not in catalog_names:
                            last_error = ValueError(f"unknown Windows-MCP tool in recovery: {patched_tool}")
                            continue
                        from .risk import classify_tool

                        risk_rank = {"low": 0, "medium": 1, "high": 2}
                        if risk_rank[classify_tool(patched_tool, patch.get("arguments"))] > risk_rank[classify_tool(node.tool, node.arguments)]:
                            last_error = PermissionError("adaptive recovery cannot increase the tool risk level")
                            continue
                        active_tool = patched_tool
                        arguments = self._resolve(patch.get("arguments") or {}, inputs, steps)
                        try:
                            result = await self.session.call_tool(active_tool, arguments, timeout=node.timeout_seconds)
                            recovered_arguments = patch.get("arguments")
                            last_error = None
                        except Exception as exc:
                            last_error = exc
                    if last_error is not None:
                        raise last_error
                    steps[current] = result
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
                    await asyncio.sleep(min(float(node.timeout_seconds), 600.0))
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
                event.update({"status": "completed", "finished_at": time.time(), "result": self._redact_result(result)})
                self._emit("node.completed", group_id=group_id, run_id=run_id, workflow_id=run.get("workflow_id"), node_id=current, node_type=node.type)
                if node.type == "end":
                    break
                choices = [edge for edge in outgoing[current] if edge.branch == branch]
                if not choices and branch != "next":
                    choices = [edge for edge in outgoing[current] if edge.branch == "next"]
                if not choices:
                    raise RuntimeError(f"node {current} has no {branch} transition")
                current = choices[0].target
            run.update({"status": "completed", "current_node_id": None, "finished_at": time.time(), "updated_at": time.time()})
        except asyncio.CancelledError:
            run.update({"status": "cancelled", "finished_at": time.time(), "updated_at": time.time()})
        except Exception as exc:
            run.update({"status": "failed", "error": {"code": "run_failed", "message": str(exc)}, "finished_at": time.time(), "updated_at": time.time()})
        finally:
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
