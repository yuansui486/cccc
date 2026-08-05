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
from urllib.parse import urlsplit, urlunsplit

from ..util.fs import atomic_write_text
from .lease import ComputerControlLease
from .storage import WorkflowStore

MCP_PROTOCOL_VERSION = "2024-11-05"
WINDOWS_MCP_PACKAGE = "windows-mcp"
WINDOWS_MCP_STDIO_LIMIT_BYTES = 64 * 1024 * 1024
DEFAULT_PYPI_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
OFFICIAL_PYPI_INDEX_URL = "https://pypi.org/simple"
_SETUP_TRANSIENT_PHASES = {"checking", "downloading", "initializing", "verifying"}


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
    value = re.sub(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@", r"\1<redacted>:<redacted>@", value)
    value = re.sub(r"(?i)([?&](?:token|secret|password|api[_-]?key)=)[^&\s]+", r"\1<redacted>", value)
    return value.strip()


def _safe_index_url(value: Any, *, default_path: str = "/simple") -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return ""
    try:
        parsed.port
    except ValueError:
        return ""
    return urlunsplit(("https", parsed.netloc, parsed.path or default_path, "", "")).rstrip("/")


def _coerce_process_id(value: Any) -> Optional[int]:
    try:
        process_id = int(value)
    except (TypeError, ValueError):
        return None
    return process_id if process_id > 0 else None


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
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._owner_ready: Optional[threading.Event] = None
        self._owner_state_lock = threading.RLock()
        self._request_id = 0
        self._lock: Optional[asyncio.Lock] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._logs: deque[str] = deque(maxlen=200)
        self._tools: List[Dict[str, Any]] = []
        self.started_at: Optional[float] = None
        self.transport_restarts = 0
        self._daemon_bound = False
        self._daemon_stopping = False
        self._daemon_owner: Optional[tuple[int, str, int]] = None
        self._daemon_generation_claim: Any = None

    def _run_owner_loop(
        self,
        loop: asyncio.AbstractEventLoop,
        ready: threading.Event,
    ) -> None:
        asyncio.set_event_loop(loop)
        self._lock = asyncio.Lock()
        ready.set()
        loop.run_forever()

    def _require_daemon_admission(self) -> None:
        with self._owner_state_lock:
            if not self._daemon_bound:
                return
            claim = self._daemon_generation_claim
            stopping = self._daemon_stopping
        if stopping or claim is None or not claim.admitted():
            raise MCPUnavailable("Computer-control daemon service is stopping")

    def _ensure_owner_loop(
        self,
        *,
        allow_daemon_stopping: bool = False,
    ) -> asyncio.AbstractEventLoop:
        if not allow_daemon_stopping:
            self._require_daemon_admission()
        with self._owner_state_lock:
            thread = self._thread
            loop = self._loop
            if thread is not None and thread.is_alive() and loop is not None:
                return loop
            if self._daemon_bound and self._daemon_stopping:
                raise MCPUnavailable("Computer-control daemon service is stopping")
            if loop is not None and not loop.is_closed():
                loop.close()
            loop = asyncio.new_event_loop()
            ready = threading.Event()
            thread = threading.Thread(
                target=self._run_owner_loop,
                args=(loop, ready),
                name="onecolleague-windows-mcp",
                daemon=True,
            )
            self._loop = loop
            self._thread = thread
            self._owner_ready = ready
            thread.start()
        if not ready.wait(5) or not thread.is_alive():
            raise MCPUnavailable("Windows-MCP owner loop failed to start")
        return loop

    @staticmethod
    def _close_rejected_coroutine(coroutine: Any) -> None:
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()

    async def _on_owner(
        self,
        coroutine: Any,
        *,
        allow_daemon_stopping: bool = False,
    ) -> Any:
        try:
            if not allow_daemon_stopping:
                self._require_daemon_admission()
            running_loop = asyncio.get_running_loop()
            if running_loop is self._loop:
                return await coroutine
            loop = self._ensure_owner_loop(
                allow_daemon_stopping=allow_daemon_stopping,
            )
        except BaseException:
            self._close_rejected_coroutine(coroutine)
            raise
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return await asyncio.wrap_future(future)

    def _on_owner_sync(
        self,
        coroutine: Any,
        *,
        timeout: Optional[float],
        allow_daemon_stopping: bool = False,
    ) -> Any:
        try:
            loop = self._ensure_owner_loop(
                allow_daemon_stopping=allow_daemon_stopping,
            )
        except BaseException:
            self._close_rejected_coroutine(coroutine)
            raise
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=None if timeout is None else max(0.0, float(timeout)))
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("Windows-MCP owner loop request timed out") from None

    def run_sync(self, coroutine: Any, *, timeout: float = 120) -> Any:
        return self._on_owner_sync(coroutine, timeout=timeout)

    def _run_sync_for_daemon_shutdown(
        self,
        coroutine: Any,
        *,
        timeout: float,
    ) -> Any:
        return self._on_owner_sync(
            coroutine,
            timeout=timeout,
            allow_daemon_stopping=True,
        )

    def _start_daemon(self, owner: Any, *, home: Path) -> None:
        from .services import _claim_daemon_generation, _release_daemon_generation

        generation_claim = _claim_daemon_generation(
            owner,
            home=home,
            subject="windows-mcp-session",
        )
        owner_key = generation_claim.owner_key
        with self._owner_state_lock:
            if self._daemon_generation_claim is not None:
                if not self._daemon_stopping and self._daemon_owner == owner_key:
                    _release_daemon_generation(generation_claim)
                    return
                _release_daemon_generation(generation_claim)
                raise RuntimeError("Windows-MCP session from the prior daemon is still active")
            if not self._daemon_bound and self._thread is not None and self._thread.is_alive():
                _release_daemon_generation(generation_claim)
                raise RuntimeError("Windows-MCP unbound owner loop is already active")
            self._daemon_bound = True
            self._daemon_stopping = False
            self._daemon_owner = owner_key
            self._daemon_generation_claim = generation_claim

    def _request_daemon_stop(self) -> None:
        with self._owner_state_lock:
            if self._daemon_bound:
                self._daemon_stopping = True

    async def _daemon_owner_drained(self) -> bool:
        current = asyncio.current_task()
        pending = [
            task
            for task in asyncio.all_tasks()
            if task is not current and not task.done()
        ]
        return bool(
            self._process is None
            and (self._stderr_task is None or self._stderr_task.done())
            and not pending
        )

    def _drain_daemon_sync(self, *, timeout: float) -> bool:
        from .services import _release_daemon_generation

        self._request_daemon_stop()
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        with self._owner_state_lock:
            thread = self._thread
            loop = self._loop
        if thread is not None and thread.is_alive():
            try:
                self._run_sync_for_daemon_shutdown(
                    self._stop_owned(),
                    timeout=max(0.0, deadline - time.monotonic()),
                )
                drained = self._run_sync_for_daemon_shutdown(
                    self._daemon_owner_drained(),
                    timeout=max(0.0, deadline - time.monotonic()),
                )
            except BaseException:
                return False
            if not drained:
                return False
            if loop is None or thread is threading.current_thread():
                return False
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                return False
        if self._process is not None or (
            self._stderr_task is not None and not self._stderr_task.done()
        ):
            return False
        with self._owner_state_lock:
            if self._thread is thread:
                self._thread = None
                self._loop = None
                self._lock = None
                self._owner_ready = None
            generation_claim = self._daemon_generation_claim
            self._daemon_generation_claim = None
            self._daemon_owner = None
        if loop is not None and not loop.is_closed():
            loop.close()
        _release_daemon_generation(generation_claim)
        return True

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
        self._require_daemon_admission()
        return await self._on_owner(self._start_owned())

    async def _start_owned(self) -> List[Dict[str, Any]]:
        self._require_daemon_admission()
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
        self._require_daemon_admission()
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
        self._require_daemon_admission()
        return await self._on_owner(self._request_owned(method, params, timeout=timeout))

    async def _request_owned(self, method: str, params: Dict[str, Any], *, timeout: Optional[float]) -> Dict[str, Any]:
        self._require_daemon_admission()
        if not self.running:
            await self._start_owned()
        assert self._lock is not None
        async with self._lock:
            return await self._request_unlocked(method, params, timeout=timeout)

    async def call_tool(self, name: str, arguments: Dict[str, Any], *, timeout: Optional[float] = None) -> Dict[str, Any]:
        self._require_daemon_admission()
        return await self._on_owner(self._call_tool_owned(name, arguments, timeout=timeout))

    async def _call_tool_owned(self, name: str, arguments: Dict[str, Any], *, timeout: Optional[float]) -> Dict[str, Any]:
        self._require_daemon_admission()
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
        self._require_daemon_admission()
        owner_timeout = None if timeout is None or float(timeout) <= 0 else float(timeout) + 10
        return self._on_owner_sync(self._call_tool_owned(name, arguments, timeout=timeout), timeout=owner_timeout)

    async def catalog(self) -> List[Dict[str, Any]]:
        self._require_daemon_admission()
        if not self.running or not self._tools:
            return await self.start()
        return list(self._tools)

    def catalog_sync(self, *, timeout: float = 120) -> List[Dict[str, Any]]:
        self._require_daemon_admission()
        return self._on_owner_sync(self._catalog_owned(), timeout=timeout)

    async def _catalog_owned(self) -> List[Dict[str, Any]]:
        self._require_daemon_admission()
        if not self.running or not self._tools:
            return await self._start_owned()
        return list(self._tools)

    async def restart(self) -> Dict[str, Any]:
        """Restart only the supervised stdio session and repeat the handshake.

        This method never installs, repairs, upgrades, or otherwise writes to
        the Windows-MCP tool environment.
        """
        self._require_daemon_admission()
        return await self._on_owner(self._restart_owned())

    def restart_sync(self, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        self._require_daemon_admission()
        return self._on_owner_sync(self._restart_owned(), timeout=timeout)

    async def _restart_owned(self) -> Dict[str, Any]:
        self._require_daemon_admission()
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
        self._tools = []
        self.started_at = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if process is None or process.returncode is not None:
            self._process = None
        task = self._stderr_task
        if task is not None and not task.done():
            task.cancel()
            if task is not asyncio.current_task():
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if task is None or task.done():
            self._stderr_task = None

    async def stop(self) -> None:
        with self._owner_state_lock:
            if self._daemon_bound and self._daemon_stopping and self._thread is None:
                if self._process is not None or self._stderr_task is not None:
                    raise MCPUnavailable("Computer-control daemon transport did not drain")
                return
        await self._on_owner(
            self._stop_owned(),
            allow_daemon_stopping=True,
        )

    def stop_sync(self, *, timeout: float = 10) -> None:
        with self._owner_state_lock:
            if self._daemon_bound and self._daemon_stopping and self._thread is None:
                if self._process is not None or self._stderr_task is not None:
                    raise MCPUnavailable("Computer-control daemon transport did not drain")
                return
        self._on_owner_sync(
            self._stop_owned(),
            timeout=timeout,
            allow_daemon_stopping=True,
        )

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
        self.log_path = self.state_root / "setup-install.log"
        self.uv_root = self.state_root / "uv-tools"
        self.tool_dir = self.uv_root / "tools"
        self.bin_dir = self.uv_root / "bin"
        self.python_dir = self.uv_root / "python"
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[None]] = None
        self._active_process: Optional[asyncio.subprocess.Process] = None
        self._daemon_stopping = threading.Event()
        self._daemon_owner: Optional[tuple[int, str, int]] = None
        self._daemon_generation_claim: Any = None
        self._attempt_package_index: Optional[str] = None
        self._attempt_fallback_allowed = False
        self._setup_logs: deque[str] = deque(maxlen=200)
        self._load_setup_logs()
        self._status: Dict[str, Any] = self._load_status()
        if str(self._status.get("phase") or "") in _SETUP_TRANSIENT_PHASES:
            previous_phase = str(self._status.get("phase") or "")
            self._append_setup_log("上一次安装任务随 OneColleague 进程结束，已标记为中断", stream="system")
            self._set(
                "failed",
                detail="setup_interrupted",
                finished_at=time.time(),
                owner_pid=None,
                process_id=None,
                can_cancel=False,
                error={
                    "code": "setup_interrupted",
                    "message": f"Windows-MCP 安装在“{previous_phase}”阶段中断，请点击修复重新开始",
                },
                failure={"phase": previous_phase, "exit_code": None, "stderr_tail": list(self._setup_logs)[-10:]},
            )
        executable = self._windows_mcp_executable()
        if executable.is_file():
            self.session.configure(executable, str(self._status.get("version") or self._installed_version()))

    def _start_daemon(self, owner: Any) -> None:
        from .services import (
            _claim_daemon_generation,
            _release_daemon_generation,
        )

        generation_claim = _claim_daemon_generation(
            owner,
            home=self.home,
            subject="windows-mcp-setup",
        )
        owner_key = generation_claim.owner_key
        if self._daemon_generation_claim is not None:
            if not self._daemon_stopping.is_set() and self._daemon_owner == owner_key:
                _release_daemon_generation(generation_claim)
                return
            _release_daemon_generation(generation_claim)
            raise RuntimeError("Windows-MCP setup from the prior daemon is still active")
        task = self._task
        if task is not None and not task.done():
            _release_daemon_generation(generation_claim)
            raise RuntimeError("Windows-MCP setup from the prior daemon is still active")
        self._daemon_owner = owner_key
        self._daemon_generation_claim = generation_claim
        self._daemon_stopping.clear()

    def _request_daemon_stop(self) -> None:
        self._daemon_stopping.set()

    async def _drain_daemon(self) -> bool:
        task = self._task
        if task is not None and not task.done():
            await self.cancel()
        return task is None or task.done()

    def _drain_daemon_sync(self, *, timeout: float) -> bool:
        from .services import _release_daemon_generation

        self._request_daemon_stop()
        task = self._task
        if task is None or task.done():
            self._daemon_owner = None
            generation_claim = self._daemon_generation_claim
            self._daemon_generation_claim = None
            _release_daemon_generation(generation_claim)
            return True
        try:
            stopped = self.session._run_sync_for_daemon_shutdown(
                self._drain_daemon(),
                timeout=max(0.0, float(timeout or 0.0)),
            )
        except BaseException:
            return False
        if stopped:
            self._daemon_owner = None
            generation_claim = self._daemon_generation_claim
            self._daemon_generation_claim = None
            _release_daemon_generation(generation_claim)
        return bool(stopped)

    def _load_setup_logs(self) -> None:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        self._setup_logs.extend(line[:4000] for line in lines[-200:] if line.strip())

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

    def _append_setup_log(self, line: Any, *, stream: str = "stdout") -> None:
        text = _redact(str(line or "").replace("\r", "").strip())
        if not text:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        formatted = f"[{stamp}] [{stream}] {text}"[:4000]
        self._setup_logs.append(formatted)
        self._status["last_activity_at"] = time.time()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(formatted + "\n")
        except OSError:
            pass

    def _begin_attempt(self, operation: str) -> str:
        attempt_id = "setup_" + uuid.uuid4().hex[:16]
        self._setup_logs.clear()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_text(self.log_path, "")
        except OSError:
            pass
        now = time.time()
        package_index, fallback_allowed = self._package_index()
        self._attempt_package_index = package_index
        self._attempt_fallback_allowed = fallback_allowed
        self._set(
            "checking",
            attempt_id=attempt_id,
            operation=operation,
            detail="checking_uv",
            step="checking_uv",
            started_at=now,
            step_started_at=now,
            last_activity_at=now,
            owner_pid=os.getpid(),
            process_id=None,
            current_command=None,
            can_cancel=True,
            package_index=_redact(package_index),
            package_index_fallback=False,
            package_index_fallback_allowed=fallback_allowed,
            python_path=None,
            python_source=None,
            uv_version=None,
            error=None,
            failure=None,
        )
        self._append_setup_log(f"开始 Windows-MCP {operation}，Python 包索引：{package_index}", stream="system")
        return attempt_id

    def _set_step(self, phase: str, detail: str, **extra: Any) -> None:
        self._set(phase, detail=detail, step=detail, step_started_at=time.time(), **extra)

    def status(self) -> Dict[str, Any]:
        if (
            self._task is not None
            and self._task.done()
            and str(self._status.get("phase") or "") in _SETUP_TRANSIENT_PHASES
        ):
            previous_phase = str(self._status.get("phase") or "")
            self._append_setup_log("安装任务意外结束，已退出进行中状态", stream="system")
            self._set(
                "failed",
                detail="setup_task_stopped",
                finished_at=time.time(),
                owner_pid=None,
                process_id=None,
                current_command=None,
                can_cancel=False,
                error={
                    "code": "setup_task_stopped",
                    "message": "Windows-MCP 安装任务意外结束，请复制诊断信息后点击修复",
                },
                failure={"phase": previous_phase, "exit_code": None, "stderr_tail": list(self._setup_logs)[-10:]},
            )
        result = dict(self._status or {"phase": "not_started", "version": ""})
        result["session_running"] = self.session.running
        result["session_started_at"] = getattr(self.session, "started_at", None)
        result["transport_restarts"] = int(getattr(self.session, "transport_restarts", 0))
        result["in_progress"] = bool(self._task and not self._task.done())
        result["can_cancel"] = bool(result["in_progress"])
        result["logs"] = (list(self._setup_logs) + [_redact(str(line)) for line in self.session.logs])[-200:]
        result["log_truncated"] = len(self._setup_logs) >= self._setup_logs.maxlen
        return result

    async def ensure(self, *, force: bool = False, upgrade: bool = False) -> Dict[str, Any]:
        if self._daemon_stopping.is_set():
            raise MCPUnavailable("Computer-control daemon service is stopping")
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
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.status()

    async def _run(self, *, force: bool, upgrade: bool) -> None:
        async with self._lock:
            operation = "upgrade" if upgrade else ("repair" if force else "install")
            setup_id = self._begin_attempt(operation)
            try:
                with self.lease.hold(
                    group_id="_global",
                    actor_id="setup",
                    run_id=setup_id,
                    observe_only=False,
                ) as lease_guard:
                    if os.name != "nt":
                        raise MCPUnavailable("Computer control requires Windows")
                    uv = await self._ensure_uv()
                    env = self._uv_environment()
                    executable = self._windows_mcp_executable()
                    if (force or upgrade) and self.session.running:
                        await self.session.stop()
                    if upgrade and executable.is_file():
                        self._set_step("downloading", "upgrading_windows_mcp")
                        await self._run_package_command(
                            [str(uv), "tool", "upgrade"],
                            [WINDOWS_MCP_PACKAGE],
                            env=env,
                            timeout=900,
                        )
                    elif force or not executable.is_file():
                        python = await self._ensure_python_313(uv, env=env)
                        self._set_step("downloading", "installing_windows_mcp")
                        command = [str(uv), "tool", "install", "--python", str(python), "--no-python-downloads"]
                        if force:
                            command.append("--force")
                        await self._run_package_command(command, [WINDOWS_MCP_PACKAGE], env=env, timeout=900)
                    if lease_guard["lost"].is_set():
                        raise PermissionError("computer_control_lease_required")
                    if not executable.is_file():
                        raise MCPUnavailable(f"Windows-MCP installation finished but {executable.name} was not created")
                    version = self._installed_version()
                    self.session.configure(executable, version)
                    await self.session.stop()
                    self._set_step("initializing", "starting_mcp_session", version=version, can_cancel=False)
                    tools = await self.session.start()
                    self.refresh_catalog(tools, version=version)
            except asyncio.CancelledError:
                self._append_setup_log("安装已由用户取消", stream="system")
                self._set(
                    "cancelled",
                    detail="setup_cancelled",
                    finished_at=time.time(),
                    owner_pid=None,
                    process_id=None,
                    current_command=None,
                    can_cancel=False,
                    error={"code": "setup_cancelled", "message": "Windows-MCP 安装已取消"},
                )
                raise
            except Exception as exc:
                process = self.session._process
                self._append_setup_log(str(exc), stream="error")
                failure = {
                    "phase": self._status.get("phase"),
                    "exit_code": process.returncode if process is not None else None,
                    "stderr_tail": list(self._setup_logs)[-10:],
                }
                self._set(
                    "failed",
                    detail=self._status.get("detail"),
                    finished_at=time.time(),
                    owner_pid=None,
                    process_id=None,
                    current_command=None,
                    can_cancel=False,
                    error={"code": "windows_mcp_setup_failed", "message": _redact(str(exc))},
                    failure=failure,
                )

    async def cancel(self) -> Dict[str, Any]:
        task = self._task
        if task is None or task.done():
            return self.status()
        self._append_setup_log("收到取消安装请求", stream="system")
        process = self._active_process
        if process is not None and process.returncode is None:
            await self._terminate_process(process)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return self.status()

    async def repair(self) -> Dict[str, Any]:
        return await self.ensure(force=True)

    async def upgrade(self) -> Dict[str, Any]:
        return await self.ensure(upgrade=True)

    def refresh_catalog(self, tools: Sequence[Dict[str, Any]], *, version: Optional[str] = None) -> Dict[str, Any]:
        current_version = str(version or self.session.version or self._status.get("version") or "")
        self._set_step("verifying", "checking_tool_catalog", version=current_version, tool_count=len(tools), can_cancel=False)
        fingerprint = self.store.fingerprint(current_version, list(tools))
        revoked = self.store.revoke_stale_trust(fingerprint)
        self._set(
            "ready",
            version=current_version,
            fingerprint=fingerprint,
            tool_count=len(tools),
            revoked_trust_count=revoked,
            detail=None,
            finished_at=time.time(),
            error=None,
            failure=None,
            python_candidates=[],
            owner_pid=None,
            process_id=None,
            current_command=None,
            can_cancel=False,
        )
        return self.status()

    def _package_index(self) -> tuple[str, bool]:
        explicit = str(os.environ.get("ONECOLLEAGUE_PYPI_INDEX_URL") or "").strip()
        inherited = str(os.environ.get("UV_DEFAULT_INDEX") or os.environ.get("UV_INDEX_URL") or "").strip()
        configured = explicit or inherited
        selected = _safe_index_url(configured or DEFAULT_PYPI_INDEX_URL)
        if not selected:
            selected = DEFAULT_PYPI_INDEX_URL
        return selected, not bool(configured and _safe_index_url(configured))

    @staticmethod
    def _should_fallback_package_index(error: Exception) -> bool:
        message = str(error).casefold()
        return any(
            token in message
            for token in (
                "timed out",
                "timeout",
                "failed to connect",
                "connection",
                "dns",
                "network",
                "certificate",
                "tls",
                "ssl",
                "failed to fetch",
                "http 404",
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "no solution found",
                "no matching distribution",
                "could not find a version",
                "not found in the package registry",
            )
        )

    async def _run_package_command(
        self,
        prefix: Sequence[str],
        suffix: Sequence[str],
        *,
        env: Dict[str, str],
        timeout: float,
    ) -> str:
        index, fallback_allowed = self._current_package_index()
        try:
            return await self._run_command(
                [*prefix, "--default-index", index, *suffix],
                env=env,
                timeout=timeout,
            )
        except Exception as exc:
            if not fallback_allowed or index == OFFICIAL_PYPI_INDEX_URL or not self._should_fallback_package_index(exc):
                raise
            self._append_setup_log(
                f"国内镜像不可用，回退官方 PyPI：{OFFICIAL_PYPI_INDEX_URL}",
                stream="system",
            )
            self._set(
                str(self._status.get("phase") or "downloading"),
                package_index=OFFICIAL_PYPI_INDEX_URL,
                package_index_fallback=True,
            )
            self._attempt_package_index = OFFICIAL_PYPI_INDEX_URL
            return await self._run_command(
                [*prefix, "--default-index", OFFICIAL_PYPI_INDEX_URL, *suffix],
                env=env,
                timeout=timeout,
            )

    def _current_package_index(self) -> tuple[str, bool]:
        if self._attempt_package_index:
            return self._attempt_package_index, self._attempt_fallback_allowed
        return self._package_index()

    def _uv_environment(self) -> Dict[str, str]:
        self.tool_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.pop("UV_CONFIG_FILE", None)
        search_path = self._command_search_path()
        if search_path:
            env["PATH"] = search_path
        env.update(
            {
                "UV_TOOL_DIR": str(self.tool_dir),
                "UV_TOOL_BIN_DIR": str(self.bin_dir),
                "UV_PYTHON_INSTALL_DIR": str(self.python_dir),
                "UV_NO_CONFIG": "1",
                "UV_NO_PROGRESS": "1",
                "PYTHONUTF8": "1",
            }
        )
        return env

    def _windows_mcp_executable(self) -> Path:
        suffix = ".exe" if os.name == "nt" else ""
        return self.bin_dir / f"windows-mcp{suffix}"

    async def _ensure_python_313(self, uv: Path, *, env: Dict[str, str]) -> Path:
        self.python_dir.mkdir(parents=True, exist_ok=True)
        find_command = [str(uv), "python", "find", "3.13", "--no-python-downloads", "--no-project"]
        self._set_step("checking", "checking_python_313")
        try:
            output = await self._run_command(find_command, env=env, timeout=30)
            python = self._python_path_from_output(output)
        except Exception:
            python = None
        if python is None:
            self._set_step("downloading", "installing_python_313")
            command = [
                str(uv),
                "-v",
                "python",
                "install",
                "3.13",
                "--install-dir",
                str(self.python_dir),
                "--no-bin",
                "--no-registry",
            ]
            mirror = _safe_index_url(
                os.environ.get("ONECOLLEAGUE_UV_PYTHON_MIRROR"),
                default_path="",
            )
            if mirror:
                command.extend(["--mirror", mirror])
            await self._run_command(command, env=env, timeout=900)
            self._set_step("checking", "checking_python_313")
            output = await self._run_command(find_command, env=env, timeout=30)
            python = self._python_path_from_output(output)
        if python is None:
            raise MCPUnavailable("uv 已完成 Python 3.13 准备，但未返回可用的解释器路径")
        try:
            managed = python.resolve().is_relative_to(self.python_dir.resolve())
        except (OSError, ValueError, AttributeError):
            managed = str(python).casefold().startswith(str(self.python_dir).casefold())
        source = "uv_managed" if managed else "system"
        self._set(
            "checking",
            detail="python_313_ready",
            python_path=str(python),
            python_source=source,
        )
        self._append_setup_log(f"使用 Python 3.13：{python}（{source}）", stream="system")
        return python

    @staticmethod
    def _python_path_from_output(output: str) -> Optional[Path]:
        for line in reversed(str(output or "").splitlines()):
            value = line.strip().strip('"')
            if not value:
                continue
            try:
                path = Path(value)
            except (TypeError, ValueError, OSError):
                continue
            if path.is_file():
                return path
        return None

    async def _ensure_uv(self) -> Path:
        errors: List[str] = []

        async def find_working_uv(*, extra_roots: Sequence[Path] = ()) -> Optional[Path]:
            for candidate in self._uv_candidates(extra_roots=extra_roots):
                try:
                    output = await self._run_command(
                        [str(candidate), "--version"],
                        env=self._bootstrap_environment(),
                        timeout=30,
                    )
                except Exception as exc:
                    errors.append(_redact(str(exc)))
                    continue
                version = str(output or "").strip().splitlines()[-1] if str(output or "").strip() else ""
                self._status["uv_version"] = _redact(version)
                return candidate
            return None

        existing = await find_working_uv()
        if existing is not None:
            self._set("checking", detail="uv_ready", uv_path=str(existing), python_candidates=[])
            return existing
        commands = self._python_commands()
        self._set_step(
            "downloading",
            "installing_uv_with_pip",
            python_candidates=[" ".join(str(part) for part in command) for command in commands],
        )
        package_index, fallback_allowed = self._current_package_index()
        for command in commands:
            try:
                script_dirs = await self._python_script_dirs(command)
            except Exception as exc:
                errors.append(_redact(str(exc)))
                continue
            for location_args in (["--user"], []):
                indexes = [package_index]
                for index in indexes:
                    try:
                        await self._run_command(
                            [
                                *command,
                                "-m",
                                "pip",
                                "install",
                                *location_args,
                                "--index-url",
                                index,
                                "uv",
                            ],
                            env=self._bootstrap_environment(),
                            timeout=600,
                        )
                    except Exception as exc:
                        errors.append(_redact(str(exc)))
                        if (
                            fallback_allowed
                            and index != OFFICIAL_PYPI_INDEX_URL
                            and self._should_fallback_package_index(exc)
                        ):
                            indexes.append(OFFICIAL_PYPI_INDEX_URL)
                            self._append_setup_log(
                                f"国内镜像不可用，回退官方 PyPI：{OFFICIAL_PYPI_INDEX_URL}",
                                stream="system",
                            )
                            self._set(
                                "downloading",
                                package_index=OFFICIAL_PYPI_INDEX_URL,
                                package_index_fallback=True,
                            )
                            self._attempt_package_index = OFFICIAL_PYPI_INDEX_URL
                            package_index = OFFICIAL_PYPI_INDEX_URL
                        continue
                    break
                else:
                    continue
                installed = await find_working_uv(extra_roots=script_dirs)
                if installed is not None:
                    self._set("checking", detail="uv_installed", uv_path=str(installed), python_candidates=[])
                    return installed
        detail = " | ".join(errors[-3:])
        if not commands:
            raise MCPUnavailable("未找到可用的 Python 命令，无法安装 uv。请安装 Python 3.9+ 或 Python Launcher (py.exe)，并将其加入 PATH")
        raise MCPUnavailable(f"Unable to install uv with pip{': ' + detail if detail else ''}")

    def _bootstrap_environment(self) -> Dict[str, str]:
        env = dict(os.environ)
        env.pop("UV_CONFIG_FILE", None)
        search_path = self._command_search_path()
        if search_path:
            env["PATH"] = search_path
        env["UV_NO_CONFIG"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        return env

    async def _python_script_dirs(self, command: Sequence[str]) -> List[Path]:
        probe = (
            "import json,os,site,sys,sysconfig;"
            "user=site.getuserbase();"
            "scripts=sysconfig.get_path('scripts');"
            "user_scripts=(os.path.join(user,'Python%d%d'%sys.version_info[:2],'Scripts') "
            "if user and os.name=='nt' else (os.path.join(user,'bin') if user else ''));"
            "print(json.dumps({'executable':sys.executable,'script_dirs':[scripts,user_scripts]}))"
        )
        output = await self._run_command(
            [*command, "-c", probe],
            env=self._bootstrap_environment(),
            timeout=30,
        )
        metadata: Dict[str, Any] = {}
        for line in reversed(output.splitlines()):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                metadata = value
                break
        if not metadata:
            raise MCPUnavailable(f"{Path(str(command[0])).name} 未返回有效的 Python 环境信息")
        roots: List[Path] = []
        for value in metadata.get("script_dirs", []):
            if isinstance(value, str) and value.strip():
                roots.append(Path(value))
        return roots

    @staticmethod
    def _command_search_path() -> str:
        values: List[str] = []
        if sys.platform == "win32":
            try:
                import winreg

                locations = (
                    (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                    (winreg.HKEY_CURRENT_USER, r"Environment"),
                )
                for hive, key_name in locations:
                    try:
                        with winreg.OpenKey(hive, key_name) as key:
                            raw, _ = winreg.QueryValueEx(key, "Path")
                    except OSError:
                        continue
                    if raw:
                        values.append(os.path.expandvars(str(raw)))
            except (ImportError, OSError):
                pass
        values.append(str(os.environ.get("PATH") or ""))
        result: List[str] = []
        seen = set()
        for value in values:
            for item in value.split(os.pathsep):
                expanded = os.path.expandvars(item.strip().strip('"'))
                if not expanded:
                    continue
                key = os.path.normcase(os.path.abspath(expanded))
                if key in seen:
                    continue
                seen.add(key)
                result.append(expanded)
        return os.pathsep.join(result)

    @staticmethod
    def _python_commands() -> List[List[str]]:
        commands: List[List[str]] = []
        seen_commands = set()

        def add(command: Sequence[str]) -> None:
            if not command or not str(command[0] or "").strip():
                return
            key = (os.path.normcase(os.path.abspath(str(command[0]))), tuple(str(item) for item in command[1:]))
            if key in seen_commands:
                return
            seen_commands.add(key)
            commands.append(list(command))

        def add_path_matches(names: Sequence[str], *arguments: str) -> None:
            for raw_root in search_path.split(os.pathsep):
                root = raw_root.strip().strip('"')
                if not root:
                    continue
                for name in names:
                    candidate = Path(root) / name
                    if candidate.is_file():
                        add([str(candidate), *arguments])

        executable = str(getattr(sys, "executable", "") or "").strip()
        executable_name = Path(executable).name.casefold() if executable else ""
        # In a Nuitka build sys.executable is onecolleague.exe, not Python.
        if executable and executable_name.startswith("python"):
            add([executable])

        search_path = WindowsMCPSetup._command_search_path()
        launcher = shutil.which("py", path=search_path)
        if launcher:
            add([str(launcher), "-3"])
        elif sys.platform == "win32":
            windows_dir = Path(str(os.environ.get("WINDIR") or r"C:\Windows"))
            launcher_path = windows_dir / "py.exe"
            if launcher_path.is_file():
                add([str(launcher_path), "-3"])
        add_path_matches(("py.exe", "py"), "-3")
        for name in ("python", "python3", "python.exe", "python3.exe"):
            candidate = shutil.which(name, path=search_path)
            if candidate:
                add([str(candidate)])
        add_path_matches(("python.exe", "python3.exe", "python", "python3"))
        if sys.platform == "win32":
            search_patterns = [
                (os.environ.get("LOCALAPPDATA"), "Programs/Python/Python*/python.exe"),
                (os.environ.get("ProgramFiles"), "Python*/python.exe"),
                (os.environ.get("ProgramFiles"), "Python/Python*/python.exe"),
                (os.environ.get("ProgramFiles(x86)"), "Python*/python.exe"),
            ]
            for raw_root, pattern in search_patterns:
                if not raw_root:
                    continue
                root = Path(str(raw_root))
                for candidate in sorted(root.glob(pattern), reverse=True):
                    if candidate.is_file():
                        add([str(candidate)])
        return commands

    def _uv_candidates(self, *, extra_roots: Sequence[Path] = ()) -> List[Path]:
        names = ["uv.exe", "uv"] if os.name == "nt" else ["uv"]
        candidates: List[Path] = []
        roots: List[Path] = []
        seen = set()

        def as_path(value: Any) -> Optional[Path]:
            if value is None or (isinstance(value, str) and not value.strip()):
                return None
            try:
                return Path(value)
            except (TypeError, ValueError, OSError):
                return None

        def add_root(value: Any) -> None:
            path = as_path(value)
            if path is not None:
                roots.append(path)

        def add_candidate(value: Any) -> None:
            path = as_path(value)
            if path is None:
                return
            key = os.path.normcase(os.path.abspath(str(path)))
            if key in seen or not path.is_file():
                return
            seen.add(key)
            candidates.append(path)

        # After pip installation, prefer the Scripts directories reported by
        # that exact Python over a stale or broken uv earlier on PATH.
        for root in extra_roots:
            root_path = as_path(root)
            if root_path is None:
                continue
            for name in names:
                add_candidate(root_path / name)

        found = shutil.which("uv", path=self._command_search_path())
        if found:
            add_candidate(found)

        executable = getattr(sys, "executable", None)
        executable_path = as_path(executable)
        if executable_path is not None:
            add_root(executable_path.parent)

        user_base = getattr(site, "USER_BASE", None)
        user_base_path = as_path(user_base)
        if user_base_path is not None:
            add_root(user_base_path / ("Scripts" if os.name == "nt" else "bin"))

        if sys.platform == "win32":
            app_data = as_path(os.environ.get("APPDATA"))
            if app_data is not None:
                roots.extend(path for path in app_data.glob("Python/Python*/Scripts") if path.is_dir())
            local_app_data = as_path(os.environ.get("LOCALAPPDATA"))
            if local_app_data is not None:
                roots.extend(path for path in local_app_data.glob("Programs/Python/Python*/Scripts") if path.is_dir())
        add_root(Path.home() / ".local" / "bin")

        for command in self._python_commands():
            if not command:
                continue
            command_path = command[0]
            executable_path = as_path(command_path)
            if executable_path is None:
                continue
            roots.extend([executable_path.parent, executable_path.parent / "Scripts"])
        for root in roots:
            for name in names:
                add_candidate(root / name)
        return candidates

    def _find_uv(self, *, extra_roots: Sequence[Path] = ()) -> Optional[Path]:
        candidates = self._uv_candidates(extra_roots=extra_roots)
        return candidates[0] if candidates else None

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
        display = _redact(f"> {display}")
        self._append_setup_log(display, stream="command")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                creationflags=_creation_flags(),
            )
        except FileNotFoundError as exc:
            command_name = Path(str(command[0])).name
            raise MCPUnavailable(
                f"{command_name} 无法启动（Windows 找不到该命令）。请确认 Python 或 uv 已正确安装并加入 PATH"
            ) from exc
        except OSError as exc:
            command_name = Path(str(command[0])).name
            detail = f"WinError {exc.winerror}" if getattr(exc, "winerror", None) is not None else str(exc)
            raise MCPUnavailable(f"{command_name} 无法启动：{detail}") from exc
        self._active_process = process
        self._set(
            str(self._status.get("phase") or "checking"),
            process_id=_coerce_process_id(getattr(process, "pid", None)),
            current_command=display.removeprefix("> "),
            can_cancel=True,
        )
        output_parts: deque[str] = deque(maxlen=500)

        async def read_stream(reader: Any, stream: str) -> None:
            if reader is None or not callable(getattr(reader, "readline", None)):
                return
            while True:
                raw = await reader.readline()
                if not raw:
                    return
                if isinstance(raw, bytes):
                    line = raw.decode("utf-8", errors="replace")
                else:
                    line = str(raw)
                output_parts.append(line)
                for child in line.replace("\r", "\n").splitlines():
                    self._append_setup_log(child, stream=stream)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    read_stream(getattr(process, "stdout", None), "stdout"),
                    read_stream(getattr(process, "stderr", None), "stderr"),
                    process.wait(),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._terminate_process(process)
            raise MCPUnavailable(f"Command timed out: {Path(command[0]).name}")
        except asyncio.CancelledError:
            await self._terminate_process(process)
            raise
        finally:
            if self._active_process is process:
                self._active_process = None
            if str(self._status.get("phase") or "") in _SETUP_TRANSIENT_PHASES:
                self._set(
                    str(self._status.get("phase") or "checking"),
                    process_id=None,
                    current_command=None,
                    can_cancel=False,
                )
        output = "".join(output_parts)
        lines = [_redact(line) for line in output.splitlines() if line.strip()]
        if process.returncode != 0:
            tail = " | ".join(lines[-8:])
            if process.returncode == 9009:
                command_name = Path(str(command[0])).name
                raise MCPUnavailable(
                    f"{command_name} 未找到（Windows 错误 9009）。请确认 Python 或 Python Launcher 已安装并加入 PATH"
                )
            raise MCPUnavailable(f"{Path(command[0]).name} exited with code {process.returncode}{': ' + tail if tail else ''}")
        return output

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if sys.platform == "win32" and getattr(process, "pid", None):
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_creation_flags(),
                )
                await asyncio.wait_for(killer.wait(), timeout=8)
            except Exception:
                pass
        if process.returncode is None:
            try:
                process.terminate()
            except (ProcessLookupError, OSError):
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            await process.wait()
