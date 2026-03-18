from __future__ import annotations

import threading
import unittest


class TestServeOps(unittest.TestCase):
    def test_start_supervisor_watchdog_sets_stop_event_when_supervisor_exits(self) -> None:
        from cccc.daemon import serve_ops

        stop_event = threading.Event()
        states = [True, False]

        def _pid_alive(_pid: int) -> bool:
            if states:
                return states.pop(0)
            return False

        thread = serve_ops.start_supervisor_watchdog_thread(
            stop_event=stop_event,
            supervisor_pid=4321,
            pid_alive=_pid_alive,
            interval_seconds=0.01,
        )

        self.assertIsNotNone(thread)
        assert thread is not None
        thread.join(timeout=1.0)
        self.assertTrue(stop_event.is_set())

    def test_start_supervisor_watchdog_returns_none_without_pid(self) -> None:
        from cccc.daemon import serve_ops

        stop_event = threading.Event()

        thread = serve_ops.start_supervisor_watchdog_thread(
            stop_event=stop_event,
            supervisor_pid=0,
            pid_alive=lambda _pid: True,
            interval_seconds=0.01,
        )

        self.assertIsNone(thread)


if __name__ == "__main__":
    unittest.main()