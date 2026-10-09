import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from no1.daemon.messaging import delivery
from no1.runners import pty_win


def session_for_test(runtime="codex"):
    session = object.__new__(pty_win.PtySession)
    session.group_id = "g-test"
    session.actor_id = "actor"
    session._runtime = runtime
    session._lock = threading.Lock()
    session._mode_tail = b""
    session._bracketed_paste = False
    session._bracketed_paste_changed_at = None
    session._win32_input_mode = False
    session._proc = SimpleNamespace(write=Mock(side_effect=len))
    return session


@pytest.mark.parametrize("split", range(1, 8))
def test_win32_negotiation_handles_split_sequences_and_disable(split):
    session = session_for_test()
    enable = b"\x1b[?9001h"
    session._update_input_modes(enable[:split])
    session._update_input_modes(enable[split:] + b"\x1b[?2004h")
    assert session._bracketed_paste
    assert session.submit_input()
    session._proc.write.assert_called_once_with(pty_win._WIN32_ENTER)

    session._update_input_modes(b"\x1b[?9001l\x1b[?9001h\x1b[?9001l")
    assert session.submit_input()
    assert session._proc.write.call_args.args == ("\r",)
    assert session._bracketed_paste


@pytest.mark.parametrize("runtime,enabled,mode,expected", [
    ("codex", True, "enter", pty_win._WIN32_ENTER),
    ("codex", False, "enter", "\r"),
    ("claude", True, "enter", "\r"),
    ("codex", True, "newline", "\n"),
    ("codex", True, "none", None),
])
def test_submit_preserves_runtime_and_config_semantics(runtime, enabled, mode, expected):
    session = session_for_test(runtime)
    session._win32_input_mode = enabled
    assert session.submit_input(mode)
    if expected is None:
        session._proc.write.assert_not_called()
    else:
        session._proc.write.assert_called_once_with(expected)


@pytest.mark.parametrize("result", [-1, 1, None, False, OSError("closed")])
def test_uncertain_submit_is_not_retried(result):
    session = session_for_test()
    session._win32_input_mode = True
    session._proc.write = Mock(side_effect=result if isinstance(result, Exception) else None, return_value=result)
    assert not session.submit_input()
    session._proc.write.assert_called_once_with(pty_win._WIN32_ENTER)


def test_conpty_zero_success_result_is_accepted():
    session = session_for_test()
    session._proc.write = Mock(return_value=0)
    assert session.submit_input()
    session._proc.write.assert_called_once_with("\r")


def test_manual_input_is_unchanged_after_negotiation():
    session = session_for_test()
    session._win32_input_mode = True
    assert session.write_input(b"\r")
    session._proc.write.assert_called_once_with("\r")


def test_supervisor_does_not_submit_to_stopped_session():
    supervisor = object.__new__(pty_win.PtySupervisor)
    supervisor._lock = threading.Lock()
    session = SimpleNamespace(is_running=lambda: False, submit_input=Mock())
    supervisor._sessions = {("g", "a"): session}
    assert not supervisor.submit_input(group_id="g", actor_id="a")
    assert not supervisor.submit_input(group_id="g", actor_id="missing")
    session.submit_input.assert_not_called()


@pytest.fixture
def transport():
    supervisor = Mock()
    supervisor.actor_running.return_value = True
    supervisor.bracketed_paste_enabled.return_value = True
    supervisor.write_input.return_value = True
    supervisor.submit_input.return_value = True
    with patch.object(delivery.pty_runner, "SUPERVISOR", supervisor), patch.object(
        delivery, "find_actor", return_value={"submit": "enter"}
    ) as actor, patch.object(delivery, "PTY_SUBMIT_DELAY_SECONDS", 0):
        yield supervisor, actor


@pytest.mark.parametrize("mode", ["enter", "newline", "none"])
@pytest.mark.parametrize("detailed", [True, False])
def test_delivery_separates_paste_and_submit(transport, mode, detailed):
    supervisor, actor = transport
    actor.return_value = {"submit": mode}
    group = SimpleNamespace(group_id="g")
    result = delivery.pty_submit_text(
        group, actor_id="a", text="你好\n第二行", wait_for_submit=True, detailed_result=detailed,
    )
    assert result.accepted if detailed else result
    supervisor.write_input.assert_called_once_with(
        group_id="g", actor_id="a", data=b"\x1b[200~" + "你好\n第二行".encode() + b"\x1b[201~",
    )
    if mode == "none":
        supervisor.submit_input.assert_not_called()
    else:
        supervisor.submit_input.assert_called_once_with(group_id="g", actor_id="a", mode=mode)


@pytest.mark.parametrize("failure", ["payload", "submit", "exception", "stopped"])
def test_delivery_reports_uncertainty_without_replaying_payload(transport, failure):
    supervisor, _actor = transport
    if failure == "payload":
        supervisor.write_input.return_value = False
    elif failure == "submit":
        supervisor.submit_input.return_value = False
    elif failure == "exception":
        supervisor.submit_input.side_effect = OSError("closed")
    else:
        supervisor.actor_running.side_effect = [True, False]
    result = delivery.pty_submit_text(SimpleNamespace(group_id="g"), actor_id="a", text="hello", detailed_result=True)
    assert not result.accepted
    assert not result.retryable
    assert result.phase == ("payload_write_unknown" if failure == "payload" else "submit_write_unknown")
    supervisor.write_input.assert_called_once()
    assert supervisor.submit_input.call_count == (0 if failure in {"payload", "stopped"} else 1)


def test_background_delivery_uses_submit_operation_and_catches_failure(transport):
    supervisor, _actor = transport
    # Run the worker inline to deterministically exercise its exception handling.
    with patch.object(delivery.threading, "Thread", side_effect=lambda *, target, **kw: SimpleNamespace(start=target)):
        supervisor.submit_input.side_effect = OSError("closed")
        assert delivery.pty_submit_text(SimpleNamespace(group_id="g"), actor_id="a", text="hello")
    supervisor.write_input.assert_called_once()
    supervisor.submit_input.assert_called_once_with(group_id="g", actor_id="a", mode="enter")
