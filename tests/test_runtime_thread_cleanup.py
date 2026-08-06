import tempfile
import threading
import time
import unittest
from pathlib import Path


class _FakeProc:
    pid = 12345
    stdin = None

    def __init__(self) -> None:
        self.released = threading.Event()

    def poll(self):
        return None

    def terminate(self) -> None:
        self.released.set()

    def wait(self, timeout=None):
        return 0

    def kill(self) -> None:
        self.released.set()


def _delayed_worker(proc: _FakeProc, started: threading.Event) -> None:
    started.set()
    proc.released.wait(timeout=1)
    time.sleep(0.2)


class TestRuntimeThreadCleanup(unittest.TestCase):
    def test_codex_stop_waits_for_worker_threads_to_exit(self) -> None:
        from no1.daemon.codex_app_sessions import CodexAppSession

        with tempfile.TemporaryDirectory() as td:
            session = CodexAppSession(group_id="g_cleanup", actor_id="peer1", cwd=Path(td), env={})
            proc = _FakeProc()
            worker_started = threading.Event()
            thread = threading.Thread(target=_delayed_worker, args=(proc, worker_started), name="test-codex-worker")
            thread.start()
            self.assertTrue(worker_started.wait(timeout=1))
            with session._lock:
                session._proc = proc  # type: ignore[assignment]
                session._running = True
                session._stdout_thread = thread

            session.stop()

            self.assertFalse(thread.is_alive())

    def test_claude_stop_waits_for_worker_threads_to_exit(self) -> None:
        from no1.daemon.claude_app_sessions import ClaudeAppSession

        with tempfile.TemporaryDirectory() as td:
            session = ClaudeAppSession(group_id="g_cleanup", actor_id="peer1", cwd=Path(td), env={})
            proc = _FakeProc()
            worker_started = threading.Event()
            thread = threading.Thread(target=_delayed_worker, args=(proc, worker_started), name="test-claude-worker")
            thread.start()
            self.assertTrue(worker_started.wait(timeout=1))
            with session._lock:
                session._proc = proc  # type: ignore[assignment]
                session._running = True
                session._stdout_thread = thread

            session.stop()

            self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
