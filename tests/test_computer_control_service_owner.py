from __future__ import annotations

import asyncio
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
from unittest.mock import ANY, AsyncMock, Mock, patch

import no1.computer_control.services as services_module
from no1.computer_control.recording import RecordingStore
from no1.computer_control.models import WorkflowDefinition
from no1.computer_control.runtime import WorkflowRunner
from no1.computer_control.services import (
    ComputerControlServices,
    _claim_daemon_generation,
    _daemon_owner_state,
    _release_daemon_generation,
    get_services,
    issue_daemon_computer_control_owner,
    start_daemon_services,
)
from no1.daemon.computer_control_ops import try_handle_computer_control_op
from no1.kernel.group import create_group
from no1.kernel.registry import load_registry
from no1.util.file_lock import (
    LockUnavailableError,
    acquire_lockfile,
    is_lockfile_handle,
    release_lockfile,
)


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

    def test_passive_runner_and_scheduler_reject_before_execution_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            service = ComputerControlServices(Path(td))
            with patch.object(service.store, "get") as get_workflow, patch.object(
                service.runner,
                "_ensure_sync_loop",
            ) as ensure_loop:
                with self.assertRaisesRegex(PermissionError, "READY daemon execution"):
                    service.runner.start_sync(
                        "g",
                        "workflow",
                        actor_id="actor",
                        version=1,
                        inputs={},
                    )
                with self.assertRaisesRegex(PermissionError, "passive.*scheduler"):
                    asyncio.run(service.scheduler.start())
            get_workflow.assert_not_called()
            ensure_loop.assert_not_called()
            self.assertIsNone(service.scheduler._task)
            self.assertFalse(service.lease.status()["active"])
            self.assertEqual(service.runner._tasks, {})
            self.assertEqual(service.runner._legacy_executions, {})
            self.assertEqual(service.runner._manual_executions, {})

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
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(scheduler, "_tick", side_effect=tick):
                    service.start_daemon(owner)
                    self.assertTrue(ticked.wait(2))
                    first_thread = scheduler._daemon_thread
                    self.assertIsNotNone(first_thread)
                    service.start_daemon(owner)
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

    def test_shutdown_revokes_all_raw_owner_child_start_paths(self) -> None:
        class FakeProcess:
            def __init__(self, effects: list[str]) -> None:
                self.returncode = None
                self.effects = effects

            def terminate(self) -> None:
                self.effects.append("process-terminate")
                self.returncode = 0

            async def wait(self) -> int:
                self.effects.append("process-wait")
                return 0

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            transport_effects: list[str] = []
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_watch",
                    return_value=None,
                ), patch.object(
                    service.scheduler,
                    "_run_daemon_thread",
                    return_value=None,
                ):
                    service.start_daemon(owner)
                    process = FakeProcess(transport_effects)

                    async def install_transport() -> None:
                        async def read_stderr() -> None:
                            try:
                                await asyncio.Event().wait()
                            finally:
                                transport_effects.append("stderr-stopped")

                        service.session._process = process
                        service.session._stderr_task = asyncio.create_task(read_stderr())
                        await asyncio.sleep(0)

                    service.session.run_sync(install_transport())
                    session_thread = service.session._thread
                    self.assertIsNotNone(session_thread)
                    self.assertTrue(session_thread.is_alive())
                    alias_owner = issue_daemon_computer_control_owner(
                        home,
                        lock_handle=lock,
                    )
                    service.begin_daemon_shutdown()
                    self.assertTrue(service.stop_daemon())
                    self.assertTrue(service.daemon_shutdown_complete())
                    self.assertFalse(session_thread.is_alive())
                    self.assertIsNone(service.session._thread)
                    self.assertIsNone(service.session._process)
                    self.assertIsNone(service.session._stderr_task)
                    self.assertEqual(
                        transport_effects,
                        ["process-terminate", "process-wait", "stderr-stopped"],
                    )

                with patch.object(
                    service.recordings,
                    "_suspend_recovered_recordings",
                ) as recover_recordings, patch.object(
                    service.runner.run_authorities,
                    "restart_candidates",
                ) as recover_runs, patch.object(
                    service.picker.overlay,
                    "start",
                ) as start_overlay:
                    start_paths = (
                        service.recordings._recover_after_restart,
                        service.runner._recover_manual_runs_after_restart,
                        service.recordings._start_watchdog,
                        service.scheduler.start_daemon,
                        service.setup._start_daemon,
                        service.picker._start_daemon,
                    )
                    for candidate_owner in (owner, alias_owner):
                        for start_path in start_paths:
                            with self.subTest(
                                owner=id(candidate_owner),
                                start_path=start_path.__qualname__,
                            ), self.assertRaisesRegex(
                                RuntimeError,
                                "shutdown admission is closed",
                            ):
                                start_path(candidate_owner)
                        with self.assertRaisesRegex(
                            RuntimeError,
                            "shutdown admission is closed",
                        ):
                            service.session._start_daemon(
                                candidate_owner,
                                home=home,
                            )

                    recover_recordings.assert_not_called()
                    recover_runs.assert_not_called()
                    with self.assertRaisesRegex(RuntimeError, "stopping"):
                        asyncio.run(service.setup.ensure())
                    with self.assertRaisesRegex(RuntimeError, "stopping"):
                        service.picker.start("g", "actor")
                    start_overlay.assert_not_called()
                with patch.object(
                    service.session,
                    "_catalog_owned",
                    new=AsyncMock(),
                ) as catalog_owned, patch.object(
                    service.session,
                    "_restart_owned",
                    new=AsyncMock(),
                ) as restart_owned, patch.object(
                    service.session,
                    "_call_tool_owned",
                    new=AsyncMock(),
                ) as call_owned, patch(
                    "no1.computer_control.mcp.asyncio.create_subprocess_exec",
                    new=AsyncMock(),
                ) as create_process:
                    for operation in (
                        service.session.catalog_sync,
                        service.session.restart_sync,
                        lambda: service.session.call_tool_sync("Snapshot", {}),
                        lambda: asyncio.run(service.session.catalog()),
                        lambda: asyncio.run(service.session.restart()),
                        lambda: asyncio.run(service.session.call_tool("Snapshot", {})),
                    ):
                        with self.subTest(
                            session_operation=repr(operation),
                        ), self.assertRaisesRegex(RuntimeError, "stopping"):
                            operation()
                    catalog_owned.assert_not_called()
                    restart_owned.assert_not_called()
                    call_owned.assert_not_called()
                    create_process.assert_not_called()
                service.session.stop_sync()
                asyncio.run(service.session.stop())
                self.assertIsNone(service.session._thread)
                self.assertIsNone(service.recordings._watchdog)
                self.assertIsNone(service.scheduler._daemon_thread)
                self.assertIsNone(service.setup._task)
                self.assertIsNone(service.setup._active_process)
                self.assertIsNone(service.picker._thread)
                self.assertIsNone(service.picker.overlay._thread)
            finally:
                if is_lockfile_handle(lock):
                    release_lockfile(lock)

    def test_shutdown_certificate_waits_for_every_generation_child_claim(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            extra_claim = None
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(
                    service.scheduler,
                    "start_daemon",
                ):
                    service.start_daemon(owner)
                extra_claim = _claim_daemon_generation(
                    owner,
                    home=home,
                    subject="stubborn-child",
                )
                service.begin_daemon_shutdown()
                self.assertTrue(service.stop_daemon())
                self.assertFalse(service.daemon_shutdown_complete())
                _release_daemon_generation(extra_claim)
                extra_claim = None
                self.assertTrue(service.daemon_shutdown_complete())
            finally:
                _release_daemon_generation(extra_claim)
                if is_lockfile_handle(lock):
                    release_lockfile(lock)

    def test_shutdown_certificate_waits_for_session_owner_loop_drain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(
                    service.scheduler,
                    "start_daemon",
                ), patch.object(
                    service.scheduler,
                    "stop_daemon",
                    return_value=True,
                ):
                    service.start_daemon(owner)
                    service.begin_daemon_shutdown()
                    with patch.object(
                        service.session,
                        "_drain_daemon_sync",
                        return_value=False,
                    ) as drain_session:
                        self.assertFalse(service.stop_daemon())
                    drain_session.assert_called_once()
                    self.assertEqual(service._daemon_start_state, "stopping")
                    self.assertIsNotNone(service.session._daemon_generation_claim)
                    self.assertFalse(service.daemon_shutdown_complete())

                    self.assertTrue(service.stop_daemon())
                    self.assertIsNone(service.session._daemon_generation_claim)
                    self.assertTrue(service.daemon_shutdown_complete())
            finally:
                if is_lockfile_handle(lock):
                    release_lockfile(lock)

    def test_session_process_handle_and_claim_survive_a_short_drain_timeout(self) -> None:
        class BlockingProcess:
            def __init__(self) -> None:
                self.returncode = None
                self.release = threading.Event()
                self.waiting = threading.Event()

            def terminate(self) -> None:
                return None

            def kill(self) -> None:
                return None

            async def wait(self) -> int:
                self.waiting.set()
                await asyncio.to_thread(self.release.wait)
                self.returncode = 0
                return 0

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            service.DRAIN_TIMEOUT_SECONDS = 0.05
            process = BlockingProcess()
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(
                    service.scheduler,
                    "start_daemon",
                ), patch.object(
                    service.scheduler,
                    "stop_daemon",
                    return_value=True,
                ):
                    service.start_daemon(owner)

                    async def install_process() -> None:
                        service.session._process = process

                    service.session.run_sync(install_process())
                    service.begin_daemon_shutdown()
                    self.assertFalse(service.stop_daemon())
                    self.assertTrue(process.waiting.wait(1))
                    self.assertIs(service.session._process, process)
                    self.assertIsNotNone(service.session._daemon_generation_claim)
                    self.assertFalse(service.daemon_shutdown_complete())
                    with self.assertRaises(LockUnavailableError):
                        self._lock(home)

                    process.release.set()
                    self.assertTrue(service.stop_daemon())
                    self.assertIsNone(service.session._process)
                    self.assertIsNone(service.session._daemon_generation_claim)
                    self.assertTrue(service.daemon_shutdown_complete())
            finally:
                process.release.set()
                if is_lockfile_handle(lock):
                    release_lockfile(lock)

    def test_recovery_generation_claims_release_when_recovery_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            try:
                with patch.object(
                    service.recordings,
                    "_suspend_recovered_recordings",
                    side_effect=RuntimeError("recording recovery failed"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "recording recovery failed"):
                        service.recordings._recover_after_restart(owner)
                with patch.object(
                    service.runner.run_authorities,
                    "restart_candidates",
                    side_effect=RuntimeError("runner recovery failed"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "runner recovery failed"):
                        service.runner._recover_manual_runs_after_restart(owner)

                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(
                    service.scheduler,
                    "start_daemon",
                ):
                    service.start_daemon(owner)
                    service.begin_daemon_shutdown()
                    self.assertTrue(service.stop_daemon())
                    self.assertTrue(service.daemon_shutdown_complete())
            finally:
                if is_lockfile_handle(lock):
                    release_lockfile(lock)

    def test_execution_claim_is_sealed_ready_bound_and_revoked_with_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(service.scheduler, "start_daemon"):
                    with self.assertRaisesRegex(PermissionError, "READY daemon execution"):
                        service.runner._require_daemon_execution_claim()
                    service.start_daemon(owner)
                    claim = service.runner._require_daemon_execution_claim()
                    self.assertIs(claim, service._daemon_execution_claim)
                    forged = object.__new__(type(claim))
                    for field in (
                        "authority_home",
                        "pid",
                        "process_epoch",
                        "lock_handle_id",
                        "service_id",
                    ):
                        setattr(forged, field, getattr(claim, field))
                    service.runner._daemon_execution_claim = forged
                    with self.assertRaisesRegex(PermissionError, "READY daemon execution"):
                        service.runner._require_daemon_execution_claim()
                    service.runner._bind_daemon_execution_claim(claim)

                    service.stop_daemon()
                    service.runner._bind_daemon_execution_claim(claim)
                    with self.assertRaisesRegex(PermissionError, "READY daemon execution"):
                        service.runner._require_daemon_execution_claim()
                    service.start_daemon(owner)
                    replacement = service.runner._require_daemon_execution_claim()
                    self.assertIsNot(replacement, claim)
                    service.runner._bind_daemon_execution_claim(claim)
                    with self.assertRaisesRegex(PermissionError, "READY daemon execution"):
                        service.runner._require_daemon_execution_claim()
                    service.runner._bind_daemon_execution_claim(replacement)
                    self.assertIs(
                        service.runner._require_daemon_execution_claim(),
                        replacement,
                    )
                    release_lockfile(lock)
                    with self.assertRaisesRegex(PermissionError, "not current"):
                        service.runner._require_daemon_execution_claim()
            finally:
                service.stop_daemon()
                if not getattr(lock, "closed", True):
                    release_lockfile(lock)

    def test_daemon_stop_retains_lock_until_blocking_provider_exits(self) -> None:
        class BlockingSession:
            transport_restarts = 0

            def __init__(self) -> None:
                self.entered = threading.Event()
                self.release = threading.Event()
                self.effects: list[str] = []

            async def catalog(self):
                return []

            async def call_tool(self, name, arguments, *, timeout):
                self.entered.set()
                try:
                    await asyncio.to_thread(self.release.wait)
                except asyncio.CancelledError:
                    await asyncio.to_thread(self.release.wait)
                self.effects.append(name)
                return {"ok": True}

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"ONECOLLEAGUE_HOME": td, "CCCC_HOME": td},
        ):
            home = Path(td)
            group_id = create_group(
                load_registry(),
                title="blocking provider",
            ).group_id
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            session = BlockingSession()
            service.session = session
            service.runner.session = session
            service.DRAIN_TIMEOUT_SECONDS = 0.1
            stopped = False
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(service.scheduler, "start_daemon"):
                    service.start_daemon(owner)
                workflow = service.store.create(
                    group_id,
                    WorkflowDefinition.model_validate(
                        {
                            "name": "blocking provider",
                            "nodes": [
                                {"id": "start", "type": "start"},
                                {
                                    "id": "mutate",
                                    "type": "action",
                                    "tool": "Mutate",
                                    "arguments": {},
                                },
                                {"id": "end", "type": "end"},
                            ],
                            "edges": [
                                {"source": "start", "target": "mutate"},
                                {"source": "mutate", "target": "end"},
                            ],
                        }
                    ),
                )
                service.runner.start_sync(
                    group_id,
                    str(workflow["manifest"]["workflow_id"]),
                    actor_id="actor",
                    version=1,
                    inputs={},
                )
                self.assertTrue(session.entered.wait(2))

                claim = service._daemon_execution_claim
                self.assertFalse(service.stop_daemon())
                self.assertEqual(service._daemon_start_state, "stopping")
                self.assertIs(service._daemon_execution_claim, claim)
                self.assertTrue(is_lockfile_handle(lock))
                with patch.object(service.store, "get") as get_workflow:
                    with self.assertRaisesRegex(
                        PermissionError,
                        "READY daemon execution",
                    ):
                        service.runner.start_sync(
                            group_id,
                            str(workflow["manifest"]["workflow_id"]),
                            actor_id="actor",
                            version=1,
                            inputs={},
                        )
                get_workflow.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, "stopping"):
                    service.start_daemon(owner)
                with self.assertRaises(LockUnavailableError):
                    self._lock(home)

                session.release.set()
                deadline = time.time() + 2
                while time.time() < deadline and not session.effects:
                    time.sleep(0.01)
                self.assertEqual(session.effects, ["Mutate"])
                self.assertTrue(service.stop_daemon())
                stopped = True
                self.assertIsNone(service.runner._sync_loop)
                self.assertIsNone(service.runner._sync_thread)
                self.assertEqual(service.runner._tasks, {})

                release_lockfile(lock)
                replacement = self._lock(home)
                release_lockfile(replacement)
            finally:
                session.release.set()
                if not stopped:
                    service.stop_daemon()
                if not getattr(lock, "closed", True):
                    release_lockfile(lock)

    def test_daemon_stop_drains_recording_watchdog_before_lock_release(self) -> None:
        for operation in ("heartbeat", "suspend"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                lock = self._lock(home)
                owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
                service = ComputerControlServices(home, role="daemon")
                service.DRAIN_TIMEOUT_SECONDS = 0.05
                service.recordings.WATCHDOG_INTERVAL_SECONDS = 0.01
                service.recordings.IDLE_SUSPEND_SECONDS = (
                    300.0 if operation == "heartbeat" else 0.0
                )
                entered = threading.Event()
                release = threading.Event()
                effects: list[str] = []

                def blocking_effect(*args, **kwargs):
                    entered.set()
                    self.assertTrue(release.wait(5))
                    effects.append(operation)
                    return {}

                patches = [
                    patch.object(service.recordings, "_recover_after_restart"),
                    patch.object(service.runner, "_recover_manual_runs_after_restart"),
                    patch.object(service.scheduler, "start_daemon"),
                    patch.object(service.scheduler, "stop_daemon", return_value=True),
                    patch.object(service.session, "stop_sync"),
                    patch.object(
                        service.recordings,
                        "_read",
                        return_value={
                            "status": "exploring",
                            "last_activity_at": time.time() if operation == "heartbeat" else 0,
                        },
                    ),
                    patch.object(
                        service.recordings.lease
                        if operation == "heartbeat"
                        else service.recordings,
                        operation,
                        side_effect=blocking_effect,
                    ),
                ]
                stopped = False
                try:
                    for active_patch in patches:
                        active_patch.start()
                        self.addCleanup(active_patch.stop)
                    service.start_daemon(owner)
                    with service.recordings._lock:
                        service.recordings._active["rec_active"] = (
                            "g",
                            "actor",
                            "request",
                            Mock(),
                        )
                    first_thread = service.recordings._watchdog
                    first_stop = service.recordings._watchdog_stop
                    self.assertTrue(entered.wait(2))

                    self.assertFalse(service.stop_daemon())
                    self.assertEqual(service._daemon_start_state, "stopping")
                    self.assertTrue(first_thread.is_alive())
                    with self.assertRaises(LockUnavailableError):
                        self._lock(home)

                    release.set()
                    self.assertTrue(service.stop_daemon())
                    self.assertEqual(effects, [operation])
                    self.assertFalse(first_thread.is_alive())
                    self.assertIsNone(service.recordings._watchdog)
                    with service.recordings._lock:
                        service.recordings._active.clear()

                    service.start_daemon(owner)
                    second_thread = service.recordings._watchdog
                    second_stop = service.recordings._watchdog_stop
                    self.assertIsNot(second_thread, first_thread)
                    self.assertIsNot(second_stop, first_stop)
                    self.assertTrue(service.stop_daemon())
                    stopped = True

                    release_lockfile(lock)
                    replacement = self._lock(home)
                    release_lockfile(replacement)
                    time.sleep(0.03)
                    self.assertEqual(effects, [operation])
                finally:
                    release.set()
                    if not stopped:
                        service.stop_daemon()
                    if not getattr(lock, "closed", True):
                        release_lockfile(lock)

    def test_daemon_stop_drains_picker_threads_before_lock_release(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            service.DRAIN_TIMEOUT_SECONDS = 0.05
            service.picker.lease.HEARTBEAT_SECONDS = 0.02
            entered = threading.Event()
            release = threading.Event()
            effects: list[str] = []

            def blocking_heartbeat(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(5))
                effects.append("heartbeat")
                return {}

            stopped = False
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(
                    service.scheduler,
                    "start_daemon",
                ), patch.object(
                    service.scheduler,
                    "stop_daemon",
                    return_value=True,
                ), patch.object(
                    service.session,
                    "stop_sync",
                ), patch.object(
                    service.picker.lease,
                    "heartbeat",
                    side_effect=blocking_heartbeat,
                ):
                    service.start_daemon(owner)
                    first = service.picker.start("g", "actor")
                    first_session = service.picker._session(first["session_id"])
                    first_heartbeat = first_session.heartbeat_thread
                    first_poll = service.picker._thread
                    self.assertTrue(entered.wait(2))

                    self.assertFalse(service.stop_daemon())
                    self.assertTrue(first_heartbeat.is_alive())
                    with self.assertRaisesRegex(Exception, "stopping"):
                        service.picker.start("g", "actor")
                    with self.assertRaises(LockUnavailableError):
                        self._lock(home)

                    release.set()
                    self.assertTrue(service.stop_daemon())
                    self.assertFalse(first_heartbeat.is_alive())
                    self.assertFalse(first_poll.is_alive())
                    self.assertEqual(effects, ["heartbeat"])

                    service.start_daemon(owner)
                    second = service.picker.start("g", "actor")
                    second_session = service.picker._session(second["session_id"])
                    self.assertIsNot(second_session.heartbeat_thread, first_heartbeat)
                    self.assertIsNot(service.picker._thread, first_poll)
                    self.assertTrue(service.stop_daemon())
                    stopped = True

                release_lockfile(lock)
                replacement = self._lock(home)
                release_lockfile(replacement)
                time.sleep(0.03)
                self.assertEqual(effects, ["heartbeat"])
            finally:
                release.set()
                if not stopped:
                    service.stop_daemon()
                if not getattr(lock, "closed", True):
                    release_lockfile(lock)

    def test_daemon_stop_drains_setup_task_before_transport_and_lock_release(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            lock = self._lock(home)
            owner = issue_daemon_computer_control_owner(home, lock_handle=lock)
            service = ComputerControlServices(home, role="daemon")
            service.DRAIN_TIMEOUT_SECONDS = 0.05
            service.setup.lease.HEARTBEAT_SECONDS = 0.02
            entered = threading.Event()
            heartbeat_entered = threading.Event()
            release = threading.Event()
            effects: list[str] = []

            async def blocking_setup(*, force: bool, upgrade: bool) -> None:
                with service.setup.lease.hold(
                    group_id="_global",
                    actor_id="setup",
                    run_id="setup_blocking",
                ):
                    entered.set()
                    while not release.is_set():
                        try:
                            await asyncio.to_thread(release.wait)
                        except asyncio.CancelledError:
                            continue
                    effects.append("setup")

            def blocking_heartbeat(*args, **kwargs):
                heartbeat_entered.set()
                self.assertTrue(release.wait(5))
                effects.append("heartbeat")
                return {}

            process = SimpleNamespace(returncode=None)

            async def terminate_process(value) -> None:
                self.assertIs(value, process)
                process.returncode = 0
                effects.append("process")

            stopped = False
            try:
                with patch.object(
                    service.recordings,
                    "_recover_after_restart",
                ), patch.object(
                    service.runner,
                    "_recover_manual_runs_after_restart",
                ), patch.object(
                    service.recordings,
                    "_start_watchdog",
                ), patch.object(
                    service.scheduler,
                    "start_daemon",
                ), patch.object(
                    service.scheduler,
                    "stop_daemon",
                    return_value=True,
                ), patch.object(
                    service.setup,
                    "_run",
                    side_effect=blocking_setup,
                ), patch.object(
                    service.setup.lease,
                    "heartbeat",
                    side_effect=blocking_heartbeat,
                ), patch.object(
                    service.setup,
                    "_terminate_process",
                    new=AsyncMock(side_effect=terminate_process),
                ), patch.object(
                    service.session,
                    "stop_sync",
                ) as stop_transport:
                    service.start_daemon(owner)
                    service.session.run_sync(service.setup.ensure())
                    first_task = service.setup._task
                    self.assertTrue(entered.wait(2))
                    service.setup._active_process = process
                    self.assertTrue(heartbeat_entered.wait(2))

                    self.assertFalse(service.stop_daemon())
                    stop_transport.assert_not_called()
                    self.assertFalse(first_task.done())
                    with self.assertRaisesRegex(Exception, "stopping"):
                        service.session.run_sync(service.setup.ensure())
                    with self.assertRaises(LockUnavailableError):
                        self._lock(home)

                    release.set()
                    self.assertTrue(service.stop_daemon())
                    self.assertTrue(first_task.done())
                    stop_transport.assert_called_once()
                    stopped = True

                release_lockfile(lock)
                replacement = self._lock(home)
                release_lockfile(replacement)
                time.sleep(0.03)
                self.assertEqual(sorted(effects), ["heartbeat", "process", "setup"])
            finally:
                release.set()
                if not stopped:
                    service.stop_daemon()
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

    def test_non_windows_daemon_rejects_computer_control_before_service_access(self) -> None:
        with patch(
            "no1.computer_control.platform_support._platform_name",
            return_value="darwin",
        ), patch(
            "no1.daemon.computer_control_ops.get_services",
        ) as get_service:
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
        self.assertEqual(
            response.error.code,
            "computer_control_platform_unsupported",
        )
        self.assertEqual(response.error.details["platform"], "darwin")
        self.assertFalse(response.error.details["retryable"])
        get_service.assert_not_called()

    def test_non_windows_daemon_skips_computer_control_startup(self) -> None:
        from no1.daemon.computer_control_ops import start_daemon_computer_control

        lock = Mock()
        with patch(
            "no1.computer_control.platform_support._platform_name",
            return_value="linux",
        ), patch(
            "no1.daemon.computer_control_ops.start_daemon_services",
        ) as start_services:
            self.assertIsNone(
                start_daemon_computer_control(Path("/tmp/home"), lock_handle=lock)
            )
        start_services.assert_not_called()

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

    def test_daemon_server_never_releases_lock_after_failed_computer_drain(self) -> None:
        from no1.daemon import server

        service = Mock()
        service.stop_daemon.return_value = False
        with patch.object(server.logger, "error") as logged:
            self.assertFalse(
                server._stop_daemon_computer_control_before_lock_release(service)
            )
        service.stop_daemon.assert_called_once_with()
        logged.assert_called_once()
        source = inspect.getsource(server.serve_forever)
        self.assertLess(
            source.index("_stop_request_execution_before_lock_release"),
            source.index("_stop_daemon_computer_control_before_lock_release"),
        )
        self.assertLess(
            source.index("_stop_daemon_computer_control_before_lock_release"),
            source.index("cleanup_after_stop"),
        )
        drain_start = source.index("_wait_for_daemon_drains_before_lock_release")
        cleanup = source.index("cleanup_after_stop", drain_start)
        self.assertNotIn("return 1", source[drain_start:cleanup])


if __name__ == "__main__":
    unittest.main()
