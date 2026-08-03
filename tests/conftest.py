from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


_SESSION_HOME: tempfile.TemporaryDirectory | None = None


@pytest.fixture(autouse=True)
def _computer_control_tests_use_windows_platform(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Core computer-control tests exercise the Windows-only implementation on any CI host."""

    if not Path(str(request.path)).name.startswith("test_computer_control"):
        return
    from no1.computer_control import platform_support

    monkeypatch.setattr(platform_support, "_platform_name", lambda: "win32")


def pytest_configure(config: pytest.Config) -> None:
    configured = str(os.environ.get("ONECOLLEAGUE_HOME") or os.environ.get("CCCC_HOME") or "").strip()
    if not configured:
        global _SESSION_HOME
        _SESSION_HOME = tempfile.TemporaryDirectory(prefix="onecolleague-pytest-")
        # Keep ONECOLLEAGUE_HOME unset so legacy tests can still override CCCC_HOME per test.
        os.environ["CCCC_HOME"] = _SESSION_HOME.name
        return

    resolved = Path(configured).expanduser().resolve()
    persistent_homes = {
        (Path.home() / ".cccc").resolve(),
        (Path.home() / ".onecolleague").resolve(),
    }
    if resolved in persistent_homes:
        raise pytest.UsageError(
            "refusing to run tests against a persistent service home; "
            "set ONECOLLEAGUE_HOME or CCCC_HOME to an isolated temporary directory"
        )
