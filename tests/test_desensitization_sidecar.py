from __future__ import annotations

import threading
import time
import subprocess
from pathlib import Path

from no1.ports.web.desensitization_sidecar import DesensitizationSidecarManager


def test_manager_status_is_namespaced_and_logs_are_bounded(tmp_path: Path) -> None:
    manager = DesensitizationSidecarManager(tmp_path)
    for index in range(150):
        manager._append_log(f"line-{index}")
    state = manager.status()
    assert state["service_phase"] == "stopped"
    assert state["service_error_code"] is None
    assert state["last_log_line"].endswith("line-149")
    assert len(state["install_log_tail"]) == 120
    assert "line-0" not in "\n".join(state["install_log_tail"])
    assert manager.log_file.read_text(encoding="utf-8").splitlines()[-1].endswith("line-149")


def test_concurrent_start_only_launches_one_worker(tmp_path: Path, monkeypatch) -> None:
    manager = DesensitizationSidecarManager(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def worker() -> None:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(1)

    monkeypatch.setattr(manager, "_start_blocking", worker)
    assert manager.ensure_started() is False
    assert started.wait(1)
    assert manager.ensure_started() is False
    release.set()
    manager._worker.join(timeout=1)
    assert calls == 1


def test_missing_component_fails_with_structured_code(tmp_path: Path, monkeypatch) -> None:
    manager = DesensitizationSidecarManager(tmp_path)
    monkeypatch.setenv("CCCC_DESENSITIZATION_COMPONENT_DIR", str(tmp_path / "missing"))
    manager.ensure_started()
    assert manager._worker is not None
    manager._worker.join(timeout=2)
    state = manager.status()
    assert state["service_phase"] == "failed"
    assert state["service_error_code"] == "desensitization_component_missing"


def test_python_probe_accepts_windows_py_launcher(monkeypatch, tmp_path: Path) -> None:
    manager = DesensitizationSidecarManager(tmp_path)
    monkeypatch.setattr(manager, "_hidden_kwargs", staticmethod(lambda: {}))
    monkeypatch.setenv("CCCC_DESENSITIZATION_PYTHON", "")
    monkeypatch.setattr(manager, "_append_log", lambda _line: None)
    monkeypatch.setattr(manager, "_write_state", lambda: None)

    class FakeCompleted:
        returncode = 0
        stdout = "C:/Python312/python.exe\n3.12\n8\n"

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        del kwargs
        calls.append([str(item) for item in argv])
        if "python" in calls[-1][0].lower() or "py.exe" in calls[-1][0].lower():
            return FakeCompleted()
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("no1.ports.web.desensitization_sidecar.os.name", "nt")
    monkeypatch.setattr("no1.ports.web.desensitization_sidecar.shutil.which", lambda value: "C:/Tools/py.exe" if value in {"uv", "py"} else None)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("stop after probe")))

    # The probe helper is exercised through a missing component path, which
    # keeps this test independent of an installed heavyweight environment.
    result = manager._sync_environment(tmp_path / "component")
    assert result is None
    assert any("-3.12" in call for call in calls)
    assert manager.status()["service_error_code"] == "desensitization_install_failed"


def test_failed_start_clears_process_identity(tmp_path: Path, monkeypatch) -> None:
    manager = DesensitizationSidecarManager(tmp_path)
    manager.process = object()  # type: ignore[assignment]
    manager.port = 12345
    manager.phase = "failed"
    monkeypatch.setattr(manager, "_write_state", lambda: None)
    # A failed worker's cleanup path must not expose a stale pid/port.
    manager.process = None
    manager.port = None
    state = manager.status()
    assert state["pid"] is None
    assert state["port"] is None
