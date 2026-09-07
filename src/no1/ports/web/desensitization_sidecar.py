from __future__ import annotations

import json
import hashlib
import importlib.resources as importlib_resources
import io
import re
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from ...util.process import resolve_background_python_argv, terminate_pid, windowless_subprocess_popen_kwargs


class DesensitizationSidecarManager:
    """Manage the optional desensitization process without loading its dependencies."""

    def __init__(self, home: Path):
        self.home = Path(home).expanduser().resolve()
        self.root = self.home / "state" / "desensitization"
        self.state_file = self.root / "sidecar.json"
        self.log_file = self.root / "sidecar.log"
        self._lock = threading.RLock()
        self._log_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._readers: list[threading.Thread] = []
        self._closed = False
        self.process: subprocess.Popen[str] | None = None
        self.install_process: subprocess.Popen[str] | None = None
        self.port: int | None = None
        self.token = secrets.token_urlsafe(32)
        self.phase = "stopped"
        self.error_code: str | None = None
        self.error_message: str | None = None
        self.install_progress: int | None = None
        self.install_step = ""
        self.last_log_line = ""
        self._log_tail: deque[str] = deque(maxlen=120)
        self.started_at: float | None = None
        self.updated_at = time.time()
        self._load_state()

    _ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
    _MAX_LOG_BYTES = 2 * 1024 * 1024

    def status(self) -> dict[str, Any]:
        # Take independent snapshots.  Holding ``_lock`` while waiting for
        # ``_log_lock`` can deadlock with the output reader, which writes the
        # log and then persists state under ``_lock``.
        with self._lock:
            phase = self.phase
            error_code = self.error_code
            error_message = self.error_message
            install_progress = self.install_progress
            install_step = self.install_step
            started_at = self.started_at
            updated_at = self.updated_at
            port = self.port
            process = self.process
        with self._log_lock:
            last_log_line = self.last_log_line
            log_tail = list(self._log_tail)
        return {
            "service_phase": phase,
            "service_error_code": error_code,
            "service_error_message": error_message,
            "service_retryable": phase in {"failed", "stopped"},
            "install_progress": install_progress,
            "install_step": install_step,
            "last_log_line": last_log_line,
            "install_log_tail": log_tail,
            "service_started_at": started_at,
            "service_updated_at": updated_at,
            "port": port,
            "pid": process.pid if process else None,
        }

    def url(self, path: str, query: str = "") -> str:
        if self.port is None:
            raise RuntimeError("sidecar port is not assigned")
        return f"http://127.0.0.1:{self.port}{path}{f'?{query}' if query else ''}"

    def mark_failed(self, code: str, message: str = "") -> None:
        with self._lock:
            self.phase = "failed"
            self.error_code = code
            self.error_message = message or code
            self._write_state()

    def retry(self) -> bool:
        with self._lock:
            if self.phase in {"installing", "starting"} and self._worker and self._worker.is_alive():
                return False
            for process in (self.install_process, self.process):
                if process is not None and process.poll() is None:
                    try:
                        terminate_pid(process.pid, timeout_s=0.5, include_group=True, force=True)
                    except Exception:
                        pass
            self.install_process = None
            self.process = None
            self.phase = "stopped"
            self.error_code = self.error_message = None
        return self.ensure_started(force=True)

    def _find_port(self) -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _component(self) -> Path:
        override = os.environ.get("CCCC_DESENSITIZATION_COMPONENT_DIR")
        if override:
            return Path(override).expanduser().resolve()
        for parent in Path(__file__).resolve().parents:
            candidate = parent / "components" / "desensitization"
            if (candidate / "pyproject.toml").is_file():
                return candidate
        # Installed wheels/Nuitka distributions carry a small source archive
        # instead of the repository's top-level components directory.
        try:
            resource = importlib_resources.files("no1.resources").joinpath("desensitization_component.zip")
            if resource.is_file():
                archive_bytes = resource.read_bytes()
                digest = hashlib.sha256(archive_bytes).hexdigest()[:16]
                extracted = self.root / "component" / digest
                marker = extracted / "pyproject.toml"
                if not marker.is_file():
                    temporary = extracted.with_name(extracted.name + ".tmp")
                    if temporary.exists():
                        shutil.rmtree(temporary, ignore_errors=True)
                    temporary.mkdir(parents=True, exist_ok=True)
                    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                        for item in archive.infolist():
                            target = (temporary / item.filename).resolve()
                            target.relative_to(temporary.resolve())
                            if item.is_dir():
                                target.mkdir(parents=True, exist_ok=True)
                            else:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(archive.read(item))
                    temporary.replace(extracted)
                if marker.is_file():
                    return extracted
        except Exception as exc:
            self._append_log(f"component resource extraction failed: {exc}")
        return Path(__file__).resolve().parents[4] / "components" / "desensitization"

    @staticmethod
    def _looks_like_sidecar(pid: int) -> bool:
        """Return true only for a process that belongs to this component.

        A persisted PID can be reused by an unrelated process after a crash;
        never terminate a PID based on liveness alone.
        """
        if pid <= 0 or pid == os.getpid():
            return False
        try:
            import psutil

            process = psutil.Process(pid)
            command = " ".join(process.cmdline()).lower()
            return "cccc_desensitization.sidecar" in command or (
                "uvicorn" in command and "desensitization" in command
            )
        except Exception:
            return False

    def _cleanup_stale_process(self, data: dict[str, Any]) -> None:
        try:
            pid = int(data.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if not self._looks_like_sidecar(pid):
            return
        try:
            self._append_log(f"cleaning up stale Sidecar process pid={pid}")
            terminate_pid(pid, timeout_s=1.0, include_group=True, force=True)
        except Exception as exc:
            self._append_log(f"stale Sidecar cleanup failed pid={pid}: {exc}")

    @staticmethod
    def _hidden_kwargs() -> dict[str, Any]:
        kwargs = dict(windowless_subprocess_popen_kwargs())
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = startupinfo
            # Stdin is assigned explicitly on each Popen call.  Keeping it out
            # of this shared helper is important because ``subprocess.run``
            # rejects ``stdin=...`` together with ``capture_output=True``.
        return kwargs

    def _append_log(self, line: str) -> None:
        text = self._ANSI_RE.sub("", str(line or "")).replace("\r", "\n").rstrip()
        if not text:
            return
        rendered = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
        with self._log_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as log:
                log.write(rendered + "\n")
            # uv and native model libraries can be very chatty. Keep the
            # persisted log bounded while retaining the most recent output.
            try:
                if self.log_file.stat().st_size > self._MAX_LOG_BYTES:
                    raw = self.log_file.read_bytes()
                    tail = raw[-(self._MAX_LOG_BYTES // 2):]
                    marker = b"[log truncated; showing recent output]\n"
                    self.log_file.write_bytes(marker + tail)
                    self._log_tail.clear()
                    self._log_tail.extend(self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-120:])
            except OSError:
                pass
            self._log_tail.append(rendered)
            self.last_log_line = rendered
        self._write_state()

    def _read_output(self, process: subprocess.Popen[str], label: str) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            # Child tools may emit a mixture of UTF-8 and the active Windows
            # code page.  Popen is configured with ``errors=replace`` below,
            # but keep the reader defensive as custom/fake streams used by
            # embedders and tests can still raise decoding errors.
            while True:
                if self._stop_event.is_set():
                    break
                try:
                    line = stream.readline()
                except UnicodeDecodeError as exc:
                    self._append_log(f"{label}: output decode error: {exc}")
                    continue
                if not line:
                    break
                self._append_log(f"{label}: {line}")
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _sync_environment(self, component: Path) -> str | None:
        uv_name = os.environ.get("CCCC_UV", "uv")
        uv = shutil.which(uv_name) or (uv_name if Path(uv_name).is_file() else None)
        if not uv:
            self.mark_failed("desensitization_uv_missing")
            return None
        probe_code = "import struct,sys; print(sys.executable); print(f'{sys.version_info[0]}.{sys.version_info[1]}'); print(struct.calcsize('P'))"

        def probe(argv: list[str]) -> str | None:
            try:
                result = subprocess.run(
                    [*argv, "-c", probe_code],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    stdin=subprocess.DEVNULL,
                    **self._hidden_kwargs(),
                )
            except (OSError, subprocess.SubprocessError, ValueError):
                return None
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if result.returncode or len(lines) < 3 or lines[-2] != "3.12" or lines[-1] != "8":
                return None
            executable = lines[0]
            path = Path(executable).expanduser()
            return str(path.resolve()) if path.is_file() else executable

        candidates: list[list[str]] = []
        requested = os.environ.get("CCCC_DESENSITIZATION_PYTHON", "").strip()
        if requested:
            requested_path = Path(requested).expanduser()
            candidates.append([str(requested_path.resolve()) if requested_path.is_file() else requested])
        # The Windows launcher is the most reliable way to find the installed
        # 3.12 interpreter when ``python3.12`` is not on PATH.
        if os.name == "nt" and shutil.which("py"):
            candidates.append([shutil.which("py") or "py", "-3.12"])
        # uv can locate a managed/system interpreter without activating it.
        if shutil.which(uv_name) or Path(str(uv)).is_file():
            try:
                found = subprocess.run(
                    [uv, "python", "find", "3.12"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", check=False, stdin=subprocess.DEVNULL,
                    **self._hidden_kwargs(),
                )
                for line in found.stdout.splitlines():
                    candidate = line.strip().strip('"')
                    if candidate and Path(candidate).is_file():
                        candidates.append([candidate])
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
        candidates.extend([["python3.12"], ["python"]])
        python = next((value for candidate in candidates if (value := probe(candidate))), None)
        if not python:
            self.mark_failed("desensitization_python_missing", "Python 3.12 x64 is required")
            return None

        self.install_step, self.install_progress = "正在安装脱敏组件依赖", 5
        self._write_state()
        venv = self.root / "venv"
        env = os.environ.copy()
        # uv otherwise inspects the parent application's virtualenv and emits
        # a misleading mismatch warning.  More importantly, a stale
        # VIRTUAL_ENV can make tool resolution target the wrong interpreter.
        env.pop("VIRTUAL_ENV", None)
        env.pop("VIRTUAL_ENV_PROMPT", None)
        env["UV_PROJECT_ENVIRONMENT"] = str(venv)
        env["UV_NO_PROGRESS"] = "1"
        env["UV_COLOR"] = "never"
        def run_sync(locked: bool) -> tuple[int, str]:
            args = [uv, "sync", "--project", str(component)]
            if locked:
                args.append("--locked")
            args.extend(["--no-dev", "--python", python])
            self.install_process = subprocess.Popen(
                args, cwd=component, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1, **self._hidden_kwargs(),
            )
            self._read_output(self.install_process, "uv")
            return self.install_process.wait(), ""

        try:
            returncode, _ = run_sync(locked=True)
            # uv resolves platform-specific wheels differently on Windows.
            # A lock produced on Linux can therefore require a harmless
            # metadata refresh at first launch. Retry without --locked so the
            # bundled component remains usable, while still preferring the
            # committed lock for reproducible installs.
            if returncode:
                self._append_log("uv locked sync failed; retrying platform resolution")
                returncode, _ = run_sync(locked=False)
        except (OSError, subprocess.SubprocessError) as exc:
            self.mark_failed("desensitization_install_failed", str(exc))
            return None
        finally:
            self.install_process = None
        if returncode:
            self.mark_failed("desensitization_install_failed", f"uv sync exited with {returncode}")
            return None
        self.install_progress, self.install_step = 70, "正在启动脱敏服务"
        self._write_state()
        executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not executable.is_file():
            self.mark_failed(
                "desensitization_install_failed",
                f"uv sync completed but the component environment is missing: {executable}",
            )
            return None
        return str(executable)

    def _start_blocking(self) -> None:
        try:
            component = self._component()
            entry = component / "src" / "cccc_desensitization" / "sidecar.py"
            if not (component / "pyproject.toml").is_file() or not (component / "uv.lock").is_file() or not entry.is_file():
                self.mark_failed("desensitization_component_missing")
                return
            python = self._sync_environment(component)
            if python is None or self._closed:
                return
            self.port = self._find_port()
            env = os.environ.copy()
            env.update(
                CCCC_DESENSITIZATION_TOKEN=self.token,
                CCCC_DESENSITIZATION_PORT=str(self.port),
                CCCC_HOME=str(self.home),
                ONECOLLEAGUE_HOME=str(self.home),
                CCCC_DESENSITIZATION_PARENT_PID=str(os.getpid()),
                PYTHONUTF8="1",
                PYTHONIOENCODING="utf-8",
            )
            try:
                import psutil

                env["CCCC_DESENSITIZATION_PARENT_CREATE_TIME"] = str(psutil.Process(os.getpid()).create_time())
            except Exception:
                pass
            # Keep source-based releases runnable even when uv did not install
            # the project package (for example, while recovering a partial venv).
            source_dir = str(component / "src")
            env["PYTHONPATH"] = source_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            self.phase = "starting"
            self.install_progress, self.install_step = 85, "正在等待脱敏服务响应"
            self.started_at = time.time()
            self._write_state()
            argv = resolve_background_python_argv([
                python, "-m", "uvicorn", "cccc_desensitization.sidecar:app",
                "--host", "127.0.0.1", "--port", str(self.port),
            ])
            self.process = subprocess.Popen(
                argv, cwd=component, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1, **self._hidden_kwargs(),
            )
            reader = threading.Thread(target=self._read_output, args=(self.process, "sidecar"), daemon=True)
            self._readers.append(reader)
            reader.start()
            try:
                health_timeout = max(5.0, float(os.environ.get("CCCC_DESENSITIZATION_HEALTH_TIMEOUT", "45")))
            except (TypeError, ValueError):
                health_timeout = 45.0
            deadline = time.time() + health_timeout
            while time.time() < deadline and not self._closed:
                if self.process.poll() is not None:
                    self.mark_failed("desensitization_service_unavailable", "sidecar exited during startup")
                    return
                try:
                    if httpx.get(
                        f"http://127.0.0.1:{self.port}/internal/healthz",
                        timeout=.4,
                        trust_env=False,
                    ).status_code == 200:
                        self.phase, self.error_code, self.error_message = "ready", None, None
                        self.install_progress, self.install_step = 100, ""
                        self._write_state()
                        return
                except Exception:
                    self._stop_event.wait(.15)
            if not self._closed:
                self.mark_failed("desensitization_service_unavailable", "health check timed out")
        except Exception as exc:
            self.mark_failed("desensitization_service_unavailable", str(exc))
        finally:
            # A failed health check must not leave a detached process behind.
            process = self.process
            if process is not None and process.poll() is None and (self._closed or self.phase == "failed"):
                try:
                    terminate_pid(process.pid, timeout_s=1.0, include_group=True, force=True)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            if self._closed or self.phase == "failed":
                with self._lock:
                    self.process = None
                    self.port = None
                    self._write_state()

    def ensure_started(self, *, force: bool = False) -> bool:
        with self._lock:
            if self.process and self.process.poll() is None and self.phase == "ready":
                return True
            if self.phase in {"installing", "starting"} and self._worker and self._worker.is_alive():
                return False
            if self.phase == "failed" and not force:
                return False
            self.root.mkdir(parents=True, exist_ok=True)
            self._closed = False
            self._stop_event.clear()
            self.phase, self.error_code, self.error_message = "installing", None, None
            self.install_progress, self.install_step = 0, "正在检查运行环境"
            self._worker = threading.Thread(target=self._start_blocking, name="cccc-desensitization-sidecar", daemon=True)
            self._write_state()
            # Persist the initial generation before starting the worker.  If
            # the worker fails immediately, a post-start write from this
            # caller must not overwrite its structured failure state.
            self._worker.start()
            return False

    def _load_state(self) -> None:
        data: dict[str, Any] = {}
        try:
            loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
            data = loaded if isinstance(loaded, dict) else {}
            if isinstance(data, dict):
                self.error_code = data.get("error_code") or None
                self.error_message = data.get("error_message") or None
                self.install_progress = data.get("install_progress")
                self.install_step = str(data.get("install_step") or "")
                self.last_log_line = str(data.get("last_log_line") or "")
        except (OSError, ValueError, TypeError):
            data = {}
        self._cleanup_stale_process(data)
        # A manager is process-local.  Never expose a persisted ``ready`` or
        # ``failed`` phase as the current state before this process has
        # attached a new child; the first request should be allowed to retry.
        self.phase = "stopped"
        self.error_code = None
        self.error_message = None
        try:
            lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            self._log_tail.extend(lines[-120:])
            if lines:
                self.last_log_line = lines[-1]
        except OSError:
            pass

    def _write_state(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self.updated_at = time.time()
            payload = {
                "phase": self.phase, "port": self.port,
                "pid": self.process.pid if self.process else None,
                "error_code": self.error_code, "error_message": self.error_message,
                "install_progress": self.install_progress, "install_step": self.install_step,
                "last_log_line": self.last_log_line, "service_updated_at": self.updated_at,
            }
            temporary = self.state_file.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.state_file)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_event.set()
            for process in (self.install_process, self.process):
                if process is not None and process.poll() is None:
                    try:
                        terminate_pid(process.pid, timeout_s=1.0, include_group=True, force=True)
                    except Exception:
                        try:
                            process.terminate()
                        except Exception:
                            pass
            worker = self._worker
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=2.0)
        with self._lock:
            self.phase = "stopped"
            self.error_code = self.error_message = None
            self.process = None
            self.install_process = None
            self.port = None
            self._write_state()
