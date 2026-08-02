from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from no1.computer_control.derived_authority import DerivedAuthorityClaim, DerivedAuthorityStore
from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.recording import RecordingStore
from no1.computer_control.requests import ComputerRequestStore
from no1.computer_control.storage import WorkflowStore
from no1.contracts.v1 import ChatMessageData
from no1.daemon.messaging.turn_provenance import (
    begin_turn_delivery_attempt,
    build_send_turn_provenance,
    finalize_turn_delivery_attempt,
    get_current_turn_grant,
    get_actor_turn_generation,
    get_daemon_turn_issuer_epoch,
    invalidate_turn_grant,
    turn_delivery_grant_receipt,
    validate_turn_grant_receipt,
)
from no1.kernel.group import Group
from no1.kernel.ledger import append_event


class TestRecordingDerivedAuthority(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {"ONECOLLEAGUE_HOME": str(self.home), "CCCC_HOME": str(self.home)},
            clear=False,
        )
        self.env.start()
        group_path = self.home / "groups" / "g"
        group_path.mkdir(parents=True)
        self.group = Group(group_id="g", path=group_path, doc={"v": 1, "group_id": "g"})
        self.group.save()
        self.issuer = get_daemon_turn_issuer_epoch()
        self.original_issuer = self.issuer
        self.generations = {("g", "actor"): 0}
        self.start_claims = {}
        self.now = 1000.0
        self.requests = ComputerRequestStore(WorkflowStore(self.home))
        self.store = DerivedAuthorityStore(
            self.home,
            issuer_epoch_provider=lambda: self.issuer,
            generation_provider=lambda group_id, actor_id: self.generations.get((group_id, actor_id), 0),
            now_provider=lambda: self.now,
        )

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def _issue(
        self,
        resource_id: str = "recording_1",
        *,
        request_id: str = "request_1",
        attempt_id: str = "attempt_1",
    ):
        start_claim = self._prepare_start_claim(request_id=request_id, attempt_id=attempt_id)
        self.start_claims[request_id] = start_claim
        return self.store.begin_recording(
            group_id="g",
            actor_id="actor",
            resource_id=resource_id,
            request_id=request_id,
            start_claim=start_claim,
        )

    def _prepare_start_claim(
        self,
        *,
        request_id: str,
        attempt_id: str = "attempt_1",
        ttl_seconds: float = 120.0,
        validation_now: float | None = None,
    ):
        current = get_current_turn_grant(self.group, "actor")
        if current is not None:
            invalidate_turn_grant(self.group, "actor", reason="test_next_root")
        provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        request = {
            "actor_id": "actor",
            "request_id": request_id,
            "allow_high_risk": True,
            "mode": "create_and_run",
            "workflow_id": "",
        }
        event = append_event(
            self.group.ledger_path,
            kind="chat.message",
            group_id="g",
            scope_key="",
            by="user",
            data=ChatMessageData(
                text="record",
                to=["actor"],
                computer_control_request=request,
                turn_provenance=provenance,
            ).model_dump(),
        )
        self.requests.append(
            "g",
            {
                **request,
                "event_id": str(event["id"]),
                "local_request_id": str(provenance.local_request_id),
                "status": "accepted",
                "created_ts": time.time(),
            },
        )
        attempt = begin_turn_delivery_attempt(
            self.group,
            actor_id="actor",
            event_ids=[str(event["id"])],
            binding={"transport": "test", "attempt_label": attempt_id},
        )
        receipt = turn_delivery_grant_receipt(attempt)
        finalize_turn_delivery_attempt(
            self.group,
            actor_id="actor",
            attempt=attempt,
            ttl_seconds=ttl_seconds,
            now=validation_now,
        )
        root = validate_turn_grant_receipt(
            self.group,
            "actor",
            turn_grant_receipt=receipt,
            now=validation_now,
        )
        self.assertIsNotNone(root)
        self.generations[("g", "actor")] = int(root["generation"])
        return self.requests.activate_recording_start_for_turn_claim(
            "g",
            request_id,
            "actor",
            claim=root,
        )

    def _assert_no_derived_paths(self) -> None:
        root = self.home / "groups" / "g" / "state" / "computer-control" / "derived-authorities"
        self.assertFalse(root.exists())

    def _validate_active(self, receipt: dict, resource_id: str = "recording_1") -> DerivedAuthorityClaim:
        return self.store.validate_active_receipt(
            receipt,
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=resource_id,
            expected_kind="recording",
        )

    def _validate_suspended(self, receipt: dict, resource_id: str) -> DerivedAuthorityClaim:
        return self.store.validate_suspended_receipt(
            receipt,
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=resource_id,
            expected_kind="recording",
        )

    def test_secret_is_receipt_only_and_exact_receipt_yields_sealed_claim(self) -> None:
        issue = self._issue()
        secret = issue.receipt["authorization_secret"]
        record = self.store.persisted_record("g", "recording_1")
        on_disk = json.dumps(record, sort_keys=True)

        self.assertNotIn(secret, on_disk)
        self.assertNotIn("authorization_secret", on_disk)
        self.assertTrue(record["secret_digest"])
        self.assertEqual(record["state"], "pending")
        with self.assertRaisesRegex(PermissionError, "receipt is invalid"):
            self._validate_active(issue.receipt)

        active = self.store.activate(issue.claim)
        claim = self._validate_active(issue.receipt)
        self.assertEqual(claim, active)
        self.assertTrue(claim.permission_snapshot["allow_high_risk"])
        self.assertFalse(claim.permission_snapshot["allow_publish"])
        with self.assertRaises(TypeError):
            claim.permission_snapshot["allow_publish"] = True  # type: ignore[index]
        with self.assertRaises(TypeError):
            DerivedAuthorityClaim(_seal=object(), record=record)

        disk_only = {
            key: value
            for key, value in record.items()
            if key not in {"secret_digest", "permission_snapshot", "state", "revision", "issued_at_epoch", "updated_at_epoch", "recovery_expires_at_epoch"}
        }
        with self.assertRaisesRegex(PermissionError, "receipt is invalid"):
            self._validate_active(disk_only)

    def test_root_to_derived_rejects_reconstructed_and_dead_capabilities_without_paths(self) -> None:
        from no1.computer_control.authorization import request_turn_authorization

        plain_root = {
            "issuer_epoch": self.issuer,
            "group_id": "g",
            "actor_id": "actor",
            "attempt_id": "copied",
            "generation": 1,
            "event_ids": ["copied"],
            "local_request_ids": ["copied"],
            "authorization_binding": {
                "authority_id": "turnauth_copied",
                "secret_digest": "d" * 64,
            },
        }
        with self.assertRaisesRegex(PermissionError, "validated turn grant claim"):
            request_turn_authorization(
                {"request_id": "copied", "group_id": "g", "actor_id": "actor"},
                plain_root,  # type: ignore[arg-type]
                Mock(),
            )
        with self.assertRaisesRegex(PermissionError, "validated recording start claim"):
            self.store.begin_recording(
                group_id="g",
                actor_id="actor",
                resource_id="recording_copied",
                request_id="copied",
                start_claim=plain_root,  # type: ignore[arg-type]
            )
        self._assert_no_derived_paths()

        current = self._prepare_start_claim(request_id="request_invalidated")
        invalidate_turn_grant(self.group, "actor", reason="test_invalidated")
        with self.assertRaisesRegex(PermissionError, "no longer current"):
            self.store.begin_recording(
                group_id="g",
                actor_id="actor",
                resource_id="recording_invalidated",
                request_id="request_invalidated",
                start_claim=current,
            )
        self._assert_no_derived_paths()

        completed = self._prepare_start_claim(request_id="request_completed")
        invalidate_turn_grant(self.group, "actor", reason="turn_completed")
        with self.assertRaisesRegex(PermissionError, "no longer current"):
            self.store.begin_recording(
                group_id="g",
                actor_id="actor",
                resource_id="recording_completed",
                request_id="request_completed",
                start_claim=completed,
            )
        self._assert_no_derived_paths()

        expired = self._prepare_start_claim(request_id="request_expired")
        expires_at = float((get_current_turn_grant(self.group, "actor") or {})["expires_at_epoch"])
        with patch(
            "no1.daemon.messaging.turn_provenance.time.time",
            return_value=expires_at + 1,
        ), self.assertRaisesRegex(PermissionError, "no longer current"):
            self.store.begin_recording(
                group_id="g",
                actor_id="actor",
                resource_id="recording_expired",
                request_id="request_expired",
                start_claim=expired,
            )
        self._assert_no_derived_paths()

    def test_recording_activation_requires_the_persisted_request_event(self) -> None:
        current = get_current_turn_grant(self.group, "actor")
        if current is not None:
            invalidate_turn_grant(self.group, "actor", reason="test_next_root")
        provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        self.requests.append(
            "g",
            {
                "request_id": "request_fabricated",
                "actor_id": "actor",
                "mode": "create_and_run",
                "event_id": "event_missing",
                "local_request_id": str(provenance.local_request_id),
                "status": "accepted",
                "created_ts": time.time(),
            },
        )
        real_event = append_event(
            self.group.ledger_path,
            kind="chat.message",
            group_id="g",
            scope_key="",
            by="user",
            data=ChatMessageData(
                text="unrelated",
                to=["actor"],
                turn_provenance=provenance,
            ).model_dump(),
        )
        attempt = begin_turn_delivery_attempt(
            self.group,
            actor_id="actor",
            event_ids=[str(real_event["id"])],
            binding={"transport": "test"},
        )
        receipt = turn_delivery_grant_receipt(attempt)
        finalize_turn_delivery_attempt(self.group, actor_id="actor", attempt=attempt)
        root = validate_turn_grant_receipt(self.group, "actor", turn_grant_receipt=receipt)
        self.assertIsNotNone(root)
        with self.assertRaisesRegex(PermissionError, "event was not found"):
            self.requests.activate_recording_start_for_turn_claim(
                "g",
                "request_fabricated",
                "actor",
                claim=root,
            )
        self._assert_no_derived_paths()

    def test_generation_snapshot_does_not_reverse_actor_to_derived_lock_order(self) -> None:
        first = self._issue(resource_id="recording_first", request_id="request_first")
        first_active = self.store.activate(first.claim)
        second_start = self._prepare_start_claim(request_id="request_second")
        validator_waiting = threading.Event()
        derivation_holds_actor = threading.Event()
        validation_errors: list[Exception] = []
        derivation_results = []
        derivation_errors: list[Exception] = []

        def coordinated_generation(group_id: str, actor_id: str) -> int:
            if threading.current_thread().name == "authority-validator":
                validator_waiting.set()
                if not derivation_holds_actor.wait(2):
                    raise RuntimeError("derivation did not enter the actor-locked provider")
            elif threading.current_thread().name == "root-derivation":
                derivation_holds_actor.set()
            return get_actor_turn_generation(group_id, actor_id)

        concurrent_store = DerivedAuthorityStore(
            self.home,
            issuer_epoch_provider=get_daemon_turn_issuer_epoch,
            generation_provider=coordinated_generation,
        )

        def validate_old_receipt() -> None:
            try:
                concurrent_store.validate_active_receipt(
                    first.receipt,
                    expected_group_id="g",
                    expected_actor_id="actor",
                    expected_resource_id="recording_first",
                    expected_kind="recording",
                )
            except Exception as exc:
                validation_errors.append(exc)

        def derive_current_root() -> None:
            try:
                derivation_results.append(
                    concurrent_store.begin_recording(
                        group_id="g",
                        actor_id="actor",
                        resource_id="recording_second",
                        request_id="request_second",
                        start_claim=second_start,
                    )
                )
            except Exception as exc:
                derivation_errors.append(exc)

        validator = threading.Thread(
            target=validate_old_receipt,
            name="authority-validator",
            daemon=True,
        )
        derivation = threading.Thread(
            target=derive_current_root,
            name="root-derivation",
            daemon=True,
        )
        validator.start()
        self.assertTrue(validator_waiting.wait(2))
        derivation.start()
        validator.join(2)
        derivation.join(2)

        self.assertFalse(validator.is_alive())
        self.assertFalse(derivation.is_alive())
        self.assertTrue(validation_errors)
        self.assertIsInstance(validation_errors[0], PermissionError)
        self.assertFalse(derivation_errors)
        self.assertEqual(len(derivation_results), 1)
        with self.assertRaisesRegex(PermissionError, "current daemon and actor generation"):
            concurrent_store.validate_active_receipt(
                first.receipt,
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="recording_first",
                expected_kind="recording",
            )

    def test_root_expiring_while_waiting_for_derived_lock_cannot_write_authority(self) -> None:
        start_claim = self._prepare_start_claim(
            request_id="request_near_expiry",
        )
        provider_entered = threading.Event()
        invalidation_done = threading.Event()
        derivation_errors: list[Exception] = []

        def generation(group_id: str, actor_id: str) -> int:
            provider_entered.set()
            return get_actor_turn_generation(group_id, actor_id)

        expiring_store = DerivedAuthorityStore(
            self.home,
            issuer_epoch_provider=get_daemon_turn_issuer_epoch,
            generation_provider=generation,
        )

        def derive() -> None:
            try:
                expiring_store.begin_recording(
                    group_id="g",
                    actor_id="actor",
                    resource_id="recording_near_expiry",
                    request_id="request_near_expiry",
                    start_claim=start_claim,
                )
            except Exception as exc:
                derivation_errors.append(exc)

        def invalidate() -> None:
            invalidate_turn_grant(self.group, "actor", reason="concurrent_invalidation")
            invalidation_done.set()

        grant = get_current_turn_grant(self.group, "actor")
        self.assertIsNotNone(grant)
        clock = {"now": float(grant["expires_at_epoch"]) - 1.0}
        with patch(
            "no1.daemon.messaging.turn_provenance.time.time",
            side_effect=lambda: clock["now"],
        ):
            held_derived = expiring_store._locked("g")
            held_derived.__enter__()
            try:
                derivation = threading.Thread(target=derive, daemon=True)
                derivation.start()
                self.assertTrue(provider_entered.wait(2))
                invalidator = threading.Thread(target=invalidate, daemon=True)
                invalidator.start()
                self.assertFalse(invalidation_done.wait(0.05))
                clock["now"] = float(grant["expires_at_epoch"]) + 1.0
            finally:
                held_derived.__exit__(None, None, None)
            derivation.join(2)
            invalidator.join(2)
        self.assertFalse(derivation.is_alive())
        self.assertFalse(invalidator.is_alive())
        self.assertTrue(invalidation_done.is_set())
        self.assertEqual(len(derivation_errors), 1)
        self.assertIsInstance(derivation_errors[0], PermissionError)
        authority_root = (
            self.home / "groups" / "g" / "state" / "computer-control" / "derived-authorities"
        )
        self.assertEqual(list(authority_root.glob("*.json")), [])

        successful_start = self._prepare_start_claim(request_id="request_write_order")
        write_entered = threading.Event()
        allow_write = threading.Event()
        invalidation_done.clear()
        issues = []
        successful_errors: list[Exception] = []
        original_write = expiring_store._write

        def blocked_write(path, value) -> None:
            write_entered.set()
            if not allow_write.wait(2):
                raise RuntimeError("authority write was not released")
            original_write(path, value)

        def derive_successfully() -> None:
            try:
                issues.append(
                    expiring_store.begin_recording(
                        group_id="g",
                        actor_id="actor",
                        resource_id="recording_write_order",
                        request_id="request_write_order",
                        start_claim=successful_start,
                    )
                )
            except Exception as exc:
                successful_errors.append(exc)

        with patch.object(expiring_store, "_write", side_effect=blocked_write):
            successful_derivation = threading.Thread(target=derive_successfully, daemon=True)
            successful_derivation.start()
            self.assertTrue(write_entered.wait(2))
            invalidator = threading.Thread(target=invalidate, daemon=True)
            invalidator.start()
            self.assertFalse(invalidation_done.wait(0.05))
            allow_write.set()
            successful_derivation.join(2)
            invalidator.join(2)

        self.assertFalse(successful_errors)
        self.assertEqual(len(issues), 1)
        self.assertTrue(invalidation_done.is_set())
        self.assertEqual(len(list(authority_root.glob("*.json"))), 1)

    def test_receipt_is_bound_to_scope_epoch_generation_and_recovery_ttl(self) -> None:
        issue = self._issue()
        self.store.activate(issue.claim)
        mutations = {
            "kind": "run",
            "resource_id": "recording_other",
            "group_id": "other",
            "actor_id": "other",
            "authority_id": "recordingauth_forged",
            "scope_digest": "0" * 64,
            "root_authority_id": "turnauth_other",
            "authorization_secret": "forged",
        }
        for field, value in mutations.items():
            with self.subTest(field=field), self.assertRaises(PermissionError):
                self._validate_active({**issue.receipt, field: value})

        self.generations[("g", "actor")] = 8
        with self.assertRaisesRegex(PermissionError, "current daemon and actor generation"):
            self._validate_active(issue.receipt)
        self.generations[("g", "actor")] = issue.claim.generation
        self.issuer = "daemon_epoch_2"
        with self.assertRaisesRegex(PermissionError, "current daemon and actor generation"):
            self._validate_active(issue.receipt)
        self.issuer = self.original_issuer
        self.now += self.store.RECOVERY_TTL_SECONDS + 1
        with self.assertRaisesRegex(PermissionError, "receipt is invalid"):
            self._validate_active(issue.receipt)

    def test_receipt_identity_is_rejected_before_locking_untrusted_paths(self) -> None:
        issue = self._issue()
        self.store.activate(issue.claim)
        cross_group_root = (
            self.home
            / "groups"
            / "other"
            / "state"
            / "computer-control"
            / "derived-authorities"
        )
        traversal_group = f"../../authority_escape_{self.home.name}"
        traversal_root = (
            self.home
            / "groups"
            / traversal_group
            / "state"
            / "computer-control"
            / "derived-authorities"
        ).resolve()
        self.assertFalse(cross_group_root.exists())
        self.assertFalse(traversal_root.exists())

        for label, forged_group, forbidden_root in (
            ("cross_group", "other", cross_group_root),
            ("path_traversal", traversal_group, traversal_root),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                PermissionError, "does not match the expected resource"
            ):
                self._validate_active({**issue.receipt, "group_id": forged_group})
            self.assertFalse(forbidden_root.exists())

    def test_lease_requires_exact_claim_and_only_newer_revision_can_reacquire(self) -> None:
        issue = self._issue()
        lease = ComputerControlLease(self.home)
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.acquire(group_id="g", actor_id="actor", run_id="recording_1", authority=issue.claim)

        lease.reserve(group_id="g", actor_id="actor", run_id="recording_1", authority=issue.claim)
        active = self.store.activate(issue.claim)
        lease.activate_reservation(pending=issue.claim, active=active)
        for label, identity in (
            ("group", {"group_id": "other", "actor_id": "actor", "run_id": "recording_1"}),
            ("actor", {"group_id": "g", "actor_id": "other", "run_id": "recording_1"}),
            ("resource", {"group_id": "g", "actor_id": "actor", "run_id": "recording_other"}),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(PermissionError, "authority_required"):
                lease.acquire(**identity, authority=active)
        lease.require(group_id="g", actor_id="actor", run_id="recording_1", authority=active)

        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.heartbeat(group_id="g", actor_id="actor", run_id="recording_1")
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.release(run_id="recording_1", authority=issue.claim)
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.require(group_id="g", actor_id="actor", run_id="recording_1", authority=issue.claim)

        self.assertTrue(lease.release(run_id="recording_1", authority=active))
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.acquire(group_id="g", actor_id="actor", run_id="recording_1", authority=active)

        suspended = self.store.suspend(active)
        resumed = self.store.resume(suspended)
        reacquired = lease.acquire(
            group_id="g",
            actor_id="actor",
            run_id="recording_1",
            authority=resumed,
        )
        self.assertEqual(reacquired["authority"], resumed.lease_identity())

        other = self._issue(
            "recording_2",
            request_id="request_2",
            attempt_id="attempt_2",
        )
        other_active = self.store.activate(other.claim)
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.acquire(
                group_id="g",
                actor_id="actor",
                run_id="recording_1",
                authority=other_active,
            )

    def test_revoked_authority_cannot_be_reclaimed_or_replaced(self) -> None:
        issue = self._issue("recording_revoked")
        active = self.store.activate(issue.claim)
        revoked = self.store.revoke(active)

        with self.assertRaisesRegex(PermissionError, "receipt is invalid"):
            self._validate_active(issue.receipt, "recording_revoked")
        with self.assertRaisesRegex(PermissionError, "receipt is invalid"):
            self._validate_suspended(issue.receipt, "recording_revoked")
        with self.assertRaisesRegex(PermissionError, "already exists"):
            self.store.begin_recording(
                group_id="g",
                actor_id="actor",
                resource_id="recording_revoked",
                request_id="request_1",
                start_claim=self.start_claims["request_1"],
            )

        lease = ComputerControlLease(self.home)
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.acquire(
                group_id="g",
                actor_id="actor",
                run_id="recording_revoked",
                authority=revoked,
            )

    def test_suspended_and_revoked_claims_cannot_drive_active_lease_actions(self) -> None:
        issue = self._issue("recording_state")
        active = self.store.activate(issue.claim)
        lease = ComputerControlLease(self.home)
        lease.acquire(group_id="g", actor_id="actor", run_id="recording_state", authority=active)
        suspended = self.store.suspend(active)
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.require(
                group_id="g",
                actor_id="actor",
                run_id="recording_state",
                authority=suspended,
            )
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.heartbeat(
                group_id="g",
                actor_id="actor",
                run_id="recording_state",
                authority=suspended,
            )
        revoked = self.store.revoke(suspended)
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            lease.release(run_id="recording_state", authority=revoked)
        lease.release(run_id="", force=True)

    def test_domain_rejects_missing_typed_claim_before_request_provider_lease_or_recording_store(self) -> None:
        workflows = Mock()
        requests = Mock()
        lease = Mock()
        session = Mock()
        recordings = RecordingStore(
            self.home,
            workflows,
            requests,
            self.store,
            lease,
            session,
        )
        with self.assertRaisesRegex(PermissionError, "validated recording start claim"):
            recordings.start(
                "g",
                actor_id="actor",
                request_id="request_1",
                start_claim=None,
                name="forged",
            )
        with self.assertRaisesRegex(PermissionError, "validated derived authority claim"):
            recordings.call(
                "g",
                "recording_1",
                actor_id="actor",
                authority=None,
                tool="Snapshot",
                arguments={},
            )
        requests.require_authorized.assert_not_called()
        session.catalog_sync.assert_not_called()
        lease.acquire.assert_not_called()
        workflows.state_root.assert_not_called()


if __name__ == "__main__":
    unittest.main()
