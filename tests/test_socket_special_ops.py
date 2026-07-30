import unittest

from no1.contracts.v1 import DaemonRequest
from no1.daemon.ops.socket_special_ops import try_handle_socket_special_op
from no1.runners.pty_attach import PtyAttachBusyError


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False
        self.timeout = None

    def settimeout(self, value) -> None:
        self.timeout = value

    def close(self) -> None:
        self.closed = True


class _FakeReservation:
    def __init__(self, metadata: dict, on_activate=None) -> None:
        self.metadata = dict(metadata)
        self.on_activate = on_activate
        self.activated = False
        self.cancelled = False

    def activate(self) -> bool:
        self.activated = True
        if self.on_activate is not None:
            self.on_activate()
        return True

    def cancel(self) -> None:
        self.cancelled = True


class TestSocketSpecialOps(unittest.TestCase):
    @staticmethod
    def _reservation(*, mode="control", writable=True, replaced=False, replay=0, on_activate=None):
        return _FakeReservation(
            {
                "mode": mode,
                "writable": writable,
                "writer_replaced": replaced,
                "replay_cursor": replay,
                "backlog_start_cursor": replay,
                "backlog_end_cursor": replay,
            },
            on_activate=on_activate,
        )
    def test_unknown_op_not_handled(self) -> None:
        req = DaemonRequest.model_validate({"op": "nope", "args": {}})
        conn = _FakeConn()
        sent: list[dict] = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: False,
            backlog_start_offset=lambda _gid, _aid: 0,
            attach_actor_socket=lambda _gid, _aid, _sock: None,
            load_group=lambda _gid: None,
            find_actor=lambda _group, _by: None,
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )
        self.assertFalse(handled)
        self.assertFalse(conn.closed)
        self.assertEqual(sent, [])

    def test_term_attach_success_transfers_socket(self) -> None:
        req = DaemonRequest.model_validate({"op": "term_attach", "args": {"group_id": "g1", "actor_id": "a1"}})
        conn = _FakeConn()
        conn.timeout = 2.0
        sent: list[dict] = []
        attached: list[tuple[str, str]] = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: True,
            backlog_start_offset=lambda _gid, _aid: 0,
            attach_actor_socket=lambda gid, aid, _sock, since=None, mode="control", takeover=False: self._reservation(
                on_activate=lambda: attached.append((gid, aid))
            ),
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _aid: {"id": "a1", "runner": "pty"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )
        self.assertTrue(handled)
        self.assertFalse(conn.closed)
        self.assertIsNone(conn.timeout)
        self.assertEqual(attached, [("g1", "a1")])
        self.assertTrue(sent and bool(sent[0].get("ok")))

    def test_term_attach_reports_ring_start_for_first_replay(self) -> None:
        req = DaemonRequest.model_validate(
            {"op": "term_attach", "args": {"group_id": "g1", "actor_id": "a1"}}
        )
        conn = _FakeConn()
        sent: list[dict] = []
        attached = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: True,
            backlog_start_offset=lambda _gid, _aid: 100,
            attach_actor_socket=lambda gid, aid, _sock, since=None, mode="control", takeover=False: self._reservation(
                replay=100,
                on_activate=lambda: attached.append((gid, aid, since)),
            ),
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _aid: {"id": "a1", "runner": "pty"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )

        self.assertTrue(handled)
        self.assertEqual(attached, [("g1", "a1", None)])
        self.assertEqual((sent[0].get("result") or {}).get("replay_cursor"), 100)

    def test_term_attach_replay_cursor_honors_live_and_expired_positions(self) -> None:
        for since, ring_start, expected in ((42, 10, 42), (5, 100, 100)):
            with self.subTest(since=since, ring_start=ring_start):
                req = DaemonRequest.model_validate(
                    {
                        "op": "term_attach",
                        "args": {"group_id": "g1", "actor_id": "a1", "since": since},
                    }
                )
                conn = _FakeConn()
                sent: list[dict] = []
                attached = []

                handled = try_handle_socket_special_op(
                    req,
                    conn,
                    send_json=lambda _conn, payload: sent.append(payload),
                    dump_response=lambda resp: resp.model_dump(),
                    error=lambda code, msg, details=None: self._error_payload(code, msg, details),
                    actor_running=lambda _gid, _aid: True,
                    backlog_start_offset=lambda _gid, _aid: ring_start,
                    attach_actor_socket=lambda gid, aid, _sock, cursor=None, mode="control", takeover=False: self._reservation(
                        replay=expected,
                        on_activate=lambda: attached.append((gid, aid, cursor)),
                    ),
                    load_group=lambda _gid: {"group_id": "g1"},
                    find_actor=lambda _group, _aid: {"id": "a1", "runner": "pty"},
                    effective_runner_kind=lambda rk: rk,
                    supported_stream_kinds=lambda: {"chat.message"},
                    start_events_stream=lambda *_args: False,
                )

                self.assertTrue(handled)
                self.assertEqual(attached, [("g1", "a1", since)])
                self.assertEqual((sent[0].get("result") or {}).get("replay_cursor"), expected)

    def test_term_attach_forwards_control_takeover(self) -> None:
        req = DaemonRequest.model_validate(
            {
                "op": "term_attach",
                "args": {
                    "group_id": "g1",
                    "actor_id": "a1",
                    "since": 42,
                    "mode": "control",
                    "takeover": True,
                },
            }
        )
        conn = _FakeConn()
        sent: list[dict] = []
        attached = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: True,
            backlog_start_offset=lambda _gid, _aid: 0,
            attach_actor_socket=lambda gid, aid, _sock, since=None, mode="control", takeover=False: self._reservation(
                mode=mode,
                writable=True,
                replaced=True,
                replay=42,
                on_activate=lambda: attached.append((gid, aid, since, mode, takeover)),
            ),
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _aid: {"id": "a1", "runner": "pty"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )

        self.assertTrue(handled)
        self.assertEqual(attached, [("g1", "a1", 42, "control", True)])
        result = sent[0].get("result") or {}
        self.assertTrue(result.get("terminal_writable"))
        self.assertTrue(result.get("writer_replaced"))

    def test_term_attach_viewer_is_read_only_and_cannot_take_over(self) -> None:
        req = DaemonRequest.model_validate(
            {
                "op": "term_attach",
                "args": {"group_id": "g1", "actor_id": "a1", "mode": "viewer", "takeover": True},
            }
        )
        conn = _FakeConn()
        sent: list[dict] = []
        attached = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: True,
            backlog_start_offset=lambda _gid, _aid: 0,
            attach_actor_socket=lambda gid, aid, _sock, since=None, mode="control", takeover=False: self._reservation(
                mode=mode,
                writable=False,
                replaced=False,
                on_activate=lambda: attached.append((gid, aid, since, mode, takeover)),
            ),
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _aid: {"id": "a1", "runner": "pty"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )

        self.assertTrue(handled)
        self.assertEqual(attached, [("g1", "a1", None, "viewer", False)])
        result = sent[0].get("result") or {}
        self.assertEqual(result.get("terminal_mode"), "viewer")
        self.assertFalse(result.get("terminal_writable"))

    def test_term_attach_ack_failure_cancels_reservation(self) -> None:
        req = DaemonRequest.model_validate(
            {"op": "term_attach", "args": {"group_id": "g1", "actor_id": "a1"}}
        )
        conn = _FakeConn()
        reservation = self._reservation()

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, _payload: (_ for _ in ()).throw(OSError("client disconnected")),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: True,
            attach_actor_socket=lambda *_args: reservation,
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _aid: {"id": "a1", "runner": "pty"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )

        self.assertTrue(handled)
        self.assertTrue(conn.closed)
        self.assertTrue(reservation.cancelled)
        self.assertFalse(reservation.activated)

    def test_term_attach_busy_returns_retryable_error_without_success_ack(self) -> None:
        req = DaemonRequest.model_validate(
            {
                "op": "term_attach",
                "args": {"group_id": "g1", "actor_id": "a1", "mode": "control", "takeover": True},
            }
        )
        conn = _FakeConn()
        sent: list[dict] = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: True,
            attach_actor_socket=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                PtyAttachBusyError("pending writer")
            ),
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _aid: {"id": "a1", "runner": "pty"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )

        self.assertTrue(handled)
        self.assertTrue(conn.closed)
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0].get("ok"))
        self.assertEqual((sent[0].get("error") or {}).get("code"), "terminal_attach_busy")

    def test_events_stream_invalid_kinds_returns_error(self) -> None:
        req = DaemonRequest.model_validate(
            {"op": "events_stream", "args": {"group_id": "g1", "kinds": ["unknown.kind"]}}
        )
        conn = _FakeConn()
        sent: list[dict] = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: False,
            backlog_start_offset=lambda _gid, _aid: 0,
            attach_actor_socket=lambda _gid, _aid, _sock: None,
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _by: {"id": "x"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )
        self.assertTrue(handled)
        self.assertTrue(conn.closed)
        self.assertTrue(sent)
        payload = sent[0]
        self.assertFalse(bool(payload.get("ok")))
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        self.assertEqual(str(error.get("code") or ""), "invalid_kinds")

    def test_events_stream_success_starts_stream(self) -> None:
        req = DaemonRequest.model_validate({"op": "events_stream", "args": {"group_id": "g1"}})
        conn = _FakeConn()
        conn.timeout = 2.0
        sent: list[dict] = []
        started: list[tuple[str, str]] = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: False,
            backlog_start_offset=lambda _gid, _aid: 0,
            attach_actor_socket=lambda _gid, _aid, _sock: None,
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _by: {"id": "x"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda _sock, group_id, by, _kinds, _since_event_id, _since_ts: started.append(
                (group_id, by)
            )
            or True,
        )
        self.assertTrue(handled)
        self.assertFalse(conn.closed)
        self.assertIsNone(conn.timeout)
        self.assertTrue(sent and bool(sent[0].get("ok")))
        self.assertEqual(started, [("g1", "user")])

    def test_term_attach_rejects_non_pty_actor(self) -> None:
        req = DaemonRequest.model_validate({"op": "term_attach", "args": {"group_id": "g1", "actor_id": "a1"}})
        conn = _FakeConn()
        sent: list[dict] = []

        handled = try_handle_socket_special_op(
            req,
            conn,
            send_json=lambda _conn, payload: sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            error=lambda code, msg, details=None: self._error_payload(code, msg, details),
            actor_running=lambda _gid, _aid: False,
            backlog_start_offset=lambda _gid, _aid: 0,
            attach_actor_socket=lambda _gid, _aid, _sock: None,
            load_group=lambda _gid: {"group_id": "g1"},
            find_actor=lambda _group, _aid: {"id": "a1", "runner": "headless"},
            effective_runner_kind=lambda rk: rk,
            supported_stream_kinds=lambda: {"chat.message"},
            start_events_stream=lambda *_args: False,
        )
        self.assertTrue(handled)
        self.assertTrue(conn.closed)
        self.assertTrue(sent)
        payload = sent[0]
        self.assertFalse(bool(payload.get("ok")))
        err = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        self.assertEqual(str(err.get("code") or ""), "not_pty_actor")

    @staticmethod
    def _error_payload(code: str, message: str, details=None):
        from no1.contracts.v1 import DaemonError, DaemonResponse

        return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=(details or {})))


if __name__ == "__main__":
    unittest.main()
