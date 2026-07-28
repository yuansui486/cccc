from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import site
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..util.fs import atomic_write_text
from .lease import ComputerControlLease
from .storage import WorkflowStore

MCP_PROTOCOL_VERSION = "2024-11-05"
WINDOWS_MCP_PACKAGE = "windows-mcp"
WINDOWS_MCP_STDIO_LIMIT_BYTES = 64 * 1024 * 1024


class MCPUnavailable(RuntimeError):
    code = "windows_mcp_transport_failed"


class MCPResponseTooLarge(MCPUnavailable):
    code = "windows_mcp_response_too_large"


class MCPOutcomeUnknown(MCPUnavailable):
    """A mutating request lost its transport after it may have reached Windows."""

    code = "outcome_unknown"


class MCPToolExecutionError(RuntimeError):
    """Windows-MCP completed the RPC request, but the tool itself failed."""

    code = "windows_mcp_tool_failed"

    def __init__(self, message: str, *, tool: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.tool = tool
        self.status_code = status_code


class ComputerControlArgumentError(ValueError):
    """A user-facing argument error with enough context for recovery UIs."""

    code = "invalid_argument_shape"
    layer = "validation"
    retryable = False

    def __init__(self, message: str, *, field_errors: Optional[Dict[str, str]] = None, next_action: str = "修正参数后重试"):
        super().__init__(message)
        self.field_errors = field_errors or {}
        self.next_action = next_action


_STATUS_CODE_RE = re.compile(r"(?im)^\s*Status\s+Code\s*:\s*(-?\d+)\s*$")


def _tool_result_text(value: Any) -> str:
    parts: List[str] = []

    def visit(child: Any) -> None:
        if isinstance(child, dict):
            text = child.get("text")
            if isinstance(text, str):
                parts.append(text)
            for key, item in child.items():
                if key != "text":
                    visit(item)
        elif isinstance(child, list):
            for item in child:
                visit(item)

    visit(value)
    return "\n".join(parts).strip()


def normalize_tool_result(tool: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """Return a successful MCP result or raise a structured execution error."""
    if not isinstance(result, dict):
        raise MCPToolExecutionError("Windows-MCP 返回了无法识别的结果", tool=tool)
    text = _tool_result_text(result)
    if result.get("isError") is True or result.get("is_error") is True:
        raise MCPToolExecutionError(text or "Windows-MCP 工具执行失败", tool=tool)
    if str(tool or "").lower() in {"powershell", "shell", "command", "runcommand"}:
        matches = _STATUS_CODE_RE.findall(text)
        if matches:
            status_code = int(matches[-1])
            if status_code != 0:
                detail = text[-2000:] if text else f"退出状态码 {status_code}"
                raise MCPToolExecutionError(
                    f"{tool} 执行失败（退出状态码 {status_code}）：{detail}",
                    tool=tool,
                    status_code=status_code,
                )
    return result


def validate_arguments_against_schema(tool: str, arguments: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Validate the useful JSON Schema subset exposed by Windows-MCP."""
    errors: List[str] = []

    # Windows-MCP uses a numeric UI-tree label or a two-number coordinate
    # array. A common AI mistake is putting an element name in ``loc`` or a
    # string name in ``label``; fail before the RPC reaches the desktop.
    if isinstance(arguments, dict):
        if "loc" in arguments:
            loc = arguments.get("loc")
            if isinstance(loc, str):
                try:
                    decoded_loc = json.loads(loc)
                except (TypeError, ValueError):
                    decoded_loc = None
                if isinstance(decoded_loc, list):
                    loc = decoded_loc
                    arguments = {**arguments, "loc": decoded_loc}
            valid_loc = isinstance(loc, (list, tuple)) and len(loc) == 2 and all(
                isinstance(item, (int, float)) and not isinstance(item, bool) for item in loc
            )
            if not valid_loc:
                errors.append("参数.loc 必须是 [x, y] 数字坐标，不能填写元素名称")
        if "label" in arguments:
            label = arguments.get("label")
            if not (isinstance(label, int) and not isinstance(label, bool)):
                errors.append("参数.label 必须是 Snapshot 返回的整数元素编号，不能填写元素名称")

    def matches_type(value: Any, expected: str) -> bool:
        return {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(expected, True)

    def visit(value: Any, node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        alternatives = node.get("oneOf") or node.get("anyOf")
        if isinstance(alternatives, list) and alternatives:
            for alternative in alternatives:
                local: List[str] = []
                before = len(errors)
                visit(value, alternative, path)
                if len(errors) == before:
                    return
                local.extend(errors[before:])
                del errors[before:]
            errors.append(f"{path} 不符合允许的参数格式")
            return
        expected = node.get("type")
        expected_types = [expected] if isinstance(expected, str) else expected if isinstance(expected, list) else []
        if expected_types and not any(matches_type(value, item) for item in expected_types if isinstance(item, str)):
            errors.append(f"{path} 类型不正确，应为 {'/'.join(str(item) for item in expected_types)}")
            return
        enum = node.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path} 必须是允许值之一")
        if isinstance(value, dict):
            required = node.get("required") if isinstance(node.get("required"), list) else []
            for key in required:
                if key not in value:
                    errors.append(f"{path}.{key} 为必填参数")
            properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
            if node.get("additionalProperties") is False:
                for key in value:
                    if key not in properties:
                        errors.append(f"{path}.{key} 不是该工具支持的参数")
            for key, child in value.items():
                if key in properties:
                    visit(child, properties[key], f"{path}.{key}")
        elif isinstance(value, list) and isinstance(node.get("items"), dict):
            for index, child in enumerate(value):
                visit(child, node["items"], f"{path}[{index}]")

    visit(arguments, schema, "参数")
    if errors:
        raise ComputerControlArgumentError(
            f"工具“{tool}”参数无效：" + "；".join(errors[:10]),
            field_errors={f"arguments.{key}": value for key, value in (("loc", "参数.loc 必须是 [x, y] 数字坐标，不能填写元素名称"), ("label", "参数.label 必须是 Snapshot 返回的整数元素编号，不能填写元素名称")) if key in arguments and any(key in error for error in errors)},
            next_action="重新观察桌面并选择元素；只有明确开启位置兜底时才允许坐标",
        )


def validate_workflow_tools(definition: Any, catalog: Sequence[Dict[str, Any]]) -> None:
    tools = {str(item.get("name") or ""): item for item in catalog if isinstance(item, dict)}
    errors: List[str] = []
    for node in getattr(definition, "nodes", []):
        if getattr(node, "type", "") != "action":
            continue
        tool = str(getattr(node, "tool", "") or "")
        item = tools.get(tool)
        title = str(getattr(node, "title", "") or getattr(node, "id", "") or tool)
        if item is None:
            errors.append(f"步骤“{title}”使用了不存在的工具“{tool}”")
            continue
        arguments = dict(getattr(node, "arguments", {}) or {})
        target = getattr(node, "target", None)
        if tool.lower() in {"click", "type"}:
            has_semantic_target = any(key in arguments for key in ("label", "element_id"))
            has_coordinate_target = any(key in arguments for key in ("loc", "x", "y"))
            if not has_semantic_target and has_coordinate_target and target is None:
                # Coordinates are retained only for explicitly opted-in legacy
                # workflows. New recordings always persist a locator instead.
                if arguments.get("coordinate_fallback") is not True:
                    errors.append(f"步骤“{title}”仅使用绝对坐标；请先选择稳定元素，或显式开启受控坐标兜底")
                    continue
            elif not has_semantic_target and not has_coordinate_target and target is None:
                errors.append(f"步骤“{title}”缺少操作目标，请设置稳定元素或受控坐标兜底")
                continue
            elif target is not None and has_coordinate_target and getattr(target, "fallback_policy", "never") != "controlled":
                errors.append(f"步骤“{title}”同时保存了元素和坐标；请删除坐标或明确开启一次性位置兜底")
                continue
        schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
        try:
            schema_arguments = {key: value for key, value in arguments.items() if key != "coordinate_fallback"}
            validate_arguments_against_schema(tool, schema_arguments, schema)
        except ValueError as exc:
            message = str(exc)
            # A locator supplies the required label/loc only after the live
            # Snapshot is resolved at runtime. Preserve validation for every
            # other argument/schema error.
            target_only_requirement = target is not None and not any(key in schema_arguments for key in ("loc", "label")) and any(token in message for token in ("loc 为必填参数", "label 为必填参数", "loc is required", "label is required"))
            if not target_only_requirement:
                errors.append(f"步骤“{title}”：{exc}")
    if errors:
        raise ValueError("工作流校验失败：" + "；".join(errors[:20]))


def _redact(value: str) -> str:
    value = re.sub(r"(?i)(token|secret|password|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2<redacted>", value)
    value = re.sub(r"(?i)(authorization:\s*)(?:bearer\s+)?\S+", r"\1<redacted>", value)
    return value.strip()


def _creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    import subprocess

    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


class WindowsMCPSession:
    """A supervised, serialized JSON-RPC session over Windows-MCP stdio."""

    def __init__(self, executable: Optional[Path] = None):
        self.executable = executable
        self.version = ""
        self._process: Optional[asyncio.subprocess.Process] = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_owner_loop, name="onecolleague-windows-mcp", daemon=True)
        self._request_id = 0
        self._lock: Optional[asyncio.Lock] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._logs: deque[str] = deque(maxlen=200)
        self._tools: List[Dict[str, Any]] = []
        self.started_at: Optional[float] = None
        self.transport_restarts = 0
        self._thread.start()

    def _run_owner_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._lock = asyncio.Lock()
        self._loop.run_forever()

    async def _on_owner(self, coroutine: Any) -> Any:
        if asyncio.get_running_loop() is self._loop:
            return await coroutine
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return await asyncio.wrap_future(future)

    def _on_owner_sync(self, coroutine: Any, *, timeout: Optional[float]) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=None if timeout is None else max(0.0, float(timeout)))
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("Windows-MCP owner loop request timed out") from None

    def run_sync(self, coroutine: Any, *, timeout: float = 120) -> Any:
        return self._on_owner_sync(coroutine, timeout=timeout)

    def configure(self, executable: Path, version: str) -> None:
        self.executable = executable
        self.version = version

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def logs(self) -> List[str]:
        return list(self._logs)

    async def start(self) -> List[Dict[str, Any]]:
        return await self._on_owner(self._start_owned())

    async def _start_owned(self) -> List[Dict[str, Any]]:
        assert self._lock is not None
        async with self._lock:
            if self.running and self._tools:
                return list(self._tools)
            last_error: Optional[BaseException] = None
            for attempt in range(2):
                await self._stop_unlocked()
                try:
                    return await self._start_unlocked()
                except asyncio.CancelledError:
                    await asyncio.shield(self._stop_unlocked())
                    raise
                except Exception as exc:
                    last_error = exc
                    self._logs.append(f"Windows-MCP startup attempt {attempt + 1} failed: {_redact(str(exc))}")
                    await self._stop_unlocked()
            detail = self._failure_detail("Windows-MCP could not complete the MCP handshake")
            raise MCPUnavailable(detail) from last_error

    async def _start_unlocked(self) -> List[Dict[str, Any]]:
        if os.name != "nt":
            raise MCPUnavailable("Windows-MCP is only supported on Windows")
        executable = self.executable
        if executable is None or not executable.is_file():
            raise MCPUnavailable("Windows-MCP is not installed in the OneColleague tool environment")
        env = dict(os.environ)
        env.update(
            {
                "ANONYMIZED_TELEMETRY": "false",
                "WINDOWS_MCP_DISABLE_FLASH": "1",
                "PYTHONUTF8": "1",
            }
        )
        self._process = await asyncio.create_subprocess_exec(
            str(executable),
            "serve",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=WINDOWS_MCP_STDIO_LIMIT_BYTES,
            env=env,
            creationflags=_creation_flags(),
        )
        self.started_at = time.time()
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self._request_unlocked(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "onecolleague", "version": "1"},
            },
            timeout=120,
        )
        await self._notify_unlocked("notifications/initialized", {})
        result = await self._request_unlocked("tools/list", {}, timeout=60)
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise MCPUnavailable("Windows-MCP returned an invalid tool catalog")
        self._tools = [item for item in tools if isinstance(item, dict)]
        return list(self._tools)

    async def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            message = _redact(line.decode("utf-8", errors="replace"))
            if message:
                self._logs.append(message[:2000])

    def _failure_detail(self, message: str) -> str:
        process = self._process
        exit_code = process.returncode if process is not None else None
        tail = " | ".join(self.logs[-5:])
        parts = [message]
        if exit_code is not None:
            parts.append(f"exit code {exit_code}")
        if tail:
            parts.append(tail)
        return ": ".join(parts)

    async def _notify_unlocked(self, method: str, params: Dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPUnavailable("Windows-MCP is not running")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()

    async def _request_unlocked(self, method: str, params: Dict[str, Any], *, timeout: Optional[float]) -> Dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None or process.returncode is not None:
            raise MCPUnavailable(self._failure_detail("Windows-MCP is not running"))
        self._request_id += 1
        request_id = self._request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()
        deadline = None if timeout is None or float(timeout) <= 0 else asyncio.get_running_loop().time() + float(timeout)
        while True:
            remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError(f"Windows-MCP request timed out: {method}")
            try:
                read = process.stdout.readline()
                line = await read if remaining is None else await asyncio.wait_for(read, timeout=remaining)
            except ValueError as exc:
                if "chunk exceed the limit" in str(exc).lower() or "separator is not found" in str(exc).lower():
                    raise MCPResponseTooLarge(
                        f"Windows-MCP response exceeded the {WINDOWS_MCP_STDIO_LIMIT_BYTES // (1024 * 1024)} MiB stdio limit"
                    ) from exc
                raise
            if not line:
                try:
                    await asyncio.wait_for(process.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
                raise MCPUnavailable(self._failure_detail(f"Windows-MCP exited while handling {method}"))
            try:
                response = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if not isinstance(response, dict) or response.get("id") != request_id:
                continue
            if isinstance(response.get("error"), dict):
                error = response["error"]
                raise RuntimeError(str(error.get("message") or "Windows-MCP call failed"))
            result = response.get("result")
            return result if isinstance(result, dict) else {"value": result}

    async def request(self, method: str, params: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        return await self._on_owner(self._request_owned(method, params, timeout=timeout))

    async def _request_owned(self, method: str, params: Dict[str, Any], *, timeout: Optional[float]) -> Dict[str, Any]:
        if not self.running:
            await self._start_owned()
        assert self._lock is not None
        async with self._lock:
            return await self._request_unlocked(method, params, timeout=timeout)

    async def call_tool(self, name: str, arguments: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        return await self._on_owner(self._call_tool_owned(name, arguments, timeout=timeout))

    async def _call_tool_owned(self, name: str, arguments: Dict[str, Any], *, timeout: Optional[float]) -> Dict[str, Any]:
        try:
            return await self._request_owned("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        except Exception as exc:
            if not self._is_transport_error(exc):
                raise
            from .risk import classify_tool

            # A mutating call may have reached Windows before the stdio process
            # exited. Replaying it could double-click, type twice, or delete twice.
            await self._stop_owned()
            try:
                await self._start_owned()
                self.transport_restarts += 1
            except Exception:
                raise MCPUnavailable(f"Windows-MCP 连接中断且自动恢复失败：{exc}") from exc
            risk = classify_tool(name, arguments)
            # Only the canonical Snapshot observation is replay-safe.  Other
            # tools classified as low risk may still have adapter-specific
            # effects, so they are not silently invoked a second time.
            if str(name or "").strip().casefold() == "snapshot":
                try:
                    return await self._request_owned("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
                except Exception as retry_exc:
                    if self._is_transport_error(retry_exc):
                        await self._stop_owned()
                    raise
            if risk == "low":
                raise MCPUnavailable("Windows-MCP 连接已恢复；只读调用未自动重放，请重新观察后继续。") from exc
            raise MCPOutcomeUnknown("Windows-MCP 连接中断；操作结果未知。请先观察桌面状态，不要直接重试。") from exc

    @staticmethod
    def _is_transport_error(exc: BaseException) -> bool:
        if isinstance(exc, (MCPUnavailable, BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return True
        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "nonetype' object has no attribute 'send",
                "event loop is closed",
                "pipe is being closed",
                "broken pipe",
                "connection reset",
                "connection lost",
                "chunk exceed the limit",
                "separator is not found",
            )
        )

    def call_tool_sync(self, name: str, arguments: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        owner_timeout = None if timeout is None or float(timeout) <= 0 else float(timeout) + 10
        return self._on_owner_sync(self._call_tool_owned(name, arguments, timeout=timeout), timeout=owner_timeout)

    async def catalog(self) -> List[Dict[str, Any]]:
        if not self.running or not self._tools:
            return await self.start()
        return list(self._tools)

    def catalog_sync(self, *, timeout: float = 120) -> List[Dict[str, Any]]:
        return self._on_owner_sync(self._catalog_owned(), timeout=timeout)

    async def _catalog_owned(self) -> List[Dict[str, Any]]:
        if not self.running or not self._tools:
            return await self._start_owned()
        return list(self._tools)

    async def restart(self) -> Dict[str, Any]:
        """Restart only the supervised stdio session and repeat the handshake.

        This method never installs, repairs, upgrades, or otherwise writes to
        the Windows-MCP tool environment.
        """
        return await self._on_owner(self._restart_owned())

    def restart_sync(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self._on_owner_sync(self._restart_owned(), timeout=timeout)

    async def _restart_owned(self) -> Dict[str, Any]:
        assert self._lock is not None
        async with self._lock:
            previous_started_at = self.started_at
            await self._stop_unlocked()
            try:
                tools = await self._start_unlocked()
            except BaseException:
                await asyncio.shield(self._stop_unlocked())
                raise
            self.transport_restarts += 1
            return {
                "previous_started_at": previous_started_at,
                "started_at": self.started_at,
                "transport_restarts": int(self.transport_restarts),
                "session_running": bool(self.running),
                "tool_count": len(tools),
                "version": self.version,
            }

    async def _stop_unlocked(self) -> None:
        process = self._process
        self._process = None
        self._tools = []
        self.started_at = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        task = self._stderr_task
        self._stderr_task = None
        if task is not None and not task.done():
            task.cancel()

    async def stop(self) -> None:
        await self._on_owner(self._stop_owned())

    def stop_sync(self, *, timeout: float = 10) -> None:
        self._on_owner_sync(self._stop_owned(), timeout=timeout)

    async def _stop_owned(self) -> None:
        assert self._lock is not None
        async with self._lock:
            await self._stop_unlocked()


class WindowsMCPSetup:
    def __init__(
        self,
        home: Path,
        session: WindowsMCPSession,
        store: WorkflowStore,
        lease: Optional[ComputerControlLease] = None,
    ):
        self.home = home
        self.session = session
        self.store = store
        self.lease = lease or ComputerControlLease(home)
        self.state_root = home / "state" / "computer-control"
        self.path = self.state_root / "setup.json"
        self.uv_root = self.state_root / "uv-tools"
        self.tool_dir = self.uv_root / "tools"
        self.bin_dir = self.uv_root / "bin"
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[None]] = None
        self._setup_logs: deque[str] = deque(maxlen=200)
        self._status: Dict[str, Any] = self._load_status()
        executable = self._windows_mcp_executable()
        if executable.is_file():
            self.session.configure(executable, str(self._status.get("version") or self._installed_version()))

    def _load_status(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _set(self, phase: str, **extra: Any) -> None:
        self._status = {
            **self._status,
            "phase": phase,
            "updated_at": time.time(),
            **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self._status, ensure_ascii=False, indent=2) + "\n")

    def status(self) -> Dict[str, Any]:
        result = dict(self._status or {"phase": "not_started", "version": ""})
        result["session_running"] = self.session.running
        result["session_started_at"] = getattr(self.session, "started_at", None)
        result["transport_restarts"] = int(getattr(self.session, "transport_restarts", 0))
        result["in_progress"] = bool(self._task and not self._task.done())
        result["logs"] = (list(self._setup_logs) + self.session.logs)[-50:]
        return result

    async def ensure(self, *, force: bool = False, upgrade: bool = False) -> Dict[str, Any]:
        if self._task and not self._task.done():
            return self.status()
        if self.session.running and self._status.get("phase") == "ready" and not force and not upgrade:
            return self.status()
        self._task = asyncio.create_task(self._run(force=force, upgrade=upgrade))
        await asyncio.sleep(0)
        return self.status()

    async def wait(self) -> Dict[str, Any]:
        task = self._task
        if task is not None:
            await task
        return self.status()

    async def _run(self, *, force: bool, upgrade: bool) -> None:
        async with self._lock:
            try:
                setup_id = "setup_" + uuid.uuid4().hex[:16]
                with self.lease.hold(
                    group_id="_global",
                    actor_id="setup",
                    run_id=setup_id,
                    observe_only=False,
                ) as lease_guard:
                    self._set("checking", error=None, failure=None)
                    if os.name != "nt":
                        raise MCPUnavailable("Computer control requires Windows")
                    uv = await self._ensure_uv()
                    env = self._uv_environment()
                    executable = self._windows_mcp_executable()
                    if (force or upgrade) and self.session.running:
                        await self.session.stop()
                    if upgrade and executable.is_file():
                        self._set("downloading", detail="upgrading_windows_mcp")
                        await self._run_command([str(uv), "tool", "upgrade", WINDOWS_MCP_PACKAGE], env=env, timeout=900)
                    elif force or not executable.is_file():
                        self._set("downloading", detail="installing_windows_mcp")
                        command = [str(uv), "tool", "install", "--python", "3.13"]
                        if force:
                            command.append("--force")
                        command.append(WINDOWS_MCP_PACKAGE)
                        await self._run_command(command, env=env, timeout=900)
                    if lease_guard["lost"].is_set():
                        raise PermissionError("computer_control_lease_required")
                    if not executable.is_file():
                        raise MCPUnavailable(f"Windows-MCP installation finished but {executable.name} was not created")
                    version = self._installed_version()
                    self.session.configure(executable, version)
                    await self.session.stop()
                    self._set("initializing", version=version, detail="starting_mcp_session")
                    tools = await self.session.start()
                    self.refresh_catalog(tools, version=version)
            except Exception as exc:
                process = self.session._process
                failure = {
                    "phase": self._status.get("phase"),
                    "exit_code": process.returncode if process is not None else None,
                    "stderr_tail": self.session.logs[-10:],
                }
                self._set(
                    "failed",
                    error={"code": "windows_mcp_setup_failed", "message": _redact(str(exc))},
                    failure=failure,
                )

    async def repair(self) -> Dict[str, Any]:
        return await self.ensure(force=True)

    async def upgrade(self) -> Dict[str, Any]:
        return await self.ensure(upgrade=True)

    def refresh_catalog(self, tools: Sequence[Dict[str, Any]], *, version: Optional[str] = None) -> Dict[str, Any]:
        current_version = str(version or self.session.version or self._status.get("version") or "")
        self._set("verifying", version=current_version, tool_count=len(tools), detail="checking_tool_catalog")
        fingerprint = self.store.fingerprint(current_version, list(tools))
        revoked = self.store.revoke_stale_trust(fingerprint)
        self._set(
            "ready",
            version=current_version,
            fingerprint=fingerprint,
            tool_count=len(tools),
            revoked_trust_count=revoked,
            detail=None,
            error=None,
            failure=None,
        )
        return self.status()

    def _uv_environment(self) -> Dict[str, str]:
        self.tool_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "UV_TOOL_DIR": str(self.tool_dir),
                "UV_TOOL_BIN_DIR": str(self.bin_dir),
                "UV_NO_PROGRESS": "1",
                "PYTHONUTF8": "1",
            }
        )
        return env

    def _windows_mcp_executable(self) -> Path:
        suffix = ".exe" if os.name == "nt" else ""
        return self.bin_dir / f"windows-mcp{suffix}"

    async def _ensure_uv(self) -> Path:
        existing = self._find_uv()
        if existing is not None:
            self._set("checking", detail="uv_ready", uv_path=str(existing))
            return existing
        self._set("downloading", detail="installing_uv_with_pip")
        errors: List[str] = []
        for command in self._python_commands():
            for location_args in (["--user"], []):
                try:
                    await self._run_command([*command, "-m", "pip", "install", *location_args, "uv"], timeout=600)
                except Exception as exc:
                    errors.append(_redact(str(exc)))
                    continue
                installed = self._find_uv()
                if installed is not None:
                    self._set("checking", detail="uv_installed", uv_path=str(installed))
                    return installed
        detail = " | ".join(errors[-3:])
        raise MCPUnavailable(f"Unable to install uv with pip{': ' + detail if detail else ''}")

    @staticmethod
    def _python_commands() -> List[List[str]]:
        commands: List[List[str]] = []
        if sys.executable:
            commands.append([sys.executable])
        if shutil.which("py"):
            commands.append([str(shutil.which("py")), "-3"])
        if shutil.which("python") and str(shutil.which("python")) != sys.executable:
            commands.append([str(shutil.which("python"))])
        return commands

    def _find_uv(self) -> Optional[Path]:
        names = ["uv.exe", "uv"] if os.name == "nt" else ["uv"]
        found = shutil.which("uv")
        if found:
            return Path(found)
        roots = [Path(sys.executable).parent, Path(site.USER_BASE) / ("Scripts" if os.name == "nt" else "bin")]
        for command in self._python_commands():
            executable = Path(command[0])
            roots.extend([executable.parent, executable.parent / "Scripts"])
        seen = set()
        for root in roots:
            for name in names:
                candidate = root / name
                key = str(candidate).lower()
                if key not in seen and candidate.is_file():
                    return candidate
                seen.add(key)
        return None

    def _installed_version(self) -> str:
        patterns = [
            "windows_mcp-*.dist-info/METADATA",
            "Lib/site-packages/windows_mcp-*.dist-info/METADATA",
            "lib/python*/site-packages/windows_mcp-*.dist-info/METADATA",
        ]
        package_root = self.tool_dir / WINDOWS_MCP_PACKAGE
        for pattern in patterns:
            for metadata in package_root.glob(pattern):
                try:
                    for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
                        if line.startswith("Version:"):
                            return line.partition(":")[2].strip()
                except OSError:
                    continue
        return str(self._status.get("version") or "latest")

    async def _run_command(
        self,
        command: Sequence[str],
        *,
        env: Optional[Dict[str, str]] = None,
        timeout: float,
    ) -> str:
        display = " ".join(command)
        self._setup_logs.append(_redact(f"> {display}"))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=_creation_flags(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise MCPUnavailable(f"Command timed out: {Path(command[0]).name}")
        output = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
        lines = [_redact(line) for line in output.splitlines() if line.strip()]
        self._setup_logs.extend(line[:2000] for line in lines[-30:])
        if process.returncode != 0:
            tail = " | ".join(lines[-8:])
            raise MCPUnavailable(f"{Path(command[0]).name} exited with code {process.returncode}{': ' + tail if tail else ''}")
        return output
