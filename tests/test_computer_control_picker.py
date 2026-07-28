import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import no1.computer_control.picker as picker_module
from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.picker import ElementPickerManager, UnavailableUIAProbe


def _element(**overrides):
    value = {
        "window_name": "Demo",
        "name": "提交",
        "control_type": "Button",
        "automation_id": "submit",
        "process_name": "demo.exe",
        "process_id": 41,
        "window_handle": 99,
        "runtime_id": [42, 1001],
        "bounds": {"x": 10, "y": 20, "width": 80, "height": 30},
    }
    value.update(overrides)
    return value


class FakeProbe:
    available = True

    def __init__(self, element=None):
        self.element = dict(element or _element())
        self.call_threads = []
        self.close_threads = []

    def element_at(self, point):
        self.call_threads.append(threading.get_ident())
        return dict(self.element)

    def close(self):
        self.close_threads.append(threading.get_ident())


class FakeOverlay:
    def __init__(self):
        self.available = False
        self.start_threads = []
        self.update_threads = []
        self.stop_calls = 0
        self.updated = threading.Event()

    def start(self):
        self.start_threads.append(threading.get_ident())
        self.available = True
        return True

    def update(self, bounds):
        self.update_threads.append(threading.get_ident())
        self.updated.set()

    def stop(self):
        self.stop_calls += 1
        self.available = False


class TestElementPickerManager(unittest.TestCase):
    def test_lock_gesture_uses_ctrl_shift_left_click(self):
        requested_keys = []

        class FakeUser32:
            @staticmethod
            def GetAsyncKeyState(key):
                requested_keys.append(key)
                return 0x8001 if key == 0x01 else 0x8000

        with patch.object(picker_module.sys, "platform", "win32"), patch.object(
            picker_module.ctypes,
            "windll",
            SimpleNamespace(user32=FakeUser32()),
        ):
            self.assertTrue(ElementPickerManager._lock_gesture_pressed())

        self.assertEqual(set(requested_keys), {0x01, 0x10, 0x11})
        self.assertNotIn(ord("L"), requested_keys)

    def test_session_lifecycle_uses_observe_only_lease(self):
        with tempfile.TemporaryDirectory() as td:
            manager = ElementPickerManager(
                Path(td),
                ComputerControlLease(Path(td)),
                probe_factory=UnavailableUIAProbe,
            )
            started = manager.start("group", "actor")
            worker = manager._thread
            self.assertEqual(started["status"], "active")
            self.assertEqual(manager.lease.status()["lease"]["run_id"], started["session_id"])
            events = manager.events(started["session_id"])
            self.assertEqual(events["events"][0]["type"], "started")
            ended = manager.cancel(started["session_id"], reason="test")
            self.assertEqual(ended["status"], "cancelled")
            self.assertFalse(manager.lease.status()["active"])
            self.assertIsNotNone(worker)
            worker.join(timeout=2)

    def test_snapshot_fallback_can_lock_and_confirm_without_native_ui(self):
        snapshot = {
            "elements": [
                {
                    "window_name": "Demo",
                    "name": "提交",
                    "control_type": "Button",
                    "bounds": {"x": 10, "y": 20, "width": 80, "height": 30},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as td, patch("no1.computer_control.picker.time.sleep", return_value=None), patch("no1.computer_control.picker._cursor_position", return_value=None):
            manager = ElementPickerManager(
                Path(td),
                ComputerControlLease(Path(td)),
                snapshot_provider=lambda: snapshot,
                probe_factory=UnavailableUIAProbe,
            )
            started = manager.start("group", "actor")
            worker = manager._thread
            locked = manager.lock(started["session_id"], point=[20, 25])
            self.assertTrue(locked["element"]["locator_preview"]["window_name"] == "Demo")
            self.assertTrue(locked["stable"])
            confirmed = manager.confirm(started["session_id"])
            self.assertEqual(confirmed["session"]["status"], "confirmed")
            self.assertEqual(confirmed["locator"]["fallback_policy"], "never")
            self.assertIsNotNone(worker)
            worker.join(timeout=2)

    def test_uia_probe_and_lock_sampling_stay_on_owner_thread(self):
        main_thread = threading.get_ident()
        factory_threads = []
        initialized_threads = []
        uninitialized_threads = []
        probe = FakeProbe()
        overlay = FakeOverlay()
        com_token = object()

        def probe_factory():
            factory_threads.append(threading.get_ident())
            return probe

        def initialize_com():
            initialized_threads.append(threading.get_ident())
            return com_token

        def uninitialize_com(token):
            self.assertIs(token, com_token)
            uninitialized_threads.append(threading.get_ident())

        with tempfile.TemporaryDirectory() as td, patch(
            "no1.computer_control.picker._initialize_com", side_effect=initialize_com
        ), patch(
            "no1.computer_control.picker._uninitialize_com", side_effect=uninitialize_com
        ), patch(
            "no1.computer_control.picker._cursor_position", return_value=[20, 25]
        ), patch(
            "no1.computer_control.picker.time.sleep", return_value=None
        ):
            manager = ElementPickerManager(
                Path(td),
                ComputerControlLease(Path(td)),
                probe_factory=probe_factory,
            )
            manager.overlay = overlay
            started = manager.start("group", "actor")
            owner_worker = manager._thread
            self.assertTrue(overlay.updated.wait(timeout=2))
            locked = manager.lock(started["session_id"], point=[20, 25])
            self.assertTrue(locked["stable"])
            calls_before_confirm = len(probe.call_threads)
            manager.confirm(started["session_id"])
            self.assertIsNotNone(owner_worker)
            owner_worker.join(timeout=2)

        self.assertFalse(owner_worker.is_alive())
        self.assertIsNone(manager._thread)
        self.assertEqual(len(probe.call_threads), calls_before_confirm)
        owner_threads = set(factory_threads + initialized_threads + probe.call_threads + overlay.start_threads + overlay.update_threads)
        self.assertEqual(len(owner_threads), 1)
        self.assertNotIn(main_thread, owner_threads)
        self.assertEqual(uninitialized_threads, factory_threads)
        self.assertEqual(probe.close_threads, factory_threads)
        self.assertGreaterEqual(overlay.stop_calls, 1)

    def test_consecutive_sessions_restart_overlay_and_keep_second_session_usable(self):
        probe = FakeProbe()
        overlay = FakeOverlay()

        with tempfile.TemporaryDirectory() as td, patch(
            "no1.computer_control.picker._initialize_com", return_value=object()
        ), patch(
            "no1.computer_control.picker._uninitialize_com"
        ), patch(
            "no1.computer_control.picker._cursor_position", return_value=[20, 25]
        ), patch(
            "no1.computer_control.picker.time.sleep", return_value=None
        ):
            manager = ElementPickerManager(
                Path(td),
                ComputerControlLease(Path(td)),
                probe_factory=lambda: probe,
            )
            manager.overlay = overlay

            first = manager.start("group", "actor")
            self.assertTrue(overlay.updated.wait(timeout=2))
            first_worker = manager._thread
            manager.cancel(first["session_id"])
            self.assertIsNotNone(first_worker)
            first_worker.join(timeout=2)
            self.assertFalse(first_worker.is_alive())

            overlay.updated.clear()
            first_update_count = len(overlay.update_threads)
            second = manager.start("group", "actor")
            second_worker = manager._thread
            self.assertIsNot(second_worker, first_worker)
            self.assertTrue(overlay.updated.wait(timeout=2))
            self.assertGreater(len(overlay.update_threads), first_update_count)
            self.assertTrue(manager.status(second["session_id"])["overlay_available"])

            locked = manager.lock(second["session_id"], point=[20, 25])
            self.assertTrue(locked["stable"])
            manager.cancel(second["session_id"])
            self.assertIsNotNone(second_worker)
            second_worker.join(timeout=2)
            self.assertFalse(second_worker.is_alive())

        self.assertGreaterEqual(len(overlay.start_threads), 2)
        self.assertGreaterEqual(overlay.stop_calls, 2)

    def test_same_name_with_different_runtime_id_is_unstable(self):
        samples = [_element(runtime_id=[42, value]) for value in (1, 2, 2)]
        stable, reasons = ElementPickerManager._assess_stability(samples)
        self.assertFalse(stable)
        self.assertIn("元素 RuntimeId 发生变化", reasons)

    def test_same_runtime_id_with_bounds_drift_is_unstable(self):
        samples = [
            _element(),
            _element(bounds={"x": 16, "y": 20, "width": 80, "height": 30}),
            _element(),
        ]
        stable, reasons = ElementPickerManager._assess_stability(samples)
        self.assertFalse(stable)
        self.assertIn("元素边界在采样期间移动", reasons)

    def test_same_runtime_id_and_bounds_is_high_stability(self):
        stable, reasons = ElementPickerManager._assess_stability([_element(), _element(), _element()])
        self.assertTrue(stable)
        self.assertEqual(reasons, [])

    def test_duplicate_name_candidates_cannot_be_high_stability(self):
        samples = [_element(_picker_match_count=2, runtime_id=None, automation_id="") for _ in range(3)]
        stable, reasons = ElementPickerManager._assess_stability(samples)
        self.assertFalse(stable)
        self.assertIn("检测到同名元素，无法确认唯一目标", reasons)

    def test_slow_snapshot_sampling_has_independent_lease_heartbeat(self):
        snapshot = {"elements": [_element()]}

        def slow_snapshot():
            threading.Event().wait(0.08)
            return snapshot

        with tempfile.TemporaryDirectory() as td, patch(
            "no1.computer_control.picker._cursor_position", return_value=None
        ):
            lease = ComputerControlLease(Path(td))
            lease.TTL_SECONDS = 0.05
            lease.HEARTBEAT_SECONDS = 0.02
            manager = ElementPickerManager(
                Path(td),
                lease,
                snapshot_provider=slow_snapshot,
                probe_factory=UnavailableUIAProbe,
            )
            started = manager.start("group", "actor")
            worker = manager._thread
            locked = manager.lock(started["session_id"], point=[20, 25])
            self.assertTrue(locked["stable"])
            self.assertTrue(lease.status()["active"])
            manager.cancel(started["session_id"])
            self.assertIsNotNone(worker)
            worker.join(timeout=2)

    def test_picker_thread_failure_releases_its_lease(self):
        class FailingProbe(FakeProbe):
            def element_at(self, point):
                del point
                raise RuntimeError("probe failed")

        with tempfile.TemporaryDirectory() as td, patch(
            "no1.computer_control.picker._cursor_position", return_value=[20, 25]
        ):
            lease = ComputerControlLease(Path(td))
            manager = ElementPickerManager(Path(td), lease, probe_factory=FailingProbe)
            started = manager.start("group", "actor")
            failed_worker = manager._thread
            self.assertIsNotNone(failed_worker)
            failed_worker.join(timeout=2)
            self.assertFalse(failed_worker.is_alive())
            self.assertIsNone(manager._thread)
            self.assertFalse(lease.status()["active"])
            self.assertEqual(manager.status(started["session_id"])["status"], "failed")


if __name__ == "__main__":
    unittest.main()
