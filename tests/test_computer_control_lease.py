import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from no1.computer_control.lease import ComputerControlLease
from no1.daemon.computer_control_ops import try_handle_computer_control_op


class TestComputerControlLeaseGuard(unittest.TestCase):
    def test_long_operation_heartbeats_until_exit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lease = ComputerControlLease(Path(td))
            lease.TTL_SECONDS = 0.12
            lease.HEARTBEAT_SECONDS = 0.04

            with lease.hold(group_id="g", actor_id="setup", run_id="setup"):
                time.sleep(0.22)
                status = lease.status()
                self.assertTrue(status["active"])
                self.assertEqual(status["lease"]["run_id"], "setup")

            self.assertFalse(lease.status()["active"])

    def test_stale_guard_never_releases_new_owner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lease = ComputerControlLease(Path(td))
            lease.TTL_SECONDS = 0.05
            lease.HEARTBEAT_SECONDS = 10

            with lease.hold(group_id="g", actor_id="old", run_id="old"):
                time.sleep(0.08)
                lease.acquire(group_id="g", actor_id="new", run_id="new")

            status = lease.status()
            self.assertTrue(status["active"])
            self.assertEqual(status["lease"]["run_id"], "new")

    def test_daemon_lease_status_does_not_open_mcp_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake = Mock()
            fake.lease = ComputerControlLease(Path(td))
            fake.session.catalog_sync.side_effect = AssertionError("catalog must not be queried")
            with patch("no1.daemon.computer_control_ops.get_services", return_value=fake), patch(
                "no1.daemon.computer_control_ops.ensure_home", return_value=Path(td)
            ):
                response, _ = try_handle_computer_control_op(
                    "computer_control",
                    {"command": "lease", "action": "status", "group_id": "_global"},
                )
            self.assertTrue(response.ok)
            self.assertFalse(response.result["result"]["active"])
            fake.session.catalog_sync.assert_not_called()

    def test_setup_upgrade_is_rejected_while_machine_is_leased(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lease = ComputerControlLease(Path(td))
            lease.acquire(group_id="g", actor_id="actor", run_id="run")
            fake = Mock()
            fake.lease = lease
            fake.setup.status.return_value = {"phase": "ready", "session_running": True}
            with patch("no1.daemon.computer_control_ops.get_services", return_value=fake), patch(
                "no1.daemon.computer_control_ops.ensure_home", return_value=Path(td)
            ):
                response, _ = try_handle_computer_control_op(
                    "computer_control",
                    {"command": "setup", "action": "upgrade", "group_id": "_global"},
                )
            self.assertFalse(response.ok)
            self.assertEqual(response.error.code, "computer_control_busy")
            fake.setup.upgrade.assert_not_called()


if __name__ == "__main__":
    unittest.main()
