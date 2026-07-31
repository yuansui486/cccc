from __future__ import annotations

import copy
import multiprocessing
import os
import inspect
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

import no1.computer_control.services as services_module
from no1.computer_control.recording import RecordingStore
from no1.computer_control.runtime import WorkflowRunner
from no1.computer_control.services import (
    ComputerControlServices,
    _daemon_owner_state,
    get_services,
    issue_daemon_computer_control_owner,
    start_daemon_services,
)
from no1.daemon.computer_control_ops import try_handle_computer_control_op
from no1.util.file_lock import acquire_lockfile, release_lockfile


class TestComputerControlServiceOwner(unittest.TestCase):
    @staticmethod
    def _lock(home: Path):
        return acquire_lockfile(home / "daemon" / "onecolleagued.lock", blocking=False)

    def test_constructors_do_not_recover_or_start_recording_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.object(
            RecordingStore,
            "_recover_after_restart",
        ) as recording_recovery, patch.object(
            WorkflowRunner,
            "_recover_manual_runs_after_restart",
        ) as run_recovery:
            service = ComputerControlServices(Path(td))

        recording_recovery.assert_not_called()
        run_recovery.assert_not_called()
        self.assertIsNone(service.recordings._watchdog)
        self.assertEqual(service.runner._tasks, {})
        self.assertEqual(service.runner._legacy_executions, {})
        self.assertEqual(service.runner._manual_executions, {})
        for action in (
            lambda: service.recordings._recover_after_restart(None),
            lambda: service.runner._recover_manual_runs_after_restart(None),
            lambda: service.recordings._start_watchdog(None),
        ):
            with self.subTest(action=repr(action)), self.assertRaisesRegex(
                PermissionError,
                "owner is required",
            ):
                action()
        self.assertIsNone(service.recordings._watchdog)

    def test_role_cache_isolates_passive_and_ready_daemon_services(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            alias = home.parent / f"{home.name}-alias"
            try:
                alias.symlink_to(home, target_is_directory=True)
            except OSError:
                alias = home
            lock = self._lock(home)
            try:
                passive = get_services(home)
                with patch.object(
                    RecordingStore, "_recover_after_restart"
                ) as recording_recovery, patch.object(
                    WorkflowRunner, "_recover_manual_runs_after_restart"
                ) as run_recovery, patch.object(
                    RecordingStore, "_start_watchdog"
                ) as start_watchdog:
                    daemon = start_daemon_services(alias, lock_handle=lock)
                    self.assertIs(get_services(home, role="daemon"), daemon)
                    self.assertIs(get_services(alias), passive)
                    self.assertIsNot(passive, daemon)
                    recording_recovery.assert_called_once_with(ANY)
                    run_recovery.assert_called_once_with(ANY)
                    start_watchdog.assert_called_once_with(ANY)
            finally:
                release_lockfile(lock)
                if alias != home:
                    alias.unlink(missing_ok=True)

    def test_owner_is_home_process_and_lock_bound_without_public_field_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other_td:
            home = Path(td)
            other = Path(other_td)
            lock = self._lock(home)
            try:
                owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
                expected = _daemon_owner_state(owner, home=home)
                object.__setattr__(owner, "authority_home", str(other))
                object.__setattr__(owner, "pid", -1)
                object.__setattr__(owner, "process_epoch", "forged")
                self.assertEqual(_daemon_owner_state(owner, home=home), expected)
                with self.assertRaisesRegex(PermissionError, "not current"):
                    _daemon_owner_state(owner, home=other)
                with patch("no1.computer_control.services.os.getpid", return_value=os.getpid() + 1):
                    with self.assertRaisesRegex(PermissionError, "not current"):
                        _daemon_owner_state(owner, home=home)
                with self.assertRaisesRegex(TypeError, "process-local"):
                    copy.copy(owner)
                with self.assertRaisesRegex(PermissionError, "owner is required"):
                    _daemon_owner_state(Mock(), home=home)
            finally:
                release_lockfile(lock)
            with self.assertRaisesRegex(PermissionError, "not current"):
                _daemon_owner_state(owner, home=home)

    def test_owner_requires_a_registered_held_lock_handle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            fake = SimpleNamespace(
                name=str(home / "daemon" / "onecolleagued.lock"),
                closed=False,
            )
            with self.assertRaisesRegex(PermissionError, "lock handle is required"):
                issue_daemon_computer_control_owner(home, lock_handle=fake)

            with self.assertRaisesRegex(PermissionError, "lock handle is required"):
                start_daemon_services(home, lock_handle=fake)

    def test_alias_retarget_does_not_move_a_daemon_owner_or_cached_service(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_home = root / "first"
            second_home = root / "second"
            first_home.mkdir()
            second_home.mkdir()
            alias = root / "alias"
            try:
                alias.symlink_to(first_home, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlinks are unavailable")
            lock = self._lock(first_home)
            try:
                with patch.object(
                    RecordingStore, "_recover_after_restart"
                ), patch.object(
                    WorkflowRunner, "_recover_manual_runs_after_restart"
                ), patch.object(RecordingStore, "_start_watchdog"):
                    service = start_daemon_services(alias, lock_handle=lock)
                alias.unlink()
                alias.symlink_to(second_home, target_is_directory=True)
                with self.assertRaisesRegex(PermissionError, "owner is required"):
                    get_services(alias, role="daemon")
                self.assertIs(get_services(first_home, role="daemon"), service)
            finally:
                release_lockfile(lock)

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "fork is unavailable")
    def test_fork_inherited_owner_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            context = multiprocessing.get_context("fork")
            queue = context.Queue()

            def validate_in_child() -> None:
                try:
                    _daemon_owner_state(owner, home=home)
                except PermissionError:
                    try:
                        issue_daemon_computer_control_owner(home, lock_handle=lock)
                    except PermissionError:
                        queue.put("rejected")
                    else:
                        queue.put("issued")
                else:
                    queue.put("accepted")

            process = context.Process(target=validate_in_child)
            process.start()
            process.join(5)
            try:
                self.assertEqual(process.exitcode, 0)
                self.assertEqual(queue.get(timeout=1), "rejected")
            finally:
                release_lockfile(lock)

    def test_second_process_cannot_acquire_daemon_owner_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            script = """
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
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(home / "daemon" / "onecolleagued.lock"),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.stdout.strip(), "rejected")
            finally:
                release_lockfile(lock)

    def test_start_is_ordered_retryable_and_starts_one_watchdog(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            effects: list[str] = []
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                    side_effect=lambda _owner: effects.append("recording"),
                ) as recording_recovery, patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                    side_effect=RuntimeError("manual recovery failed"),
                ) as run_recovery, patch.object(
                    service.recordings,
                    "_start_watchdog",
                    side_effect=lambda _owner: effects.append("watchdog"),
                ) as start_watchdog:
                    with self.assertRaisesRegex(RuntimeError, "manual recovery failed"):
                        service.start_daemon(owner)
                    self.assertEqual(effects, ["recording"])
                    self.assertIsNone(service.recordings._watchdog)
                    start_watchdog.assert_not_called()

                    run_recovery.side_effect = lambda _owner: effects.append("manual")
                    service.start_daemon(owner)
                    service.start_daemon(owner)
                    self.assertEqual(
                        effects,
                        ["recording", "recording", "manual", "watchdog"],
                    )
                    self.assertEqual(recording_recovery.call_count, 2)
                    self.assertEqual(run_recovery.call_count, 2)
                    start_watchdog.assert_called_once_with(owner)
            finally:
                release_lockfile(lock)

    def test_daemon_scheduler_is_singleton_and_stops_when_owner_lock_is_released(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            scheduler = service.scheduler
            scheduler.LOOP_SECONDS = 0.01
            ticked = threading.Event()

            async def tick() -> None:
                ticked.set()

            try:
                with patch.object(scheduler, "_tick", side_effect=tick):
                    scheduler.start_daemon(owner)
                    self.assertTrue(ticked.wait(2))
                    first_thread = scheduler._daemon_thread
                    self.assertIsNotNone(first_thread)
                    scheduler.start_daemon(owner)
                    self.assertIs(scheduler._daemon_thread, first_thread)

                    release_lockfile(lock)
                    deadline = time.monotonic() + 2
                    while scheduler._daemon_thread is not None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertIsNone(scheduler._daemon_thread)
                    self.assertFalse(first_thread.is_alive())
            finally:
                if not getattr(lock, "closed", True):
                    release_lockfile(lock)

    def test_ready_daemon_restarts_scheduler_after_worker_exit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            try:
                with patch.object(
                    service.recordings, "_recover_after_restart"
                ), patch.object(
                    service.runner, "_recover_manual_runs_after_restart"
                ), patch.object(
                    service.recordings, "_start_watchdog"
                ), patch.object(
                    service.scheduler, "_run_daemon_thread", return_value=None
                ) as worker:
                    service.start_daemon(owner)
                    deadline = time.monotonic() + 2
                    while (
                        service.scheduler._daemon_thread is not None
                        and service.scheduler._daemon_thread.is_alive()
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                    self.assertFalse(service.scheduler._daemon_thread.is_alive())

                    service.start_daemon(owner)
                    self.assertEqual(worker.call_count, 2)
                    self.assertEqual(service._daemon_start_state, "ready")
            finally:
                service.stop_daemon()
                release_lockfile(lock)

    def test_concurrent_start_runs_recovery_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            entered = threading.Event()
            release = threading.Event()
            results: list[ComputerControlServices] = []

            def recover_recording(_owner) -> None:
                entered.set()
                self.assertTrue(release.wait(5))

            def start() -> None:
                results.append(service.start_daemon(owner))

            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                    side_effect=recover_recording,
                ) as recording_recovery, patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ) as run_recovery, patch.object(
                    service.recordings,
                    "_start_watchdog",
                ) as start_watchdog:
                    first = threading.Thread(target=start)
                    second = threading.Thread(target=start)
                    first.start()
                    self.assertTrue(entered.wait(5))
                    second.start()
                    release.set()
                    first.join(5)
                    second.join(5)
                    self.assertEqual(results, [service, service])
                    recording_recovery.assert_called_once_with(owner)
                    run_recovery.assert_called_once_with(owner)
                    start_watchdog.assert_called_once_with(owner)
            finally:
                release_lockfile(lock)

    def test_not_ready_daemon_service_rejects_business_calls(self) -> None:
        with patch(
            "no1.daemon.computer_control_ops.get_services",
            side_effect=RuntimeError("recovery failed"),
        ):
            response, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "catalog",
                    "group_id": "g",
                    "actor_id": "user",
                    "caller_surface": "local_web",
                },
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "computer_control_not_ready")

    def test_startup_failure_keeps_owner_for_later_ready_retry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            try:
                with patch.object(
                    RecordingStore,
                    "_recover_after_restart",
                ) as recording_recovery, patch.object(
                    WorkflowRunner,
                    "_recover_manual_runs_after_restart",
                    side_effect=RuntimeError("manual recovery failed"),
                ) as run_recovery, patch.object(
                    RecordingStore,
                    "_start_watchdog",
                ) as start_watchdog:
                    with self.assertRaisesRegex(RuntimeError, "manual recovery failed"):
                        start_daemon_services(home, lock_handle=lock)
                    failed = next(
                        service
                        for service in services_module._services.values()
                        if service.home == home.resolve() and service.role == "daemon"
                    )
                    self.assertIsNone(failed.recordings._watchdog)
                    start_watchdog.assert_not_called()

                    with self.assertRaisesRegex(RuntimeError, "manual recovery failed"):
                        get_services(home, role="daemon")
                    start_watchdog.assert_not_called()

                    run_recovery.side_effect = None
                    ready = get_services(home, role="daemon")
                    self.assertEqual(ready._daemon_start_state, "ready")
                    self.assertEqual(recording_recovery.call_count, 3)
                    self.assertEqual(run_recovery.call_count, 3)
                    start_watchdog.assert_called_once_with(ANY)
            finally:
                release_lockfile(lock)

    def test_reacquiring_lock_in_this_process_gets_a_fresh_daemon_service(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with patch.object(RecordingStore, "_recover_after_restart"), patch.object(
                WorkflowRunner, "_recover_manual_runs_after_restart"
            ), patch.object(RecordingStore, "_start_watchdog"):
                first_lock = self._lock(home)
                try:
                    first = start_daemon_services(home, lock_handle=first_lock)
                finally:
                    release_lockfile(first_lock)

                second_lock = self._lock(home)
                try:
                    second = start_daemon_services(home, lock_handle=second_lock)
                finally:
                    release_lockfile(second_lock)

            self.assertIsNot(first, second)
            self.assertEqual(first._daemon_start_state, "ready")
            self.assertEqual(second._daemon_start_state, "ready")

    def test_daemon_start_hook_failure_is_nonfatal(self) -> None:
        from no1.daemon import server

        lock = Mock(closed=False)
        with patch.object(
            server,
            "start_daemon_computer_control",
            side_effect=RuntimeError("recovery failed"),
        ), patch.object(server.logger, "exception") as logged:
            self.assertFalse(
                server._start_daemon_computer_control_after_lock(Path("/tmp/home"), lock)
            )
        logged.assert_called_once_with(
            "Computer-control daemon service recovery is not ready"
        )
        source = inspect.getsource(server.serve_forever)
        self.assertLess(source.index("acquire_lockfile"), source.index("_start_daemon_computer_control_after_lock"))
        self.assertLess(source.index("_start_daemon_computer_control_after_lock"), source.index("bind_server_socket"))


if __name__ == "__main__":
    unittest.main()
