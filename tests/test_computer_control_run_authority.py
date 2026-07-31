from __future__ import annotations

import json
import math
import os
import pickle
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import no1.computer_control.run_authority as run_authority_module
from no1.computer_control.authorization import (
    normalized_run_inputs,
    run_inputs_digest,
    workflow_definition_digest,
)
from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.models import WorkflowDefinition, computer_control_permissions
from no1.computer_control.requests import ComputerRequestStore
from no1.computer_control.run_authority import (
    RunAuthorityStore,
    RunExecutionClaim,
    RunExecutionSeedClaim,
)
from no1.computer_control.storage import WorkflowStore
from no1.contracts.v1 import ChatMessageData
from no1.daemon.messaging.turn_provenance import (
    begin_turn_delivery_attempt,
    build_send_turn_provenance,
    finalize_turn_delivery_attempt,
    get_actor_turn_generation,
    get_current_turn_grant,
    get_daemon_turn_issuer_epoch,
    invalidate_turn_grant,
    turn_delivery_grant_receipt,
    validate_turn_grant_receipt,
)
from no1.kernel.group import Group
from no1.kernel.ledger import append_event


_MISSING = object()


class TestRunAuthorityKernel(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"CCCC_HOME": str(self.home)}, clear=False)
        self.env.start()
        group_path = self.home / "groups" / "g"
        group_path.mkdir(parents=True)
        self.group = Group(group_id="g", path=group_path, doc={"v": 1, "group_id": "g"})
        self.group.save()
        self.workflows = WorkflowStore(self.home)
        self.requests = ComputerRequestStore(self.workflows)
        self.workflow = self.workflows.create(
            "g",
            WorkflowDefinition(
                name="Run authority test",
                inputs={"message": "default"},
                nodes=[
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                edges=[{"source": "start", "target": "end"}],
            ),
        )
        self.workflow_id = str(self.workflow["manifest"]["workflow_id"])
        self.version = int(self.workflow["version"])
        self.issuer = get_daemon_turn_issuer_epoch()
        self.now = 1000.0
        self.authorities = RunAuthorityStore(
            self.home,
            issuer_epoch_provider=lambda: self.issuer,
            generation_provider=get_actor_turn_generation,
            now_provider=lambda: self.now,
        )
        self.lease = ComputerControlLease(self.home)

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    @property
    def derived_root(self) -> Path:
        return self.group.path / "state" / "computer-control" / "derived-authorities"

    @property
    def lease_path(self) -> Path:
        return self.home / "state" / "computer-control" / "lease.json"

    @property
    def lineage_path(self) -> Path:
        return self.home / "state" / "computer-control" / "lease-authority-lineages.json"

    def _prepare_start_claim(
        self,
        *,
        request_id: str = "request_1",
        inputs: object = _MISSING,
    ):
        current = get_current_turn_grant(self.group, "actor")
        if current is not None:
            invalidate_turn_grant(self.group, "actor", reason="test_next_root")
        provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        request = {
            "request_id": request_id,
            "actor_id": "actor",
            "mode": "run_existing",
            "workflow_id": self.workflow_id,
            "allow_high_risk": True,
            "allow_publish": False,
            "allow_trust": True,
            "allow_unattended_triggers": False,
            "allow_workflow_edit": False,
        }
        if inputs is not _MISSING:
            request["inputs"] = inputs
        event = append_event(
            self.group.ledger_path,
            kind="chat.message",
            group_id="g",
            scope_key="",
            by="user",
            data=ChatMessageData(
                text="run workflow",
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
            binding={"transport": "test", "request_id": request_id},
        )
        receipt = turn_delivery_grant_receipt(attempt)
        finalize_turn_delivery_attempt(self.group, actor_id="actor", attempt=attempt)
        root = validate_turn_grant_receipt(
            self.group,
            "actor",
            turn_grant_receipt=receipt,
        )
        self.assertIsNotNone(root)
        presented_inputs = {} if inputs is _MISSING else inputs
        return self.requests.activate_run_start_for_turn_claim(
            "g",
            request_id,
            "actor",
            claim=root,
            workflow_id=self.workflow_id,
            version=self.version,
            inputs=presented_inputs,
        )

    def _issue(self, *, resource_id: str = "run_1", request_id: str = "request_1", inputs: object = _MISSING):
        claim = self._prepare_start_claim(request_id=request_id, inputs=inputs)
        return claim, self.authorities.begin_run(
            group_id="g",
            actor_id="actor",
            resource_id=resource_id,
            request_id=request_id,
            start_claim=claim,
        )

    def _authority_path(self) -> Path:
        paths = list(self.derived_root.glob("*.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def _accept_with_lease(self, issue):
        seed = self.authorities.require_execution_seed(issue.execution_seed)
        self.lease.reserve_run(group_id="g", actor_id="actor", run_id="run_1", authority=seed)
        accepted = self.authorities.accept_execution(seed)
        self.lease.activate_run_reservation(pending=seed, active=accepted.execution_claim)
        return accepted

    def test_real_request_issues_exact_scope_and_secret_is_receipt_only(self) -> None:
        inputs = {"message": "hello", "nested": {"count": 2}}
        _, issue = self._issue(inputs=inputs)
        record = self.authorities.persisted_record("g", "run_1")
        persisted = json.dumps(record, sort_keys=True)

        self.assertEqual(record["workflow_id"], self.workflow_id)
        self.assertEqual(record["version"], self.version)
        self.assertEqual(
            record["definition_digest"],
            workflow_definition_digest(self.workflow["definition"]),
        )
        self.assertEqual(record["inputs_digest"], run_inputs_digest(inputs))
        request = self.requests.get("g", "request_1") or {}
        self.assertEqual(record["permission_snapshot"], computer_control_permissions(request))
        self.assertNotIn(issue.receipt["authorization_secret"], persisted)
        self.assertNotIn("authorization_secret", persisted)
        self.assertGreater(record["operation_expires_at_epoch"], record["created_at_epoch"])

    def test_plain_start_claim_and_invalid_inputs_create_no_derived_paths(self) -> None:
        with self.assertRaisesRegex(PermissionError, "validated run start claim"):
            self.authorities.begin_run(
                group_id="g",
                actor_id="actor",
                resource_id="run_1",
                request_id="request_1",
                start_claim={},  # type: ignore[arg-type]
            )
        self.assertFalse(self.derived_root.exists())
        self.assertEqual(normalized_run_inputs(None), {})
        with self.assertRaisesRegex(PermissionError, "must be an object"):
            normalized_run_inputs([])
        with self.assertRaisesRegex(PermissionError, "not JSON serializable"):
            normalized_run_inputs({"value": math.nan})

    def test_missing_and_explicit_empty_inputs_normalize_equally(self) -> None:
        claim = self._prepare_start_claim(inputs=_MISSING)
        self.assertEqual(claim.inputs_digest, run_inputs_digest({}))

    def test_activated_inputs_are_not_publicly_mutable(self) -> None:
        self._prepare_start_claim(inputs={"nested": {"value": 1}})
        path = self.requests._path("g")
        before = path.read_bytes()
        for action in (
            lambda: self.requests.update("g", "request_1", inputs={"nested": {"value": 2}}),
            lambda: self.requests.append("g", {"request_id": "request_1", "inputs": {}}),
        ):
            with self.subTest(action=action):
                with self.assertRaises(PermissionError):
                    action()
                self.assertEqual(path.read_bytes(), before)

    def test_scope_tampering_fails_even_when_receipt_is_changed_to_match(self) -> None:
        _, issue = self._issue(inputs={"message": "hello"})
        accepted = self.authorities.accept_execution(issue.execution_seed)
        path = self._authority_path()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["definition_digest"] = "a" * 64
        record["scope_digest"] = self.authorities._scope_digest(self.authorities._scope(record))
        self._write_json(path, record)
        changed_receipt = {
            **issue.receipt,
            "definition_digest": "a" * 64,
            "scope_digest": record["scope_digest"],
        }

        with self.assertRaisesRegex(PermissionError, "integrity"):
            self.authorities.validate_operation_receipt(
                changed_receipt,
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
            )
        with self.assertRaisesRegex(PermissionError, "integrity"):
            self.authorities.require_execution_claim(accepted.execution_claim)

    def test_operation_expiry_and_generation_only_revoke_actor_control(self) -> None:
        _, issue = self._issue()
        accepted = self.authorities.accept_execution(issue.execution_seed)
        self.now += self.authorities.OPERATION_TTL_SECONDS + 1
        with self.assertRaisesRegex(PermissionError, "receipt is invalid"):
            self.authorities.validate_operation_receipt(
                issue.receipt,
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
            )
        self.assertIs(
            self.authorities.require_execution_claim(accepted.execution_claim),
            accepted.execution_claim,
        )
        self.issuer = "issuer_restarted"
        with self.assertRaisesRegex(PermissionError, "receipt is invalid"):
            self.authorities.validate_operation_receipt(
                issue.receipt,
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
            )
        with self.assertRaisesRegex(PermissionError, "stale"):
            self.authorities.require_execution_claim(accepted.execution_claim)
        self.issuer = get_daemon_turn_issuer_epoch()
        invalidate_turn_grant(self.group, "actor", reason="new_generation")
        self.assertIs(
            self.authorities.require_execution_claim(accepted.execution_claim),
            accepted.execution_claim,
        )

    def test_execution_claim_is_scope_exact_nonserializable_and_terminal_fails(self) -> None:
        _, issue = self._issue()
        accepted = self.authorities.accept_execution(issue.execution_seed)
        claim = accepted.execution_claim
        with self.assertRaises(TypeError):
            pickle.dumps(issue.execution_seed)
        with self.assertRaises(TypeError):
            pickle.dumps(claim)
        with self.assertRaises(TypeError):
            RunExecutionSeedClaim(_seal=object(), record={}, _validator=lambda value: value)
        with self.assertRaises(TypeError):
            RunExecutionClaim(_seal=object(), record={}, _validator=lambda value: value)

        object.__setattr__(claim, "workflow_id", "wf_other")
        with self.assertRaisesRegex(PermissionError, "stale"):
            self.authorities.require_execution_claim(claim)
        object.__setattr__(claim, "workflow_id", self.workflow_id)
        path = self._authority_path()
        record = json.loads(path.read_text(encoding="utf-8"))
        record["execution_state"] = "completed"
        record["execution_state_revision"] = 3
        self._write_json(path, record)
        with self.assertRaisesRegex(PermissionError, "integrity"):
            self.authorities.require_execution_claim(claim)

    def test_same_root_request_is_single_use_across_resources(self) -> None:
        claim, issue = self._issue()
        before = {path.name: path.read_bytes() for path in self.derived_root.glob("*.json")}
        with self.assertRaisesRegex(PermissionError, "root claim"):
            self.authorities.begin_run(
                group_id="g",
                actor_id="actor",
                resource_id="run_2",
                request_id="request_1",
                start_claim=claim,
            )
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.derived_root.glob("*.json")},
            before,
        )
        self.authorities.revoke_pending(issue.operation_claim)
        with self.assertRaisesRegex(PermissionError, "root claim"):
            self.authorities.begin_run(
                group_id="g",
                actor_id="actor",
                resource_id="run_2",
                request_id="request_1",
                start_claim=claim,
            )

    def test_stale_seed_and_execution_fail_before_any_lease_write(self) -> None:
        _, issue = self._issue()
        self.authorities.revoke_pending(issue.operation_claim)
        with self.assertRaisesRegex(PermissionError, "seed claim is stale"):
            self.lease.reserve_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=issue.execution_seed,
            )
        self.assertFalse(self.lease_path.exists())
        self.assertFalse(self.lineage_path.exists())

    def test_failed_transition_write_does_not_publish_new_integrity(self) -> None:
        _, issue = self._issue()
        path = self._authority_path()
        before = path.read_bytes()
        original_sign = run_authority_module._sign_record_integrity

        def seal_invalid_revision(record: dict) -> dict:
            return original_sign({**record, "control_revision": 99})

        with patch(
            "no1.computer_control.run_authority._sign_record_integrity",
            side_effect=seal_invalid_revision,
        ):
            with self.assertRaisesRegex(PermissionError, "integrity"):
                self.authorities.accept_execution(issue.execution_seed)
        self.assertEqual(path.read_bytes(), before)
        self.assertIs(
            self.authorities.require_execution_seed(issue.execution_seed),
            issue.execution_seed,
        )
        with patch(
            "no1.computer_control.run_authority.atomic_write_text",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.authorities.accept_execution(issue.execution_seed)
        self.assertEqual(path.read_bytes(), before)
        self.assertIs(
            self.authorities.require_execution_seed(issue.execution_seed),
            issue.execution_seed,
        )
        accepted = self.authorities.accept_execution(issue.execution_seed)
        self.assertEqual(accepted.execution_claim.state, "accepted")
        accepted_bytes = path.read_bytes()
        path.write_bytes(before)
        with self.assertRaisesRegex(PermissionError, "integrity"):
            self.authorities.require_execution_seed(issue.execution_seed)
        path.write_bytes(accepted_bytes)
        self.assertIs(
            self.authorities.require_execution_claim(accepted.execution_claim),
            accepted.execution_claim,
        )
        self.assertFalse(self.lease_path.exists())
        self.assertFalse(self.lineage_path.exists())

    def test_old_epoch_seed_and_terminal_execution_do_not_touch_lease(self) -> None:
        _, issue = self._issue()
        self.issuer = "issuer_restarted"
        with self.assertRaisesRegex(PermissionError, "seed claim is stale"):
            self.lease.reserve_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=issue.execution_seed,
            )
        self.assertFalse(self.lease_path.exists())
        self.issuer = get_daemon_turn_issuer_epoch()
        accepted = self._accept_with_lease(issue)
        lease_before = self.lease_path.read_bytes()
        path = self._authority_path()
        original = json.loads(path.read_text(encoding="utf-8"))
        for state in ("running", "failed"):
            with self.subTest(state=state):
                record = {**original, "execution_state": state, "execution_state_revision": 3}
                self._write_json(path, record)
                with self.assertRaisesRegex(PermissionError, "integrity"):
                    self.lease.heartbeat_run(
                        group_id="g",
                        actor_id="actor",
                        run_id="run_1",
                        authority=accepted.execution_claim,
                    )
                with self.assertRaisesRegex(PermissionError, "integrity"):
                    self.lease.release_run(run_id="run_1", authority=accepted.execution_claim)
                self.assertEqual(self.lease_path.read_bytes(), lease_before)
                self.assertFalse(self.lineage_path.exists())
                self._write_json(path, original)

    def test_run_lease_is_exact_and_retired_lineage_cannot_reacquire(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        claim = accepted.execution_claim
        self.assertEqual(
            self.lease.require_run(
                group_id="g", actor_id="actor", run_id="run_1", authority=claim
            )["authority"]["kind"],
            "run",
        )
        with self.assertRaises(PermissionError):
            self.lease.require_run(
                group_id="other", actor_id="actor", run_id="run_1", authority=claim
            )
        with self.assertRaises(PermissionError):
            self.lease.reserve(
                group_id="g", actor_id="actor", run_id="run_1", authority=claim  # type: ignore[arg-type]
            )
        self.assertTrue(self.lease.release_run(run_id="run_1", authority=claim))
        self.assertTrue(self.lineage_path.exists())
        with self.assertRaises(PermissionError):
            self.lease.reserve_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=issue.execution_seed,
            )


if __name__ == "__main__":
    unittest.main()
