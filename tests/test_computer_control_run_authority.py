from __future__ import annotations

import gc
import json
import math
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import threading
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
    RunRecoveryClaim,
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

    def _write_manual_run(self, record: dict, *, status: str = "running") -> Path:
        path = self.workflows.state_root("g") / "runs" / "run_1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(
            path,
            {
                "origin": "manual_actor",
                "status": status,
                "group_id": "g",
                "actor_id": "actor",
                "run_id": "run_1",
                "workflow_id": record["workflow_id"],
                "version": record["version"],
                "run_authority": self.authorities._record_run_anchor_identity(record),
            },
        )
        return path

    @staticmethod
    def _legacy_initial_run(resource_id: str) -> dict:
        return {
            "origin": "legacy_internal",
            "status": "initializing",
            "group_id": "g",
            "actor_id": "actor",
            "run_id": resource_id,
            "workflow_id": "workflow_legacy",
            "version": 1,
        }

    def _allocate_legacy(self, resource_id: str = "run_1"):
        return self.authorities.allocate_legacy_run(
            group_id="g",
            actor_id="actor",
            resource_id=resource_id,
            initial_run=self._legacy_initial_run(resource_id),
            reserve_lease=lambda claim: self.lease.reserve_legacy_run(
                group_id="g",
                actor_id="actor",
                run_id=resource_id,
                allocation=claim,
            ),
            cancel_reservation=lambda claim: self.lease.cancel_legacy_run_reservation(
                allocation=claim
            ),
        )

    def _recover_in_fresh_process(self, claim, *, terminal: bool = False) -> dict:
        record = self.authorities.persisted_record(claim.group_id, claim.resource_id)
        config = {
            "home": str(self.home),
            "group": record["group_id"],
            "actor": record["actor_id"],
            "resource": record["resource_id"],
            "authority": record["authority_id"],
            "execution": record["execution_id"],
            "terminal": terminal,
        }
        script = f"""
import json
from pathlib import Path
from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.run_authority import RunAuthorityStore

cfg = json.loads({json.dumps(config)!r})
home = Path(cfg['home'])
kwargs = dict(
    expected_group_id=cfg['group'], expected_actor_id=cfg['actor'],
    expected_resource_id=cfg['resource'], expected_authority_id=cfg['authority'],
    expected_execution_id=cfg['execution'],
)
store = RunAuthorityStore(
    home,
    issuer_epoch_provider=lambda: 'issuer_process_b',
    generation_provider=lambda g, a: 1,
)
if cfg['terminal']:
    recovery = store.prepare_terminal_reconciliation(**kwargs)
    released = False
    terminal = store.finish_terminal_reconciliation(recovery)
else:
    recovery = store.prepare_restart_recovery(**kwargs)
    released = ComputerControlLease(home).release_run_after_restart(authority=recovery)
    terminal = store.finish_restart_recovery(recovery)
rejected = 0
for method in (
    store.require_operation_claim,
    store.require_execution_claim,
    store.require_termination_claim,
):
    try:
        method(recovery)
    except PermissionError:
        rejected += 1
try:
    recovery.require_current()
except PermissionError:
    rejected += 1
run_path = home / 'groups' / cfg['group'] / 'state' / 'computer-control' / 'runs' / f"{{cfg['resource']}}.json"
run = json.loads(run_path.read_text()) if run_path.exists() else None
store_c = RunAuthorityStore(
    home,
    issuer_epoch_provider=lambda: 'issuer_process_c',
    generation_provider=lambda g, a: 1,
)
if run is None:
    idempotent = 0
    for method in (
        store_c.prepare_restart_recovery,
        store_c.prepare_terminal_reconciliation,
    ):
        try:
            method(**kwargs)
        except PermissionError:
            idempotent += 1
else:
    again = store_c.prepare_terminal_reconciliation(**kwargs)
    store_c.finish_terminal_reconciliation(again)
    idempotent = int(
        store_c.validate_terminal_record(cfg['group'], cfg['resource'])['execution_state']
        == terminal['execution_state']
    )
print(json.dumps({{
    'target': terminal['execution_state'],
    'released': released,
    'run_status': None if run is None else run['status'],
    'lease_phase': getattr(recovery, 'lease_phase', ''),
    'rejected': rejected,
    'idempotent': idempotent,
}}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_manual_and_legacy_share_one_locked_run_namespace(self) -> None:
        start_claim = self._prepare_start_claim()
        allocation, run = self._allocate_legacy()
        run_path = self.workflows.state_root("g") / "runs" / "run_1.json"
        run_before = run_path.read_bytes()
        lease_before = self.lease_path.read_bytes()

        with self.assertRaisesRegex(PermissionError, "already exists"):
            self.authorities.begin_run(
                group_id="g",
                actor_id="actor",
                resource_id="run_1",
                request_id="request_1",
                start_claim=start_claim,
            )
        with self.assertRaisesRegex(PermissionError, "already exists"):
            self._allocate_legacy()

        self.assertEqual(run_path.read_bytes(), run_before)
        self.assertEqual(self.lease_path.read_bytes(), lease_before)
        self.assertEqual(run["legacy_allocation"]["allocation_id"], allocation.allocation_id)
        self.assertEqual(
            self.lease.status()["lease"]["legacy_allocation"],
            run["legacy_allocation"],
        )
        self.assertEqual(list(self.derived_root.glob("*.json")), [])

    def test_legacy_allocation_rejects_orphan_authority_or_run_marker(self) -> None:
        for resource_id, marker in (
            ("run_authority_marker", "authority"),
            ("run_path_marker", "run"),
        ):
            with self.subTest(marker=marker):
                authority_path = self.authorities._path("g", resource_id)
                run_path = self.authorities._run_path("g", resource_id)
                marker_path = authority_path if marker == "authority" else run_path
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_bytes(b"marker\n")
                before = marker_path.read_bytes()
                with self.assertRaisesRegex(PermissionError, "already exists"):
                    self._allocate_legacy(resource_id)
                self.assertEqual(marker_path.read_bytes(), before)
                self.assertFalse(self.lease.status()["active"])

    def test_legacy_allocation_holds_authority_lock_while_waiting_for_lease(self) -> None:
        start_claim = self._prepare_start_claim()
        reserve_entered = threading.Event()
        allocation_done = threading.Event()
        allocation_errors: list[BaseException] = []
        manual_errors: list[BaseException] = []

        def allocate() -> None:
            try:
                self.authorities.allocate_legacy_run(
                    group_id="g",
                    actor_id="actor",
                    resource_id="run_1",
                    initial_run=self._legacy_initial_run("run_1"),
                    reserve_lease=lambda claim: (
                        reserve_entered.set(),
                        self.lease.reserve_legacy_run(
                            group_id="g",
                            actor_id="actor",
                            run_id="run_1",
                            allocation=claim,
                        ),
                    )[1],
                    cancel_reservation=lambda claim: self.lease.cancel_legacy_run_reservation(
                        allocation=claim
                    ),
                )
            except BaseException as exc:
                allocation_errors.append(exc)
            finally:
                allocation_done.set()

        def begin_manual() -> None:
            try:
                self.authorities.begin_run(
                    group_id="g",
                    actor_id="actor",
                    resource_id="run_1",
                    request_id="request_1",
                    start_claim=start_claim,
                )
            except BaseException as exc:
                manual_errors.append(exc)

        with self.lease._locked():
            allocation_thread = threading.Thread(target=allocate)
            allocation_thread.start()
            self.assertTrue(reserve_entered.wait(5))
            manual_thread = threading.Thread(target=begin_manual)
            manual_thread.start()
            time.sleep(0.05)
            self.assertFalse(allocation_done.is_set())
            self.assertTrue(manual_thread.is_alive())

        allocation_thread.join(5)
        manual_thread.join(5)
        self.assertFalse(allocation_thread.is_alive())
        self.assertFalse(manual_thread.is_alive())
        self.assertEqual(allocation_errors, [])
        self.assertEqual(len(manual_errors), 1)
        self.assertIsInstance(manual_errors[0], PermissionError)
        self.assertTrue(self.lease.status()["active"])

    def test_legacy_write_rejects_replaced_disk_anchor_without_changes(self) -> None:
        allocation, run = self._allocate_legacy()
        run_path = self.authorities._run_path("g", "run_1")
        replaced = json.loads(run_path.read_text(encoding="utf-8"))
        replaced["legacy_allocation"]["allocation_id"] = "legacyalloc_" + "a" * 48
        self._write_json(run_path, replaced)
        before = run_path.read_bytes()
        run["status"] = "running"
        with self.assertRaisesRegex(PermissionError, "no longer current"):
            self.authorities.write_legacy_run(allocation, run)
        self.assertEqual(run_path.read_bytes(), before)

    def test_legacy_operation_rejects_awaitable_callbacks_without_body_effects(self) -> None:
        allocation, _ = self._allocate_legacy()
        self.lease.activate_legacy_run_reservation(allocation=allocation)
        run_path = self.authorities._run_path("g", "run_1")
        effects: list[str] = []

        async def async_body(_value):
            effects.append("executed")

        callbacks = (
            {
                "validate_current": async_body,
                "require_lease": lambda claim: self.lease.require_legacy_run(
                    allocation=claim
                ),
                "callback": lambda _current: None,
                "message": "validator must be synchronous",
            },
            {
                "validate_current": lambda _current: None,
                "require_lease": async_body,
                "callback": lambda _current: None,
                "message": "lease callback must be synchronous",
            },
            {
                "validate_current": lambda _current: None,
                "require_lease": lambda claim: self.lease.require_legacy_run(
                    allocation=claim
                ),
                "callback": async_body,
                "message": "callback must be synchronous",
            },
        )
        for case in callbacks:
            with self.subTest(message=case["message"]):
                before = (run_path.read_bytes(), self.lease_path.read_bytes())
                with self.assertRaisesRegex(TypeError, case["message"]):
                    self.authorities.perform_legacy_operation_with_callback(
                        allocation,
                        validate_current=case["validate_current"],
                        require_lease=case["require_lease"],
                        callback=case["callback"],
                        write_run=True,
                    )
                self.assertEqual((run_path.read_bytes(), self.lease_path.read_bytes()), before)
                self.assertEqual(effects, [])

    def test_legacy_allocation_preserves_reservation_error_and_cleanup_prefix(self) -> None:
        original = RuntimeError("reserve callback failed after write")

        def reserve_then_fail(claim):
            self.lease.reserve_legacy_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                allocation=claim,
            )
            raise original

        with self.assertRaises(RuntimeError) as raised:
            self.authorities.allocate_legacy_run(
                group_id="g",
                actor_id="actor",
                resource_id="run_1",
                initial_run=self._legacy_initial_run("run_1"),
                reserve_lease=reserve_then_fail,
                cancel_reservation=lambda claim: self.lease.cancel_legacy_run_reservation(
                    allocation=claim
                ),
            )
        self.assertIs(raised.exception, original)
        self.assertFalse(self.lease.status()["active"])
        self.assertFalse(self.authorities._run_path("g", "run_1").exists())

        cleanup_error = RuntimeError("cleanup failed")

        def cleanup_then_fail(_claim):
            raise cleanup_error

        with self.assertRaises(RuntimeError) as raised:
            self.authorities.allocate_legacy_run(
                group_id="g",
                actor_id="actor",
                resource_id="run_1",
                initial_run=self._legacy_initial_run("run_1"),
                reserve_lease=reserve_then_fail,
                cancel_reservation=cleanup_then_fail,
            )
        self.assertIs(raised.exception, original)
        self.assertTrue(
            any("cleanup failed" in note for note in getattr(original, "__notes__", []))
        )
        self.assertTrue(self.lease.status()["active"])
        self.assertFalse(self.authorities._run_path("g", "run_1").exists())

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

    def test_terminal_receipt_is_read_only_and_scope_exact(self) -> None:
        _, issue = self._issue()
        receipt = dict(issue.receipt)
        accepted = self._accept_with_lease(issue)
        running = self.authorities.mark_running(accepted.execution_claim)
        active_read = self.authorities.validate_read_receipt(
            receipt,
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
        )
        self.assertEqual(active_read.state, "running")
        self.assertIs(self.authorities.require_read_claim(active_read), active_read)
        self.assertTrue(self.lease.release_run(run_id="run_1", authority=running))
        self.authorities.mark_completed(running)

        read = self.authorities.validate_read_receipt(
            receipt,
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
        )
        self.assertEqual(read.state, "completed")
        self.assertEqual(read.origin_issuer_epoch, receipt["issuer_epoch"])
        self.assertIs(self.authorities.require_read_claim(read), read)
        with self.assertRaisesRegex(PermissionError, "operation claim"):
            self.authorities.require_operation_claim(read)
        with self.assertRaisesRegex(PermissionError, "execution claim"):
            self.authorities.require_execution_claim(read)

        for field, expected in (
            ("group_id", "other"),
            ("actor_id", "other"),
            ("resource_id", "other"),
        ):
            kwargs = {
                "expected_group_id": "g",
                "expected_actor_id": "actor",
                "expected_resource_id": "run_1",
            }
            kwargs[f"expected_{field}"] = expected
            with self.subTest(expected_field=field), self.assertRaises(PermissionError):
                self.authorities.validate_read_receipt(receipt, **kwargs)

        mutations = {
            "issuer_epoch": "other",
            "origin_issuer_epoch": "other",
            "authority_id": "other",
            "execution_id": "other",
            "workflow_id": "other",
            "version": int(receipt["version"]) + 1,
            "definition_digest": "a" * 64,
            "inputs_digest": "b" * 64,
            "scope_digest": "c" * 64,
            "authorization_secret": "other",
        }
        for field, value in mutations.items():
            changed = {**receipt, field: value}
            with self.subTest(receipt_field=field), self.assertRaises(PermissionError):
                self.authorities.validate_read_receipt(
                    changed,
                    expected_group_id="g",
                    expected_actor_id="actor",
                    expected_resource_id="run_1",
                )

    def test_operation_callback_rejects_async_effects_without_side_effects(self) -> None:
        _, issue = self._issue()
        accepted = self.authorities.accept_execution(issue.execution_seed)
        marker = self.home / "async-operation-effect"

        async def async_effect(_anchor):
            marker.write_text("unexpected", encoding="utf-8")
            return {"accepted": True}

        with self.assertRaisesRegex(
            TypeError,
            "run operation callback must complete synchronously",
        ):
            self.authorities.perform_operation_with_callback(
                accepted.operation_claim,
                async_effect,
            )
        self.assertFalse(marker.exists())
        self.assertIs(
            self.authorities.require_operation_claim(accepted.operation_claim),
            accepted.operation_claim,
        )

    def test_all_run_claim_types_reject_a_copied_home_without_side_effects(self) -> None:
        def copied_stores():
            copied_temp = tempfile.TemporaryDirectory()
            self.addCleanup(copied_temp.cleanup)
            copied_home = Path(copied_temp.name) / "home"
            shutil.copytree(self.home, copied_home)
            store = RunAuthorityStore(
                copied_home,
                issuer_epoch_provider=lambda: self.issuer,
                generation_provider=get_actor_turn_generation,
                now_provider=lambda: self.now,
            )
            return copied_home, store, ComputerControlLease(copied_home)

        def tree_bytes(root: Path) -> dict[str, bytes]:
            return {
                str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }

        _, issue = self._issue()
        pending_home, pending_store, pending_lease = copied_stores()
        pending_before = tree_bytes(pending_home)
        for claim in (issue.operation_claim, issue.execution_seed):
            object.__setattr__(claim, "authority_home", str(pending_home.resolve()))
        for action in (
            lambda: pending_store.accept_execution(issue.execution_seed),
            lambda: pending_store.require_execution_seed(issue.execution_seed),
            lambda: pending_store.revoke_pending(issue.operation_claim),
            lambda: pending_lease.reserve_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=issue.execution_seed,
            ),
        ):
            with self.subTest(phase="pending", action=repr(action)):
                with self.assertRaisesRegex(PermissionError, "home|authority_required"):
                    action()
                self.assertEqual(tree_bytes(pending_home), pending_before)

        accepted = self._accept_with_lease(issue)
        running = self.authorities.mark_running(accepted.execution_claim)
        read = self.authorities.validate_read_receipt(
            issue.receipt,
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
        )
        stop = self.authorities.validate_stop_owner(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
        )
        live_home, live_store, live_lease = copied_stores()
        live_before = tree_bytes(live_home)
        for claim in (accepted.operation_claim, running, read, stop):
            object.__setattr__(claim, "authority_home", str(live_home.resolve()))
        callbacks: list[dict] = []
        live_actions = (
            lambda: live_store.perform_operation_with_callback(
                accepted.operation_claim,
                lambda anchor: callbacks.append(anchor) or "accepted-in-copy",
            ),
            lambda: live_store.require_operation_claim(accepted.operation_claim),
            lambda: live_store.require_read_claim(read),
            lambda: live_store.require_execution_claim(running),
            lambda: live_store.mark_waiting_approval(running),
            lambda: live_store.begin_cancel(stop),
            lambda: live_lease.require_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=running,
            ),
        )
        for action in live_actions:
            with self.subTest(phase="live", action=repr(action)):
                with self.assertRaisesRegex(PermissionError, "home|authority_required"):
                    action()
                self.assertEqual(callbacks, [])
                self.assertEqual(tree_bytes(live_home), live_before)

        termination = self.authorities.begin_cancel(stop)
        terminating_home, terminating_store, terminating_lease = copied_stores()
        terminating_before = tree_bytes(terminating_home)
        object.__setattr__(termination, "authority_home", str(terminating_home.resolve()))
        for action in (
            lambda: terminating_store.require_termination_claim(termination),
            lambda: terminating_store.mark_cancelled(termination),
            lambda: terminating_lease.release_run(run_id="run_1", authority=termination),
        ):
            with self.subTest(phase="terminating", action=repr(action)):
                with self.assertRaisesRegex(PermissionError, "home|authority_required"):
                    action()
                self.assertEqual(tree_bytes(terminating_home), terminating_before)

        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record)
        self.issuer = "issuer_after_restart"
        recovery = self.authorities.prepare_restart_recovery(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
            expected_authority_id=running.authority_id,
            expected_execution_id=running.execution_id,
        )
        recovery_home, recovery_store, recovery_lease = copied_stores()
        recovery_before = tree_bytes(recovery_home)
        object.__setattr__(recovery, "authority_home", str(recovery_home.resolve()))
        for action in (
            lambda: recovery_store.require_recovery_claim(recovery),
            lambda: recovery_store.finish_restart_recovery(recovery),
            lambda: recovery_lease.release_run_after_restart(authority=recovery),
        ):
            with self.subTest(phase="recovery", action=repr(action)):
                with self.assertRaisesRegex(PermissionError, "home|authority_required"):
                    action()
                self.assertEqual(tree_bytes(recovery_home), recovery_before)

        self.assertTrue(self.lease.release_run_after_restart(authority=recovery))
        self.authorities.finish_restart_recovery(recovery)
        self.issuer = "issuer_after_terminal"
        reconciliation = self.authorities.prepare_terminal_reconciliation(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
            expected_authority_id=running.authority_id,
            expected_execution_id=running.execution_id,
        )
        terminal_home, terminal_store, _ = copied_stores()
        terminal_before = tree_bytes(terminal_home)
        object.__setattr__(reconciliation, "authority_home", str(terminal_home.resolve()))
        for action in (
            lambda: terminal_store.require_terminal_reconciliation_claim(reconciliation),
            lambda: terminal_store.finish_terminal_reconciliation(reconciliation),
        ):
            with self.subTest(phase="terminal", action=repr(action)):
                with self.assertRaisesRegex(PermissionError, "home"):
                    action()
                self.assertEqual(tree_bytes(terminal_home), terminal_before)

    def test_canonical_home_alias_is_equivalent_and_redirection_does_not_drift(self) -> None:
        alias_temp = tempfile.TemporaryDirectory()
        self.addCleanup(alias_temp.cleanup)
        alias = Path(alias_temp.name) / "authority-home"
        try:
            alias.symlink_to(self.home, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")

        alias_store = RunAuthorityStore(
            alias,
            issuer_epoch_provider=lambda: self.issuer,
            generation_provider=get_actor_turn_generation,
            now_provider=lambda: self.now,
        )
        alias_lease = ComputerControlLease(alias)
        _, issue = self._issue()
        alias_lease.reserve_run(
            group_id="g",
            actor_id="actor",
            run_id="run_1",
            authority=issue.execution_seed,
        )
        accepted = self.authorities.accept_execution(issue.execution_seed)
        alias_lease.activate_run_reservation(
            pending=issue.execution_seed,
            active=accepted.execution_claim,
        )
        self.assertEqual(alias_store.authority_home, str(self.home.resolve()))
        self.assertIs(
            alias_store.require_execution_claim(accepted.execution_claim),
            accepted.execution_claim,
        )

        redirected_temp = tempfile.TemporaryDirectory()
        self.addCleanup(redirected_temp.cleanup)
        redirected_home = Path(redirected_temp.name) / "home"
        shutil.copytree(self.home, redirected_home)
        redirected_before = {
            str(path.relative_to(redirected_home)): path.read_bytes()
            for path in redirected_home.rglob("*")
            if path.is_file()
        }
        alias.unlink()
        alias.symlink_to(redirected_home, target_is_directory=True)

        running = alias_store.mark_running(accepted.execution_claim)
        self.assertEqual(running.state, "running")
        self.assertEqual(
            alias_lease.require_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=running,
            )["run_id"],
            "run_1",
        )
        self.assertEqual(
            {
                str(path.relative_to(redirected_home)): path.read_bytes()
                for path in redirected_home.rglob("*")
                if path.is_file()
            },
            redirected_before,
        )

        redirected_store = RunAuthorityStore(
            alias,
            issuer_epoch_provider=lambda: self.issuer,
            generation_provider=get_actor_turn_generation,
            now_provider=lambda: self.now,
        )
        self.assertEqual(redirected_store.authority_home, str(redirected_home.resolve()))
        with self.assertRaisesRegex(PermissionError, "another home"):
            redirected_store.require_execution_claim(running)
        with self.assertRaisesRegex(PermissionError, "authority_required"):
            ComputerControlLease(alias).require_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=running,
            )

    def test_claim_owner_registry_is_identity_bound_and_automatically_reclaimed(self) -> None:
        _, issue = self._issue()
        claim = issue.execution_seed
        identity = id(claim)
        registered = run_authority_module._CLAIM_OWNERS[identity]
        self.assertIs(registered[0](), claim)
        self.assertEqual(registered[1], str(self.home.resolve()))

        del claim
        del issue
        gc.collect()

        self.assertNotIn(identity, run_authority_module._CLAIM_OWNERS)

    def test_execution_claim_is_scope_exact_nonserializable_and_terminal_fails(self) -> None:
        _, issue = self._issue()
        accepted = self.authorities.accept_execution(issue.execution_seed)
        claim = accepted.execution_claim
        with self.assertRaises(TypeError):
            pickle.dumps(issue.execution_seed)
        with self.assertRaises(TypeError):
            pickle.dumps(claim)
        with self.assertRaises(TypeError):
            RunExecutionSeedClaim(
                _seal=object(),
                _authority_home=str(self.home.resolve()),
                record={},
                _validator=lambda value: value,
            )
        with self.assertRaises(TypeError):
            RunExecutionClaim(
                _seal=object(),
                _authority_home=str(self.home.resolve()),
                record={},
                _validator=lambda value: value,
            )

        forged = object.__new__(RunExecutionClaim)
        for field, value in vars(claim).items():
            object.__setattr__(forged, field, value)
        with self.assertRaisesRegex(PermissionError, "another home"):
            self.authorities.require_execution_claim(forged)

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

    def test_execution_progress_keeps_one_lease_lineage_and_stales_old_claims(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        accepted_claim = accepted.execution_claim
        running = self.authorities.mark_running(accepted_claim)

        with self.assertRaisesRegex(PermissionError, "stale"):
            self.lease.require_run(
                group_id="g",
                actor_id="actor",
                run_id="run_1",
                authority=accepted_claim,
            )
        self.lease.require_run(
            group_id="g", actor_id="actor", run_id="run_1", authority=running
        )
        waiting = self.authorities.mark_waiting_recovery(running)
        self.lease.heartbeat_run(
            group_id="g", actor_id="actor", run_id="run_1", authority=waiting
        )
        resumed = self.authorities.mark_running(waiting)
        self.assertTrue(self.lease.release_run(run_id="run_1", authority=resumed))
        self.authorities.mark_completed(resumed)

        record = self.authorities.persisted_record("g", "run_1")
        self.assertEqual(record["control_state"], "revoked")
        self.assertEqual(record["execution_state"], "completed")
        self.assertFalse(self.lease_path.exists())

    def test_restart_recovery_requires_old_epoch_and_exact_path_anchored_run(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        run_path = self._write_manual_run(record)
        authority_path = self._authority_path()
        before_authority = authority_path.read_bytes()
        before_lease = self.lease_path.read_bytes()

        with self.assertRaisesRegex(PermissionError, "identity is invalid"):
            self.authorities.prepare_restart_recovery(
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
                expected_authority_id=accepted.execution_claim.authority_id,
                expected_execution_id=accepted.execution_claim.execution_id,
            )
        self.assertEqual(authority_path.read_bytes(), before_authority)
        self.assertEqual(self.lease_path.read_bytes(), before_lease)

        self.issuer = "issuer_after_restart"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["version"] = "not-an-integer"
        self._write_json(run_path, run)
        with self.assertRaisesRegex(PermissionError, "record is invalid"):
            self.authorities.prepare_restart_recovery(
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
                expected_authority_id=accepted.execution_claim.authority_id,
                expected_execution_id=accepted.execution_claim.execution_id,
            )
        self._write_manual_run(record)
        anchored = json.loads(run_path.read_text(encoding="utf-8"))
        self.assertNotIn("issuer_epoch", anchored["run_authority"])
        anchored["run_authority"]["issuer_epoch"] = "copied_process_epoch"
        self._write_json(run_path, anchored)
        with self.assertRaisesRegex(PermissionError, "record is invalid"):
            self.authorities.prepare_restart_recovery(
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
                expected_authority_id=accepted.execution_claim.authority_id,
                expected_execution_id=accepted.execution_claim.execution_id,
            )
        self._write_manual_run(record)
        with self.assertRaisesRegex(PermissionError, "identity is invalid"):
            self.authorities.prepare_restart_recovery(
                expected_group_id="g",
                expected_actor_id="other",
                expected_resource_id="run_1",
                expected_authority_id=accepted.execution_claim.authority_id,
                expected_execution_id=accepted.execution_claim.execution_id,
            )

    def test_restart_recovery_lease_branches_are_exact_and_reduce_only(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record)
        self.issuer = "issuer_after_restart"
        recovery = self.authorities.prepare_restart_recovery(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
            expected_authority_id=accepted.execution_claim.authority_id,
            expected_execution_id=accepted.execution_claim.execution_id,
        )
        before_authority = self._authority_path().read_bytes()
        self.assertTrue(self.lease.release_run_after_restart(authority=recovery))
        terminal = self.authorities.finish_restart_recovery(recovery)
        self.assertEqual(terminal["execution_state"], "interrupted")
        self.assertEqual(
            self.authorities.validate_terminal_record("g", "run_1")["control_state"],
            "revoked",
        )
        with self.assertRaisesRegex(PermissionError, "stale"):
            recovery.require_current()
        self.assertNotEqual(self._authority_path().read_bytes(), before_authority)

    def test_restart_recovery_preserves_other_actor_same_run_id(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record)
        self.issuer = "issuer_after_restart"
        recovery = self.authorities.prepare_restart_recovery(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
            expected_authority_id=accepted.execution_claim.authority_id,
            expected_execution_id=accepted.execution_claim.execution_id,
        )
        self.lease.path.unlink()
        self.lease.acquire(group_id="g", actor_id="other", run_id="run_1")
        lease_before = self.lease_path.read_bytes()
        authority_before = self._authority_path().read_bytes()
        with self.assertRaises(PermissionError):
            self.lease.release_run_after_restart(authority=recovery)
        terminal = self.authorities.finish_restart_recovery(recovery)
        self.assertEqual(terminal["execution_state"], "interrupted")
        self.assertEqual(self.lease_path.read_bytes(), lease_before)
        self.assertNotEqual(self._authority_path().read_bytes(), authority_before)

    def test_restart_recovery_blocks_exact_tuple_with_wrong_lineage(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record)
        self.issuer = "issuer_after_restart"
        recovery = self.authorities.prepare_restart_recovery(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
            expected_authority_id=accepted.execution_claim.authority_id,
            expected_execution_id=accepted.execution_claim.execution_id,
        )
        lease = json.loads(self.lease_path.read_text(encoding="utf-8"))
        lease["authority"]["authority_id"] = "runauth_other"
        self._write_json(self.lease_path, lease)
        lease_before = self.lease_path.read_bytes()
        authority_before = self._authority_path().read_bytes()
        with self.assertRaises(PermissionError):
            self.lease.release_run_after_restart(authority=recovery)
        with self.assertRaisesRegex(PermissionError, "lease must be released"):
            self.authorities.finish_restart_recovery(recovery)
        self.assertEqual(self.lease_path.read_bytes(), lease_before)
        self.assertEqual(self._authority_path().read_bytes(), authority_before)

    def test_restart_recovery_rejects_path_escape_and_malformed_digest(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        run_path = self._write_manual_run(record)
        self.issuer = "issuer_after_restart"
        authority_path = self._authority_path()
        authority_before = authority_path.read_bytes()
        lease_before = self.lease_path.read_bytes()

        malformed = json.loads(authority_path.read_text(encoding="utf-8"))
        malformed["integrity_digest"] = "g" * 64
        self._write_json(authority_path, malformed)
        with self.assertRaisesRegex(PermissionError, "integrity"):
            self.authorities.prepare_restart_recovery(
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
                expected_authority_id=accepted.execution_claim.authority_id,
                expected_execution_id=accepted.execution_claim.execution_id,
            )
        authority_path.write_bytes(authority_before)

        outside = self.home / "outside-runs"
        runs_root = run_path.parent
        shutil.move(str(runs_root), str(outside))
        runs_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(PermissionError, "path is invalid"):
            self.authorities.prepare_restart_recovery(
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id="run_1",
                expected_authority_id=accepted.execution_claim.authority_id,
                expected_execution_id=accepted.execution_claim.execution_id,
            )
        self.assertEqual(authority_path.read_bytes(), authority_before)
        self.assertEqual(self.lease_path.read_bytes(), lease_before)

    def test_coordinated_scope_mutation_cannot_release_exact_old_lease(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        authority_path = self._authority_path()
        record = json.loads(authority_path.read_text(encoding="utf-8"))
        record["definition_digest"] = "a" * 64
        record["scope_digest"] = self.authorities._scope_digest(
            self.authorities._scope(record)
        )
        self._write_json(authority_path, record)
        self._write_manual_run(record)
        self.issuer = "issuer_after_restart"
        recovery = self.authorities.prepare_restart_recovery(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
            expected_authority_id=accepted.execution_claim.authority_id,
            expected_execution_id=accepted.execution_claim.execution_id,
        )
        with self.assertRaises(PermissionError):
            self.lease.release_run_after_restart(authority=recovery)
        with self.assertRaisesRegex(PermissionError, "lease must be released"):
            self.authorities.finish_restart_recovery(recovery)

    def test_unrelated_lease_is_preserved_while_old_authority_is_tombstoned(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record)
        self.issuer = "issuer_after_restart"
        recovery = self.authorities.prepare_restart_recovery(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id="run_1",
            expected_authority_id=accepted.execution_claim.authority_id,
            expected_execution_id=accepted.execution_claim.execution_id,
        )
        self.assertTrue(self.lease.release_run_after_restart(authority=recovery))
        self.lease.acquire(group_id="other", actor_id="other", run_id="other_run")
        lease_before = self.lease_path.read_bytes()
        terminal = self.authorities.finish_restart_recovery(recovery)
        self.assertEqual(terminal["execution_state"], "interrupted")
        self.assertEqual(self.lease_path.read_bytes(), lease_before)
        with self.assertRaises(TypeError):
            RunRecoveryClaim(
                _seal=object(),
                _authority_home=str(self.home.resolve()),
                record=record,
                target_state="interrupted",
                lease_phase="execution",
                run_status="running",
                _validator=lambda value: value,
            )

    def test_old_epoch_record_is_recovered_in_a_fresh_process(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record)
        script = f"""
import json
from pathlib import Path
from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.run_authority import RunAuthorityStore

home = Path({str(self.home)!r})
store = RunAuthorityStore(home, issuer_epoch_provider=lambda: 'issuer_process_b', generation_provider=lambda g, a: 1)
claim = store.prepare_restart_recovery(
    expected_group_id='g', expected_actor_id='actor', expected_resource_id='run_1',
    expected_authority_id={accepted.execution_claim.authority_id!r},
    expected_execution_id={accepted.execution_claim.execution_id!r},
)
ComputerControlLease(home).release_run_after_restart(authority=claim)
terminal = store.finish_restart_recovery(claim)
validated = store.validate_terminal_record('g', 'run_1')
rejected = []
for method in (store.require_operation_claim, store.require_execution_claim, store.require_termination_claim):
    try:
        method(claim)
    except PermissionError:
        rejected.append(True)
try:
    claim.require_current()
except PermissionError:
    rejected.append(True)
print(json.dumps({{'terminal': terminal['execution_state'], 'validated': validated['control_state'], 'rejected': len(rejected)}}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            env={**os.environ, "PYTHONPATH": str(Path.cwd() / "src")},
            text=True,
            capture_output=True,
            timeout=20,
            check=True,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload, {"terminal": "interrupted", "validated": "revoked", "rejected": 4})

    def test_subprocess_recovers_no_run_pending_without_a_lease(self) -> None:
        _, issue = self._issue()
        payload = self._recover_in_fresh_process(issue.execution_seed)
        self.assertEqual(
            payload,
            {
                "target": "start_failed",
                "released": False,
                "run_status": None,
                "lease_phase": "prepared",
                "rejected": 4,
                "idempotent": 2,
            },
        )

    def test_subprocess_recovers_no_run_pending_reservation(self) -> None:
        _, issue = self._issue()
        self.lease.reserve_run(
            group_id="g",
            actor_id="actor",
            run_id="run_1",
            authority=issue.execution_seed,
        )
        payload = self._recover_in_fresh_process(issue.execution_seed)
        self.assertEqual(payload["target"], "start_failed")
        self.assertEqual(payload["lease_phase"], "prepared")
        self.assertTrue(payload["released"])
        self.assertIsNone(payload["run_status"])
        self.assertEqual(payload["idempotent"], 2)

    def test_subprocess_recovers_initializing_pending_reservation(self) -> None:
        _, issue = self._issue()
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="initializing")
        self.lease.reserve_run(
            group_id="g",
            actor_id="actor",
            run_id="run_1",
            authority=issue.execution_seed,
        )
        payload = self._recover_in_fresh_process(issue.execution_seed)
        self.assertEqual(payload["target"], "start_failed")
        self.assertEqual(payload["run_status"], "start_failed")
        self.assertEqual(payload["lease_phase"], "prepared")
        self.assertTrue(payload["released"])
        self.assertEqual(payload["idempotent"], 1)

    def test_subprocess_recovers_initializing_pending_without_lease(self) -> None:
        _, issue = self._issue()
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="initializing")
        payload = self._recover_in_fresh_process(issue.execution_seed)
        self.assertEqual(payload["target"], "start_failed")
        self.assertEqual(payload["run_status"], "start_failed")
        self.assertEqual(payload["lease_phase"], "prepared")
        self.assertFalse(payload["released"])

    def test_subprocess_recovers_initializing_accepted_reservation(self) -> None:
        _, issue = self._issue()
        self.lease.reserve_run(
            group_id="g",
            actor_id="actor",
            run_id="run_1",
            authority=issue.execution_seed,
        )
        accepted = self.authorities.accept_execution(issue.execution_seed)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="initializing")
        payload = self._recover_in_fresh_process(accepted.execution_claim)
        self.assertEqual(payload["target"], "start_failed")
        self.assertEqual(payload["run_status"], "start_failed")
        self.assertEqual(payload["lease_phase"], "prepared")
        self.assertTrue(payload["released"])

    def test_subprocess_recovers_initializing_accepted_active_lease(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="initializing")
        payload = self._recover_in_fresh_process(accepted.execution_claim)
        self.assertEqual(payload["target"], "start_failed")
        self.assertEqual(payload["run_status"], "start_failed")
        self.assertEqual(payload["lease_phase"], "execution")
        self.assertTrue(payload["released"])

    def test_subprocess_recovers_running_and_waiting_as_interrupted(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        running = self.authorities.mark_running(accepted.execution_claim)
        waiting = self.authorities.mark_waiting_recovery(running)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="recovering")
        payload = self._recover_in_fresh_process(waiting)
        self.assertEqual(payload["target"], "interrupted")
        self.assertEqual(payload["run_status"], "interrupted")
        self.assertEqual(payload["lease_phase"], "execution")
        self.assertTrue(payload["released"])
        self.assertEqual(payload["idempotent"], 1)

    def test_subprocess_recovers_running_after_lease_was_already_released(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        running = self.authorities.mark_running(accepted.execution_claim)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="running")
        self.assertTrue(self.lease.release_run(run_id="run_1", authority=running))
        payload = self._recover_in_fresh_process(running)
        self.assertEqual(payload["target"], "interrupted")
        self.assertEqual(payload["run_status"], "interrupted")
        self.assertFalse(payload["released"])
        self.assertEqual(payload["idempotent"], 1)

    def test_subprocess_recovers_running_with_exact_active_lease(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        running = self.authorities.mark_running(accepted.execution_claim)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="running")
        payload = self._recover_in_fresh_process(running)
        self.assertEqual(payload["target"], "interrupted")
        self.assertEqual(payload["run_status"], "interrupted")
        self.assertTrue(payload["released"])
        self.assertEqual(payload["lease_phase"], "execution")

    def test_subprocess_reconciles_terminal_authority_without_downgrade(self) -> None:
        _, issue = self._issue()
        accepted = self._accept_with_lease(issue)
        running = self.authorities.mark_running(accepted.execution_claim)
        record = self.authorities.persisted_record("g", "run_1")
        self._write_manual_run(record, status="running")
        self.assertTrue(self.lease.release_run(run_id="run_1", authority=running))
        terminal = self.authorities.mark_completed(running)

        payload = self._recover_in_fresh_process(terminal, terminal=True)
        self.assertEqual(payload["target"], "completed")
        self.assertEqual(payload["run_status"], "completed")
        self.assertFalse(payload["released"])
        self.assertEqual(payload["rejected"], 4)
        self.assertEqual(payload["idempotent"], 1)


if __name__ == "__main__":
    unittest.main()
