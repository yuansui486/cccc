from __future__ import annotations

import asyncio
import base64
import json
import inspect
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util.fs import atomic_write_text
from .lease import ComputerControlLease
from .authorization import (
    RunStartClaim,
    normalized_run_inputs,
    run_inputs_digest,
    workflow_definition_digest,
)
from .run_authority import (
    LegacyRunAllocationClaim,
    RunAuthorityStore,
    RunExecutionClaim,
    RunOperationClaim,
    RunReadClaim,
    RunStopOwnerClaim,
    RunTerminationClaim,
)
from .mcp import (
    MCPOutcomeUnknown,
    MCPUnavailable,
    WindowsMCPSession,
    normalize_tool_result,
    validate_arguments_against_schema,
    validate_workflow_tools,
)
from .models import WorkflowDefinition
from .requests import ComputerRequestStore
from .storage import WorkflowStore
from .elements import element_center, locator_from_element, normalize_snapshot, resolve_locator

REF_RE = re.compile(r"^\$\{(inputs|steps)\.([A-Za-z0-9_.-]+)\}$")
SECRET_RE = re.compile(r"^\$\{secret:([A-Za-z_][A-Za-z0-9_]*)\}$")
_LEGACY_EXECUTION_SEAL = object()


@dataclass(frozen=True)
class _LegacyExecution:
    allocation: LegacyRunAllocationClaim
    daemon_execution_claim: Any
    _seal: object = _LEGACY_EXECUTION_SEAL


@dataclass
class _ManualExecution:
    authorities: RunAuthorityStore
    claim: RunExecutionClaim
    termination: RunTerminationClaim | None = None


class ElementResolutionError(ValueError):
    """Structured, user-facing error raised before an element side effect."""

    def __init__(self, code: str, message: str, *, next_action: str = "", field_errors: Optional[Dict[str, str]] = None, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.layer = "element_resolution"
        self.next_action = next_action
        self.field_errors = field_errors or {}
        self.retryable = retryable


@dataclass
class ObservationContext:
    """One-shot bridge between a fresh Snapshot and one Click/Type RPC."""

    observation_id: str
    provider: str
    captured_at: float
    focused_window: str
    target_window: str
    desktop_name: str
    element_id: str
    window_element_counts: Dict[str, int]
    target_diagnostics: Dict[str, Any]
    mcp_label: Optional[int] = None
    handle: Any = None
    bounds: Optional[Dict[str, Any]] = None
    status: str = "prepared"

    def public(self) -> Dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "provider": self.provider,
            "captured_at": self.captured_at,
            "focused_window": self.focused_window,
            "target_window": self.target_window,
            "desktop_name": self.desktop_name,
            "element_id": self.element_id,
            "window_element_counts": dict(self.window_element_counts),
            "target_diagnostics": dict(self.target_diagnostics),
            "status": self.status,
            "one_shot": True,
            "ephemeral_fields": ["mcp_label", "handle", "bounds"],
        }

    def consume(self, status: str) -> None:
        self.status = status
        self.mcp_label = None
        self.handle = None
        self.bounds = None


class WorkflowRunner:
    def __init__(
        self,
        home: Path,
        store: WorkflowStore,
        lease: ComputerControlLease,
        session: WindowsMCPSession,
        fingerprint_provider: Optional[Any] = None,
        observation_provider: Optional[Any] = None,
        *,
        run_authorities: Optional[RunAuthorityStore] = None,
        requests: Optional[ComputerRequestStore] = None,
    ):
        self.home = home
        self.store = store
        self.lease = lease
        self.session = session
        self.fingerprint_provider = fingerprint_provider or (lambda: "")
        self.observation_provider = observation_provider
        self.run_authorities = run_authorities
        self.run_namespace = run_authorities or RunAuthorityStore(
            home,
            issuer_epoch_provider=lambda: "legacy_namespace",
            generation_provider=lambda _group_id, _actor_id: 1,
        )
        self.requests = requests
        self._observation_contexts: Dict[str, ObservationContext] = {}
        self._tasks: Dict[tuple[str, str, str], asyncio.Task[None]] = {}
        self._cancelled: set[tuple[str, str, str]] = set()
        self._legacy_executions: Dict[tuple[str, str, str], _LegacyExecution] = {}
        self._manual_executions: Dict[tuple[str, str, str], _ManualExecution] = {}
        self._sync_loop: asyncio.AbstractEventLoop | None = None
        self._sync_thread: threading.Thread | None = None
        self._sync_lock = threading.RLock()
        self._daemon_execution_claim: Any = None
        self._daemon_stopping = False
        self._sync_futures: set[Any] = set()
        self._sync_operations: set[asyncio.Task[Any]] = set()
        self._daemon_cancelled_tasks: set[Any] = set()
        self._daemon_cancelled_futures: set[Any] = set()

    class _ExternalLeaseLost(RuntimeError):
        def __init__(self, lease: Optional[Dict[str, Any]] = None):
            super().__init__("computer control is now occupied by another group")
            self.lease = lease or {}

    @staticmethod
    def _run_key(group_id: str, actor_id: str, run_id: str) -> tuple[str, str, str]:
        return (str(group_id), str(actor_id), str(run_id))

    @staticmethod
    def _require_run_path_segment(run_id: str) -> str:
        resource = str(run_id or "")
        if (
            not resource
            or len(resource) > 200
            or resource in {".", ".."}
            or "/" in resource
            or "\\" in resource
            or "\x00" in resource
            or Path(resource).is_absolute()
        ):
            raise PermissionError("computer control run id must be a single path segment")
        return resource

    @staticmethod
    def _remove_current(
        values: Dict[tuple[str, str, str], Any],
        key: tuple[str, str, str],
        expected: Any,
    ) -> bool:
        if values.get(key) is not expected:
            return False
        values.pop(key, None)
        return True

    def _bind_daemon_execution_claim(self, claim: Any) -> None:
        # Issuance happens while daemon recovery is still finishing. The first
        # execution validates READY again at its own linearization point.
        if claim is None:
            raise PermissionError("current READY daemon execution claim is required")
        with self._sync_lock:
            self._daemon_execution_claim = claim
            self._daemon_stopping = False
            self._daemon_cancelled_tasks.clear()
            self._daemon_cancelled_futures.clear()

    def _clear_daemon_execution_claim(self, expected: Any) -> None:
        with self._sync_lock:
            if self._daemon_execution_claim is expected:
                self._daemon_execution_claim = None

    def _require_daemon_execution_claim(self, expected: Any = None) -> Any:
        from .services import _validate_daemon_execution_claim

        claim = self._daemon_execution_claim if expected is None else expected
        if self._daemon_execution_claim is not claim:
            raise PermissionError("current READY daemon execution claim is required")
        _validate_daemon_execution_claim(claim, home=self.home)
        return claim

    def _recover_manual_runs_after_restart(self, owner: Any) -> None:
        from .services import _claim_daemon_generation, _release_daemon_generation

        generation_claim = _claim_daemon_generation(
            owner,
            home=self.home,
            subject="runner-recovery",
        )
        try:
            assert self.run_authorities is not None
            for candidate in self.run_authorities.restart_candidates():
                expected = {
                    "expected_group_id": candidate["group_id"],
                    "expected_actor_id": candidate["actor_id"],
                    "expected_resource_id": candidate["resource_id"],
                    "expected_authority_id": candidate["authority_id"],
                    "expected_execution_id": candidate["execution_id"],
                }
                try:
                    if candidate["control_state"] == "revoked":
                        claim = self.run_authorities.prepare_terminal_reconciliation(**expected)
                        self.run_authorities.finish_terminal_reconciliation(claim)
                    else:
                        claim = self.run_authorities.prepare_restart_recovery(**expected)
                        self.lease.release_run_after_restart(authority=claim)
                        self.run_authorities.finish_restart_recovery(claim)
                except PermissionError:
                    continue
        finally:
            _release_daemon_generation(generation_claim)

    def _require_execution(
        self,
        run: Dict[str, Any],
        execution: _LegacyExecution | _ManualExecution,
    ) -> None:
        origin = str(run.get("origin") or "legacy_internal")
        if isinstance(execution, _LegacyExecution):
            self._require_daemon_execution_claim(execution.daemon_execution_claim)
            if execution._seal is not _LEGACY_EXECUTION_SEAL or origin != "legacy_internal":
                raise PermissionError("manual actor run requires execution authority")
            current = self.run_namespace.require_legacy_run_current(
                execution.allocation
            )
            if any(
                str(run.get(field) or "") != str(current.get(field) or "")
                for field in ("group_id", "actor_id", "run_id")
            ) or run.get("legacy_allocation") != current.get("legacy_allocation"):
                raise PermissionError("legacy run allocation anchor is invalid")
            return
        if not isinstance(execution, _ManualExecution) or origin != "manual_actor":
            raise PermissionError("validated manual run execution authority is required")
        execution.claim.require_current()
        expected = execution.authorities.run_anchor_for_execution_claim(execution.claim)
        if run.get("run_authority") != expected:
            raise PermissionError("manual actor run authority anchor is invalid")

    def _require_provider_call(
        self,
        run: Dict[str, Any],
        execution: _LegacyExecution | _ManualExecution,
    ) -> None:
        """Revalidate both authority and exact desktop ownership per RPC."""
        self._require_execution(run, execution)
        group_id = str(run["group_id"])
        actor_id = str(run["actor_id"])
        run_id = str(run["run_id"])
        if isinstance(execution, _ManualExecution):
            self.lease.require_run(
                group_id=group_id,
                actor_id=actor_id,
                run_id=run_id,
                authority=execution.claim,
            )
        else:
            self.lease.require_legacy_run(
                allocation=execution.allocation,
            )

    def _write_execution(
        self,
        group_id: str,
        run: Dict[str, Any],
        execution: _LegacyExecution | _ManualExecution,
    ) -> None:
        self._require_execution(run, execution)
        if isinstance(execution, _LegacyExecution):
            self.run_namespace.write_legacy_run(execution.allocation, run)
        else:
            self._write_unchecked(group_id, run)

    def _write_manual_operation(
        self,
        group_id: str,
        run: Dict[str, Any],
        operation_claim: RunOperationClaim,
    ) -> None:
        if self.run_authorities is None:
            raise PermissionError("run authority store is unavailable")
        expected = self.run_authorities.run_anchor_for_operation_claim(operation_claim)
        if (
            str(run.get("origin") or "") != "manual_actor"
            or run.get("run_authority") != expected
        ):
            raise PermissionError("manual actor run authority anchor is invalid")
        self._write_unchecked(group_id, run)

    def _transition_manual(
        self,
        run: Dict[str, Any],
        execution: _LegacyExecution | _ManualExecution,
        state: str,
    ) -> None:
        if not isinstance(execution, _ManualExecution):
            return
        if state == "running" and execution.claim.state != "running":
            execution.claim = execution.authorities.mark_running(execution.claim)
        elif state == "waiting_recovery":
            execution.claim = execution.authorities.mark_waiting_recovery(execution.claim)
        elif state == "waiting_approval":
            execution.claim = execution.authorities.mark_waiting_approval(execution.claim)
        elif state == "awaiting_verification":
            execution.claim = execution.authorities.mark_awaiting_verification(execution.claim)

    def _finalize_manual_execution(
        self,
        group_id: str,
        run: Dict[str, Any],
        execution: _ManualExecution,
    ) -> None:
        status = str(run.get("status") or "")
        if execution.termination is not None:
            run["status"] = "cancelled"
            self.lease.release_run(run_id=str(run["run_id"]), authority=execution.termination)
            execution.authorities.mark_cancelled(execution.termination)
        elif status == "awaiting_verification":
            if execution.claim.state != "awaiting_verification":
                self._transition_manual(run, execution, "awaiting_verification")
            self.lease.release_run(run_id=str(run["run_id"]), authority=execution.claim)
        else:
            terminal_status = (
                "completed"
                if status == "completed"
                else "failed"
                if status == "failed"
                else "interrupted"
            )
            run["status"] = terminal_status
            self.lease.release_run(run_id=str(run["run_id"]), authority=execution.claim)
            def finalize(require_current: Any) -> str:
                if terminal_status != "completed":
                    return terminal_status
                authorization = (
                    run.get("authorization")
                    if isinstance(run.get("authorization"), dict)
                    else {}
                )
                has_request_authorization = bool(
                    str(authorization.get("request_id") or "").strip()
                )
                finalized: Dict[str, Any] = {}
                try:
                    if has_request_authorization and authorization.get("allow_publish") is not False:
                        require_current()
                        finalized["published"] = self.store.publish(
                            group_id,
                            str(run["workflow_id"]),
                            int(run["version"]),
                        )
                    fingerprint = str(self.fingerprint_provider() or "")
                    if (
                        has_request_authorization
                        and authorization.get("allow_trust") is not False
                        and fingerprint
                    ):
                        require_current()
                        finalized["trusted"] = self.store.trust(
                            group_id,
                            str(run["workflow_id"]),
                            int(run["version"]),
                            fingerprint=fingerprint,
                            permissions=["all_windows_mcp_tools"],
                        )
                    run["finalization"] = {
                        "published": "published" in finalized,
                        "trusted": "trusted" in finalized,
                        "unattended_triggers_authorized": has_request_authorization
                        and authorization.get("allow_unattended_triggers") is not False,
                    }
                    return "completed"
                except Exception as exc:
                    run["finalization"] = {
                        "published": "published" in finalized,
                        "trusted": "trusted" in finalized,
                        "unattended_triggers_authorized": has_request_authorization
                        and authorization.get("allow_unattended_triggers") is not False,
                    }
                    run.update(
                        {
                            "status": "failed",
                            "error": {
                                "code": "finalization_failed",
                                "layer": "runtime",
                                "message": str(exc)[:2000],
                                "retryable": True,
                            },
                            "finished_at": time.time(),
                            "updated_at": time.time(),
                        }
                    )
                    return "failed"

            try:
                execution.authorities.finish_execution_with_callback(
                    execution.claim,
                    finalize,
                )
            except PermissionError:
                if execution.termination is None:
                    raise
                run["status"] = "cancelled"
                execution.authorities.mark_cancelled(execution.termination)
        self._write_unchecked(group_id, run)
        if str(run.get("status") or "") != "awaiting_verification":
            key = self._run_key(group_id, str(run["actor_id"]), str(run["run_id"]))
            self._remove_current(self._manual_executions, key, execution)

    async def _heartbeat_loop(
        self,
        run: Dict[str, Any],
        lost: asyncio.Event,
        execution: _LegacyExecution | _ManualExecution,
    ) -> None:
        group_id, actor_id, run_id = str(run["group_id"]), str(run["actor_id"]), str(run["run_id"])
        while not lost.is_set():
            try:
                await asyncio.sleep(max(1.0, float(self.lease.HEARTBEAT_SECONDS) / 2))
                if lost.is_set():
                    return
                if isinstance(execution, _ManualExecution):
                    self._require_execution(run, execution)
                    self.lease.heartbeat_run(
                        group_id=group_id,
                        actor_id=actor_id,
                        run_id=run_id,
                        authority=execution.claim,
                    )
                else:
                    self._require_execution(run, execution)
                    self.lease.heartbeat_legacy_run(allocation=execution.allocation)
                run["lease"] = {"last_heartbeat_at": time.time(), "status": "owned"}
            except asyncio.CancelledError:
                raise
            except PermissionError:
                if isinstance(execution, _ManualExecution):
                    run["lease"] = {"status": "lost"}
                    run["status"] = "external_blocked"
                    lost.set()
                    return
                current = self.lease.status().get("lease")
                run["lease"] = {"status": "lost", "owner": current or {}}
                run["status"] = "external_blocked"
                run["external_blocked"] = {"reason": "computer_control_busy", "lease": current or {}, "at": time.time()}
                self._emit("run.external_blocked", group_id=group_id, run_id=run_id, lease=current or {})
                lost.set()
                return

    def _ensure_sync_loop(self) -> asyncio.AbstractEventLoop:
        with self._sync_lock:
            if self._daemon_stopping:
                raise PermissionError("computer-control daemon is stopping")
            if self._sync_loop is not None and self._sync_loop.is_running():
                return self._sync_loop
            loop = asyncio.new_event_loop()

            def run_loop() -> None:
                asyncio.set_event_loop(loop)
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            thread = threading.Thread(target=run_loop, name="onecolleague-computer-runner", daemon=True)
            thread.start()
            self._sync_loop = loop
            self._sync_thread = thread
            return loop

    def _run_sync_operation(self, operation: Any) -> Any:
        with self._sync_lock:
            if self._daemon_stopping:
                close = getattr(operation, "close", None)
                if callable(close):
                    close()
                raise PermissionError("computer-control daemon is stopping")
            loop = self._ensure_sync_loop()

            async def tracked() -> Any:
                task = asyncio.current_task()
                assert task is not None
                with self._sync_lock:
                    self._sync_operations.add(task)
                try:
                    return await operation
                finally:
                    with self._sync_lock:
                        self._sync_operations.discard(task)

            future = asyncio.run_coroutine_threadsafe(tracked(), loop)
            self._sync_futures.add(future)
        try:
            return future.result()
        finally:
            with self._sync_lock:
                self._sync_futures.discard(future)

    @staticmethod
    def _cancel_task_threadsafe(task: asyncio.Task[Any]) -> None:
        if task.done():
            return
        loop = task.get_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(task.cancel)

    def request_daemon_stop(self) -> None:
        with self._sync_lock:
            self._daemon_stopping = True
            futures = list(self._sync_futures)
            tasks = list(self._tasks.values()) + list(self._sync_operations)
            self._daemon_cancelled_futures.update(futures)
            self._daemon_cancelled_tasks.update(tasks)
        for future in futures:
            future.cancel()
        for task in tasks:
            self._cancel_task_threadsafe(task)

    def drain_daemon(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._sync_lock:
                futures = [future for future in self._sync_futures if not future.done()]
                tasks = [
                    task
                    for task in list(self._tasks.values()) + list(self._sync_operations)
                    if not task.done()
                ]
            for future in futures:
                with self._sync_lock:
                    should_cancel = future not in self._daemon_cancelled_futures
                    self._daemon_cancelled_futures.add(future)
                if should_cancel:
                    future.cancel()
            for task in tasks:
                with self._sync_lock:
                    should_cancel = task not in self._daemon_cancelled_tasks
                    self._daemon_cancelled_tasks.add(task)
                if should_cancel:
                    self._cancel_task_threadsafe(task)
            if not futures and not tasks:
                break
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

        with self._sync_lock:
            loop = self._sync_loop
            thread = self._sync_thread
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                return False
        with self._sync_lock:
            if self._sync_loop is loop:
                self._sync_loop = None
            if self._sync_thread is thread:
                self._sync_thread = None
        return True

    async def _catalog(self) -> List[Dict[str, Any]]:
        method = getattr(self.session, "catalog", None)
        if not callable(method):
            return []
        value = method()
        if inspect.isawaitable(value):
            value = await value
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _schema_supports(schema: Any, property_name: str) -> bool:
        if not isinstance(schema, dict):
            return False
        properties = schema.get("properties")
        if isinstance(properties, dict) and property_name in properties:
            return True
        for key in ("oneOf", "anyOf", "allOf"):
            alternatives = schema.get(key)
            if isinstance(alternatives, list) and any(WorkflowRunner._schema_supports(item, property_name) for item in alternatives):
                return True
        return False

    @staticmethod
    def _schema_property_type(schema: Any, property_name: str) -> Any:
        if not isinstance(schema, dict):
            return None
        properties = schema.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get(property_name), dict):
            return properties[property_name].get("type")
        for key in ("oneOf", "anyOf", "allOf"):
            alternatives = schema.get(key)
            if isinstance(alternatives, list):
                for alternative in alternatives:
                    value = WorkflowRunner._schema_property_type(alternative, property_name)
                    if value is not None:
                        return value
        return None

    @classmethod
    def _validate_element_argument_shape(cls, tool: str, arguments: Dict[str, Any], schema: Dict[str, Any]) -> None:
        if "loc" in arguments:
            loc = arguments.get("loc")
            if isinstance(loc, str):
                try:
                    decoded = json.loads(loc)
                except (TypeError, ValueError):
                    decoded = None
                if not isinstance(decoded, list):
                    raise ElementResolutionError(
                        "invalid_argument_shape",
                        f"工具“{tool}”的 loc 必须是 [x, y] 坐标，不能填写元素名称",
                        next_action="重新观察并选择元素，或输入两个数字坐标",
                        field_errors={"loc": "请输入 [x, y] 数字数组"},
                        retryable=False,
                    )
                if len(decoded) < 2 or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in decoded[:2]):
                    raise ElementResolutionError(
                        "invalid_argument_shape",
                        f"工具“{tool}”的 loc 必须是 [x, y] 数字数组",
                        next_action="重新捕获目标元素",
                        field_errors={"loc": "请输入 [x, y] 数字数组"},
                        retryable=False,
                    )
                arguments["loc"] = decoded
            elif not isinstance(loc, (list, tuple)) or len(loc) < 2 or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in loc[:2]):
                raise ElementResolutionError(
                    "invalid_argument_shape",
                    f"工具“{tool}”的 loc 必须是 [x, y] 数字数组",
                    next_action="重新捕获目标元素",
                    field_errors={"loc": "请输入 [x, y] 数字数组"},
                    retryable=False,
                )
        label_type = cls._schema_property_type(schema, "label")
        label_requires_integer = label_type == "integer" or isinstance(label_type, list) and "integer" in label_type
        if isinstance(arguments.get("label"), str) and label_requires_integer:
            raise ElementResolutionError(
                "invalid_argument_shape",
                f"工具“{tool}”的 label 必须是当前 Snapshot 的整数元素编号，不能填写元素名称",
                next_action="重新观察后使用元素编号，或让系统从元素中心生成一次性 loc",
                field_errors={"label": "元素名称不能直接作为整数 label"},
                retryable=False,
            )
        for key in ("x", "y"):
            if key in arguments and (isinstance(arguments[key], bool) or not isinstance(arguments[key], (int, float))):
                raise ElementResolutionError(
                    "invalid_argument_shape",
                    f"工具“{tool}”的 {key} 必须是数字",
                    next_action="重新捕获目标元素",
                    field_errors={key: "请输入数字"},
                    retryable=False,
                )

    @staticmethod
    def _window_matches(actual: str, expected: str) -> bool:
        actual_value, expected_value = str(actual or "").strip().casefold(), str(expected or "").strip().casefold()
        if not actual_value or not expected_value:
            return False
        if actual_value == expected_value:
            return True

        def decorated_title(longer: str, shorter: str) -> bool:
            if not longer.startswith(shorter) or len(longer) == len(shorter):
                return False
            boundary = longer[len(shorter)]
            return boundary.isspace() or boundary in "-\u2013\u2014|\u00b7:：([（【"

        return decorated_title(actual_value, expected_value) or decorated_title(expected_value, actual_value)

    def _prepare_observation_context(
        self,
        snapshot: Dict[str, Any],
        selected: Optional[Dict[str, Any]],
        locator: Optional[Dict[str, Any]],
        event: Dict[str, Any],
        *,
        tool: str,
    ) -> ObservationContext:
        selected = selected if isinstance(selected, dict) else {}
        locator = locator if isinstance(locator, dict) else {}
        focused_window = str(snapshot.get("focused_window") or "").strip()
        target_window = str(selected.get("window_name") or locator.get("window_name") or "").strip()
        provided_observation_id = str(snapshot.get("observation_id") or "").strip()
        provided_captured_at = snapshot.get("captured_at")
        context = ObservationContext(
            observation_id=provided_observation_id or "obs_" + uuid.uuid4().hex[:16],
            provider=str(snapshot.get("provider") or "windows-mcp"),
            captured_at=float(provided_captured_at) if isinstance(provided_captured_at, (int, float)) else time.time(),
            focused_window=focused_window,
            target_window=target_window,
            desktop_name=str(selected.get("desktop_name") or ""),
            element_id=str(selected.get("element_id") or ""),
            window_element_counts={
                str(key): int(value)
                for key, value in (snapshot.get("window_element_counts") if isinstance(snapshot.get("window_element_counts"), dict) else {}).items()
                if isinstance(value, int)
            },
            target_diagnostics=(event.get("target_resolution") or {}).get("diagnostics", {}) if isinstance((event.get("target_resolution") or {}).get("diagnostics", {}), dict) else {},
            mcp_label=selected.get("mcp_label") if isinstance(selected.get("mcp_label"), int) and not isinstance(selected.get("mcp_label"), bool) else None,
            handle=selected.get("handle"),
            bounds=dict(selected["bounds"]) if isinstance(selected.get("bounds"), dict) else None,
        )
        public = context.public()
        event["observation_context"] = public
        observations = event.setdefault("observations", [])
        if isinstance(observations, list):
            observations.append(public)
        self._observation_contexts[context.observation_id] = context
        if tool == "type" and self.observation_provider is None and target_window and focused_window and not self._window_matches(focused_window, target_window):
            self._consume_observation_context(event, "rejected_focus_mismatch")
            raise ElementResolutionError(
                "target_window_not_focused",
                f"输入目标窗口“{target_window}”当前未聚焦，已停止避免输入到其他窗口",
                next_action="先激活目标窗口并重新观察，再执行输入",
                retryable=True,
            )
        return context

    async def _provider_foreground_window(self) -> str:
        provider = self.observation_provider
        if provider is None:
            return ""
        value: Any = None
        if callable(provider):
            value = provider()
        else:
            for name in ("current_foreground_window", "foreground_window", "observe"):
                member = getattr(provider, name, None)
                if callable(member):
                    value = member()
                    break
                if isinstance(member, str):
                    value = member
                    break
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            candidate = value.get("focused_window") or value.get("foreground_window") or value.get("window_name") or value.get("name")
            if isinstance(candidate, dict):
                candidate = candidate.get("name") or candidate.get("title") or candidate.get("window_name")
            return str(candidate or "").strip()
        return ""

    async def _enhance_observation(self, snapshot: Dict[str, Any], locator: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        provider = self.observation_provider
        enhance = getattr(provider, "enhance", None) if provider is not None else None
        if not callable(enhance):
            return snapshot
        try:
            value = enhance(snapshot, locator or {})
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            raise ElementResolutionError(
                "snapshot_unavailable",
                f"增强元素观察失败：{str(exc)[:500]}",
                next_action="确认交互式桌面可用后重新观察",
                retryable=True,
            ) from exc
        return value if isinstance(value, dict) else snapshot

    async def _validate_observation_before_call(self, event: Dict[str, Any], tool: str) -> ObservationContext | None:
        public = event.get("observation_context") if isinstance(event.get("observation_context"), dict) else {}
        observation_id = str(public.get("observation_id") or "")
        context = self._observation_contexts.get(observation_id)
        if context is None:
            if observation_id:
                raise ElementResolutionError(
                    "observation_expired",
                    "该元素观察已经被使用或失效，必须重新观察后再执行",
                    next_action="重新观察目标元素",
                    retryable=True,
                )
            return None
        if context.status != "prepared":
            raise ElementResolutionError(
                "observation_expired",
                "该元素观察已经被使用，必须重新观察后再执行",
                next_action="重新观察目标元素",
                retryable=True,
            )
        current_foreground = await self._provider_foreground_window()
        action_name = str(tool or "").casefold()
        is_type = action_name == "type"
        expected = context.target_window or context.focused_window
        if not current_foreground or not expected:
            self._consume_observation_context(event, "rejected_focus_unknown")
            operation = "输入" if is_type else "点击"
            raise ElementResolutionError(
                "target_window_focus_unknown",
                f"无法确认{operation}目标窗口处于前台，已停止避免误操作",
                next_action="解锁桌面并激活目标窗口后重新观察",
                retryable=True,
            )
        if not self._window_matches(current_foreground, expected):
            self._consume_observation_context(event, "rejected_foreground_changed")
            message = (
                f"输入前台窗口不是目标窗口“{expected}”（当前为“{current_foreground}”），已停止避免误输入"
                if is_type
                else f"点击目标窗口“{expected}”当前未处于前台（当前为“{current_foreground}”），已停止避免误点"
            )
            raise ElementResolutionError(
                "foreground_window_changed",
                message,
                next_action="重新观察当前桌面后重试",
                retryable=True,
            )
        return context

    def _consume_observation_context(self, event: Dict[str, Any], status: str) -> None:
        public = event.get("observation_context") if isinstance(event.get("observation_context"), dict) else {}
        observation_id = str(public.get("observation_id") or "")
        context = self._observation_contexts.pop(observation_id, None)
        if context is None and str(public.get("status") or "") not in {"", "prepared"}:
            return
        if context is not None:
            recorded_arguments = event.get("arguments") if isinstance(event.get("arguments"), dict) else None
            if recorded_arguments is not None:
                if context.mcp_label is not None and recorded_arguments.get("label") == context.mcp_label:
                    recorded_arguments.pop("label", None)
                    recorded_arguments["target_binding"] = "已消费的一次性元素编号"
                target_resolution = event.get("target_resolution") if isinstance(event.get("target_resolution"), dict) else {}
                if context.bounds is not None and target_resolution.get("strategy") == "element_derived_position":
                    for key in ("loc", "x", "y"):
                        recorded_arguments.pop(key, None)
                    recorded_arguments["target_binding"] = "已消费的元素派生位置"
            context.consume(status)
        public["status"] = status
        public["consumed_at"] = time.time()

    async def _resolve_element_action(
        self,
        run: Dict[str, Any],
        node: Any,
        arguments: Dict[str, Any],
        event: Dict[str, Any],
        *,
        execution: _LegacyExecution | _ManualExecution,
        tool_override: str = "",
    ) -> Dict[str, Any]:
        """Resolve an action target from a fresh UI tree before mutating input."""
        tool_name = str(tool_override or getattr(node, "tool", "") or "")
        tool = tool_name.lower()
        if tool not in {"click", "type"}:
            return arguments
        if getattr(node, "target", None) is None and not any(key in arguments for key in ("label", "loc", "x", "y")):
            # Preserve compatibility with synthetic/test tools that do not
            # expose a UI target (real Windows-MCP Click/Type schemas reject
            # such calls during the normal workflow validation).
            return arguments
        self._require_provider_call(run, execution)
        snapshot_result = await self.session.call_tool("Snapshot", {}, timeout=None)
        snapshot = normalize_snapshot(snapshot_result)
        self._validate_element_argument_shape(tool_name, arguments, {})
        target_override = event.get("target") if isinstance(event.get("target"), dict) else None
        target = target_override if target_override is not None else getattr(node, "target", None)
        locator = target.model_dump(mode="json") if hasattr(target, "model_dump") else dict(target) if isinstance(target, dict) else None
        self._require_provider_call(run, execution)
        snapshot = await self._enhance_observation(snapshot, locator)
        match: Dict[str, Any] = {"status": "not_found", "matches": [], "match_count": 0, "confidence": "none"}
        if locator and str(locator.get("strategy") or "uia") != "position":
            match = resolve_locator(snapshot, locator)
        elif isinstance(arguments.get("label"), int) and not isinstance(arguments.get("label"), bool):
            match = resolve_locator(snapshot, {"mcp_label": int(arguments["label"])})
        elif isinstance(arguments.get("label"), str) and arguments.get("label"):
            # Legacy definitions sometimes stored a visible name in label. It
            # is a lookup hint only; it must never be sent back to MCP.
            label = str(arguments.get("label"))
            match = resolve_locator(snapshot, {"name": label, "text": label, "match": "exact"})
        selected = match.get("matches", [])[0] if match.get("status") == "unique" else None
        resolution: Dict[str, Any] = {
            "strategy": "element" if selected is not None else "unresolved",
            "status": match.get("status"),
            "confidence": match.get("confidence"),
            "candidate_count": int(match.get("match_count") or 0),
            "diagnostics": match.get("diagnostics") or {},
        }
        if selected is None:
            if match.get("status") == "ambiguous":
                event["target_resolution"] = resolution
                raise ElementResolutionError(
                    "target_ambiguous",
                    "最新桌面中找到多个相似元素，已停止避免误操作",
                    next_action="补充窗口、控件类型、父级或 AutomationId 约束后重试",
                    retryable=True,
                )
            configured_fallback = str((locator or {}).get("fallback_policy") or "")
            if configured_fallback == "controlled":
                fallback_policy = "controlled"
            elif (locator or {}).get("strategy") == "position" or arguments.get("coordinate_fallback") is True:
                fallback_policy = "controlled"
            else:
                fallback_policy = "never"
            has_coordinate = any(key in arguments for key in ("loc", "x", "y"))
            anchor = (locator or {}).get("position_anchor") if isinstance((locator or {}).get("position_anchor"), dict) else {}
            if fallback_policy == "controlled" and not has_coordinate and isinstance(anchor, dict) and isinstance(anchor.get("x"), (int, float)) and isinstance(anchor.get("y"), (int, float)):
                arguments = {**arguments, "loc": [round(float(anchor["x"])), round(float(anchor["y"]))]}
                resolution.update({"strategy": "controlled_coordinate_fallback", "stability": "low", "reason": "元素暂时未找到，使用已验证位置锚点一次"})
                event["target_resolution"] = resolution
                arguments.pop("label", None)
                self._prepare_observation_context(snapshot, None, locator, event, tool=tool)
                return {key: value for key, value in arguments.items() if key != "coordinate_fallback"}
            if fallback_policy == "never" or not has_coordinate:
                event["target_resolution"] = resolution
                raise ElementResolutionError(
                    "target_not_found",
                    "未能在最新桌面快照中唯一找到操作元素，已停止避免误点",
                    next_action="重新观察桌面并确认目标窗口/元素仍然存在",
                    retryable=True,
                )
            self._validate_element_argument_shape(tool_name, arguments, {})
            resolution.update({"strategy": "controlled_coordinate_fallback", "stability": "low", "reason": "元素未找到，使用原始坐标一次"})
            event["target_resolution"] = resolution
            # A legacy visible name is not a valid MCP label. Coordinate
            # fallback is explicit, so discard that lookup-only hint.
            arguments.pop("label", None)
            self._prepare_observation_context(snapshot, None, locator, event, tool=tool)
            return {key: value for key, value in arguments.items() if key != "coordinate_fallback"}

        if not bool(selected.get("visible", True)) or not bool(selected.get("enabled", True)):
            event["target_resolution"] = {**resolution, "reason": "元素不可见或不可用"}
            raise ElementResolutionError(
                "target_not_found",
                "目标元素当前不可见或不可用",
                next_action="确认目标窗口已打开且元素已启用，然后重新观察",
                retryable=True,
            )
        output = dict(arguments)
        self._require_provider_call(run, execution)
        catalog = await self._catalog()
        item = next((item for item in catalog if str(item.get("name") or "") == tool_name), {})
        schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
        mcp_label = selected.get("mcp_label")
        center = element_center(selected)
        # Snapshot labels are ephemeral integers. Never pass an element name
        # as ``label``; when a tool cannot accept the temporary label, derive
        # a one-shot coordinate from the same fresh observation.
        if isinstance(mcp_label, int) and not isinstance(mcp_label, bool) and (not schema or self._schema_supports(schema, "label")):
            output["label"] = mcp_label
            output.pop("loc", None)
            output.pop("x", None)
            output.pop("y", None)
        elif center is not None and (not schema or self._schema_supports(schema, "loc")):
            output["loc"] = center
            output.pop("label", None)
            output.pop("x", None)
            output.pop("y", None)
            resolution.update({"strategy": "element_derived_position", "stability": "low"})
        elif center is not None and self._schema_supports(schema, "x") and self._schema_supports(schema, "y"):
            output["x"], output["y"] = center
            output.pop("label", None)
            output.pop("loc", None)
            resolution.update({"strategy": "element_derived_position", "stability": "low"})
        elif self._schema_supports(schema, "label"):
            event["target_resolution"] = {**resolution, "reason": "当前元素没有可用临时编号或坐标"}
            raise ElementResolutionError(
                "invalid_argument_shape",
                "Windows-MCP 需要整数元素编号，但当前 Snapshot 没有可用编号",
                next_action="重新观察元素；如该应用不暴露 UIA 元素，请显式允许一次性位置兜底",
                field_errors={"label": "当前元素没有可用临时编号"},
                retryable=True,
            )
        self._validate_element_argument_shape(tool_name, output, schema)
        event["target_resolution"] = {**resolution, "element": locator_from_element(selected)}
        self._prepare_observation_context(snapshot, selected, locator, event, tool=tool)
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
        trigger_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Submit a run from the synchronous MCP tool server."""
        self._require_daemon_execution_claim()
        return self._run_sync_operation(
            self.start(
                group_id,
                workflow_id,
                actor_id=actor_id,
                version=version,
                inputs=inputs,
                authorization=authorization,
                trigger_context=trigger_context,
            )
        )

    def _run_path(self, group_id: str, run_id: str) -> Path:
        resource = self._require_run_path_segment(run_id)
        state_root = self.store.state_root(group_id).resolve()
        runs_root = (state_root / "runs").resolve()
        path = (runs_root / f"{resource}.json").resolve()
        if runs_root.parent != state_root or path.parent != runs_root:
            raise PermissionError("computer control run path is invalid")
        return path

    def _approval_path(self, group_id: str, run_id: str, node_id: str) -> Path:
        return self.store.state_root(group_id) / "approvals" / run_id / f"{node_id}.json"

    def _recovery_path(self, group_id: str, run_id: str, recovery_id: str) -> Path:
        return self.store.state_root(group_id) / "recoveries" / run_id / f"{recovery_id}.json"

    def _write_unchecked(self, group_id: str, run: Dict[str, Any]) -> None:
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

    def _get_unchecked(self, group_id: str, run_id: str) -> Dict[str, Any]:
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

    def get(self, group_id: str, run_id: str) -> Dict[str, Any]:
        return self.run_namespace.read_legacy_run(group_id, run_id)

    def get_manual(
        self,
        group_id: str,
        run_id: str,
        *,
        actor_id: str,
        operation_claim: Optional[RunOperationClaim],
    ) -> Dict[str, Any]:
        if self.run_authorities is None or not isinstance(operation_claim, RunOperationClaim):
            raise PermissionError("validated run operation claim is required")
        operation_claim.require_current()
        expected_anchor = self.run_authorities.run_anchor_for_operation_claim(operation_claim)
        run = self._get_unchecked(group_id, run_id)
        if (
            str(run.get("origin") or "") != "manual_actor"
            or operation_claim.group_id != group_id
            or operation_claim.actor_id != actor_id
            or operation_claim.resource_id != run_id
            or str(run.get("group_id") or "") != group_id
            or str(run.get("actor_id") or "") != actor_id
            or str(run.get("run_id") or "") != run_id
            or str(run.get("workflow_id") or "") != operation_claim.workflow_id
            or int(run.get("version") or 0) != operation_claim.version
            or run.get("run_authority") != expected_anchor
        ):
            raise PermissionError("manual actor run authority does not match")
        return run

    def get_manual_read(
        self,
        group_id: str,
        run_id: str,
        *,
        actor_id: str,
        read_claim: Optional[RunReadClaim],
    ) -> Dict[str, Any]:
        if self.run_authorities is None or not isinstance(read_claim, RunReadClaim):
            raise PermissionError("validated run read claim is required")
        read_claim.require_current()
        expected_anchor = self.run_authorities.run_anchor_for_read_claim(read_claim)
        run = self._get_unchecked(group_id, run_id)
        if (
            str(run.get("origin") or "") != "manual_actor"
            or read_claim.group_id != group_id
            or read_claim.actor_id != actor_id
            or read_claim.resource_id != run_id
            or str(run.get("group_id") or "") != group_id
            or str(run.get("actor_id") or "") != actor_id
            or str(run.get("run_id") or "") != run_id
            or str(run.get("workflow_id") or "") != read_claim.workflow_id
            or int(run.get("version") or 0) != read_claim.version
            or run.get("run_authority") != expected_anchor
        ):
            raise PermissionError("manual actor run read authority does not match")
        return run

    def list(self, group_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        root = self.store.state_root(group_id) / "runs"
        if not root.exists():
            return []
        bounded_limit = max(1, min(limit, 200))
        result = []
        for path in sorted(
            root.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                value = self.run_namespace.read_legacy_run(group_id, path.stem)
            except (KeyError, OSError, PermissionError, ValueError):
                continue
            if isinstance(value, dict):
                result.append(value)
                if len(result) >= bounded_limit:
                    break
        return result

    def decide_approval(self, group_id: str, run_id: str, node_id: str, *, approved: bool) -> Dict[str, Any]:
        self._require_daemon_execution_claim()
        run = self.get(group_id, run_id)
        key = self._run_key(group_id, str(run.get("actor_id") or ""), run_id)
        execution = self._legacy_executions.get(key)
        if not isinstance(execution, _LegacyExecution):
            raise PermissionError("legacy run execution allocation is unavailable")

        def validate(current: Dict[str, Any]) -> None:
            if (
                self._legacy_executions.get(key) is not execution
                or key in self._cancelled
            ):
                raise PermissionError("legacy run execution allocation is unavailable")
            if (
                str(current.get("status") or "") != "waiting_approval"
                or str(current.get("current_node_id") or "") != node_id
            ):
                raise ValueError("run is not waiting for approval on this node")

        def decide(_current: Dict[str, Any]) -> Dict[str, Any]:
            path = self._approval_path(group_id, run_id, node_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                json.dumps(
                    {"approved": bool(approved), "decided_at": time.time()},
                    ensure_ascii=False,
                )
                + "\n",
            )
            return {"run_id": run_id, "node_id": node_id, "approved": bool(approved)}

        return self.run_namespace.perform_legacy_operation_with_callback(
            execution.allocation,
            validate_current=validate,
            require_lease=lambda allocation: self.lease.require_legacy_run(
                allocation=allocation
            ),
            callback=decide,
        )

    def decide_approval_manual(
        self,
        group_id: str,
        run_id: str,
        node_id: str,
        *,
        actor_id: str,
        operation_claim: Optional[RunOperationClaim],
        approved: bool,
    ) -> Dict[str, Any]:
        self.get_manual(
            group_id,
            run_id,
            actor_id=actor_id,
            operation_claim=operation_claim,
        )
        assert isinstance(operation_claim, RunOperationClaim)

        def decide(expected_anchor: Dict[str, Any]) -> Dict[str, Any]:
            run = self._get_unchecked(group_id, run_id)
            if (
                str(run.get("origin") or "") != "manual_actor"
                or operation_claim.group_id != group_id
                or operation_claim.actor_id != actor_id
                or operation_claim.resource_id != run_id
                or str(run.get("group_id") or "") != group_id
                or str(run.get("actor_id") or "") != actor_id
                or str(run.get("run_id") or "") != run_id
                or str(run.get("workflow_id") or "") != operation_claim.workflow_id
                or int(run.get("version") or 0) != operation_claim.version
                or run.get("run_authority") != expected_anchor
            ):
                raise PermissionError("manual actor run authority does not match")
            if (
                str(run.get("status") or "") != "waiting_approval"
                or str(run.get("current_node_id") or "") != node_id
            ):
                raise ValueError("run is not waiting for approval on this node")
            path = self._approval_path(group_id, run_id, node_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                path,
                json.dumps(
                    {"approved": bool(approved), "decided_at": time.time()},
                    ensure_ascii=False,
                )
                + "\n",
            )
            return {"run_id": run_id, "node_id": node_id, "approved": bool(approved)}

        return self.run_authorities.perform_operation_with_callback(
            operation_claim,
            decide,
        )

    def submit_recovery(
        self,
        group_id: str,
        run_id: str,
        recovery_id: str = "",
        *,
        actor_id: str,
        tool: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        resolution: str = "retry",
        node_id: str = "",
        target: Optional[Dict[str, Any]] = None,
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        self._require_daemon_execution_claim()
        run = self.get(group_id, run_id)
        run_key = self._run_key(group_id, actor_id, run_id)
        execution = self._legacy_executions.get(run_key)
        if not isinstance(execution, _LegacyExecution):
            raise PermissionError("legacy run execution allocation is unavailable")
        resolution = str(resolution or "retry").strip().lower()
        if resolution not in {"reobserve", "retry", "repair_step", "replace_target", "skip", "cancel"}:
            raise ValueError("unsupported recovery resolution")
        presented_recovery_id = str(recovery_id or "")
        idempotency = str(idempotency_key or "").strip()

        def validate(current: Dict[str, Any]) -> None:
            current_recovery = (
                current.get("recovery")
                if isinstance(current.get("recovery"), dict)
                else {}
            )
            expected_recovery_id = str(current_recovery.get("recovery_id") or "")
            if (
                self._legacy_executions.get(run_key) is not execution
                or run_key in self._cancelled
            ):
                raise PermissionError("legacy run execution allocation is unavailable")
            if str(current.get("status") or "") != "recovering":
                raise ValueError("run is not waiting for this recovery")
            if str(current.get("actor_id") or "") != actor_id:
                raise PermissionError("recovery belongs to another actor")
            if presented_recovery_id and expected_recovery_id != presented_recovery_id:
                raise ValueError("recovery context has changed; refresh and retry")

        def submit(current: Dict[str, Any]) -> Dict[str, Any]:
            current_recovery = current["recovery"]
            expected_recovery_id = str(current_recovery.get("recovery_id") or "")
            if (
                idempotency
                and str(current_recovery.get("resolved_idempotency_key") or "")
                == idempotency
            ):
                return {
                    "run_id": run_id,
                    "recovery_id": expected_recovery_id,
                    "accepted": True,
                    "idempotent": True,
                }
            path = self._recovery_path(group_id, run_id, expected_recovery_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            original_arguments = (
                current_recovery.get("arguments")
                if isinstance(current_recovery.get("arguments"), dict)
                else {}
            )
            value = {
                "tool": str(tool or current_recovery.get("tool") or "").strip(),
                "arguments": (
                    arguments if isinstance(arguments, dict) else dict(original_arguments)
                ),
                "target": (
                    target if isinstance(target, dict) else current_recovery.get("target")
                ),
                "resolution": resolution,
                "node_id": node_id or current_recovery.get("node_id"),
                "actor_id": actor_id,
                "idempotency_key": idempotency,
                "submitted_at": time.time(),
            }
            atomic_write_text(
                path,
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            )
            if idempotency:
                current_recovery["resolved_idempotency_key"] = idempotency
            return {
                "run_id": run_id,
                "recovery_id": expected_recovery_id,
                "accepted": True,
            }

        return self.run_namespace.perform_legacy_operation_with_callback(
            execution.allocation,
            validate_current=validate,
            require_lease=lambda allocation: self.lease.require_legacy_run(
                allocation=allocation
            ),
            callback=submit,
            write_run=bool(idempotency),
        )

    def submit_recovery_manual(
        self,
        group_id: str,
        run_id: str,
        recovery_id: str = "",
        *,
        actor_id: str,
        operation_claim: Optional[RunOperationClaim],
        tool: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        resolution: str = "retry",
        node_id: str = "",
        target: Optional[Dict[str, Any]] = None,
        idempotency_key: str = "",
    ) -> Dict[str, Any]:
        self.get_manual(
            group_id,
            run_id,
            actor_id=actor_id,
            operation_claim=operation_claim,
        )
        resolution = str(resolution or "retry").strip().lower()
        if resolution not in {"reobserve", "retry", "repair_step", "replace_target", "skip", "cancel"}:
            raise ValueError("unsupported recovery resolution")
        key = str(idempotency_key or "").strip()
        assert isinstance(operation_claim, RunOperationClaim)

        def submit(expected_anchor: Dict[str, Any]) -> Dict[str, Any]:
            run = self._get_unchecked(group_id, run_id)
            if (
                str(run.get("origin") or "") != "manual_actor"
                or operation_claim.group_id != group_id
                or operation_claim.actor_id != actor_id
                or operation_claim.resource_id != run_id
                or str(run.get("group_id") or "") != group_id
                or str(run.get("actor_id") or "") != actor_id
                or str(run.get("run_id") or "") != run_id
                or str(run.get("workflow_id") or "") != operation_claim.workflow_id
                or int(run.get("version") or 0) != operation_claim.version
                or run.get("run_authority") != expected_anchor
            ):
                raise PermissionError("manual actor run authority does not match")
            recovery = run.get("recovery") if isinstance(run.get("recovery"), dict) else {}
            if str(run.get("status") or "") != "recovering":
                raise ValueError("run is not waiting for this recovery")
            current_recovery_id = str(recovery.get("recovery_id") or "")
            if (
                not current_recovery_id.startswith("recovery_")
                or len(current_recovery_id) != len("recovery_") + 12
                or any(character not in "0123456789abcdef" for character in current_recovery_id[9:])
            ):
                raise PermissionError("manual run recovery identity is invalid")
            if recovery_id and current_recovery_id != recovery_id:
                raise ValueError("recovery context has changed; refresh and retry")
            if key and str(recovery.get("resolved_idempotency_key") or "") == key:
                return {
                    "run_id": run_id,
                    "recovery_id": current_recovery_id,
                    "accepted": True,
                    "idempotent": True,
                }
            path = self._recovery_path(group_id, run_id, current_recovery_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            original_arguments = (
                recovery.get("arguments")
                if isinstance(recovery.get("arguments"), dict)
                else {}
            )
            value = {
                "tool": str(tool or recovery.get("tool") or "").strip(),
                "arguments": arguments
                if isinstance(arguments, dict)
                else dict(original_arguments),
                "target": target if isinstance(target, dict) else recovery.get("target"),
                "resolution": resolution,
                "node_id": node_id or recovery.get("node_id"),
                "actor_id": actor_id,
                "idempotency_key": key,
                "submitted_at": time.time(),
            }
            atomic_write_text(
                path,
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            )
            if key:
                recovery["resolved_idempotency_key"] = key
                self._write_unchecked(group_id, run)
            return {
                "run_id": run_id,
                "recovery_id": current_recovery_id,
                "accepted": True,
            }

        return self.run_authorities.perform_operation_with_callback(
            operation_claim,
            submit,
        )

    def recovery_context(self, group_id: str, run_id: str) -> Dict[str, Any]:
        run = self.get(group_id, run_id)
        recovery = run.get("recovery") if isinstance(run.get("recovery"), dict) else None
        if not recovery:
            return {"run_id": run_id, "pending": False, "status": run.get("status"), "run": run}
        return {"run_id": run_id, "pending": str(run.get("status") or "") == "recovering", "status": run.get("status"), "recovery": recovery}

    def recovery_context_manual(
        self,
        group_id: str,
        run_id: str,
        *,
        actor_id: str,
        operation_claim: Optional[RunOperationClaim],
    ) -> Dict[str, Any]:
        run = self.get_manual(
            group_id,
            run_id,
            actor_id=actor_id,
            operation_claim=operation_claim,
        )
        recovery = run.get("recovery") if isinstance(run.get("recovery"), dict) else None
        if not recovery:
            return {"run_id": run_id, "pending": False, "status": run.get("status"), "run": run}
        return {
            "run_id": run_id,
            "pending": str(run.get("status") or "") == "recovering",
            "status": run.get("status"),
            "recovery": recovery,
        }

    async def _wait_for_recovery(
        self,
        run: Dict[str, Any],
        event: Dict[str, Any],
        *,
        error: Exception,
        attempt: int,
        execution: _LegacyExecution | _ManualExecution,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        group_id, run_id = str(run["group_id"]), str(run["run_id"])
        key = self._run_key(group_id, str(run["actor_id"]), run_id)
        recovery_id = "recovery_" + uuid.uuid4().hex[:12]
        observation: Any = None
        try:
            self._require_provider_call(run, execution)
            observation = normalize_tool_result("Snapshot", await self.session.call_tool("Snapshot", {}, timeout=None))
            observation = self._redact_result(observation)
        except Exception as observation_error:
            observation = {"error": str(observation_error)[:1000]}
        self._require_provider_call(run, execution)
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
            "error": {
                "code": str(getattr(error, "code", "tool_call_failed")),
                "layer": str(getattr(error, "layer", "tool")),
                "message": str(error)[:1000],
                "next_action": str(getattr(error, "next_action", "") or ""),
                "field_errors": getattr(error, "field_errors", {}) if isinstance(getattr(error, "field_errors", {}), dict) else {},
                "retryable": bool(getattr(error, "retryable", True)),
            },
            "tool": active_tool,
            # Keep templates (including secret references) rather than
            # resolved values so recovery can retry without leaking secrets.
            "arguments": self._redact_result(arguments if isinstance(arguments, dict) else {}),
            "target": event.get("target") if isinstance(event.get("target"), dict) else None,
            "tool_schema": tool_schema if isinstance(tool_schema, dict) else {},
            "observation": observation,
            "delivery_status": "pending",
        }
        run["status"] = "recovering"
        self._transition_manual(run, execution, "waiting_recovery")
        run["recovery"] = recovery
        event["status"] = "recovering"
        event["recovery_id"] = recovery_id
        self._write_execution(group_id, run, execution)
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
                    "请通过恢复接口提交 resolution（reobserve、retry、repair_step、replace_target、skip 或 cancel）；"
                    "服务器会自动补齐 recovery_id、工具 Schema 和原始参数。"
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
        self._write_execution(group_id, run, execution)
        self._emit("recovery.delivery", group_id=group_id, run_id=run_id, recovery_id=recovery_id, status=recovery["delivery_status"])
        if not delivered:
            raise RuntimeError("AI 自适应恢复通知无法投递：执行智能体未运行或当前不可接收消息")
        path = self._recovery_path(group_id, run_id, recovery_id)
        while True:
            if key in self._cancelled:
                raise asyncio.CancelledError()
            if path.exists():
                try:
                    patch = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    patch = {}
                if isinstance(patch, dict) and isinstance(patch.get("arguments"), dict):
                    self._transition_manual(run, execution, "running")
                    run["status"] = "running"
                    run.pop("recovery", None)
                    event["status"] = "running"
                    self._write_execution(group_id, run, execution)
                    return patch
            await asyncio.sleep(0.5)

    async def _wait_for_approval(
        self,
        run: Dict[str, Any],
        node_id: str,
        *,
        execution: _LegacyExecution | _ManualExecution,
        timeout: Optional[float] = None,
    ) -> None:
        group_id, run_id = str(run["group_id"]), str(run["run_id"])
        key = self._run_key(group_id, str(run["actor_id"]), run_id)
        path = self._approval_path(group_id, run_id, node_id)
        run["status"] = "waiting_approval"
        self._transition_manual(run, execution, "waiting_approval")
        self._write_execution(group_id, run, execution)
        self._emit("run.approval_required", group_id=group_id, run_id=run_id, node_id=node_id)
        while True:
            if key in self._cancelled:
                raise asyncio.CancelledError()
            if path.exists():
                try:
                    decision = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    decision = {}
                if decision.get("approved") is True:
                    self._transition_manual(run, execution, "running")
                    run["status"] = "running"
                    self._write_execution(group_id, run, execution)
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
        trigger_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        daemon_execution_claim = self._require_daemon_execution_claim()
        selected = self.store.get(group_id, workflow_id, version=version)
        definition = WorkflowDefinition.model_validate({k: v for k, v in selected["definition"].items() if k != "change_note"})
        self._validate_node_path_ids(definition)
        effective_inputs = self._effective_inputs(definition, inputs)
        run_id = self._require_run_path_segment("run_" + uuid.uuid4().hex[:16])
        key = self._run_key(group_id, actor_id, run_id)
        now = time.time()
        trigger = {
            key: trigger_context.get(key)
            for key in (
                "trigger_id",
                "name",
                "type",
                "scheduled_for",
                "detected_at",
                "definition_fingerprint",
                "source",
            )
            if isinstance(trigger_context, dict) and trigger_context.get(key) not in (None, "")
        }
        if isinstance(trigger_context, dict) and isinstance(trigger_context.get("evidence"), dict):
            trigger["evidence"] = dict(trigger_context["evidence"])
        run = {
            "origin": "legacy_internal",
            "run_id": run_id,
            "group_id": group_id,
            "workflow_id": workflow_id,
            "version": selected["version"],
            "actor_id": actor_id,
            "status": "initializing",
            "current_node_id": None,
            "started_at": now,
            "updated_at": now,
            "events": [],
            **({"trigger": trigger} if trigger else {}),
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
                    "allow_workflow_edit",
                    "created_ts",
                )
            } if isinstance(authorization, dict) else {},
        }
        allocation, run = self.run_namespace.allocate_legacy_run(
            group_id=group_id,
            actor_id=actor_id,
            resource_id=run_id,
            initial_run=run,
            reserve_lease=lambda claim: self.lease.reserve_legacy_run(
                group_id=group_id,
                actor_id=actor_id,
                run_id=run_id,
                allocation=claim,
            ),
            cancel_reservation=lambda claim: self.lease.cancel_legacy_run_reservation(
                allocation=claim,
            ),
        )
        execution = _LegacyExecution(allocation, daemon_execution_claim)
        active_lease = False
        try:
            self._require_execution(run, execution)
            self.lease.activate_legacy_run_reservation(allocation=allocation)
            active_lease = True
            self._require_provider_call(run, execution)
            start_catalog = await self._catalog()
            if start_catalog:
                validate_workflow_tools(definition, start_catalog)
            self._require_provider_call(run, execution)
            run["status"] = "running"
            run["updated_at"] = time.time()
            self._write_execution(group_id, run, execution)
            task = asyncio.create_task(
                self._execute(run, definition, effective_inputs, execution)
            )
            self._legacy_executions[key] = execution
            self._tasks[key] = task
            task.add_done_callback(
                lambda completed, run_key=key: self._remove_current(
                    self._tasks,
                    run_key,
                    completed,
                )
            )
            self._emit(
                "run.started",
                group_id=group_id,
                run_id=run_id,
                workflow_id=workflow_id,
                actor_id=actor_id,
                version=selected["version"],
                trigger=trigger or None,
            )
            return run
        except BaseException as start_error:
            try:
                self._require_execution(run, execution)
                released = (
                    self.lease.release_legacy_run(allocation=allocation)
                    if active_lease
                    else self.lease.cancel_legacy_run_reservation(allocation=allocation)
                )
                if not released:
                    raise PermissionError("legacy run allocation cleanup lost ownership")
            except BaseException as cleanup_error:
                try:
                    start_error.add_note(
                        "legacy run allocation cleanup failed: "
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    )
                except Exception:
                    pass
            else:
                run.update(
                    {
                        "status": "start_failed",
                        "finished_at": time.time(),
                        "updated_at": time.time(),
                    }
                )
                try:
                    self.run_namespace.write_legacy_run(allocation, run)
                except BaseException as projection_error:
                    try:
                        start_error.add_note(
                            "legacy run start-failed projection failed: "
                            f"{type(projection_error).__name__}: {projection_error}"
                        )
                    except Exception:
                        pass
            raise

    def start_manual_sync(
        self,
        group_id: str,
        workflow_id: str,
        *,
        actor_id: str,
        version: Optional[int],
        inputs: Dict[str, Any],
        request_id: str,
        start_claim: RunStartClaim,
    ) -> Dict[str, Any]:
        return self._run_sync_operation(
            self.start_manual(
                group_id,
                workflow_id,
                actor_id=actor_id,
                version=version,
                inputs=inputs,
                request_id=request_id,
                start_claim=start_claim,
            )
        )

    async def start_manual(
        self,
        group_id: str,
        workflow_id: str,
        *,
        actor_id: str,
        version: Optional[int],
        inputs: Dict[str, Any],
        request_id: str,
        start_claim: RunStartClaim,
    ) -> Dict[str, Any]:
        if self.run_authorities is None:
            raise PermissionError("run authority store is unavailable")
        if self.requests is None:
            raise PermissionError("computer control request store is unavailable")
        normalized_inputs = normalized_run_inputs(inputs)
        selected = self.store.get(group_id, workflow_id, version=version)
        selected_version = int(selected.get("version") or 0)
        actual_definition_digest = workflow_definition_digest(selected.get("definition"))
        if (
            start_claim.group_id != group_id
            or start_claim.actor_id != actor_id
            or start_claim.request_id != request_id
            or start_claim.workflow_id != workflow_id
            or start_claim.version != selected_version
            or start_claim.definition_digest != actual_definition_digest
            or start_claim.inputs_digest != run_inputs_digest(normalized_inputs)
        ):
            raise PermissionError("run start claim does not match the selected execution scope")
        definition = WorkflowDefinition.model_validate(
            {key: value for key, value in selected["definition"].items() if key != "change_note"}
        )
        self._validate_node_path_ids(definition)
        run_id = self._require_run_path_segment("run_" + uuid.uuid4().hex[:16])
        key = self._run_key(group_id, actor_id, run_id)
        issue = self.run_authorities.begin_run(
            group_id=group_id,
            actor_id=actor_id,
            resource_id=run_id,
            request_id=request_id,
            start_claim=start_claim,
        )
        now = time.time()
        run = {
            "origin": "manual_actor",
            "run_id": run_id,
            "group_id": group_id,
            "workflow_id": workflow_id,
            "version": int(selected["version"]),
            "actor_id": actor_id,
            "status": "initializing",
            "current_node_id": None,
            "started_at": now,
            "updated_at": now,
            "events": [],
            "metrics": {
                "replay_success": False,
                "transport_restarts": 0,
                "transport_restarts_at_start": int(
                    getattr(self.session, "transport_restarts", 0)
                ),
            },
            "authorization": {
                "request_id": request_id,
                **dict(issue.operation_claim.permission_snapshot),
            },
            "run_authority": self.run_authorities.run_anchor_for_execution_claim(
                issue.execution_seed
            ),
        }
        reserved = False
        accepted = None
        active_lease = False
        execution: _ManualExecution | None = None
        try:
            self._write_unchecked(group_id, run)
            self.lease.reserve_run(
                group_id=group_id,
                actor_id=actor_id,
                run_id=run_id,
                authority=issue.execution_seed,
            )
            reserved = True
            self.requests.mark_run_started(
                group_id,
                request_id,
                run_id=run_id,
                status="initializing",
            )
            accepted = self.run_authorities.accept_execution(issue.execution_seed)
            self.lease.activate_run_reservation(
                pending=issue.execution_seed,
                active=accepted.execution_claim,
            )
            active_lease = True
            execution = _ManualExecution(self.run_authorities, accepted.execution_claim)
            self._manual_executions[key] = execution
            self._require_execution(run, execution)
            self._require_provider_call(run, execution)
            start_catalog = await self._catalog()
            if start_catalog:
                validate_workflow_tools(definition, start_catalog)
            self._require_provider_call(run, execution)
            self._transition_manual(run, execution, "running")
            run["status"] = "running"
            run["updated_at"] = time.time()
            self._write_execution(group_id, run, execution)
            effective_inputs = self._effective_inputs(definition, normalized_inputs)
            task = asyncio.create_task(self._execute(run, definition, effective_inputs, execution))
            self._tasks[key] = task
            task.add_done_callback(
                lambda completed, run_key=key: self._remove_current(
                    self._tasks,
                    run_key,
                    completed,
                )
            )
            self._emit(
                "run.started",
                group_id=group_id,
                run_id=run_id,
                workflow_id=workflow_id,
                actor_id=actor_id,
                version=selected["version"],
            )
            return {**run, "run_authority_receipt": issue.receipt}
        except Exception as start_error:
            if execution is not None:
                self._remove_current(self._manual_executions, key, execution)
            rollback_errors: List[str] = []

            def attempt_rollback(label: str, callback: Any) -> bool:
                try:
                    callback()
                except Exception as cleanup_error:
                    rollback_errors.append(
                        f"{label}: {type(cleanup_error).__name__}: {cleanup_error}"
                    )
                    return False
                return True

            authority_closed = False
            if accepted is not None:
                rollback_claim = execution.claim if execution is not None else accepted.execution_claim
                if active_lease:
                    lease_closed = attempt_rollback(
                        "release active run lease",
                        lambda: self.lease.release_run(
                            run_id=run_id,
                            authority=rollback_claim,
                        ),
                    )
                elif reserved:
                    lease_closed = attempt_rollback(
                        "cancel accepted run reservation",
                        lambda: self.lease.cancel_accepted_run_reservation(
                            pending=issue.execution_seed,
                            active=accepted.execution_claim,
                        ),
                    )
                else:
                    lease_closed = True
                if lease_closed:
                    authority_closed = attempt_rollback(
                        "fail accepted run authority",
                        lambda: self.run_authorities.fail_accepted_start(rollback_claim),
                    )
            else:
                if reserved:
                    lease_closed = attempt_rollback(
                        "cancel pending run reservation",
                        lambda: self.lease.cancel_run_reservation(
                            authority=issue.execution_seed
                        ),
                    )
                else:
                    lease_closed = True
                if lease_closed:
                    authority_closed = attempt_rollback(
                        "revoke pending run authority",
                        lambda: self.run_authorities.revoke_pending(issue.operation_claim),
                    )
            if authority_closed:
                run.update({"status": "start_failed", "updated_at": time.time()})
                if self._run_path(group_id, run_id).exists():
                    attempt_rollback(
                        "write start-failed run projection",
                        lambda: self._write_unchecked(group_id, run),
                    )
            if rollback_errors:
                try:
                    start_error.add_note(
                        "manual run rollback incomplete; restart recovery will reconcile: "
                        + "; ".join(rollback_errors)
                    )
                except Exception:
                    pass
            raise

    @staticmethod
    def _validate_node_path_ids(definition: WorkflowDefinition) -> None:
        for node in definition.nodes:
            node_id = str(node.id)
            if (
                node_id in {".", ".."}
                or "/" in node_id
                or "\\" in node_id
                or "\x00" in node_id
                or Path(node_id).is_absolute()
            ):
                raise PermissionError("workflow node id must be a single path segment")

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

    async def _execute(
        self,
        run: Dict[str, Any],
        definition: WorkflowDefinition,
        inputs: Dict[str, Any],
        execution: _LegacyExecution | _ManualExecution,
    ) -> None:
        group_id, run_id, actor_id = run["group_id"], run["run_id"], run["actor_id"]
        key = self._run_key(str(group_id), str(actor_id), str(run_id))
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
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(run, lease_lost, execution))
        try:
            while True:
                if key in self._cancelled:
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
                self._write_execution(group_id, run, execution)
                self._emit("node.started", group_id=group_id, run_id=run_id, workflow_id=run.get("workflow_id"), node_id=current, node_type=node.type)
                result: Any = None
                branch = "next"
                if node.type == "action":
                    self._require_provider_call(run, execution)
                    logical_arguments = self._resolve(node.arguments, inputs, steps)
                    arguments = await self._resolve_element_action(
                        run,
                        node,
                        dict(logical_arguments),
                        event,
                        execution=execution,
                    )
                    event["arguments"] = self._redact_result(arguments)
                    event["target"] = node.target.model_dump(mode="json") if node.target is not None else None
                    self._require_provider_call(run, execution)
                    live_catalog = await self._catalog()
                    catalog_item = next((item for item in live_catalog if str(item.get("name") or "") == node.tool), {})
                    if catalog_item:
                        try:
                            validate_arguments_against_schema(
                                node.tool,
                                arguments,
                                catalog_item.get("inputSchema") if isinstance(catalog_item.get("inputSchema"), dict) else {},
                            )
                        except Exception:
                            self._consume_observation_context(event, "rejected_validation")
                            raise
                    last_error: Optional[Exception] = None
                    active_tool = node.tool
                    event["tool"] = active_tool
                    recovered_arguments: Optional[Dict[str, Any]] = None
                    configured_attempts = node.retries + 1
                    for attempt in range(configured_attempts):
                        try:
                            self._require_provider_call(run, execution)
                            await self._validate_observation_before_call(event, active_tool)
                            self._require_provider_call(run, execution)
                            result = normalize_tool_result(active_tool, await self.session.call_tool(active_tool, arguments, timeout=node.timeout_seconds))
                            self._consume_observation_context(event, "consumed_success")
                            last_error = None
                            break
                        except asyncio.CancelledError:
                            self._consume_observation_context(event, "consumed_cancelled")
                            raise
                        except Exception as exc:
                            self._consume_observation_context(event, "consumed_failed")
                            last_error = exc
                            event["attempt"] = attempt + 1
                            if isinstance(exc, (MCPUnavailable, MCPOutcomeUnknown)):
                                raise
                            if attempt + 1 < configured_attempts and active_tool.lower() in {"click", "type"}:
                                # Snapshot labels/handles/bounds belong to one
                                # call only. Every retry starts from business
                                # arguments and creates a new observation.
                                arguments = await self._resolve_element_action(
                                    run,
                                    node,
                                    dict(logical_arguments),
                                    event,
                                    execution=execution,
                                )
                                event["arguments"] = self._redact_result(arguments)
                                if catalog_item:
                                    try:
                                        validate_arguments_against_schema(
                                            node.tool,
                                            arguments,
                                            catalog_item.get("inputSchema") if isinstance(catalog_item.get("inputSchema"), dict) else {},
                                        )
                                    except Exception:
                                        self._consume_observation_context(event, "rejected_validation")
                                        raise
                    recovery_attempt = 0
                    while last_error is not None and node.adaptive and recovery_attempt < 3:
                        recovery_attempt += 1
                        patch = await self._wait_for_recovery(
                            run,
                            event,
                            error=last_error,
                            attempt=recovery_attempt,
                            execution=execution,
                            arguments=node.arguments,
                        )
                        resolution = str(patch.get("resolution") or "retry").strip().lower()
                        if resolution == "cancel":
                            raise asyncio.CancelledError()
                        if resolution == "skip":
                            event["recovery_skipped"] = True
                            result = {"skipped": True, "reason": "用户选择跳过失败步骤"}
                            last_error = None
                            break
                        patched_tool = str(patch.get("tool") or active_tool).strip() or active_tool
                        self._require_provider_call(run, execution)
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
                        recovery_logical_arguments = patch.get("arguments") if isinstance(patch.get("arguments"), dict) else logical_arguments
                        arguments = self._resolve(recovery_logical_arguments, inputs, steps)
                        if isinstance(patch.get("target"), dict):
                            event["target"] = patch["target"]
                        if patched_tool.lower() in {"click", "type"}:
                            arguments = await self._resolve_element_action(
                                run,
                                node,
                                arguments,
                                event,
                                execution=execution,
                                tool_override=patched_tool,
                            )
                        event["tool"] = active_tool
                        try:
                            self._require_provider_call(run, execution)
                            await self._validate_observation_before_call(event, active_tool)
                            self._require_provider_call(run, execution)
                            result = normalize_tool_result(active_tool, await self.session.call_tool(active_tool, arguments, timeout=node.timeout_seconds))
                            self._consume_observation_context(event, "consumed_success")
                            recovered_arguments = patch.get("arguments")
                            last_error = None
                        except asyncio.CancelledError:
                            self._consume_observation_context(event, "consumed_cancelled")
                            raise
                        except Exception as exc:
                            self._consume_observation_context(event, "consumed_failed")
                            last_error = exc
                    if last_error is not None:
                        raise last_error
                    # A heartbeat loss after the side effect must not erase
                    # the completed action. The next mutating node will stop;
                    # an end node can still capture final evidence so a user
                    # can perform read-only verification safely.
                    if lease_lost.is_set():
                        event["lease_warning"] = "电脑控制租约在动作完成后失联；后续写操作已暂停"
                    if node.tool.lower() in {"click", "type"}:
                        try:
                            self._require_provider_call(run, execution)
                            after_snapshot = normalize_snapshot(await self.session.call_tool("Snapshot", {}, timeout=None))
                            target_locator = event.get("target") if isinstance(event.get("target"), dict) else None
                            self._require_provider_call(run, execution)
                            after_snapshot = await self._enhance_observation(after_snapshot, target_locator)
                            event["post_action_observation"] = {
                                "observation_id": after_snapshot.get("observation_id"),
                                "captured_at": after_snapshot.get("captured_at"),
                                "provider": after_snapshot.get("provider"),
                                "focused_window": after_snapshot.get("focused_window"),
                                "target_window": after_snapshot.get("target_window"),
                                "target_window_element_count": after_snapshot.get("target_window_element_count"),
                                "element_count": after_snapshot.get("count", 0),
                                "window_element_counts": after_snapshot.get("window_element_counts", {}),
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
                    self._write_execution(group_id, run, execution)
                    await self._wait_for_approval(
                        run,
                        current,
                        execution=execution,
                    )
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
                    self._require_provider_call(run, execution)
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
                run["verification"] = {
                    "passed": True,
                    "summary": "所有步骤和最终证据已完成",
                    "verified_by": actor_id,
                    "verified_at": time.time(),
                    "automatic": True,
                }
                if isinstance(execution, _ManualExecution):
                    run["status"] = "completed"
                else:
                    authorization = (
                        run.get("authorization")
                        if isinstance(run.get("authorization"), dict)
                        else {}
                    )
                    has_request_authorization = bool(
                        str(authorization.get("request_id") or "").strip()
                    )
                    finalized: Dict[str, Any] = {}
                    if has_request_authorization and authorization.get("allow_publish") is not False:
                        finalized["published"] = self.store.publish(
                            group_id, str(run["workflow_id"]), int(run["version"])
                        )
                    fingerprint = str(self.fingerprint_provider() or "")
                    if (
                        has_request_authorization
                        and authorization.get("allow_trust") is not False
                        and fingerprint
                    ):
                        finalized["trusted"] = self.store.trust(
                            group_id,
                            str(run["workflow_id"]),
                            int(run["version"]),
                            fingerprint=fingerprint,
                            permissions=["all_windows_mcp_tools"],
                        )
                    run["finalization"] = {
                        "published": "published" in finalized,
                        "trusted": "trusted" in finalized,
                        "unattended_triggers_authorized": has_request_authorization
                        and authorization.get("allow_unattended_triggers") is not False,
                    }
                    run["status"] = "published" if finalized else "verified"
            else:
                run["status"] = "awaiting_verification"
        except self._ExternalLeaseLost as exc:
            if run.get("events") and isinstance(run["events"][-1], dict):
                self._consume_observation_context(run["events"][-1], "consumed_lease_lost")
            run.update({"status": "external_blocked", "error": {"code": "computer_control_busy", "message": str(exc)}, "finished_at": time.time(), "updated_at": time.time()})
            run["lease_recovery"] = {"eligible": bool(run.get("metrics", {}).get("replay_success")) and bool(run.get("evidence")), "reason": "lease_lost"}
        except asyncio.CancelledError:
            if run.get("events") and isinstance(run["events"][-1], dict):
                self._consume_observation_context(run["events"][-1], "consumed_cancelled")
            run.update({"status": "cancelled", "finished_at": time.time(), "updated_at": time.time()})
        except Exception as exc:
            if run.get("events") and isinstance(run["events"][-1], dict):
                self._consume_observation_context(run["events"][-1], "consumed_failed")
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
                        "error": {
                            "code": str(getattr(exc, "code", "run_failed")),
                            "layer": str(getattr(exc, "layer", "runtime")),
                            "message": str(exc)[:2000],
                            "next_action": str(getattr(exc, "next_action", "") or ""),
                            "field_errors": getattr(exc, "field_errors", {}) if isinstance(getattr(exc, "field_errors", {}), dict) else {},
                            "retryable": bool(getattr(exc, "retryable", True)),
                        },
                    })
            run.update({
                "status": "failed",
                "error": {
                    "code": str(getattr(exc, "code", "run_failed")),
                    "layer": str(getattr(exc, "layer", "runtime")),
                    "message": str(exc),
                    "next_action": str(getattr(exc, "next_action", "") or ""),
                    "field_errors": getattr(exc, "field_errors", {}) if isinstance(getattr(exc, "field_errors", {}), dict) else {},
                    "retryable": bool(getattr(exc, "retryable", True)),
                },
                "finished_at": time.time(),
                "updated_at": time.time(),
            })
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
            if isinstance(execution, _ManualExecution):
                self._finalize_manual_execution(group_id, run, execution)
            else:
                self._require_execution(run, execution)
                released = self.lease.release_legacy_run(
                    allocation=execution.allocation
                )
                if not released and str(run.get("status") or "") != "external_blocked":
                    run.update(
                        {
                            "status": "external_blocked",
                            "error": {
                                "code": "computer_control_busy",
                                "message": "legacy run lease ownership was lost before finalization",
                            },
                            "finished_at": time.time(),
                            "updated_at": time.time(),
                        }
                    )
                self.run_namespace.write_legacy_run(execution.allocation, run)
            try:
                from ..kernel.events import publish_event

                publish_event(
                    f"computer_control.run.{run.get('status', 'finished')}",
                    {"group_id": group_id, "run_id": run_id, "workflow_id": run.get("workflow_id"), "status": run.get("status")},
                )
            except Exception:
                pass
            self._cancelled.discard(key)
            if isinstance(execution, _LegacyExecution) and str(
                run.get("status") or ""
            ) not in {
                "initializing",
                "running",
                "recovering",
                "waiting_approval",
                "awaiting_verification",
                "external_blocked",
            }:
                self._remove_current(self._legacy_executions, key, execution)

    @staticmethod
    def _redact_result(value: Any) -> Any:
        if value is None:
            return None
        sensitive = ("password", "passwd", "secret", "api_key", "token", "authorization", "cookie")

        def redact(child: Any, key: str = "") -> Any:
            if any(part in key.casefold() for part in sensitive):
                return "<redacted>"
            if isinstance(child, dict):
                return {str(name): redact(item, str(name)) for name, item in child.items()}
            if isinstance(child, list):
                return [redact(item, key) for item in child]
            return child

        value = redact(value)
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
        self._require_daemon_execution_claim()
        run = self.get(group_id, run_id)
        actor_id = str(run.get("actor_id") or "")
        key = self._run_key(group_id, actor_id, run_id)
        execution = self._legacy_executions.get(key)
        if not isinstance(execution, _LegacyExecution):
            raise PermissionError("legacy run execution allocation is unavailable")

        def validate(current: Dict[str, Any]) -> None:
            if self._legacy_executions.get(key) is not execution:
                raise PermissionError("legacy run execution allocation is unavailable")
            if str(current.get("status") or "") not in {
                "running",
                "recovering",
                "waiting_approval",
                "awaiting_verification",
                "external_blocked",
            }:
                raise PermissionError("legacy run execution allocation is unavailable")

        self.run_namespace.perform_legacy_operation_with_callback(
            execution.allocation,
            validate_current=validate,
            require_lease=None,
            callback=lambda _current: self._cancelled.add(key),
        )
        task = self._tasks.get(key)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        else:
            self._require_execution(run, execution)
            self.lease.release_legacy_run(allocation=execution.allocation)
            run.update(
                {
                    "status": "cancelled",
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                }
            )
            self.run_namespace.write_legacy_run(execution.allocation, run)
            self._remove_current(self._legacy_executions, key, execution)
            self._cancelled.discard(key)
        if emergency:
            await self.session.stop()
        return self._get_unchecked(group_id, run_id)

    def cancel_sync(self, group_id: str, run_id: str, *, emergency: bool = False) -> Dict[str, Any]:
        self._require_daemon_execution_claim()
        return self._run_sync_operation(
            self.cancel(group_id, run_id, emergency=emergency)
        )

    async def cancel_manual(
        self,
        group_id: str,
        run_id: str,
        *,
        stop_claim: Optional[RunStopOwnerClaim],
        emergency: bool = False,
    ) -> Dict[str, Any]:
        if self.run_authorities is None or not isinstance(stop_claim, RunStopOwnerClaim):
            raise PermissionError("validated run stop-owner claim is required")
        if stop_claim.group_id != group_id or stop_claim.resource_id != run_id:
            raise PermissionError("run stop-owner claim does not match the expected resource")
        key = self._run_key(group_id, stop_claim.actor_id, run_id)
        execution = self._manual_executions.get(key)
        if not isinstance(execution, _ManualExecution):
            raise PermissionError("manual actor run execution is unavailable")
        holder: Dict[str, Dict[str, Any]] = {}

        def validate_live(expected_anchor: Dict[str, Any]) -> None:
            run = self._get_unchecked(group_id, run_id)
            if (
                self._manual_executions.get(key) is not execution
                or str(run.get("origin") or "") != "manual_actor"
                or str(run.get("group_id") or "") != group_id
                or str(run.get("actor_id") or "") != stop_claim.actor_id
                or str(run.get("run_id") or "") != run_id
                or run.get("run_authority") != expected_anchor
            ):
                raise PermissionError("manual actor run execution is unavailable")
            holder["run"] = run

        def on_started(claim: RunTerminationClaim) -> None:
            execution.termination = claim
            self._cancelled.add(key)

        self.run_authorities.begin_cancel_for_execution(
            stop_claim,
            execution.claim,
            validate_live=validate_live,
            on_started=on_started,
        )
        run = holder["run"]
        task = self._tasks.get(key)
        if task is not None:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        else:
            run.update({"status": "cancelled", "finished_at": time.time(), "updated_at": time.time()})
            self._finalize_manual_execution(group_id, run, execution)
        if emergency:
            await self.session.stop()
        return self._get_unchecked(group_id, run_id)

    def cancel_manual_sync(
        self,
        group_id: str,
        run_id: str,
        *,
        stop_claim: Optional[RunStopOwnerClaim],
        emergency: bool = False,
    ) -> Dict[str, Any]:
        return self._run_sync_operation(
            self.cancel_manual(
                group_id,
                run_id,
                stop_claim=stop_claim,
                emergency=emergency,
            )
        )

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
        self._require_daemon_execution_claim()
        run = self.get(group_id, run_id)
        if str(run.get("actor_id") or "") != actor_id:
            raise PermissionError("run belongs to another actor")
        key = self._run_key(group_id, actor_id, run_id)
        execution = self._legacy_executions.get(key)
        if not isinstance(execution, _LegacyExecution):
            raise PermissionError("legacy run execution allocation is unavailable")
        finalization_error: list[BaseException] = []

        def validate(current: Dict[str, Any]) -> None:
            if (
                self._legacy_executions.get(key) is not execution
                or key in self._cancelled
            ):
                raise PermissionError("legacy run execution allocation is unavailable")
            status = str(current.get("status") or "")
            metrics = current.get("metrics") if isinstance(current.get("metrics"), dict) else {}
            lease_recovery = (
                status == "external_blocked"
                and bool(metrics.get("replay_success"))
                and bool(current.get("evidence"))
            )
            if status != "awaiting_verification" and not lease_recovery:
                raise ValueError("run is not awaiting verification")
            failure_markers = [
                item
                for item in (
                    current.get("events")
                    if isinstance(current.get("events"), list)
                    else []
                )
                if isinstance(item, dict)
                and (
                    str(item.get("status") or "") in {"failed", "recovering"}
                    or self._contains_failure_marker(item.get("result"))
                    or self._contains_failure_marker(item.get("error"))
                )
            ]
            if passed and (
                failure_markers
                or (current.get("error") and not lease_recovery)
                or not bool(metrics.get("replay_success"))
            ):
                raise ValueError("该运行包含失败步骤，不能验证为成功")
            authorization = (
                current.get("authorization")
                if isinstance(current.get("authorization"), dict)
                else {}
            )
            if (
                passed
                and str(authorization.get("request_id") or "").strip()
                and authorization.get("allow_trust") is not False
                and not fingerprint
            ):
                raise ValueError("Windows-MCP fingerprint is unavailable")

        def prepare_lease(allocation: LegacyRunAllocationClaim) -> Dict[str, Any]:
            self.lease.reserve_legacy_run(
                group_id=group_id,
                actor_id=actor_id,
                run_id=run_id,
                allocation=allocation,
            )
            self.lease.activate_legacy_run_reservation(allocation=allocation)
            return self.lease.require_legacy_run(allocation=allocation)

        def finalize(current: Dict[str, Any]) -> Dict[str, Any]:
            authorization = (
                current.get("authorization")
                if isinstance(current.get("authorization"), dict)
                else {}
            )
            has_request_authorization = bool(
                str(authorization.get("request_id") or "").strip()
            )
            current["verification"] = {
                "passed": bool(passed),
                "summary": str(summary or "")[:2000],
                "evidence_ids": [str(item)[:200] for item in evidence_ids[:100]],
                "verified_by": actor_id,
                "verified_at": time.time(),
            }
            finalized: Dict[str, Any] = {}
            try:
                if not passed:
                    current["status"] = "verification_failed"
                    return current
                workflow_id, version = str(current["workflow_id"]), int(current["version"])
                finalized: Dict[str, Any] = {}
                if has_request_authorization and authorization.get("allow_publish") is not False:
                    finalized["published"] = self.store.publish(group_id, workflow_id, version)
                if has_request_authorization and authorization.get("allow_trust") is not False:
                    finalized["trusted"] = self.store.trust(
                        group_id,
                        workflow_id,
                        version,
                        fingerprint=fingerprint,
                        permissions=["all_windows_mcp_tools"],
                    )
                current["status"] = "published" if finalized else "verified"
            except BaseException as exc:
                finalization_error.append(exc)
                current["status"] = "verification_failed"
                current["error"] = {
                    "code": "verification_finalization_failed",
                    "message": str(exc)[:2000],
                }
            finally:
                current["finalization"] = {
                    "published": "published" in finalized,
                    "trusted": "trusted" in finalized,
                    "unattended_triggers_authorized": has_request_authorization and authorization.get("allow_unattended_triggers") is not False,
                }
                current["updated_at"] = time.time()
                if not self.lease.release_legacy_run(allocation=execution.allocation):
                    raise PermissionError(
                        "legacy run lease ownership was lost before verification"
                    )
            return current

        result = self.run_namespace.perform_legacy_operation_with_callback(
            execution.allocation,
            validate_current=validate,
            require_lease=prepare_lease,
            callback=finalize,
            write_run=True,
        )
        self._remove_current(self._legacy_executions, key, execution)
        self._emit(
            "run.verified",
            group_id=group_id,
            run_id=run_id,
            workflow_id=result.get("workflow_id"),
            passed=bool(passed),
            status=result["status"],
        )
        if finalization_error:
            raise finalization_error[0]
        return result

    def verify_manual(
        self,
        group_id: str,
        run_id: str,
        *,
        actor_id: str,
        operation_claim: Optional[RunOperationClaim],
        passed: bool,
        summary: str,
        evidence_ids: List[str],
        fingerprint: str,
    ) -> Dict[str, Any]:
        run = self.get_manual(
            group_id,
            run_id,
            actor_id=actor_id,
            operation_claim=operation_claim,
        )
        key = self._run_key(group_id, actor_id, run_id)
        execution = self._manual_executions.get(key)
        if (
            not isinstance(execution, _ManualExecution)
            or execution.authorities is not self.run_authorities
            or self.run_authorities is None
            or self.run_authorities.run_anchor_for_execution_claim(execution.claim)
            != run.get("run_authority")
        ):
            raise PermissionError("manual actor run execution is unavailable")
        if str(run.get("status") or "") != "awaiting_verification":
            raise ValueError("run is not awaiting verification")
        failure_markers = [
            item
            for item in (run.get("events") if isinstance(run.get("events"), list) else [])
            if isinstance(item, dict)
            and (
                str(item.get("status") or "") in {"failed", "recovering"}
                or self._contains_failure_marker(item.get("result"))
                or self._contains_failure_marker(item.get("error"))
            )
        ]
        if passed and (
            failure_markers
            or run.get("error")
            or not bool((run.get("metrics") or {}).get("replay_success"))
        ):
            raise ValueError("该运行包含失败步骤，不能验证为成功")
        assert isinstance(operation_claim, RunOperationClaim)
        if passed and not fingerprint:
            authorization = run.get("authorization") if isinstance(run.get("authorization"), dict) else {}
            if authorization.get("allow_trust") is not False:
                raise ValueError("Windows-MCP fingerprint is unavailable")

        def finalize(require_current: Any) -> str:
            run["verification"] = {
                "passed": bool(passed),
                "summary": str(summary or "")[:2000],
                "evidence_ids": [str(item)[:200] for item in evidence_ids[:100]],
                "verified_by": actor_id,
                "verified_at": time.time(),
            }
            if not passed:
                run["status"] = "failed"
                return "failed"
            authorization = (
                run.get("authorization")
                if isinstance(run.get("authorization"), dict)
                else {}
            )
            finalized: Dict[str, Any] = {}
            try:
                if authorization.get("allow_publish") is not False:
                    require_current()
                    finalized["published"] = self.store.publish(
                        group_id,
                        str(run["workflow_id"]),
                        int(run["version"]),
                    )
                if authorization.get("allow_trust") is not False:
                    require_current()
                    finalized["trusted"] = self.store.trust(
                        group_id,
                        str(run["workflow_id"]),
                        int(run["version"]),
                        fingerprint=fingerprint,
                        permissions=["all_windows_mcp_tools"],
                    )
                run["status"] = "completed"
                target = "completed"
            except Exception as exc:
                run.update(
                    {
                        "status": "failed",
                        "error": {
                            "code": "finalization_failed",
                            "layer": "runtime",
                            "message": str(exc)[:2000],
                            "retryable": True,
                        },
                        "finished_at": time.time(),
                    }
                )
                target = "failed"
            run["finalization"] = {
                "published": "published" in finalized,
                "trusted": "trusted" in finalized,
                "unattended_triggers_authorized": authorization.get("allow_unattended_triggers") is not False,
            }
            return target

        execution.authorities.finish_execution_with_callback(
            execution.claim,
            finalize,
            operation_claim=operation_claim,
        )
        run["updated_at"] = time.time()
        self._write_unchecked(group_id, run)
        self._remove_current(self._manual_executions, key, execution)
        return run
