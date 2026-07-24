from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import site
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..util.fs import atomic_write_text
from .storage import WorkflowStore

MCP_PROTOCOL_VERSION = "2024-11-05"
WINDOWS_MCP_PACKAGE = "windows-mcp"


class MCPUnavailable(RuntimeError):
    pass


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
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._logs: deque[str] = deque(maxlen=200)
        self._tools: List[Dict[str, Any]] = []
        self.started_at: Optional[float] = None

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
        current = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not current and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._start_owned(), self._loop)
            return await asyncio.wrap_future(future)
        return await self._start_owned()

    async def _start_owned(self) -> List[Dict[str, Any]]:
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            if self.running and self._tools:
                return list(self._tools)
            last_error: Optional[BaseException] = None
            for attempt in range(2):
                await self._stop_unlocked()
                try:
                    return await self._start_unlocked()
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

    async def _request_unlocked(self, method: str, params: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None or process.returncode is not None:
            raise MCPUnavailable(self._failure_detail("Windows-MCP is not running"))
        self._request_id += 1
        request_id = self._request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Windows-MCP request timed out: {method}")
            line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
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

    async def request(self, method: str, params: Dict[str, Any], *, timeout: float = 60) -> Dict[str, Any]:
        current = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not current and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._request_owned(method, params, timeout=timeout), self._loop)
            return await asyncio.wrap_future(future)
        return await self._request_owned(method, params, timeout=timeout)

    async def _request_owned(self, method: str, params: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
        if not self.running:
            await self._start_owned()
        async with self._lock:
            return await self._request_unlocked(method, params, timeout=timeout)

    async def call_tool(self, name: str, arguments: Dict[str, Any], *, timeout: float = 60) -> Dict[str, Any]:
        try:
            return await self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        except MCPUnavailable as exc:
            from .risk import classify_tool

            # A mutating call may have reached Windows before the stdio process
            # exited. Replaying it could double-click, type twice, or delete twice.
            await self.stop()
            try:
                await self.start()
            except Exception:
                raise MCPUnavailable(f"Windows-MCP 连接中断且自动恢复失败：{exc}") from exc
            if classify_tool(name, arguments) == "low":
                return await self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
            raise MCPUnavailable("Windows-MCP 连接中断；为避免重复操作，本步骤未自动重试。请确认桌面状态后重新运行。") from exc

    async def catalog(self) -> List[Dict[str, Any]]:
        if not self.running or not self._tools:
            return await self.start()
        return list(self._tools)

    def catalog_sync(self, *, timeout: float = 120) -> List[Dict[str, Any]]:
        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.catalog(), self._loop)
            return future.result(timeout=timeout)
        return asyncio.run(self.catalog())

    async def _stop_unlocked(self) -> None:
        process = self._process
        self._process = None
        self._tools = []
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
        current = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not current and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._stop_owned(), self._loop)
            await asyncio.wrap_future(future)
            return
        await self._stop_owned()

    async def _stop_owned(self) -> None:
        async with self._lock:
            await self._stop_unlocked()


class WindowsMCPSetup:
    def __init__(self, home: Path, session: WindowsMCPSession, store: WorkflowStore):
        self.home = home
        self.session = session
        self.store = store
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
                self._set("checking", error=None, failure=None)
                if os.name != "nt":
                    raise MCPUnavailable("Computer control requires Windows")
                uv = await self._ensure_uv()
                env = self._uv_environment()
                executable = self._windows_mcp_executable()
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
                if not executable.is_file():
                    raise MCPUnavailable(f"Windows-MCP installation finished but {executable.name} was not created")
                version = self._installed_version()
                self.session.configure(executable, version)
                await self.session.stop()
                self._set("initializing", version=version, detail="starting_mcp_session")
                tools = await self.session.start()
                self._set("verifying", version=version, tool_count=len(tools), detail="checking_tool_catalog")
                fingerprint = self.store.fingerprint(version, tools)
                revoked = self.store.revoke_stale_trust(fingerprint)
                self._set(
                    "ready",
                    version=version,
                    fingerprint=fingerprint,
                    tool_count=len(tools),
                    revoked_trust_count=revoked,
                    detail=None,
                    error=None,
                    failure=None,
                )
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
        await self.session.stop()
        return await self.ensure(force=True)

    async def upgrade(self) -> Dict[str, Any]:
        return await self.ensure(upgrade=True)

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
