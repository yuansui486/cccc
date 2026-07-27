from __future__ import annotations

import json
import base64
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..util.fs import atomic_write_text
from .audit import audit
from .lease import ComputerControlLease
from .mcp import MCPOutcomeUnknown, WindowsMCPSession, normalize_tool_result, validate_arguments_against_schema
from .models import WorkflowDefinition
from .requests import ComputerRequestStore
from .risk import classify_tool
from .runtime import WorkflowRunner
from .storage import WorkflowStore


class RecordingStore:
    IDLE_TIMEOUT_SECONDS = 5 * 60
    TOTAL_TIMEOUT_SECONDS = 30 * 60
    MAX_TOOL_CALLS = 100
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        home: Path,
        workflows: WorkflowStore,
        requests: ComputerRequestStore,
        lease: ComputerControlLease,
        session: WindowsMCPSession,
        fingerprint_provider: Optional[Callable[[], str]] = None,
    ):
        self.home = home
        self.workflows = workflows
        self.requests = requests
        self.lease = lease
        self.session = session
        self.fingerprint_provider = fingerprint_provider or (lambda: "")
        self._lock = threading.RLock()
        self._active: Dict[str, tuple[str, str, str]] = {}
        self._inflight: set[str] = set()
        self._watchdog = threading.Thread(target=self._watch, name="onecolleague-recording-watchdog", daemon=True)
        self._watchdog.start()

    def _path(self, group_id: str, recording_id: str) -> Path:
        if not recording_id.startswith("rec_") or not recording_id[4:].isalnum():
            raise KeyError(recording_id)
        return self.workflows.state_root(group_id) / "recordings" / f"{recording_id}.json"

    def _write(self, group_id: str, value: Dict[str, Any]) -> None:
        path = self._path(group_id, str(value["recording_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    def get(
        self,
        group_id: str,
        recording_id: str,
        *,
        actor_id: Optional[str] = None,
        compact: bool = False,
        evidence_offset: int = 0,
        evidence_limit: int = 20,
    ) -> Dict[str, Any]:
        try:
            value = json.loads(self._path(group_id, recording_id).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise KeyError(recording_id) from exc
        if not isinstance(value, dict):
            raise KeyError(recording_id)
        if actor_id is not None and str(value.get("actor_id") or "") != actor_id:
            raise PermissionError("recording belongs to another actor")
        return self._compact(value, evidence_offset=evidence_offset, evidence_limit=evidence_limit) if compact else value

    @staticmethod
    def _compact(value: Dict[str, Any], *, evidence_offset: int = 0, evidence_limit: int = 20) -> Dict[str, Any]:
        offset = max(0, int(evidence_offset))
        limit = max(0, min(int(evidence_limit), 100))
        evidence = value.get("evidence") if isinstance(value.get("evidence"), list) else []
        result = {key: child for key, child in value.items() if key not in {"evidence", "failures"}}
        result["evidence"] = evidence[offset : offset + limit]
        result["evidence_page"] = {"offset": offset, "limit": limit, "total": len(evidence), "has_more": offset + limit < len(evidence)}
        failures = value.get("failures") if isinstance(value.get("failures"), list) else []
        result["recent_failures"] = failures[-5:]
        result["failure_count"] = len(failures)
        return result

    def _watch(self) -> None:
        while True:
            time.sleep(5)
            now = time.time()
            with self._lock:
                active = list(self._active.items())
            for recording_id, (group_id, actor_id, request_id) in active:
                try:
                    value = self.get(group_id, recording_id, actor_id=actor_id)
                    hard_expired = now - float(value.get("created_at") or 0) > self.TOTAL_TIMEOUT_SECONDS
                    idle = now - float(value.get("last_activity_at") or value.get("updated_at") or 0) > self.IDLE_TIMEOUT_SECONDS
                    if hard_expired:
                        self.abort(group_id, recording_id, actor_id=actor_id, reason="recording_expired")
                    elif idle and recording_id not in self._inflight:
                        self.suspend(group_id, recording_id, actor_id=actor_id, reason="recording_idle")
                    else:
                        self.lease.heartbeat(group_id=group_id, actor_id=actor_id, run_id=recording_id)
                except Exception:
                    with self._lock:
                        self._active.pop(recording_id, None)

    def start(
        self,
        group_id: str,
        *,
        actor_id: str,
        request_id: str,
        name: str,
        description: str = "",
        inputs: Optional[Dict[str, Any]] = None,
        triggers: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        request = self.requests.require_authorized(group_id, request_id, actor_id)
        if str(request.get("mode") or "") != "create_and_run":
            raise PermissionError("request does not authorize workflow recording")
        if any(bool(item.get("enabled")) for item in (triggers or []) if isinstance(item, dict)):
            if not all(request.get(key) is not False for key in ("allow_publish", "allow_trust", "allow_unattended_triggers")):
                raise PermissionError("enabled unattended triggers are not authorized")
        recording_id = "rec_" + uuid.uuid4().hex[:16]
        self.lease.acquire(group_id=group_id, actor_id=actor_id, run_id=recording_id)
        now = time.time()
        value = {
            "recording_id": recording_id,
            "group_id": group_id,
            "actor_id": actor_id,
            "request_id": request_id,
            "status": "exploring",
            "name": str(name or "电脑控制工作流")[:200],
            "description": str(description or "")[:2000],
            "inputs": inputs if isinstance(inputs, dict) else {},
            "triggers": triggers if isinstance(triggers, list) else [],
            "steps": [],
            "evidence": [],
            "failures": [],
            "warnings": [],
            "metrics": {"successful_calls": 0, "failed_calls": 0, "tool_calls": 0, "consecutive_failures": 0, "transport_restarts": 0},
            "high_risk_approved": str(request.get("status") or "") == "approved" or bool(request.get("high_risk_approved")),
            "created_at": now,
            "last_activity_at": now,
            "updated_at": now,
        }
        self._write(group_id, value)
        with self._lock:
            self._active[recording_id] = (group_id, actor_id, request_id)
        self.requests.update(group_id, request_id, status="exploring", recording_id=recording_id)
        audit(self.home, "recording.started", group_id=group_id, actor_id=actor_id, details={"recording_id": recording_id, "request_id": request_id})
        return value

    @staticmethod
    def _summary(value: Any) -> Any:
        try:
            encoded = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return {"type": type(value).__name__}
        if len(encoded) <= 8000:
            return value
        return {"truncated": True, "preview": encoded[:8000], "size": len(encoded)}

    @staticmethod
    def _argument_summary(value: Any, key: str = "") -> Any:
        if any(token in key.lower() for token in ("password", "passwd", "secret", "api_key", "token")):
            return "<redacted>"
        if isinstance(value, dict):
            return {str(child_key): RecordingStore._argument_summary(child, str(child_key)) for child_key, child in value.items()}
        if isinstance(value, list):
            return [RecordingStore._argument_summary(child, key) for child in value[:20]]
        if isinstance(value, str):
            return value[:500]
        return value

    def _evidence_result(self, group_id: str, recording_id: str, evidence_id: str, value: Any) -> Any:
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
                    relative = Path("artifacts") / recording_id / f"{evidence_id}_{counter}{suffix}"
                    path = self.workflows.state_root(group_id) / "recordings" / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                    return {
                        "type": "image_artifact",
                        "artifact_id": f"{evidence_id}_{counter}",
                        "path": str(relative),
                        "mime_type": mime,
                        "size": len(raw),
                    }
                return {key: visit(item) for key, item in child.items()}
            if isinstance(child, list):
                return [visit(item) for item in child]
            return child

        return self._summary(visit(value))

    def _active_recording(self, group_id: str, recording_id: str, actor_id: str) -> Dict[str, Any]:
        value = self.get(group_id, recording_id, actor_id=actor_id)
        if value.get("status") != "exploring":
            raise ValueError("recording is not active")
        self.requests.require_authorized(group_id, str(value["request_id"]), actor_id)
        self.lease.require(group_id=group_id, actor_id=actor_id, run_id=recording_id)
        now = time.time()
        if now - float(value["created_at"]) > self.TOTAL_TIMEOUT_SECONDS:
            self.abort(group_id, recording_id, actor_id=actor_id, reason="recording_expired")
            raise TimeoutError("recording has expired")
        return value

    def resume(self, group_id: str, recording_id: str, *, actor_id: str) -> Dict[str, Any]:
        with self._lock:
            value = self.get(group_id, recording_id, actor_id=actor_id)
            if value.get("status") != "suspended":
                raise ValueError("只有已暂停的录制可以继续")
            self.requests.require_authorized(group_id, str(value["request_id"]), actor_id)
            if time.time() - float(value.get("created_at") or 0) > self.TOTAL_TIMEOUT_SECONDS:
                value.update({"status": "aborted", "abort_reason": "recording_expired", "updated_at": time.time()})
                self._write(group_id, value)
                raise TimeoutError("录制已超过 30 分钟，不能继续")
            self.lease.acquire(group_id=group_id, actor_id=actor_id, run_id=recording_id)
            now = time.time()
            value.update({"status": "exploring", "requires_snapshot_baseline": True, "resumed_at": now, "last_activity_at": now, "updated_at": now})
            self._write(group_id, value)
            self._active[recording_id] = (group_id, actor_id, str(value["request_id"]))
            return self._compact(value)

    def call(
        self,
        group_id: str,
        recording_id: str,
        *,
        actor_id: str,
        tool: str,
        arguments: Dict[str, Any],
        record: bool = True,
        workflow_arguments: Optional[Dict[str, Any]] = None,
        title: str = "",
        success_condition: Any = "",
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        signature = json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock:
            value = self._active_recording(group_id, recording_id, actor_id)
            metrics = value.setdefault("metrics", {})
            if int(metrics.get("tool_calls") or 0) >= self.MAX_TOOL_CALLS:
                raise RuntimeError("录制已达到 100 次工具调用上限，请整理步骤后提交")
            if int(metrics.get("consecutive_failures") or 0) >= self.MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError("已连续失败 3 次，请先重新观察桌面或调整方案")
            if str(value.get("last_failed_signature") or "") == signature:
                raise RuntimeError("相同参数刚刚执行失败，请勿原样重试；先观察桌面并修正参数")
            if value.get("requires_snapshot_baseline") and tool.lower() != "snapshot":
                raise RuntimeError("继续录制后必须先执行一次 Snapshot 建立新的桌面基线")
            if recording_id in self._inflight:
                raise RuntimeError("该录制正在执行另一个电脑操作")
            self._inflight.add(recording_id)
        try:
            catalog_items = self.session.catalog_sync()
            catalog = {str(item.get("name") or ""): item for item in catalog_items if isinstance(item, dict)}
            catalog_item = catalog.get(tool)
            if catalog_item is None:
                raise ValueError(f"找不到 Windows-MCP 工具：{tool}")
            schema = catalog_item.get("inputSchema") if isinstance(catalog_item.get("inputSchema"), dict) else {}
            validate_arguments_against_schema(tool, arguments, schema)
            with self._lock:
                value = self._active_recording(group_id, recording_id, actor_id)
            request = self.requests.require_authorized(group_id, str(value["request_id"]), actor_id)
            risk = classify_tool(tool, arguments)
            if risk == "high" and not bool(request.get("allow_high_risk")) and not bool(value.get("high_risk_approved")):
                raise PermissionError("high-risk tool requires approval")
            template_args = workflow_arguments if workflow_arguments is not None else arguments
            resolved = WorkflowRunner._resolve(template_args, WorkflowRunner._effective_input_values(value["inputs"]), {})
            if resolved != arguments:
                raise ValueError("workflow_arguments do not resolve to the actual arguments")
            before_restarts = self.session.transport_restarts
            started = time.time()
            try:
                result = normalize_tool_result(tool, self.session.call_tool_sync(tool, arguments, timeout=float(timeout_seconds)))
            except Exception as exc:
                with self._lock:
                    value = self.get(group_id, recording_id, actor_id=actor_id)
                failure = {
                    "tool": tool,
                    "risk": risk,
                    "code": getattr(exc, "code", "tool_call_failed"),
                    "message": str(exc)[:1000],
                    "at": time.time(),
                }
                value["failures"].append(failure)
                value["metrics"]["failed_calls"] += 1
                value["metrics"]["tool_calls"] = int(value["metrics"].get("tool_calls") or 0) + 1
                value["metrics"]["consecutive_failures"] = int(value["metrics"].get("consecutive_failures") or 0) + 1
                value["metrics"]["transport_restarts"] += self.session.transport_restarts - before_restarts
                value["last_failed_signature"] = signature
                value["last_activity_at"] = time.time()
                value["updated_at"] = time.time()
                self._write(group_id, value)
                audit(self.home, "recording.call_failed", group_id=group_id, actor_id=actor_id, details={"recording_id": recording_id, **failure})
                if isinstance(exc, MCPOutcomeUnknown):
                    raise
                raise
            with self._lock:
                value = self.get(group_id, recording_id, actor_id=actor_id)
            evidence_id = "ev_" + uuid.uuid4().hex[:12]
            stored_result = self._evidence_result(group_id, recording_id, evidence_id, result)
            evidence = {
                "evidence_id": evidence_id,
                "tool": tool,
                "recorded": bool(record),
                "risk": risk,
                "result": stored_result,
                "elapsed_ms": round((time.time() - started) * 1000),
                "created_at": time.time(),
            }
            value["evidence"].append(evidence)
            if record:
                step_id = f"step_{len(value['steps']) + 1}"
                stability = (
                    "low"
                    if tool.lower() in {"click", "type"} and any(key in arguments for key in ("loc", "x", "y"))
                    else "normal"
                )
                value["steps"].append({
                    "id": step_id,
                    "type": "action",
                    "title": str(title or tool)[:200],
                    "tool": tool,
                    "arguments": template_args,
                    "success_condition": success_condition,
                    "timeout_seconds": max(1, min(int(timeout_seconds), 600)),
                    "retries": 0,
                    "adaptive": True,
                    "stability": stability,
                    "evidence_id": evidence_id,
                })
                value["metrics"]["successful_calls"] += 1
            value["metrics"]["tool_calls"] = int(value["metrics"].get("tool_calls") or 0) + 1
            value["metrics"]["consecutive_failures"] = 0
            value["metrics"]["transport_restarts"] += self.session.transport_restarts - before_restarts
            value.pop("last_failed_signature", None)
            if tool.lower() == "snapshot":
                value["requires_snapshot_baseline"] = False
            value["last_activity_at"] = time.time()
            value["updated_at"] = time.time()
            self._write(group_id, value)
            self.lease.heartbeat(group_id=group_id, actor_id=actor_id, run_id=recording_id)
            audit(
                self.home,
                "recording.call_succeeded",
                group_id=group_id,
                actor_id=actor_id,
                details={
                    "recording_id": recording_id,
                    "tool": tool,
                    "risk": risk,
                    "recorded": bool(record),
                    "arguments": self._argument_summary(arguments),
                    "evidence_id": evidence_id,
                    "step_count": len(value["steps"]),
                },
            )
            return {"recording": self._compact(value), "result": stored_result, "evidence_id": evidence_id}
        finally:
            with self._lock:
                self._inflight.discard(recording_id)

    def wait(self, group_id: str, recording_id: str, *, actor_id: str, duration_seconds: float, title: str = "") -> Dict[str, Any]:
        with self._lock:
            self._active_recording(group_id, recording_id, actor_id)
            if recording_id in self._inflight:
                raise RuntimeError("该录制正在执行另一个电脑操作")
            self._inflight.add(recording_id)
        duration = max(0.0, min(float(duration_seconds), 600.0))
        try:
            time.sleep(duration)
            with self._lock:
                value = self._active_recording(group_id, recording_id, actor_id)
            value["steps"].append({
                "id": f"step_{len(value['steps']) + 1}",
                "type": "wait",
                "title": str(title or "等待")[:200],
                "duration_seconds": duration,
            })
            value["last_activity_at"] = time.time()
            value["updated_at"] = time.time()
            self._write(group_id, value)
            self.lease.heartbeat(group_id=group_id, actor_id=actor_id, run_id=recording_id)
            return self._compact(value)
        finally:
            with self._lock:
                self._inflight.discard(recording_id)

    def update_step(self, group_id: str, recording_id: str, *, actor_id: str, step_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"title", "arguments", "success_condition", "timeout_seconds", "retries", "adaptive"}
        with self._lock:
            value = self._active_recording(group_id, recording_id, actor_id)
            step = next((item for item in value["steps"] if item.get("id") == step_id), None)
            if step is None:
                raise KeyError(step_id)
            step.update({key: child for key, child in patch.items() if key in allowed})
            value["updated_at"] = time.time()
            self._definition(value)
            self._write(group_id, value)
            return value

    def undo(self, group_id: str, recording_id: str, *, actor_id: str) -> Dict[str, Any]:
        with self._lock:
            value = self._active_recording(group_id, recording_id, actor_id)
            if not value["steps"]:
                raise ValueError("recording has no step to undo")
            removed = value["steps"].pop()
            value["updated_at"] = time.time()
            self._write(group_id, value)
            return {"recording": value, "removed": removed, "side_effects_reversed": False}

    @staticmethod
    def _definition(value: Dict[str, Any]) -> WorkflowDefinition:
        nodes = [{"id": "start", "type": "start", "title": "开始"}, *value["steps"], {"id": "end", "type": "end", "title": "结束"}]
        clean_nodes = [{key: child for key, child in node.items() if key not in {"stability", "evidence_id"}} for node in nodes]
        edges = [{"source": clean_nodes[index]["id"], "target": clean_nodes[index + 1]["id"]} for index in range(len(clean_nodes) - 1)]
        definition = WorkflowDefinition.model_validate({
            "name": value["name"],
            "description": value["description"],
            "inputs": value["inputs"],
            "nodes": clean_nodes,
            "edges": edges,
            "triggers": value["triggers"],
            "save_screenshots": True,
        })
        if any(trigger.enabled for trigger in definition.triggers):
            if not any(node.type == "action" and bool(node.success_condition) for node in definition.nodes):
                raise ValueError("unattended workflows require at least one machine-verifiable success condition")
        return definition

    def commit(self, group_id: str, recording_id: str, *, actor_id: str) -> Dict[str, Any]:
        with self._lock:
            value = self._active_recording(group_id, recording_id, actor_id)
            if not value["steps"]:
                raise ValueError("cannot commit an empty recording")
            definition = self._definition(value)
            request_id = str(value["request_id"])
            for manifest in self.workflows.list(group_id, include_archived=True):
                if manifest.get("source_request_id") == request_id and not manifest.get("published_version"):
                    self.workflows.archive(group_id, str(manifest["workflow_id"]))
            created = self.workflows.create(group_id, definition, created_by=actor_id, source_request_id=request_id)
            workflow_id = str(created["manifest"]["workflow_id"])
            created = self.workflows.auto_finalize(
                group_id,
                workflow_id,
                int(created["version"]),
                fingerprint=str(self.fingerprint_provider() or ""),
            )
            value.update({"status": "committed", "workflow_id": workflow_id, "committed_at": time.time(), "updated_at": time.time()})
            self._write(group_id, value)
            self.lease.release(run_id=recording_id)
            self._active.pop(recording_id, None)
            self.requests.update(group_id, request_id, status="draft_created", workflow_id=workflow_id)
            audit(self.home, "recording.committed", group_id=group_id, actor_id=actor_id, details={"recording_id": recording_id, "workflow_id": workflow_id, "step_count": len(value["steps"])})
            return {"recording": value, "workflow": created}

    def abort(self, group_id: str, recording_id: str, *, actor_id: str, reason: str = "aborted") -> Dict[str, Any]:
        with self._lock:
            value = self.get(group_id, recording_id, actor_id=actor_id)
            if value.get("status") == "exploring":
                value.update({"status": "aborted", "abort_reason": reason, "updated_at": time.time()})
                self._write(group_id, value)
            try:
                self.lease.release(run_id=recording_id)
            except PermissionError:
                pass
            self._active.pop(recording_id, None)
            audit(self.home, "recording.aborted", group_id=group_id, actor_id=actor_id, details={"recording_id": recording_id, "reason": reason})
            return value

    def suspend(self, group_id: str, recording_id: str, *, actor_id: str, reason: str = "recording_idle") -> Dict[str, Any]:
        with self._lock:
            value = self.get(group_id, recording_id, actor_id=actor_id)
            if value.get("status") == "exploring":
                value.update({"status": "suspended", "suspend_reason": reason, "suspended_at": time.time(), "updated_at": time.time()})
                self._write(group_id, value)
            try:
                self.lease.release(run_id=recording_id)
            except PermissionError:
                pass
            self._active.pop(recording_id, None)
            audit(self.home, "recording.suspended", group_id=group_id, actor_id=actor_id, details={"recording_id": recording_id, "reason": reason})
            return self._compact(value)
