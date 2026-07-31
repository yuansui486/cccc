import copy
import gc
import json
import tempfile
import time
import unittest
import weakref
from pathlib import Path
from unittest.mock import Mock, patch

import no1.computer_control.run_authority as run_authority_module
from no1.computer_control.lease import ComputerControlLease, LeaseConflict
from no1.computer_control.run_authority import (
    LegacyRunAllocationClaim,
    RunAuthorityStore,
)
from no1.daemon.computer_control_ops import try_handle_computer_control_op


class TestComputerControlLeaseGuard(unittest.TestCase):
    @staticmethod
    def _allocate(home: Path, *, run_id: str = "run"):
        store = RunAuthorityStore(
            home,
            issuer_epoch_provider=lambda: "legacy_namespace",
            generation_provider=lambda _group_id, _actor_id: 1,
        )
        lease = ComputerControlLease(home)
        claim, run = store.allocate_legacy_run(
            group_id="g",
            actor_id="actor",
            resource_id=run_id,
            initial_run={
                "origin": "legacy_internal",
                "status": "initializing",
                "group_id": "g",
                "actor_id": "actor",
                "run_id": run_id,
            },
            reserve_lease=lambda allocation: lease.reserve_legacy_run(
                group_id="g",
                actor_id="actor",
                run_id=run_id,
                allocation=allocation,
            ),
            cancel_reservation=lambda allocation: lease.cancel_legacy_run_reservation(
                allocation=allocation
            ),
        )
        return store, lease, claim, run

    def test_legacy_allocation_lease_is_non_idempotent_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, lease, claim, run = self._allocate(Path(td))
            with self.assertRaises(LeaseConflict):
                lease.reserve_legacy_run(
                    group_id="g",
                    actor_id="actor",
                    run_id="run",
                    allocation=claim,
                )
            activated = lease.activate_legacy_run_reservation(allocation=claim)
            self.assertNotIn("reservation", activated)
            self.assertEqual(
                lease.require_legacy_run(allocation=claim)["legacy_allocation"],
                run["legacy_allocation"],
            )
            lease.heartbeat_legacy_run(allocation=claim)

            with self.assertRaises(LeaseConflict):
                lease.acquire(group_id="g", actor_id="actor", run_id="run")
            with self.assertRaises(PermissionError):
                lease.require(group_id="g", actor_id="actor", run_id="run")
            with self.assertRaises(PermissionError):
                lease.heartbeat(group_id="g", actor_id="actor", run_id="run")
            with self.assertRaises(PermissionError):
                lease.release(run_id="run")

            self.assertTrue(lease.release_legacy_run(allocation=claim))
            self.assertFalse(lease.release_legacy_run(allocation=claim))

    def test_forged_copied_and_publicly_tampered_allocation_claims_are_rejected(self) -> None:
        fields = (
            "authority_home",
            "allocation_id",
            "group_id",
            "actor_id",
            "resource_id",
            "_seal",
        )
        for field in fields:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                _, lease, claim, _ = self._allocate(Path(td))
                lease.activate_legacy_run_reservation(allocation=claim)
                before = lease.path.read_bytes()
                object.__setattr__(claim, field, object() if field == "_seal" else "tampered")
                with self.assertRaises(PermissionError):
                    lease.require_legacy_run(allocation=claim)
                self.assertEqual(lease.path.read_bytes(), before)

        with tempfile.TemporaryDirectory() as td:
            _, lease, claim, _ = self._allocate(Path(td))
            lease.activate_legacy_run_reservation(allocation=claim)
            forged = LegacyRunAllocationClaim(
                {
                    "authority_home": claim.authority_home,
                    **claim.public_identity(),
                }
            )
            copied = copy.copy(claim)
            for candidate in (forged, copied):
                with self.subTest(candidate=type(candidate).__name__):
                    with self.assertRaises(PermissionError):
                        lease.require_legacy_run(allocation=candidate)

    def test_allocation_claim_is_canonical_home_bound_across_alias_redirection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home_a = root / "a"
            home_b = root / "b"
            home_a.mkdir()
            home_b.mkdir()
            alias = root / "alias"
            alias.symlink_to(home_a, target_is_directory=True)

            store, lease, claim, _ = self._allocate(alias)
            same_target = ComputerControlLease(home_a)
            same_target.activate_legacy_run_reservation(allocation=claim)
            self.assertEqual(
                same_target.require_legacy_run(allocation=claim)["run_id"],
                "run",
            )

            alias.unlink()
            alias.symlink_to(home_b, target_is_directory=True)
            lease.heartbeat_legacy_run(allocation=claim)
            self.assertFalse((home_b / "state" / "computer-control" / "lease.json").exists())
            redirected = ComputerControlLease(alias)
            with self.assertRaises(PermissionError):
                redirected.require_legacy_run(allocation=claim)
            with self.assertRaises(PermissionError):
                RunAuthorityStore(
                    alias,
                    issuer_epoch_provider=lambda: "legacy_namespace",
                    generation_provider=lambda _group_id, _actor_id: 1,
                ).write_legacy_run(claim, store._read(store._run_path("g", "run")))

    def test_cross_home_and_stale_allocation_cleanup_have_zero_effect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home_a = root / "a"
            home_b = root / "b"
            _, lease_a, old_claim, _ = self._allocate(home_a)
            lease_a.activate_legacy_run_reservation(allocation=old_claim)
            lease_b = ComputerControlLease(home_b)
            before_b = list(home_b.rglob("*")) if home_b.exists() else []
            with self.assertRaises(PermissionError):
                lease_b.release_legacy_run(allocation=old_claim)
            self.assertEqual(list(home_b.rglob("*")) if home_b.exists() else [], before_b)

            self.assertTrue(lease_a.release_legacy_run(allocation=old_claim))
            run_path = (
                home_a
                / "groups"
                / "g"
                / "state"
                / "computer-control"
                / "runs"
                / "run.json"
            )
            run_path.unlink()
            _, replacement_lease, replacement, _ = self._allocate(home_a)
            replacement_lease.activate_legacy_run_reservation(allocation=replacement)
            replacement_before = replacement_lease.path.read_bytes()
            self.assertFalse(lease_a.cancel_legacy_run_reservation(allocation=old_claim))
            self.assertFalse(lease_a.release_legacy_run(allocation=old_claim))
            self.assertEqual(replacement_lease.path.read_bytes(), replacement_before)

    def test_allocation_owner_registry_is_identity_bound_and_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, _, claim, _ = self._allocate(Path(td))
            identity = id(claim)
            reference = weakref.ref(claim)
            self.assertIn(identity, run_authority_module._LEGACY_ALLOCATION_OWNERS)
            del claim
            gc.collect()
            self.assertIsNone(reference())
            self.assertNotIn(identity, run_authority_module._LEGACY_ALLOCATION_OWNERS)

    def test_stale_registry_cleanup_cannot_remove_reused_identity_slot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _, _, claim, _ = self._allocate(Path(td))
            identity = id(claim)
            stale_reference = run_authority_module._LEGACY_ALLOCATION_OWNERS[identity][0]
            replacement = LegacyRunAllocationClaim(
                {
                    "authority_home": str(Path(td).resolve()),
                    "allocation_id": "legacyalloc_" + "a" * 48,
                    "group_id": "g",
                    "actor_id": "actor",
                    "resource_id": "replacement",
                }
            )
            replacement_reference = weakref.ref(replacement)
            replacement_entry = (
                replacement_reference,
                str(Path(td).resolve()),
                {"sentinel": "replacement"},
            )
            with run_authority_module._LEGACY_ALLOCATION_OWNERS_GUARD:
                run_authority_module._LEGACY_ALLOCATION_OWNERS[identity] = replacement_entry
            run_authority_module._discard_legacy_allocation_owner(
                identity,
                stale_reference,
            )
            self.assertIs(
                run_authority_module._LEGACY_ALLOCATION_OWNERS[identity],
                replacement_entry,
            )
            with run_authority_module._LEGACY_ALLOCATION_OWNERS_GUARD:
                run_authority_module._LEGACY_ALLOCATION_OWNERS.pop(identity, None)

    def test_legacy_lease_disk_tuple_and_allocation_tampering_is_zero_effect(self) -> None:
        variants = (
            ("group_id", "other"),
            ("actor_id", "other"),
            ("run_id", "other"),
            ("legacy_allocation.allocation_id", "legacyalloc_" + "f" * 48),
        )
        for phase in ("reservation", "active"):
            for field, replacement in variants:
                with self.subTest(phase=phase, field=field), tempfile.TemporaryDirectory() as td:
                    _, lease, claim, _ = self._allocate(Path(td))
                    if phase == "active":
                        lease.activate_legacy_run_reservation(allocation=claim)
                    value = json.loads(lease.path.read_text(encoding="utf-8"))
                    if field.startswith("legacy_allocation."):
                        value["legacy_allocation"][field.split(".", 1)[1]] = replacement
                    else:
                        value[field] = replacement
                    lease.path.write_text(
                        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    before = lease.path.read_bytes()
                    if phase == "reservation":
                        with self.assertRaises(PermissionError):
                            lease.activate_legacy_run_reservation(allocation=claim)
                        self.assertFalse(
                            lease.cancel_legacy_run_reservation(allocation=claim)
                        )
                    else:
                        with self.assertRaises(PermissionError):
                            lease.require_legacy_run(allocation=claim)
                        with self.assertRaises(PermissionError):
                            lease.heartbeat_legacy_run(allocation=claim)
                        self.assertFalse(lease.release_legacy_run(allocation=claim))
                    self.assertEqual(lease.path.read_bytes(), before)

    def test_legacy_authority_free_lease_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lease = ComputerControlLease(Path(td))
            acquired = lease.acquire(group_id="g", actor_id="actor", run_id="run")
            self.assertNotIn("authority", acquired)
            self.assertEqual(
                lease.require(group_id="g", actor_id="actor", run_id="run")["run_id"],
                "run",
            )
            lease.heartbeat(group_id="g", actor_id="actor", run_id="run")
            self.assertTrue(lease.release(run_id="run"))
            self.assertFalse(lease.status()["active"])

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
                    {"command": "lease", "action": "status", "group_id": "_global", "caller_surface": "local_web"},
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
                    {"command": "setup", "action": "upgrade", "group_id": "_global", "caller_surface": "local_web"},
                )
            self.assertFalse(response.ok)
            self.assertEqual(response.error.code, "computer_control_busy")
            fake.setup.upgrade.assert_not_called()


if __name__ == "__main__":
    unittest.main()
