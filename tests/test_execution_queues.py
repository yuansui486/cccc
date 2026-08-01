from __future__ import annotations

import inspect
import threading
import tempfile
import time
import unittest
from pathlib import Path


class _Conn:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[dict] = []

    def close(self) -> None:
        self.closed = True


class TestExecutionQueues(unittest.TestCase):
    def test_request_execution_queue_processes_request_and_closes_connection(self) -> None:
        from no1.contracts.v1 import DaemonResponse
        from no1.daemon.ops.execution_queues import DaemonRequestExecutionQueue

        stop_event = threading.Event()
        handled: list[dict] = []
        exits: list[bool] = []
        conn = _Conn()

        queue = DaemonRequestExecutionQueue(
            stop_event=stop_event,
            handle_request=lambda req: (handled.append(req) or DaemonResponse(ok=True, result={"ok": True}), False),
            send_json=lambda queued_conn, payload: queued_conn.sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            logger=__import__("logging").getLogger("test"),
            on_should_exit=lambda: exits.append(True),
        )
        thread = threading.Thread(target=queue.run_forever, daemon=True)
        thread.start()
        self.assertTrue(queue.submit(conn=conn, req={"op": "ping"}))
        for _ in range(50):
            if conn.closed:
                break
            time.sleep(0.01)
        queue.close_admission()
        thread.join(timeout=1.0)

        self.assertEqual(handled, [{"op": "ping"}])
        self.assertTrue(conn.closed)
        self.assertTrue(conn.sent)
        self.assertEqual(exits, [])

    def test_request_execution_shutdown_rejects_admission_and_drains_inflight_before_lock_release(self) -> None:
        from no1.contracts.v1 import DaemonResponse
        from no1.daemon import server
        from no1.daemon.ops.execution_queues import DaemonRequestExecutionQueue
        from no1.daemon.serve_ops import cleanup_after_stop
        from no1.util.file_lock import acquire_lockfile, is_lockfile_handle, release_lockfile

        stop_event = threading.Event()
        request_started = threading.Event()
        release_request = threading.Event()
        handled: list[str] = []
        conn = _Conn()
        queued_conn = _Conn()

        def handle(req: dict[str, str]):
            op = req["op"]
            handled.append(op)
            if op == "group_bridge_management_pairing_remote_sync":
                request_started.set()
                release_request.wait(5.0)
            return DaemonResponse(ok=True, result={"status": "approved"}), False

        queue = DaemonRequestExecutionQueue(
            stop_event=stop_event,
            handle_request=handle,
            send_json=lambda queued_conn, payload: queued_conn.sent.append(payload),
            dump_response=lambda resp: resp.model_dump(),
            logger=__import__("logging").getLogger("test"),
            on_should_exit=stop_event.set,
        )
        worker = threading.Thread(target=queue.run_forever, daemon=True)
        worker.start()
        self.assertTrue(queue.submit(conn=conn, req={"op": "group_bridge_management_pairing_remote_sync"}))
        self.assertTrue(request_started.wait(1.0))
        self.assertTrue(queue.submit(conn=queued_conn, req={"op": "ping"}))

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        temp_home = Path(temp_dir.name)
        lock_handle = acquire_lockfile(temp_home / "onecolleagued.lock", blocking=False)
        try:
            self.assertFalse(
                server._stop_request_execution_before_lock_release(
                    [(queue, [worker])],
                    stop_event=stop_event,
                    timeout=0.01,
                )
            )
            self.assertTrue(is_lockfile_handle(lock_handle))
            self.assertTrue(stop_event.is_set())
            self.assertFalse(conn.closed)
            self.assertFalse(queued_conn.closed)
            self.assertFalse(queue.submit(conn=_Conn(), req={"op": "ping"}))

            release_request.set()
            self.assertTrue(
                server._stop_request_execution_before_lock_release(
                    [(queue, [worker])],
                    timeout=1.0,
                )
            )
            self.assertTrue(conn.closed)
            self.assertTrue(queued_conn.closed)
            self.assertEqual(
                handled,
                ["group_bridge_management_pairing_remote_sync", "ping"],
            )
            cleanup_after_stop(
                stop_event=stop_event,
                home=temp_home,
                best_effort_killpg=lambda *_args, **_kwargs: None,
                im_stop_all=lambda *_args, **_kwargs: None,
                codex_stop_all=lambda: None,
                pty_stop_all=lambda: None,
                headless_stop_all=lambda: None,
                sock_path=temp_home / "missing.sock",
                addr_path=temp_home / "missing.addr",
                pid_path=temp_home / "missing.pid",
                release_lockfile=release_lockfile,
                lock_handle=lock_handle,
            )
            self.assertFalse(is_lockfile_handle(lock_handle))
        finally:
            release_request.set()
            worker.join(timeout=1.0)
            if is_lockfile_handle(lock_handle):
                release_lockfile(lock_handle)

    def test_server_drains_request_workers_before_other_owner_cleanup_and_lock_release(self) -> None:
        from no1.daemon import server

        source = inspect.getsource(server.serve_forever)
        request_stop = source.index("_stop_request_execution_before_lock_release")
        outbox_stop = source.index("remote_outbox_worker.stop", request_stop)
        cleanup = source.index("cleanup_after_stop", outbox_stop)
        self.assertLess(request_stop, outbox_stop)
        self.assertLess(outbox_stop, cleanup)

    def test_group_space_sync_run_queue_dedupes_and_upgrades_force(self) -> None:
        from no1.daemon.ops.execution_queues import GroupSpaceSyncRunQueue

        queue = GroupSpaceSyncRunQueue()
        first = queue.submit(group_id="g1", provider="notebooklm", force=False, by="user")
        second = queue.submit(group_id="g1", provider="notebooklm", force=True, by="peer1")
        ran: list[tuple[str, bool, str]] = []
        processed = queue.drain(
            limit=4,
            runner=lambda task: ran.append((task.group_id, bool(task.force), task.by)),
        )

        self.assertEqual(bool(first.get("queued")), True)
        self.assertEqual(bool(first.get("completed")), False)
        self.assertNotIn("completion_signal", first)
        self.assertNotIn("recommended_next_action", first)
        self.assertEqual(str(second.get("reason") or ""), "already_pending")
        self.assertEqual(bool(second.get("completed")), False)
        self.assertNotIn("completion_signal", second)
        self.assertEqual(processed, 1)
        self.assertEqual(ran, [("g1", True, "peer1")])

    def test_group_space_sync_run_queue_keeps_followup_when_already_running(self) -> None:
        from no1.daemon.ops.execution_queues import GroupSpaceSyncRunQueue

        queue = GroupSpaceSyncRunQueue()
        first = queue.submit(group_id="g1", provider="notebooklm", force=False, by="user")
        first_runs: list[tuple[str, bool, str]] = []

        processed = queue.drain(
            limit=1,
            runner=lambda task: (
                first_runs.append((task.group_id, bool(task.force), task.by)),
                queue.submit(group_id="g1", provider="notebooklm", force=True, by="peer1"),
            ),
        )
        self.assertEqual(bool(first.get("queued")), True)
        self.assertEqual(processed, 1)
        self.assertEqual(first_runs, [("g1", False, "user")])

        second_runs: list[tuple[str, bool, str]] = []
        processed = queue.drain(
            limit=1,
            runner=lambda task: second_runs.append((task.group_id, bool(task.force), task.by)),
        )
        self.assertEqual(processed, 1)
        self.assertEqual(second_runs, [("g1", True, "peer1")])

    def test_group_space_sync_run_queue_upgrades_followup_force_while_running(self) -> None:
        from no1.daemon.ops.execution_queues import GroupSpaceSyncRunQueue

        queue = GroupSpaceSyncRunQueue()
        queue.submit(group_id="g1", provider="notebooklm", force=False, by="user")
        observed: list[dict[str, object]] = []

        queue.drain(
            limit=1,
            runner=lambda _task: (
                observed.append(queue.submit(group_id="g1", provider="notebooklm", force=False, by="peer1")),
                observed.append(queue.submit(group_id="g1", provider="notebooklm", force=True, by="peer2")),
            ),
        )

        self.assertEqual(str(observed[0].get("reason") or ""), "queued_after_running")
        self.assertEqual(bool(observed[0].get("queued")), True)
        self.assertEqual(bool(observed[0].get("completed")), False)
        self.assertNotIn("completion_signal", observed[0])
        self.assertEqual(str(observed[1].get("reason") or ""), "already_pending")
        self.assertEqual(bool(observed[1].get("force")), True)

        reruns: list[tuple[str, bool, str]] = []
        queue.drain(
            limit=1,
            runner=lambda task: reruns.append((task.group_id, bool(task.force), task.by)),
        )
        self.assertEqual(reruns, [("g1", True, "peer2")])


if __name__ == "__main__":
    unittest.main()
