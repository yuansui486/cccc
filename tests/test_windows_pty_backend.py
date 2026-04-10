import os
import queue
import time
import unittest
from collections import deque
from pathlib import Path


class _WakeSocket:
    def __init__(self) -> None:
        self._reads = 0

    def recv(self, _size: int) -> bytes:
        self._reads += 1
        return b"x" if self._reads == 1 else b""


class _NonReentrantLock:
    def __init__(self) -> None:
        self._held = False

    def __enter__(self):
        if self._held:
            raise AssertionError("lock re-entered")
        self._held = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._held = False
        return False


class TestWindowsPtyBackendInternals(unittest.TestCase):
    def test_on_wake_readable_does_not_reenter_session_lock(self) -> None:
        from cccc.runners.pty_win import PtySession

        session = object.__new__(PtySession)
        session._wake_r = _WakeSocket()
        session._attach_q = queue.Queue()
        session._output_q = queue.Queue()
        session._output_q.put(b"hello")
        session._lock = _NonReentrantLock()
        session._clients = {}
        session._running = True
        session._backlog = deque()
        session._backlog_bytes = 0
        session._first_output_at = None
        session._last_output_at = None
        session._max_backlog_bytes = 1024
        session._terminal_signal_buffer = ""
        session._runtime = "codex"
        session._terminal_override = None
        session._max_client_buffer_bytes = 0

        session._on_wake_readable()

        self.assertEqual(session.tail_output(max_bytes=32), b"hello")


@unittest.skipUnless(os.name == "nt", "Windows-only ConPTY backend check")
class TestWindowsPtyBackend(unittest.TestCase):
    def test_windows_pty_backend_is_available(self) -> None:
        from cccc.runners import pty as pty_runner

        self.assertTrue(
            bool(getattr(pty_runner, "PTY_SUPPORTED", False)),
            msg="Windows PTY backend unavailable (expected ConPTY via pywinpty)",
        )

    def test_conpty_session_smoke_echo_output(self) -> None:
        from cccc.runners import pty as pty_runner

        self.assertTrue(bool(getattr(pty_runner, "PTY_SUPPORTED", False)))

        session = pty_runner.PtySession(
            group_id="g_win",
            actor_id="a_win",
            cwd=Path.cwd(),
            command=["cmd.exe", "/c", "echo", "CCCC_CONPTY_OK"],
            env={},
        )
        try:
            deadline = time.time() + 8.0
            output = b""
            while time.time() < deadline:
                output = session.tail_output(max_bytes=200_000)
                if b"CCCC_CONPTY_OK" in output:
                    break
                if not session.is_running() and output:
                    break
                time.sleep(0.1)
            self.assertIn(
                b"CCCC_CONPTY_OK",
                output,
                msg=f"ConPTY session did not emit expected echo output. tail={output[-200:]}",
            )
        finally:
            session.stop()


if __name__ == "__main__":
    unittest.main()
