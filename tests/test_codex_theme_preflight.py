from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from no1.daemon.codex_app_sessions import CodexAppSession, CodexRequestRejectedError


@pytest.mark.parametrize("unmaterialized", [True, False])
@pytest.mark.parametrize("history_mode", ["legacy", "paginated"])
def test_theme_checks_history_before_stopping_tui(unmaterialized, history_mode):
    session = CodexAppSession(group_id="g_test", actor_id="peer", cwd=Path("."), env={},
                              start_remote_tui=True, persist_headless_state=False)
    session._running = True
    session._proc = SimpleNamespace(pid=1, poll=lambda: None)
    session._pty_session = SimpleNamespace(pid=2)
    session._session_state.thread_id = "thread-test"
    calls = []

    def request(method, params, **kwargs):
        calls.append("probe")
        if params.get("includeTurns") or method == "thread/turns/list":
            raise CodexRequestRejectedError(method=method, code=-32600, message=(
                "thread is not materialized yet; includeTurns is unavailable before first user message"
                if unmaterialized else "no rollout found for thread id thread-test"))
        return {"thread": {"id": "thread-test", "historyMode": history_mode, "status": {"type": "idle"}}}

    with patch.object(session, "_request", side_effect=request), patch(
        "no1.daemon.codex_app_sessions.pty_runner.SUPERVISOR.stop_actor",
        side_effect=lambda **kwargs: calls.append("stop"),
    ) as stop, patch.object(session, "_start_remote_tui", return_value=SimpleNamespace(pid=3)) as start, patch(
        "no1.daemon.codex_app_sessions.write_pty_state"
    ):
        assert session.refresh_remote_tui_theme("light") is unmaterialized
        if unmaterialized:
            assert calls == ["probe", "probe", "stop"]
            assert start.call_args.kwargs["resume_thread_id"] == ""
            assert session._session_state.thread_id is None
        else:
            stop.assert_not_called()
            start.assert_not_called()
            assert session._session_state.thread_id == "thread-test"
