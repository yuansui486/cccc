import sys
import os
import queue
import selectors
import socket
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch


class TestPtyHistoryPage(unittest.TestCase):
    def _session(self, pty_runner=None):
        if pty_runner is None:
            from no1.runners import pty as pty_runner

        session = pty_runner.PtySession.__new__(pty_runner.PtySession)
        session.group_id = "g1"
        session.actor_id = "a1"
        session._runtime = "codex"
        session._lock = threading.Lock()
        session._backlog = deque()
        session._backlog_bytes = 0
        session._max_backlog_bytes = 10
        session._first_output_at = None
        session._last_output_at = None
        session._terminal_signal_buffer = ""
        session._terminal_override = None
        session._mode_tail = b""
        session._query_tail = b""
        session._bracketed_paste = False
        session._bracketed_paste_changed_at = None
        session._clients = {}
        session._attach_reservations = {}
        session._accepting_attaches = True
        session._writer_fd = None
        session._attach_q = queue.Queue()
        session._selector = selectors.DefaultSelector()
        session._max_client_buffer_bytes = 1_000
        session._cmd_w = -1
        if pty_runner.__name__.endswith("pty_win"):
            session._notify_wake = lambda: None
        return session

    @staticmethod
    def _runner_modules():
        from no1.runners import pty as posix_pty
        from no1.runners import pty_win as windows_pty

        return (posix_pty, windows_pty)

    def _activate(self, session, reservation) -> None:
        with patch.object(os, "write", return_value=1):
            self.assertTrue(reservation.activate())
        session._activate_client_now(reservation)

    def test_attach_reservation_reports_actual_writer_ownership(self) -> None:
        session = self._session()
        first, first_peer = socket.socketpair()
        second, second_peer = socket.socketpair()
        try:
            first_reservation = session.reserve_attach_client(first, mode="control")
            self.assertTrue(first_reservation.metadata["writable"])
            self.assertFalse(first_reservation.metadata["writer_replaced"])
            self._activate(session, first_reservation)

            second_reservation = session.reserve_attach_client(second, mode="control", takeover=False)
            self.assertFalse(second_reservation.metadata["writable"])
            self.assertFalse(second_reservation.metadata["writer_replaced"])
            second_reservation.cancel()
        finally:
            session.detach_client(first.fileno())
            first_peer.close()
            second_peer.close()
            session._selector.close()

    def test_takeover_without_existing_writer_does_not_report_replaced(self) -> None:
        session = self._session()
        sock, peer = socket.socketpair()
        try:
            reservation = session.reserve_attach_client(sock, mode="control", takeover=True)
            self.assertTrue(reservation.metadata["writable"])
            self.assertFalse(reservation.metadata["writer_replaced"])
            reservation.cancel()
        finally:
            peer.close()
            session._selector.close()

    def test_attach_reservation_clamps_future_cursor_to_backlog_end(self) -> None:
        session = self._session()
        session._append_backlog(b"abcde")
        sock, peer = socket.socketpair()
        try:
            reservation = session.reserve_attach_client(sock, since=999, mode="viewer")
            self.assertEqual(reservation.metadata["replay_cursor"], 5)
            self.assertEqual(reservation.metadata["backlog_end_cursor"], 5)
            self.assertEqual(session._clients[sock.fileno()].outbuf, b"")
            reservation.cancel()
        finally:
            peer.close()
            session._selector.close()

    def test_cancel_is_idempotent_and_rolls_back_writer(self) -> None:
        session = self._session()
        sock, peer = socket.socketpair()
        fileno = sock.fileno()
        try:
            reservation = session.reserve_attach_client(sock, mode="control")
            reservation.cancel()
            reservation.cancel()
            self.assertFalse(reservation.activate())
            self.assertNotIn(fileno, session._clients)
            self.assertNotIn(fileno, session._attach_reservations)
            self.assertIsNone(session._writer_fd)
        finally:
            peer.close()
            session._selector.close()

    def test_output_between_reserve_and_activate_is_delivered(self) -> None:
        session = self._session()
        session._append_backlog(b"old")
        sock, peer = socket.socketpair()
        try:
            reservation = session.reserve_attach_client(sock, mode="viewer")
            session._append_backlog(b"new")
            session._queue_output_for_clients(b"new")
            self.assertEqual(session._clients[sock.fileno()].outbuf, b"oldnew")
            self._activate(session, reservation)
            session._on_client_writable(sock.fileno())
            self.assertEqual(peer.recv(16), b"oldnew")
        finally:
            session.detach_client(sock.fileno())
            peer.close()
            session._selector.close()

    def test_close_gate_rejects_late_reservation(self) -> None:
        session = self._session()
        session._begin_attach_shutdown()
        sock, peer = socket.socketpair()
        try:
            with self.assertRaisesRegex(RuntimeError, "closing"):
                session.reserve_attach_client(sock)
            self.assertEqual(session._clients, {})
            self.assertEqual(session._attach_reservations, {})
            self.assertIsNone(session._writer_fd)
        finally:
            sock.close()
            peer.close()
            session._selector.close()

    def test_activation_linearizes_before_cancel_during_selector_registration(self) -> None:
        session = self._session()
        sock, peer = socket.socketpair()
        fileno = sock.fileno()
        reservation = session.reserve_attach_client(sock, mode="control")
        entered = threading.Event()
        release = threading.Event()
        real_register = session._selector.register

        def blocking_register(*args, **kwargs):
            entered.set()
            release.wait(timeout=2)
            return real_register(*args, **kwargs)

        with patch.object(os, "write", return_value=1), patch.object(
            session._selector, "register", side_effect=blocking_register
        ):
            self.assertTrue(reservation.activate())
            activating = threading.Thread(target=session._activate_client_now, args=(reservation,))
            activating.start()
            self.assertTrue(entered.wait(timeout=1))
            cancelling = threading.Thread(target=reservation.cancel)
            cancelling.start()
            release.set()
            activating.join(timeout=1)
            cancelling.join(timeout=1)

        try:
            key = session._selector.get_key(fileno)
            client = session._clients[fileno]
            self.assertIs(key.fileobj, client.sock)
            self.assertTrue(client.active)
            self.assertTrue(client.writer)
            self.assertTrue(client.writer_lease)
            self.assertEqual(session._writer_fd, fileno)
            self.assertEqual(reservation._state, "active")
        finally:
            session.detach_client(fileno)
            peer.close()
            session._selector.close()

    def test_reserve_and_close_are_atomic_across_platforms(self) -> None:
        for pty_runner in self._runner_modules():
            with self.subTest(module=pty_runner.__name__):
                session = self._session(pty_runner)
                sock, peer = socket.socketpair()
                outcome = []
                session._lock.acquire()

                def reserve() -> None:
                    try:
                        outcome.append(session.reserve_attach_client(sock, mode="control"))
                    except RuntimeError:
                        outcome.append(None)

                reserve_thread = threading.Thread(target=reserve)
                close_thread = threading.Thread(target=session._begin_attach_shutdown)
                reserve_thread.start()
                close_thread.start()
                session._lock.release()
                reserve_thread.join(timeout=1)
                close_thread.join(timeout=1)

                try:
                    self.assertFalse(session._accepting_attaches)
                    self.assertEqual(session._clients, {})
                    self.assertEqual(session._attach_reservations, {})
                    self.assertIsNone(session._writer_fd)
                    if outcome and outcome[0] is not None:
                        self.assertEqual(outcome[0]._state, "cancelled")
                finally:
                    sock.close()
                    peer.close()
                    session._selector.close()

    def test_previous_writer_fd_reuse_cannot_promote_viewer_across_platforms(self) -> None:
        for pty_runner in self._runner_modules():
            with self.subTest(module=pty_runner.__name__):
                session = self._session(pty_runner)
                old_sock, old_peer = socket.socketpair()
                takeover_sock, takeover_peer = socket.socketpair()
                viewer_sock, viewer_peer = socket.socketpair()
                previous_fd = old_sock.fileno()
                previous = pty_runner._PtyClient(
                    sock=old_sock,
                    control=True,
                    writer=True,
                    outbuf=bytearray(),
                    active=True,
                )
                session._clients[previous_fd] = previous
                session._writer_fd = previous_fd
                reservation = session.reserve_attach_client(takeover_sock, mode="control", takeover=True)
                reused_viewer = pty_runner._PtyClient(
                    sock=viewer_sock,
                    control=False,
                    writer=False,
                    outbuf=bytearray(),
                    active=True,
                )
                session._clients[previous_fd] = reused_viewer

                reservation.cancel()

                try:
                    self.assertIs(session._clients.get(previous_fd), reused_viewer)
                    self.assertFalse(reused_viewer.writer)
                    self.assertIsNone(session._writer_fd)
                finally:
                    session._clients.clear()
                    old_sock.close()
                    old_peer.close()
                    takeover_peer.close()
                    viewer_sock.close()
                    viewer_peer.close()
                    session._selector.close()

    def test_stale_reservation_cannot_touch_reused_client_across_platforms(self) -> None:
        from no1.runners.pty_attach import PtyAttachReservation

        for pty_runner in self._runner_modules():
            with self.subTest(module=pty_runner.__name__):
                session = self._session(pty_runner)
                old_sock, old_peer = socket.socketpair()
                new_sock, new_peer = socket.socketpair()
                fileno = old_sock.fileno()
                stale = session.reserve_attach_client(old_sock, mode="viewer")
                replacement = pty_runner._PtyClient(
                    sock=new_sock,
                    control=False,
                    writer=False,
                    outbuf=bytearray(b"new"),
                    active=False,
                )
                current = PtyAttachReservation(
                    session=session,
                    fileno=fileno,
                    reserved_client=replacement,
                    previous_writer_fd=None,
                    previous_writer_client=None,
                    metadata={"mode": "viewer"},
                )
                session._clients[fileno] = replacement
                session._attach_reservations[fileno] = current

                self.assertFalse(stale.activate())
                stale.cancel()

                try:
                    self.assertIs(session._clients.get(fileno), replacement)
                    self.assertIs(session._attach_reservations.get(fileno), current)
                    self.assertEqual(replacement.outbuf, b"new")
                finally:
                    current.cancel()
                    old_peer.close()
                    new_peer.close()
                    session._selector.close()

    def test_history_page_uses_absolute_byte_cursors(self) -> None:
        session = self._session()
        session._append_backlog(b"abcde")
        session._append_backlog(b"fghij")

        page = session.history_page(limit_bytes=4)

        self.assertEqual(page["data"], b"ghij")
        self.assertEqual(page["start_cursor"], 6)
        self.assertEqual(page["end_cursor"], 10)
        self.assertEqual(page["has_more"], True)
        self.assertEqual(page["cursor_expired"], False)

    def test_history_page_before_cursor_returns_older_slice(self) -> None:
        session = self._session()
        session._append_backlog(b"abcde")
        session._append_backlog(b"fghij")

        page = session.history_page(before=6, limit_bytes=3)

        self.assertEqual(page["data"], b"def")
        self.assertEqual(page["start_cursor"], 3)
        self.assertEqual(page["end_cursor"], 6)
        self.assertEqual(page["has_more"], True)

    def test_history_page_reports_expired_cursor_after_backlog_drop(self) -> None:
        session = self._session()
        session._append_backlog(b"abcde")
        session._append_backlog(b"fghij")
        session._append_backlog(b"klmno")

        page = session.history_page(before=4, limit_bytes=3)

        self.assertEqual(page["data"], b"")
        self.assertEqual(page["start_cursor"], 5)
        self.assertEqual(page["end_cursor"], 5)
        self.assertEqual(page["has_more"], False)
        self.assertEqual(page["cursor_expired"], True)

    def test_history_since_returns_backlog_after_cursor_without_duplicates(self) -> None:
        session = self._session()
        session._append_backlog(b"abcde")
        session._append_backlog(b"fghij")

        self.assertEqual(session.history_since(5), b"fghij")
        self.assertEqual(session.history_since(10), b"")

    def test_supervisor_keeps_tail_output_after_session_exit(self) -> None:
        from no1.runners import pty as pty_runner

        supervisor = pty_runner.PtySupervisor()
        session = self._session()

        with supervisor._lock:
            supervisor._sessions[(session.group_id, session.actor_id)] = session
        session._append_backlog(b"failed\n")

        supervisor._on_session_exit(session)

        self.assertFalse(supervisor.actor_running("g1", "a1"))
        self.assertEqual(supervisor.tail_output(group_id="g1", actor_id="a1", max_bytes=200), b"failed\n")

    def test_supervisor_keeps_exit_code_when_stopped_session_has_no_output(self) -> None:
        from no1.runners import pty as pty_runner

        class FakeProc:
            def poll(self) -> int:
                return 7

        supervisor = pty_runner.PtySupervisor()
        session = self._session()
        session._proc = FakeProc()

        with supervisor._lock:
            supervisor._sessions[(session.group_id, session.actor_id)] = session

        supervisor._on_session_exit(session)

        output = supervisor.tail_output(group_id="g1", actor_id="a1", max_bytes=200).decode("utf-8")
        self.assertIn("Process exited with code 7", output)

    def test_supervisor_limits_each_exit_snapshot_to_256_kib_tail(self) -> None:
        from no1.runners import pty as pty_runner

        snapshot_limit = 256 * 1024
        supervisor = pty_runner.PtySupervisor()
        session = self._session()
        retained_tail = b"x" * snapshot_limit
        output = b"discarded-prefix" + retained_tail
        session._max_backlog_bytes = len(output) + 1

        with supervisor._lock:
            supervisor._sessions[(session.group_id, session.actor_id)] = session
        session._append_backlog(output)

        supervisor._on_session_exit(session)

        self.assertEqual(
            supervisor.tail_output(group_id="g1", actor_id="a1", max_bytes=len(output)),
            retained_tail,
        )
        expired_page = supervisor.history_page(
            group_id="g1",
            actor_id="a1",
            before=len(b"discarded-prefix") - 1,
            limit_bytes=100,
        )
        self.assertEqual(expired_page["start_cursor"], len(b"discarded-prefix"))
        self.assertTrue(expired_page["cursor_expired"])

    def test_supervisor_limits_exit_snapshot_cache_to_8_mib(self) -> None:
        from no1.runners import pty as pty_runner

        snapshot_size = 256 * 1024
        supervisor = pty_runner.PtySupervisor()
        output = b"x" * snapshot_size

        for index in range(33):
            session = self._session()
            session.actor_id = f"a{index}"
            session._max_backlog_bytes = snapshot_size + 1
            with supervisor._lock:
                supervisor._sessions[(session.group_id, session.actor_id)] = session
            session._append_backlog(output)
            supervisor._on_session_exit(session)

        self.assertEqual(supervisor.tail_output(group_id="g1", actor_id="a0", max_bytes=1), b"")
        self.assertEqual(supervisor.tail_output(group_id="g1", actor_id="a1", max_bytes=1), b"x")
        self.assertEqual(supervisor.tail_output(group_id="g1", actor_id="a32", max_bytes=1), b"x")

    def _assert_stop_keeps_final_output(self, stop) -> None:
        from no1.runners import pty as pty_runner

        supervisor = pty_runner.PtySupervisor()
        session = self._session()
        session._max_backlog_bytes = 1_000
        session._append_backlog(b"before stop\n")

        def stop_with_exit_callback() -> None:
            session._append_backlog(b"during stop\n")
            supervisor._on_session_exit(session)
            session._append_backlog(b"after exit callback\n")

        session.stop = stop_with_exit_callback
        with supervisor._lock:
            supervisor._sessions[(session.group_id, session.actor_id)] = session

        stop(supervisor, session)

        self.assertEqual(
            supervisor.tail_output(group_id=session.group_id, actor_id=session.actor_id, max_bytes=1_000),
            b"before stop\nduring stop\nafter exit callback\n",
        )

    def test_stop_actor_snapshots_after_stop_and_exit_callback(self) -> None:
        self._assert_stop_keeps_final_output(
            lambda supervisor, session: supervisor.stop_actor(
                group_id=session.group_id,
                actor_id=session.actor_id,
            )
        )

    def test_stop_group_snapshots_after_stop_and_exit_callback(self) -> None:
        self._assert_stop_keeps_final_output(
            lambda supervisor, session: supervisor.stop_group(group_id=session.group_id)
        )

    def test_stop_all_snapshots_after_stop_and_exit_callback(self) -> None:
        self._assert_stop_keeps_final_output(lambda supervisor, session: supervisor.stop_all())

    def test_session_stop_waits_for_reader_thread_to_drain_output(self) -> None:
        from no1.runners import pty as pty_runner

        class ExitedProc:
            pid = 123

            def poll(self) -> int:
                return 0

        session = self._session()
        session._max_backlog_bytes = 1_000
        session._proc = ExitedProc()
        session._master_fd = -1
        session._running = True

        class ReaderThread:
            def join(self, timeout=None) -> None:
                session._append_backlog(b"final reader output\n")

            def is_alive(self) -> bool:
                return False

        session._thread = ReaderThread()

        with patch.object(pty_runner, "_best_effort_killpg"):
            session.stop()

        self.assertEqual(session.tail_output(max_bytes=1_000), b"final reader output\n")

    def test_stop_actor_drains_sigterm_output_before_snapshot(self) -> None:
        from no1.runners import pty as pty_runner

        supervisor = pty_runner.PtySupervisor()
        script = (
            "import os, signal\n"
            "def stop(_signum, _frame):\n"
            "    os.write(1, b'termination marker\\n')\n"
            "    os._exit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "os.write(1, b'ready\\n')\n"
            "signal.pause()\n"
        )
        session = supervisor.start_actor(
            group_id="g-real",
            actor_id="a-real",
            cwd=Path.cwd(),
            command=[sys.executable, "-u", "-c", script],
            env={},
            max_backlog_bytes=10_000,
        )
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if b"ready" in session.tail_output(max_bytes=1_000):
                break
            time.sleep(0.01)
        self.assertIn(b"ready", session.tail_output(max_bytes=1_000))

        supervisor.stop_actor(group_id="g-real", actor_id="a-real")

        self.assertIn(
            b"termination marker",
            supervisor.tail_output(group_id="g-real", actor_id="a-real", max_bytes=1_000),
        )

    def test_start_actor_registers_before_exit_callback_enters_blocking_hook(self) -> None:
        from no1.runners import pty as pty_runner

        hook_entered = threading.Event()
        release_hook = threading.Event()

        class AliveReader:
            def join(self, timeout=None) -> None:
                pass

            def is_alive(self) -> bool:
                return True

        class ImmediatelyExitedSession:
            def __init__(self, **kwargs) -> None:
                self.group_id = kwargs["group_id"]
                self.actor_id = kwargs["actor_id"]
                self._thread = AliveReader()
                self._exit_thread = threading.Thread(target=kwargs["on_exit"], args=(self,))
                self._exit_thread.start()
                hook_entered.wait(timeout=0.1)

            def is_running(self) -> bool:
                return False

            def _backlog_snapshot(self):
                return b"fast exit\n", 0, len(b"fast exit\n")

            def returncode(self) -> int:
                return 1

        supervisor = pty_runner.PtySupervisor()

        def blocking_exit_hook(_exited_session) -> None:
            hook_entered.set()
            release_hook.wait(timeout=2.0)

        supervisor.set_exit_hook(blocking_exit_hook)
        with patch.object(pty_runner, "PtySession", ImmediatelyExitedSession):
            session = supervisor.start_actor(
                group_id="g-fast",
                actor_id="a-fast",
                cwd=Path.cwd(),
                command=["unused"],
                env={},
            )

        try:
            self.assertTrue(hook_entered.wait(timeout=1.0))
            self.assertFalse(supervisor.actor_running("g-fast", "a-fast"))
            self.assertEqual(
                supervisor.tail_output(group_id="g-fast", actor_id="a-fast", max_bytes=1_000),
                b"fast exit\n",
            )
            with supervisor._lock:
                self.assertNotIn(("g-fast", "a-fast"), supervisor._sessions)
        finally:
            release_hook.set()
            session._exit_thread.join(timeout=1.0)

    def test_only_current_takeover_writer_lease_can_resize_across_platforms(self) -> None:
        for pty_runner in self._runner_modules():
            with self.subTest(module=pty_runner.__name__):
                session = self._session(pty_runner)
                session.resize = Mock()
                first_sock, first_peer = socket.socketpair()
                second_sock, second_peer = socket.socketpair()
                third_sock, third_peer = socket.socketpair()
                try:
                    first = session.reserve_attach_client(first_sock, mode="control")
                    self._activate(session, first)
                    first_lease = str(first.metadata.get("writer_lease") or "")
                    self.assertTrue(first_lease)
                    self.assertTrue(session.resize_if_writer(writer_lease=first_lease, cols=100, rows=30))

                    second = session.reserve_attach_client(second_sock, mode="control", takeover=True)
                    self._activate(session, second)
                    second_lease = str(second.metadata.get("writer_lease") or "")
                    self.assertTrue(second_lease)
                    self.assertFalse(session.resize_if_writer(writer_lease=first_lease, cols=101, rows=31))

                    third = session.reserve_attach_client(third_sock, mode="control", takeover=True)
                    self._activate(session, third)
                    third_lease = str(third.metadata.get("writer_lease") or "")
                    self.assertTrue(third_lease)
                    self.assertNotEqual(second_lease, third_lease)
                    self.assertFalse(session.resize_if_writer(writer_lease="", cols=102, rows=32))
                    self.assertFalse(session.resize_if_writer(writer_lease="wrong", cols=102, rows=32))
                    self.assertFalse(session.resize_if_writer(writer_lease=second_lease, cols=102, rows=32))
                    self.assertTrue(session.resize_if_writer(writer_lease=third_lease, cols=103, rows=33))
                    self.assertEqual(session.resize.call_count, 2)
                finally:
                    for sock in (first_sock, second_sock, third_sock):
                        session.detach_client(sock.fileno())
                    for peer in (first_peer, second_peer, third_peer):
                        peer.close()
                    session._selector.close()

    def test_cancel_after_activation_preserves_client_writer_and_lease_across_platforms(self) -> None:
        for pty_runner in self._runner_modules():
            with self.subTest(module=pty_runner.__name__):
                session = self._session(pty_runner)
                sock, peer = socket.socketpair()
                try:
                    reservation = session.reserve_attach_client(sock, mode="control")
                    self._activate(session, reservation)
                    fileno = sock.fileno()
                    client = session._clients[fileno]
                    lease = client.writer_lease

                    reservation.cancel()
                    reservation.cancel()

                    self.assertIs(session._clients.get(fileno), client)
                    self.assertEqual(session._writer_fd, fileno)
                    self.assertTrue(client.active)
                    self.assertTrue(client.writer)
                    self.assertEqual(client.writer_lease, lease)
                    self.assertEqual(reservation._state, "active")
                finally:
                    session.detach_client(sock.fileno())
                    peer.close()
                    session._selector.close()

    def test_term_resize_requires_current_writer_lease(self) -> None:
        from no1.daemon.ops import maintenance_ops

        base_args = {"group_id": "g1", "actor_id": "a1", "cols": 100, "rows": 30}
        with patch.object(maintenance_ops, "load_group", return_value={"group_id": "g1"}), patch.object(
            maintenance_ops.pty_runner.SUPERVISOR,
            "resize_if_writer",
            side_effect=lambda **kwargs: kwargs.get("writer_lease") == "current",
        ) as resize_if_writer:
            missing = maintenance_ops.handle_term_resize(base_args)
            wrong = maintenance_ops.handle_term_resize({**base_args, "writer_lease": "old"})
            current = maintenance_ops.handle_term_resize({**base_args, "writer_lease": "current"})

        self.assertFalse(missing.ok)
        self.assertEqual(missing.error.code, "terminal_writer_lease_required")
        self.assertFalse(wrong.ok)
        self.assertEqual(wrong.error.code, "terminal_not_writable")
        self.assertTrue(current.ok)
        self.assertEqual(resize_if_writer.call_count, 2)

    def test_pending_takeover_serializes_later_takeover_across_platforms(self) -> None:
        for pty_runner in self._runner_modules():
            with self.subTest(module=pty_runner.__name__, order="cancel-later"):
                session = self._session(pty_runner)
                session.resize = Mock()
                sockets = [socket.socketpair() for _ in range(3)]
                try:
                    first = session.reserve_attach_client(sockets[0][0], mode="control")
                    self._activate(session, first)
                    second = session.reserve_attach_client(sockets[1][0], mode="control", takeover=True)

                    self.assertTrue(second.metadata["writable"])
                    with self.assertRaisesRegex(RuntimeError, "writer attach is still pending"):
                        session.reserve_attach_client(sockets[2][0], mode="control", takeover=True)
                    self.assertNotIn(sockets[2][0].fileno(), session._clients)
                    self.assertNotIn(sockets[2][0].fileno(), session._attach_reservations)
                    self._activate(session, second)

                    second_lease = str(second.metadata.get("writer_lease") or "")
                    self.assertEqual(session._writer_fd, sockets[1][0].fileno())
                    self.assertTrue(session._clients[sockets[1][0].fileno()].writer)
                    self.assertTrue(session.resize_if_writer(writer_lease=second_lease, cols=120, rows=40))
                finally:
                    for sock, peer in sockets:
                        session.detach_client(sock.fileno())
                        peer.close()
                    session._selector.close()

            with self.subTest(module=pty_runner.__name__, order="cancel-earlier"):
                session = self._session(pty_runner)
                sockets = [socket.socketpair() for _ in range(3)]
                try:
                    first = session.reserve_attach_client(sockets[0][0], mode="control")
                    self._activate(session, first)
                    first_lease = str(first.metadata.get("writer_lease") or "")
                    second = session.reserve_attach_client(sockets[1][0], mode="control", takeover=True)
                    with self.assertRaisesRegex(RuntimeError, "writer attach is still pending"):
                        session.reserve_attach_client(sockets[2][0], mode="control", takeover=True)
                    self.assertNotIn(sockets[2][0].fileno(), session._clients)
                    self.assertNotIn(sockets[2][0].fileno(), session._attach_reservations)

                    second.cancel()
                    third = session.reserve_attach_client(sockets[2][0], mode="control", takeover=True)
                    self._activate(session, third)

                    third_lease = str(third.metadata.get("writer_lease") or "")
                    self.assertEqual(session._writer_fd, sockets[2][0].fileno())
                    self.assertFalse(session._clients[sockets[0][0].fileno()].writer)
                    self.assertTrue(session._clients[sockets[2][0].fileno()].writer)
                    session.resize = Mock()
                    self.assertFalse(session.resize_if_writer(writer_lease=first_lease, cols=120, rows=40))
                    self.assertTrue(session.resize_if_writer(writer_lease=third_lease, cols=120, rows=40))
                finally:
                    for sock, peer in sockets:
                        session.detach_client(sock.fileno())
                        peer.close()
                    session._selector.close()

    def test_detached_writer_is_not_automatically_promoted_across_platforms(self) -> None:
        for pty_runner in self._runner_modules():
            with self.subTest(module=pty_runner.__name__):
                session = self._session(pty_runner)
                first_sock, first_peer = socket.socketpair()
                takeover_sock, takeover_peer = socket.socketpair()
                reconnect_sock, reconnect_peer = socket.socketpair()
                try:
                    first = session.reserve_attach_client(first_sock, mode="control")
                    self._activate(session, first)
                    first_lease = str(first.metadata.get("writer_lease") or "")
                    takeover = session.reserve_attach_client(takeover_sock, mode="control", takeover=True)
                    self._activate(session, takeover)

                    session.detach_client(takeover_sock.fileno())

                    first_client = session._clients[first_sock.fileno()]
                    self.assertIsNone(session._writer_fd)
                    self.assertFalse(first_client.writer)
                    session.resize = Mock()
                    self.assertFalse(session.resize_if_writer(writer_lease=first_lease, cols=120, rows=40))

                    first_peer.sendall(b"blocked")
                    if pty_runner.__name__.endswith("pty_win"):
                        session.write_input = Mock(return_value=True)
                        session._on_client_readable(first_sock.fileno())
                        session.write_input.assert_not_called()
                    else:
                        session._master_fd = 999
                        with patch.object(os, "write") as write_master:
                            session._on_client_readable(first_sock.fileno())
                        write_master.assert_not_called()

                    session.detach_client(first_sock.fileno())
                    reconnect = session.reserve_attach_client(reconnect_sock, mode="control", takeover=True)
                    self._activate(session, reconnect)
                    reconnect_lease = str(reconnect.metadata.get("writer_lease") or "")
                    self.assertEqual(session._writer_fd, reconnect_sock.fileno())
                    self.assertTrue(session._clients[reconnect_sock.fileno()].writer)
                    self.assertTrue(session.resize_if_writer(writer_lease=reconnect_lease, cols=120, rows=40))
                finally:
                    for sock in (first_sock, takeover_sock, reconnect_sock):
                        session.detach_client(sock.fileno())
                    for peer in (first_peer, takeover_peer, reconnect_peer):
                        peer.close()
                    session._selector.close()

if __name__ == "__main__":
    unittest.main()
