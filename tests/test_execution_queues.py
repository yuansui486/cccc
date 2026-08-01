from __future__ import annotations

import inspect
import subprocess
import sys
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


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
        admission_close = source.index("_begin_daemon_computer_control_shutdown")
        request_stop = source.index("_stop_request_execution_before_lock_release")
        outbox_stop = source.index("remote_outbox_worker.stop", request_stop)
        final_computer_check = source.index(
            "_daemon_computer_control_shutdown_complete",
            outbox_stop,
        )
        cleanup = source.index("cleanup_after_stop", outbox_stop)
        self.assertLess(admission_close, request_stop)
        self.assertLess(request_stop, outbox_stop)
        self.assertLess(outbox_stop, final_computer_check)
        self.assertLess(final_computer_check, cleanup)
        self.assertEqual(source.count("cleanup_after_stop("), 1)

    def test_shutdown_drain_signals_each_owner_and_remembers_convergence(self) -> None:
        from no1.daemon import server

        attempts = {"request": 0, "outbox": 0, "computer": 0}
        effects: list[str] = []

        def stop(name: str, succeed_at: int) -> bool:
            attempts[name] += 1
            effects.append(name)
            if name == "outbox" and attempts[name] == 1:
                raise RuntimeError("transient outbox stop failure")
            return attempts[name] >= succeed_at

        server._wait_for_daemon_drains_before_lock_release(
            stop_requests=lambda: stop("request", 2),
            stop_remote_outbox=lambda: stop("outbox", 2),
            stop_computer_control=lambda: stop("computer", 3),
            retry_seconds=0,
        )

        self.assertEqual(
            effects,
            ["request", "outbox", "computer", "request", "outbox", "computer", "computer"],
        )

    def test_computer_stop_unblocks_provider_request_before_cleanup(self) -> None:
        from no1.contracts.v1 import DaemonResponse
        from no1.daemon import server
        from no1.daemon.ops.execution_queues import DaemonRequestExecutionQueue
        from no1.util.file_lock import acquire_lockfile, is_lockfile_handle, release_lockfile

        stop_event = threading.Event()
        request_started = threading.Event()
        provider_cancelled = threading.Event()
        computer_stop_called = threading.Event()
        computer_stop_calls: list[str] = []
        conn = _Conn()

        def handle_request(_req: dict[str, str]):
            request_started.set()
            provider_cancelled.wait(5)
            return DaemonResponse(ok=True, result={"cancelled": True}), False

        queue = DaemonRequestExecutionQueue(
            stop_event=stop_event,
            handle_request=handle_request,
            send_json=lambda queued_conn, payload: queued_conn.sent.append(payload),
            dump_response=lambda response: response.model_dump(),
            logger=__import__("logging").getLogger("test"),
            on_should_exit=stop_event.set,
        )
        worker = threading.Thread(target=queue.run_forever, daemon=True)
        worker.start()
        self.assertTrue(queue.submit(conn=conn, req={"op": "computer_control"}))
        self.assertTrue(request_started.wait(1))

        with tempfile.TemporaryDirectory() as td:
            lock_handle = acquire_lockfile(Path(td) / "onecolleagued.lock", blocking=False)
            cleanup_calls: list[str] = []

            def stop_computer_control() -> bool:
                computer_stop_calls.append("stop")
                computer_stop_called.set()
                provider_cancelled.set()
                return True

            try:
                server._wait_for_daemon_drains_before_lock_release(
                    stop_requests=lambda: server._stop_request_execution_before_lock_release(
                        [(queue, [worker])],
                        stop_event=stop_event,
                        timeout=0.01,
                    ),
                    stop_remote_outbox=lambda: True,
                    stop_computer_control=stop_computer_control,
                    retry_seconds=0.01,
                )
                cleanup_calls.append("cleanup")
                self.assertTrue(computer_stop_called.is_set())
                self.assertTrue(conn.closed)
                self.assertFalse(worker.is_alive())
                self.assertFalse(queue.submit(conn=_Conn(), req={"op": "ping"}))
                self.assertTrue(is_lockfile_handle(lock_handle))
                self.assertEqual(computer_stop_calls, ["stop"])
                self.assertEqual(cleanup_calls, ["cleanup"])
            finally:
                provider_cancelled.set()
                worker.join(timeout=1)
                if is_lockfile_handle(lock_handle):
                    release_lockfile(lock_handle)

    def test_inflight_request_cannot_resurrect_stopped_daemon_generation(self) -> None:
        from no1.computer_control.recording import RecordingStore
        from no1.computer_control.runtime import WorkflowRunner
        from no1.computer_control.services import get_services, start_daemon_services
        from no1.contracts.v1 import DaemonResponse
        from no1.daemon import server
        from no1.daemon.ops.execution_queues import DaemonRequestExecutionQueue
        from no1.util.file_lock import acquire_lockfile, is_lockfile_handle, release_lockfile

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock_handle = acquire_lockfile(home / "daemon" / "onecolleagued.lock", blocking=False)
            computer_stopped = threading.Event()
            request_started = threading.Event()
            resurrection_errors: list[str] = []
            conn = _Conn()

            with patch.object(RecordingStore, "_recover_after_restart"), patch.object(
                WorkflowRunner,
                "_recover_manual_runs_after_restart",
            ), patch.object(RecordingStore, "_start_watchdog"), patch(
                "no1.computer_control.scheduler.ComputerControlScheduler.start_daemon"
            ):
                service = start_daemon_services(home, lock_handle=lock_handle)

                def handle_request(_req: dict[str, str]):
                    request_started.set()
                    computer_stopped.wait(5)
                    for resurrect in (
                        lambda: start_daemon_services(home, lock_handle=lock_handle),
                        lambda: get_services(home, role="daemon"),
                    ):
                        try:
                            resurrect()
                        except RuntimeError as exc:
                            resurrection_errors.append(str(exc))
                    return DaemonResponse(ok=True, result={"resurrected": False}), False

                stop_event = threading.Event()
                queue = DaemonRequestExecutionQueue(
                    stop_event=stop_event,
                    handle_request=handle_request,
                    send_json=lambda queued_conn, payload: queued_conn.sent.append(payload),
                    dump_response=lambda response: response.model_dump(),
                    logger=__import__("logging").getLogger("test"),
                    on_should_exit=stop_event.set,
                )
                worker = threading.Thread(target=queue.run_forever, daemon=True)
                worker.start()
                self.assertTrue(queue.submit(conn=conn, req={"op": "computer_control"}))
                self.assertTrue(request_started.wait(1))
                service.begin_daemon_shutdown()

                def stop_computer_control() -> bool:
                    stopped = server._stop_daemon_computer_control_before_lock_release(service)
                    if stopped:
                        computer_stopped.set()
                    return stopped

                try:
                    server._wait_for_daemon_drains_before_lock_release(
                        stop_requests=lambda: server._stop_request_execution_before_lock_release(
                            [(queue, [worker])],
                            stop_event=stop_event,
                            timeout=0.01,
                        ),
                        stop_remote_outbox=lambda: True,
                        stop_computer_control=stop_computer_control,
                        retry_seconds=0.01,
                    )
                    self.assertEqual(len(resurrection_errors), 2)
                    self.assertTrue(
                        all("shutdown admission is closed" in error for error in resurrection_errors)
                    )
                    self.assertTrue(service.daemon_shutdown_complete())
                    self.assertEqual(service._daemon_start_state, "new")
                    self.assertIsNone(service._daemon_execution_claim)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(queue.submit(conn=_Conn(), req={"op": "ping"}))
                    self.assertTrue(is_lockfile_handle(lock_handle))
                finally:
                    computer_stopped.set()
                    worker.join(timeout=1)
                    if is_lockfile_handle(lock_handle):
                        release_lockfile(lock_handle)

    def test_shutdown_drain_holds_lock_until_external_setup_child_exits(self) -> None:
        from no1.daemon import server
        from no1.util.file_lock import acquire_lockfile, release_lockfile

        with tempfile.TemporaryDirectory() as td:
            lock_path = Path(td) / "daemon" / "onecolleagued.lock"
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            owner_ready = threading.Event()
            stop_attempted = threading.Event()
            terminator_started = threading.Event()
            errors: list[BaseException] = []

            def terminate_later() -> None:
                time.sleep(0.3)
                child.terminate()
                child.wait(timeout=5)

            def stop_computer_control() -> bool:
                stop_attempted.set()
                if not terminator_started.is_set():
                    terminator_started.set()
                    threading.Thread(target=terminate_later, daemon=True).start()
                return child.poll() is not None

            def shutdown_owner() -> None:
                lock_handle = acquire_lockfile(lock_path, blocking=False)
                owner_ready.set()
                try:
                    server._wait_for_daemon_drains_before_lock_release(
                        stop_requests=lambda: True,
                        stop_remote_outbox=lambda: True,
                        stop_computer_control=stop_computer_control,
                        retry_seconds=0.01,
                    )
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    release_lockfile(lock_handle)

            owner = threading.Thread(target=shutdown_owner)
            owner.start()
            self.assertTrue(owner_ready.wait(2))
            self.assertTrue(stop_attempted.wait(2))
            probe = """
import sys
from pathlib import Path
from no1.util.file_lock import LockUnavailableError, acquire_lockfile
try:
    acquire_lockfile(Path(sys.argv[1]), blocking=False)
except LockUnavailableError:
    print('rejected')
else:
    print('accepted')
"""
            try:
                while child.poll() is None:
                    result = subprocess.run(
                        [sys.executable, "-c", probe, str(lock_path)],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.stdout.strip(), "rejected")
                    time.sleep(0.02)
                owner.join(timeout=5)
                self.assertFalse(owner.is_alive())
                self.assertEqual(errors, [])
                result = subprocess.run(
                    [sys.executable, "-c", probe, str(lock_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.stdout.strip(), "accepted")
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                owner.join(timeout=5)

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
