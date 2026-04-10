import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = Mock()
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.pid = 1234

    def poll(self):
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None


def _thread_with_order(order: list[str], label: str) -> Mock:
    thread = Mock()
    thread.start.side_effect = lambda: order.append(label)
    return thread


class TestHeadlessBootstrapControlTurns(unittest.TestCase):
    def test_claude_start_queues_bootstrap_before_turn_thread(self) -> None:
        from cccc.daemon.claude_app_sessions import ClaudeAppSession

        proc = _FakeProc()
        order: list[str] = []
        session = ClaudeAppSession(group_id="g_test", actor_id="peer1", cwd=Path("."), env={})
        threads = [
            _thread_with_order(order, "stdout"),
            _thread_with_order(order, "stderr"),
            _thread_with_order(order, "turn"),
        ]

        with (
            patch("cccc.daemon.claude_app_sessions.subprocess.Popen", return_value=proc) as popen,
            patch("cccc.daemon.claude_app_sessions.threading.Thread", side_effect=threads),
            patch("cccc.daemon.claude_app_sessions.time.sleep", return_value=None),
            patch.object(session, "_persist_state"),
            patch.object(session, "_queue_bootstrap_control_turn", side_effect=lambda: order.append("bootstrap") or True),
        ):
            session.start()

        self.assertEqual(order, ["stdout", "stderr", "bootstrap", "turn"])
        self.assertIn("--include-hook-events", popen.call_args.args[0])

    def test_codex_start_queues_bootstrap_before_turn_thread(self) -> None:
        from cccc.daemon.codex_app_sessions import CodexAppSession

        proc = _FakeProc()
        order: list[str] = []
        session = CodexAppSession(group_id="g_test", actor_id="peer1", cwd=Path("."), env={})
        threads = [
            _thread_with_order(order, "stdout"),
            _thread_with_order(order, "stderr"),
            _thread_with_order(order, "turn"),
        ]

        with (
            patch("cccc.daemon.codex_app_sessions.subprocess.Popen", return_value=proc),
            patch("cccc.daemon.codex_app_sessions.threading.Thread", side_effect=threads),
            patch.object(session, "_persist_state"),
            patch.object(session, "_emit"),
            patch.object(
                session,
                "_request",
                side_effect=[{}, {"thread": {"id": "thread-1"}}],
            ),
            patch.object(session, "_queue_bootstrap_control_turn", side_effect=lambda: order.append("bootstrap") or True),
        ):
            session.start()

        self.assertEqual(order, ["stdout", "stderr", "bootstrap", "turn"])


if __name__ == "__main__":
    unittest.main()