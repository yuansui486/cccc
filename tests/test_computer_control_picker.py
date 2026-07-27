import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.picker import ElementPickerManager


class TestElementPickerManager(unittest.TestCase):
    def test_session_lifecycle_uses_observe_only_lease(self):
        with tempfile.TemporaryDirectory() as td:
            manager = ElementPickerManager(Path(td), ComputerControlLease(Path(td)))
            started = manager.start("group", "actor")
            self.assertEqual(started["status"], "active")
            self.assertEqual(manager.lease.status()["lease"]["run_id"], started["session_id"])
            events = manager.events(started["session_id"])
            self.assertEqual(events["events"][0]["type"], "started")
            ended = manager.cancel(started["session_id"], reason="test")
            self.assertEqual(ended["status"], "cancelled")
            self.assertFalse(manager.lease.status()["active"])

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
            manager = ElementPickerManager(Path(td), ComputerControlLease(Path(td)), snapshot_provider=lambda: snapshot)
            # Force the portable path even on a developer Windows machine.
            manager.probe.available = False
            started = manager.start("group", "actor")
            locked = manager.lock(started["session_id"], point=[20, 25])
            self.assertTrue(locked["element"]["locator_preview"]["window_name"] == "Demo")
            self.assertTrue(locked["stable"])
            confirmed = manager.confirm(started["session_id"])
            self.assertEqual(confirmed["session"]["status"], "confirmed")
            self.assertEqual(confirmed["locator"]["fallback_policy"], "never")


if __name__ == "__main__":
    unittest.main()
