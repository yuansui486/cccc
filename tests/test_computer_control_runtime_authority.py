from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import no1.computer_control.runtime as runtime_module
from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.models import WorkflowDefinition
from no1.computer_control.requests import ComputerRequestStore
from no1.computer_control.run_authority import RunAuthorityStore
from no1.computer_control.runtime import WorkflowRunner
from no1.computer_control.storage import WorkflowStore
from no1.contracts.v1 import ChatMessageData
from no1.daemon.messaging.turn_provenance import (
    begin_turn_delivery_attempt,
    build_send_turn_provenance,
    finalize_turn_delivery_attempt,
    get_actor_turn_generation,
    get_daemon_turn_issuer_epoch,
    turn_delivery_grant_receipt,
    validate_turn_grant_receipt,
)
from no1.daemon.computer_control_ops import try_handle_computer_control_op
from no1.kernel.actors import add_actor
from no1.kernel.group import Group
from no1.kernel.ledger import append_event


class _Session:
    transport_restarts = 0

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.catalog_calls = 0

    async def catalog(self):
        self.catalog_calls += 1
        return []

    async def call_tool(self, name, arguments, *, timeout):
        self.calls.append(name)
        return {"ok": True}

    async def stop(self) -> None:
        return None


class TestManualRunAuthorityRuntime(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"CCCC_HOME": str(self.home)}, clear=False)
        self.env.start()
        group_path = self.home / "groups" / "g"
        group_path.mkdir(parents=True)
        self.group = Group(group_id="g", path=group_path, doc={"v": 1, "group_id": "g"})
        add_actor(self.group, actor_id="actor", title="Actor", runtime="codex", runner="headless")
        self.group.save()
        self.store = WorkflowStore(self.home)
        self.requests = ComputerRequestStore(self.store)
        self.lease = ComputerControlLease(self.home)
        self.issuer = get_daemon_turn_issuer_epoch()
        self.authorities = RunAuthorityStore(
            self.home,
            issuer_epoch_provider=lambda: self.issuer,
            generation_provider=get_actor_turn_generation,
        )
        self.session = _Session()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def _workflow(
        self,
        name: str = "manual",
        *,
        auto_verify: bool = True,
        nodes: list[dict] | None = None,
        edges: list[dict] | None = None,
    ) -> dict:
        return self.store.create(
            "g",
            WorkflowDefinition(
                name=name,
                inputs={"message": "default"},
                auto_verify=auto_verify,
                save_screenshots=False,
                nodes=nodes
                or [
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                edges=edges or [{"source": "start", "target": "end"}],
            ),
        )

    def _start_claim(self, workflow: dict, *, request_id: str, inputs: dict | None = None):
        workflow_id = str(workflow["manifest"]["workflow_id"])
        version = int(workflow["version"])
        request_inputs = dict(inputs or {})
        provenance = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        request = {
            "request_id": request_id,
            "actor_id": "actor",
            "mode": "run_existing",
            "workflow_id": workflow_id,
            "inputs": request_inputs,
            "allow_high_risk": True,
            "allow_publish": True,
            "allow_trust": True,
            "allow_unattended_triggers": False,
            "allow_workflow_edit": False,
        }
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
        assert root is not None
        claim = self.requests.activate_run_start_for_turn_claim(
            "g",
            request_id,
            "actor",
            claim=root,
            workflow_id=workflow_id,
            version=version,
            inputs=request_inputs,
        )
        return request, claim

    def _runner(self, *, fingerprint: str = "fp") -> WorkflowRunner:
        return WorkflowRunner(
            self.home,
            self.store,
            self.lease,
            self.session,
            fingerprint_provider=lambda: fingerprint,
            run_authorities=self.authorities,
            requests=self.requests,
        )

    @staticmethod
    def _wait_terminal(runner: WorkflowRunner, run_id: str) -> dict:
        deadline = time.time() + 5
        while time.time() < deadline:
            run = runner._get_unchecked("g", run_id)
            if run["status"] not in {"initializing", "running"}:
                return run
            time.sleep(0.01)
        raise AssertionError("manual run did not reach a stable state")

    def test_auto_completion_uses_matching_terminal_state_and_restart_is_idempotent(self) -> None:
        workflow = self._workflow()
        request, claim = self._start_claim(workflow, request_id="req-complete")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        completed = self._wait_terminal(runner, started["run_id"])
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["finalization"]["published"])
        self.assertTrue(completed["finalization"]["trusted"])
        terminal = self.authorities.validate_terminal_record("g", started["run_id"])
        self.assertEqual(terminal["execution_state"], "completed")
        self.assertFalse(self.lease.status()["active"])
        read = self.authorities.validate_read_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        self.assertEqual(
            runner.get_manual_read(
                "g", started["run_id"], actor_id="actor", read_claim=read
            )["status"],
            "completed",
        )
        self.assertEqual(runner.list("g"), [])

        service = type("Service", (), {})()
        service.runner = runner
        service.requests = self.requests
        service.setup = type("Setup", (), {"status": lambda self: {"fingerprint": "fp"}})()
        with patch("no1.daemon.computer_control_ops.get_services", return_value=service):
            response, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "run",
                    "action": "status",
                    "group_id": "g",
                    "actor_id": "actor",
                    "run_id": started["run_id"],
                    "caller_surface": "local_mcp",
                    "run_authority_receipt": started["run_authority_receipt"],
                },
            )
        self.assertTrue(response.ok, response.error)
        self.assertEqual(response.result["result"]["status"], "completed")

        self.issuer = "daemon-restarted"
        restarted_authorities = RunAuthorityStore(
            self.home,
            issuer_epoch_provider=lambda: self.issuer,
            generation_provider=get_actor_turn_generation,
        )
        restarted_runner = WorkflowRunner(
            self.home,
            self.store,
            self.lease,
            self.session,
            run_authorities=restarted_authorities,
            requests=self.requests,
        )
        self.assertEqual(runner._get_unchecked("g", started["run_id"])["status"], "completed")
        self.assertEqual(
            restarted_authorities.validate_terminal_record("g", started["run_id"])[
                "execution_state"
            ],
            "completed",
        )
        restarted_read = restarted_authorities.validate_read_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        self.assertEqual(
            restarted_runner.get_manual_read(
                "g",
                started["run_id"],
                actor_id="actor",
                read_claim=restarted_read,
            )["status"],
            "completed",
        )

    def test_scope_mismatch_has_no_derived_or_run_side_effects(self) -> None:
        workflow = self._workflow("scope-a")
        other = self._workflow("scope-b")
        request, claim = self._start_claim(
            workflow,
            request_id="req-scope",
            inputs={"message": "expected"},
        )
        runner = self._runner()
        cases = (
            (str(other["manifest"]["workflow_id"]), 1, request["inputs"]),
            (str(workflow["manifest"]["workflow_id"]), 999, request["inputs"]),
            (str(workflow["manifest"]["workflow_id"]), 1, {"message": "other"}),
        )
        for workflow_id, version, inputs in cases:
            with self.subTest(workflow_id=workflow_id, version=version, inputs=inputs):
                with self.assertRaises((PermissionError, KeyError)):
                    asyncio.run(
                        runner.start_manual(
                            "g",
                            workflow_id,
                            actor_id="actor",
                            version=version,
                            inputs=inputs,
                            request_id=request["request_id"],
                            start_claim=claim,
                        )
                    )
                self.assertEqual(list((self.store.state_root("g") / "runs").glob("*.json")), [])
                self.assertEqual(
                    list((self.store.state_root("g") / "derived-authorities").glob("*.json")),
                    [],
                )
                self.assertEqual(self.session.catalog_calls, 0)

    def test_definition_change_after_activation_is_rejected_without_side_effects(self) -> None:
        workflow = self._workflow("definition")
        request, claim = self._start_claim(workflow, request_id="req-definition")
        workflow_id = str(workflow["manifest"]["workflow_id"])
        version_path = self.store.root("g") / workflow_id / "versions" / "1.json"
        definition = json.loads(version_path.read_text(encoding="utf-8"))
        definition["description"] = "changed after activation"
        version_path.write_text(json.dumps(definition), encoding="utf-8")
        runner = self._runner()
        with self.assertRaisesRegex(PermissionError, "execution scope"):
            asyncio.run(
                runner.start_manual(
                    "g",
                    workflow_id,
                    actor_id="actor",
                    version=1,
                    inputs=request["inputs"],
                    request_id=request["request_id"],
                    start_claim=claim,
                )
            )
        self.assertEqual(self.session.catalog_calls, 0)
        self.assertFalse((self.store.state_root("g") / "derived-authorities").exists())

    def test_request_projection_failure_precedes_accept_provider_and_task(self) -> None:
        workflow = self._workflow("projection")
        request, claim = self._start_claim(workflow, request_id="req-projection")
        runner = self._runner()
        with patch.object(self.requests, "mark_run_started", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                asyncio.run(
                    runner.start_manual(
                        "g",
                        str(workflow["manifest"]["workflow_id"]),
                        actor_id="actor",
                        version=1,
                        inputs=request["inputs"],
                        request_id=request["request_id"],
                        start_claim=claim,
                    )
                )
        authority_files = list(
            (self.store.state_root("g") / "derived-authorities").glob("*.json")
        )
        self.assertEqual(len(authority_files), 1)
        authority = json.loads(authority_files[0].read_text(encoding="utf-8"))
        self.assertEqual(authority["execution_state"], "start_failed")
        run_files = list((self.store.state_root("g") / "runs").glob("*.json"))
        self.assertEqual(json.loads(run_files[0].read_text(encoding="utf-8"))["status"], "start_failed")
        self.assertNotIn("run_id", self.requests.get("g", request["request_id"]))
        self.assertFalse(self.lease.status()["active"])
        self.assertEqual(self.session.catalog_calls, 0)
        self.assertEqual(runner._tasks, {})

    def test_start_rollback_preserves_original_error_when_active_lease_release_fails(self) -> None:
        workflow = self._workflow("active-rollback")
        request, claim = self._start_claim(workflow, request_id="req-active-rollback")
        runner = self._runner()
        with patch.object(runner, "_catalog", side_effect=ValueError("catalog invalid")), patch.object(
            self.lease,
            "release_run",
            side_effect=OSError("lease release failed"),
        ):
            with self.assertRaisesRegex(ValueError, "catalog invalid"):
                asyncio.run(
                    runner.start_manual(
                        "g",
                        str(workflow["manifest"]["workflow_id"]),
                        actor_id="actor",
                        version=1,
                        inputs=request["inputs"],
                        request_id=request["request_id"],
                        start_claim=claim,
                    )
                )

        authority_path = next(
            (self.store.state_root("g") / "derived-authorities").glob("*.json")
        )
        authority_record = json.loads(authority_path.read_text(encoding="utf-8"))
        run_id = str(authority_record["resource_id"])
        run = runner._get_unchecked("g", run_id)
        self.assertEqual(run["status"], "initializing")
        self.assertEqual(authority_record["control_state"], "active")
        self.assertTrue(self.lease.status()["active"])
        self.issuer = "daemon-restarted-active-rollback"
        authority = self.authorities.restart_candidates()[0]
        self.assertEqual(authority["resource_id"], run_id)
        self._runner()
        self.assertEqual(
            runner._get_unchecked("g", run_id)["status"],
            "start_failed",
        )
        self.assertFalse(self.lease.status()["active"])

    def test_start_rollback_preserves_original_error_when_pending_reservation_cancel_fails(self) -> None:
        workflow = self._workflow("pending-rollback")
        request, claim = self._start_claim(workflow, request_id="req-pending-rollback")
        runner = self._runner()
        with patch.object(
            self.requests,
            "mark_run_started",
            side_effect=ValueError("request projection invalid"),
        ), patch.object(
            self.lease,
            "cancel_run_reservation",
            side_effect=OSError("reservation cancel failed"),
        ):
            with self.assertRaisesRegex(ValueError, "request projection invalid"):
                asyncio.run(
                    runner.start_manual(
                        "g",
                        str(workflow["manifest"]["workflow_id"]),
                        actor_id="actor",
                        version=1,
                        inputs=request["inputs"],
                        request_id=request["request_id"],
                        start_claim=claim,
                    )
                )

        authority_path = next(
            (self.store.state_root("g") / "derived-authorities").glob("*.json")
        )
        authority_record = json.loads(authority_path.read_text(encoding="utf-8"))
        run_id = str(authority_record["resource_id"])
        run = runner._get_unchecked("g", run_id)
        self.assertEqual(run["status"], "initializing")
        self.assertEqual(authority_record["control_state"], "pending")
        self.assertTrue(self.lease.status()["active"])
        self.issuer = "daemon-restarted-pending-rollback"
        authority = self.authorities.restart_candidates()[0]
        self.assertEqual(authority["resource_id"], run_id)
        self._runner()
        self.assertEqual(
            runner._get_unchecked("g", run_id)["status"],
            "start_failed",
        )
        self.assertFalse(self.lease.status()["active"])

    def test_manual_start_rejects_node_ids_with_path_semantics_before_side_effects(self) -> None:
        invalid_ids = ("/tmp/escape", "../escape", "dir/escape", "dir\\escape")
        for index, node_id in enumerate(invalid_ids):
            with self.subTest(node_id=node_id):
                workflow = self._workflow(
                    f"path-{index}",
                    nodes=[
                        {"id": "start", "type": "start"},
                        {"id": node_id, "type": "approval"},
                        {"id": "end", "type": "end"},
                    ],
                    edges=[
                        {"source": "start", "target": node_id},
                        {"source": node_id, "target": "end"},
                    ],
                )
                request, claim = self._start_claim(
                    workflow,
                    request_id=f"req-path-{index}",
                )
                before_request = self.requests._path("g").read_bytes()
                before_run_files = list(
                    (self.store.state_root("g") / "runs").glob("*.json")
                )
                before_authorities = list(
                    (self.store.state_root("g") / "derived-authorities").glob("*.json")
                )
                runner = self._runner()
                with self.assertRaisesRegex(PermissionError, "single path segment"):
                    asyncio.run(
                        runner.start_manual(
                            "g",
                            str(workflow["manifest"]["workflow_id"]),
                            actor_id="actor",
                            version=1,
                            inputs=request["inputs"],
                            request_id=request["request_id"],
                            start_claim=claim,
                        )
                    )
                self.assertEqual(self.requests._path("g").read_bytes(), before_request)
                self.assertEqual(
                    list((self.store.state_root("g") / "runs").glob("*.json")),
                    before_run_files,
                )
                self.assertEqual(
                    list(
                        (self.store.state_root("g") / "derived-authorities").glob("*.json")
                    ),
                    before_authorities,
                )
                self.assertFalse(self.lease.status()["active"])
                self.assertEqual(self.session.catalog_calls, 0)

        workflow = self._workflow(
            "safe-node-id",
            nodes=[
                {"id": "start", "type": "start"},
                {"id": "确认 步骤", "type": "approval"},
                {"id": "end", "type": "end"},
            ],
            edges=[
                {"source": "start", "target": "确认 步骤"},
                {"source": "确认 步骤", "target": "end"},
            ],
        )
        request, claim = self._start_claim(workflow, request_id="req-safe-node-id")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        self.assertEqual(self._wait_terminal(runner, started["run_id"])["status"], "waiting_approval")
        stop = self.authorities.validate_stop_owner(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        runner.cancel_manual_sync("g", started["run_id"], stop_claim=stop)

    def test_cancel_wrong_identity_or_missing_live_execution_has_zero_authority_change(self) -> None:
        workflow = self._workflow(
            "waiting",
            auto_verify=False,
            nodes=[
                {"id": "start", "type": "start"},
                {"id": "wait", "type": "wait", "duration_seconds": 10},
                {"id": "end", "type": "end"},
            ],
            edges=[
                {"source": "start", "target": "wait"},
                {"source": "wait", "target": "end"},
            ],
        )
        request, claim = self._start_claim(workflow, request_id="req-cancel")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        stop = self.authorities.validate_stop_owner(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        before = self.authorities.persisted_record("g", started["run_id"])
        with self.assertRaises(PermissionError):
            runner.cancel_manual_sync("wrong", started["run_id"], stop_claim=stop)
        self.assertEqual(self.authorities.persisted_record("g", started["run_id"]), before)
        execution = runner._manual_executions.pop(started["run_id"])
        try:
            with self.assertRaises(PermissionError):
                runner.cancel_manual_sync("g", started["run_id"], stop_claim=stop)
            self.assertEqual(self.authorities.persisted_record("g", started["run_id"]), before)
        finally:
            runner._manual_executions[started["run_id"]] = execution
            runner.cancel_manual_sync("g", started["run_id"], stop_claim=stop)

    def test_finalize_and_cancel_are_serialized_at_publish(self) -> None:
        workflow = self._workflow("finalize", auto_verify=False)
        request, claim = self._start_claim(workflow, request_id="req-finalize")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        waiting = self._wait_terminal(runner, started["run_id"])
        self.assertEqual(waiting["status"], "awaiting_verification")
        operation = self.authorities.validate_operation_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        stop = self.authorities.validate_stop_owner(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        entered = threading.Event()
        release = threading.Event()
        original_publish = self.store.publish

        def blocked_publish(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original_publish(*args, **kwargs)

        verify_error: list[BaseException] = []
        cancel_error: list[BaseException] = []

        def verify() -> None:
            try:
                runner.verify_manual(
                    "g",
                    started["run_id"],
                    actor_id="actor",
                    operation_claim=operation,
                    passed=True,
                    summary="ok",
                    evidence_ids=[],
                    fingerprint="fp",
                )
            except BaseException as exc:
                verify_error.append(exc)

        def cancel() -> None:
            try:
                asyncio.run(runner.cancel_manual("g", started["run_id"], stop_claim=stop))
            except BaseException as exc:
                cancel_error.append(exc)

        with patch.object(self.store, "publish", side_effect=blocked_publish):
            verify_thread = threading.Thread(target=verify)
            verify_thread.start()
            self.assertTrue(entered.wait(5))
            cancel_thread = threading.Thread(target=cancel)
            cancel_thread.start()
            time.sleep(0.05)
            self.assertTrue(cancel_thread.is_alive())
            release.set()
            verify_thread.join(5)
            cancel_thread.join(5)
        self.assertEqual(verify_error, [])
        self.assertEqual(len(cancel_error), 1)
        self.assertIsInstance(cancel_error[0], PermissionError)
        self.assertEqual(runner._get_unchecked("g", started["run_id"])["status"], "completed")
        self.assertEqual(
            self.authorities.validate_terminal_record("g", started["run_id"])[
                "execution_state"
            ],
            "completed",
        )

    def test_partial_finalize_failure_is_failed_before_cancel_can_proceed(self) -> None:
        workflow = self._workflow("partial-finalize", auto_verify=False)
        request, claim = self._start_claim(workflow, request_id="req-partial")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        self.assertEqual(self._wait_terminal(runner, started["run_id"])["status"], "awaiting_verification")
        operation = self.authorities.validate_operation_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        stop = self.authorities.validate_stop_owner(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        trust_entered = threading.Event()
        release_trust = threading.Event()

        def failed_trust(*args, **kwargs):
            trust_entered.set()
            self.assertTrue(release_trust.wait(5))
            raise OSError("trust write failed")

        verify_result: list[dict] = []
        cancel_error: list[BaseException] = []

        def verify() -> None:
            verify_result.append(
                runner.verify_manual(
                    "g",
                    started["run_id"],
                    actor_id="actor",
                    operation_claim=operation,
                    passed=True,
                    summary="ok",
                    evidence_ids=[],
                    fingerprint="fp",
                )
            )

        def cancel() -> None:
            try:
                asyncio.run(runner.cancel_manual("g", started["run_id"], stop_claim=stop))
            except BaseException as exc:
                cancel_error.append(exc)

        with patch.object(self.store, "trust", side_effect=failed_trust):
            verify_thread = threading.Thread(target=verify)
            verify_thread.start()
            self.assertTrue(trust_entered.wait(5))
            cancel_thread = threading.Thread(target=cancel)
            cancel_thread.start()
            time.sleep(0.05)
            self.assertTrue(cancel_thread.is_alive())
            release_trust.set()
            verify_thread.join(5)
            cancel_thread.join(5)
        self.assertEqual(verify_result[0]["status"], "failed")
        self.assertEqual(verify_result[0]["finalization"]["published"], True)
        self.assertEqual(verify_result[0]["finalization"]["trusted"], False)
        self.assertEqual(len(cancel_error), 1)
        self.assertIsInstance(cancel_error[0], PermissionError)
        manifest = self.store.get(
            "g", str(workflow["manifest"]["workflow_id"]), version=1
        )["manifest"]
        self.assertEqual(manifest["published_version"], 1)
        self.assertEqual(
            self.authorities.validate_terminal_record("g", started["run_id"])[
                "execution_state"
            ],
            "failed",
        )
        read = self.authorities.validate_read_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        self.assertEqual(
            runner.get_manual_read(
                "g", started["run_id"], actor_id="actor", read_claim=read
            )["status"],
            "failed",
        )

    def test_cancel_awaiting_verification_removes_all_live_indexes(self) -> None:
        workflow = self._workflow("cancel-verification", auto_verify=False)
        request, claim = self._start_claim(workflow, request_id="req-cancel-verification")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        self.assertEqual(self._wait_terminal(runner, started["run_id"])["status"], "awaiting_verification")
        stop = self.authorities.validate_stop_owner(
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        cancelled = runner.cancel_manual_sync("g", started["run_id"], stop_claim=stop)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertNotIn(started["run_id"], runner._manual_executions)
        self.assertNotIn(started["run_id"], runner._tasks)
        read = self.authorities.validate_read_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        self.assertEqual(
            runner.get_manual_read(
                "g", started["run_id"], actor_id="actor", read_claim=read
            )["status"],
            "cancelled",
        )

    def test_lease_release_during_enhance_blocks_inner_catalog_and_action(self) -> None:
        tree = (
            "Focused Window:\nName\n---------\nOrders  0  Normal  800 600 123\n\n"
            "UI Tree:\n"
            'desktop "Desktop 1"\n'
            '└── window "Orders"\n'
            '    └── (100,200) Button "Submit" [action: click]\n'
        )

        class Session(_Session):
            async def catalog(self):
                self.catalog_calls += 1
                return [
                    {"name": "Snapshot", "inputSchema": {"type": "object"}},
                    {
                        "name": "Click",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"label": {"type": "integer"}},
                        },
                    },
                ]

            async def call_tool(self, name, arguments, *, timeout):
                self.calls.append(name)
                if name == "Snapshot":
                    return {"content": [{"type": "text", "text": tree}]}
                return {"clicked": True}

        entered = threading.Event()
        release = threading.Event()

        class Provider:
            async def enhance(self, snapshot, locator):
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
                return snapshot

            def current_foreground_window(self):
                return "Orders"

        self.session = Session()
        workflow = self._workflow(
            "provider-barrier",
            nodes=[
                {"id": "start", "type": "start"},
                {
                    "id": "click",
                    "type": "action",
                    "tool": "Click",
                    "arguments": {},
                    "adaptive": False,
                    "target": {"window_name": "Orders", "name": "Submit"},
                },
                {"id": "end", "type": "end"},
            ],
            edges=[
                {"source": "start", "target": "click"},
                {"source": "click", "target": "end"},
            ],
        )
        request, claim = self._start_claim(workflow, request_id="req-provider")
        runner = WorkflowRunner(
            self.home,
            self.store,
            self.lease,
            self.session,
            observation_provider=Provider(),
            run_authorities=self.authorities,
            requests=self.requests,
        )
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        self.assertTrue(entered.wait(5))
        execution = runner._manual_executions[started["run_id"]]
        self.assertTrue(
            self.lease.release_run(run_id=started["run_id"], authority=execution.claim)
        )
        release.set()
        terminal = self._wait_terminal(runner, started["run_id"])
        self.assertEqual(terminal["status"], "interrupted")
        self.assertEqual(self.session.calls, ["Snapshot"])
        self.assertEqual(self.session.catalog_calls, 1)

    def test_lease_release_during_start_catalog_prevents_running_and_task(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        class Session(_Session):
            async def catalog(self):
                self.catalog_calls += 1
                entered.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)
                return []

        self.session = Session()
        workflow = self._workflow("start-catalog")
        request, claim = self._start_claim(workflow, request_id="req-start-catalog")
        runner = self._runner()
        errors: list[BaseException] = []

        def start() -> None:
            try:
                runner.start_manual_sync(
                    "g",
                    str(workflow["manifest"]["workflow_id"]),
                    actor_id="actor",
                    version=1,
                    inputs=request["inputs"],
                    request_id=request["request_id"],
                    start_claim=claim,
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=start)
        thread.start()
        self.assertTrue(entered.wait(5))
        deadline = time.time() + 5
        while not runner._manual_executions and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(runner._manual_executions), 1)
        run_id, execution = next(iter(runner._manual_executions.items()))
        self.assertTrue(self.lease.release_run(run_id=run_id, authority=execution.claim))
        release.set()
        thread.join(5)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PermissionError)
        self.assertEqual(runner._tasks, {})
        self.assertEqual(runner._manual_executions, {})
        self.assertEqual(runner._get_unchecked("g", run_id)["status"], "start_failed")
        self.assertEqual(
            self.authorities.validate_terminal_record("g", run_id)["execution_state"],
            "start_failed",
        )

    def test_local_mcp_approval_uses_operation_receipt_and_legacy_paths_reject(self) -> None:
        workflow = self._workflow(
            "approval",
            nodes=[
                {"id": "start", "type": "start"},
                {"id": "approval", "type": "approval"},
                {"id": "end", "type": "end"},
            ],
            edges=[
                {"source": "start", "target": "approval"},
                {"source": "approval", "target": "end"},
            ],
        )
        request, claim = self._start_claim(workflow, request_id="req-approval")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            waiting = runner._get_unchecked("g", started["run_id"])
            if waiting["status"] == "waiting_approval":
                break
            time.sleep(0.01)
        self.assertEqual(waiting["status"], "waiting_approval")
        approval_path = runner._approval_path("g", started["run_id"], "approval")
        with self.assertRaises(PermissionError):
            runner.decide_approval("g", started["run_id"], "approval", approved=True)
        self.assertFalse(approval_path.exists())

        service = type("Service", (), {})()
        service.runner = runner
        service.requests = self.requests
        service.setup = type("Setup", (), {"status": lambda self: {"fingerprint": "fp"}})()
        with patch("no1.daemon.computer_control_ops.get_services", return_value=service):
            web_response, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "run",
                    "action": "approve",
                    "group_id": "g",
                    "actor_id": "actor",
                    "run_id": started["run_id"],
                    "node_id": "approval",
                    "approved": True,
                    "caller_surface": "local_web",
                },
            )
        self.assertFalse(web_response.ok)
        self.assertFalse(approval_path.exists())

        with patch("no1.daemon.computer_control_ops.get_services", return_value=service):
            response, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "run",
                    "action": "approve",
                    "group_id": "g",
                    "actor_id": "actor",
                    "run_id": started["run_id"],
                    "node_id": "approval",
                    "approved": True,
                    "caller_surface": "local_mcp",
                    "run_authority_receipt": started["run_authority_receipt"],
                },
            )
        self.assertTrue(response.ok, response.error)
        self.assertTrue(approval_path.exists())
        deadline = time.time() + 5
        while time.time() < deadline:
            completed = runner._get_unchecked("g", started["run_id"])
            if completed["status"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(completed["status"], "completed")

    def test_manual_approval_write_and_cancel_are_linearized(self) -> None:
        def start_waiting(suffix: str):
            workflow = self._workflow(
                f"approval-race-{suffix}",
                nodes=[
                    {"id": "start", "type": "start"},
                    {"id": "approval", "type": "approval"},
                    {"id": "end", "type": "end"},
                ],
                edges=[
                    {"source": "start", "target": "approval"},
                    {"source": "approval", "target": "end"},
                ],
            )
            request, claim = self._start_claim(
                workflow,
                request_id=f"req-approval-race-{suffix}",
            )
            runner = self._runner()
            started = runner.start_manual_sync(
                "g",
                str(workflow["manifest"]["workflow_id"]),
                actor_id="actor",
                version=1,
                inputs=request["inputs"],
                request_id=request["request_id"],
                start_claim=claim,
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                run = runner._get_unchecked("g", started["run_id"])
                if run["status"] == "waiting_approval":
                    break
                time.sleep(0.01)
            self.assertEqual(run["status"], "waiting_approval")
            operation = self.authorities.validate_operation_receipt(
                started["run_authority_receipt"],
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id=started["run_id"],
            )
            stop = self.authorities.validate_stop_owner(
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id=started["run_id"],
            )
            return runner, started["run_id"], operation, stop

        runner, run_id, operation, stop = start_waiting("operation-first")
        approval_path = runner._approval_path("g", run_id, "approval")
        entered = threading.Event()
        release = threading.Event()
        original_atomic_write = runtime_module.atomic_write_text

        def blocking_approval_write(path, value):
            if Path(path) == approval_path:
                entered.set()
                self.assertTrue(release.wait(5))
            return original_atomic_write(path, value)

        operation_result: list[dict] = []
        operation_errors: list[BaseException] = []
        cancel_result: list[dict] = []

        def approve() -> None:
            try:
                operation_result.append(
                    runner.decide_approval_manual(
                        "g",
                        run_id,
                        "approval",
                        actor_id="actor",
                        operation_claim=operation,
                        approved=True,
                    )
                )
            except BaseException as exc:
                operation_errors.append(exc)

        def cancel() -> None:
            cancel_result.append(runner.cancel_manual_sync("g", run_id, stop_claim=stop))

        with patch("no1.computer_control.runtime.atomic_write_text", side_effect=blocking_approval_write):
            operation_thread = threading.Thread(target=approve)
            operation_thread.start()
            self.assertTrue(entered.wait(5))
            cancel_thread = threading.Thread(target=cancel)
            cancel_thread.start()
            time.sleep(0.05)
            self.assertTrue(cancel_thread.is_alive())
            release.set()
            operation_thread.join(5)
            cancel_thread.join(5)
        self.assertEqual(operation_errors, [])
        self.assertEqual(operation_result[0]["approved"], True)
        self.assertTrue(approval_path.exists())
        self.assertEqual(cancel_result[0]["status"], "cancelled")

        runner, run_id, operation, stop = start_waiting("cancel-first")
        approval_path = runner._approval_path("g", run_id, "approval")
        entered = threading.Event()
        release = threading.Event()
        original_authority_write = self.authorities._write

        def blocking_cancel_write(path, value):
            if str(value.get("control_state") or "") == "terminating":
                entered.set()
                self.assertTrue(release.wait(5))
            return original_authority_write(path, value)

        operation_result = []
        operation_errors = []
        cancel_result = []
        with patch.object(self.authorities, "_write", side_effect=blocking_cancel_write):
            cancel_thread = threading.Thread(target=cancel)
            cancel_thread.start()
            self.assertTrue(entered.wait(5))
            operation_thread = threading.Thread(target=approve)
            operation_thread.start()
            time.sleep(0.05)
            self.assertTrue(operation_thread.is_alive())
            release.set()
            cancel_thread.join(5)
            operation_thread.join(5)
        self.assertEqual(cancel_result[0]["status"], "cancelled")
        self.assertEqual(operation_result, [])
        self.assertEqual(len(operation_errors), 1)
        self.assertIsInstance(operation_errors[0], PermissionError)
        self.assertFalse(approval_path.exists())

    def test_manual_recovery_write_and_cancel_are_linearized(self) -> None:
        def start_recovering(suffix: str):
            workflow = self._workflow(
                f"recovery-race-{suffix}",
                nodes=[
                    {"id": "start", "type": "start"},
                    {"id": "wait", "type": "wait", "duration_seconds": 30},
                    {"id": "end", "type": "end"},
                ],
                edges=[
                    {"source": "start", "target": "wait"},
                    {"source": "wait", "target": "end"},
                ],
            )
            request, claim = self._start_claim(
                workflow,
                request_id=f"req-recovery-race-{suffix}",
            )
            runner = self._runner()
            started = runner.start_manual_sync(
                "g",
                str(workflow["manifest"]["workflow_id"]),
                actor_id="actor",
                version=1,
                inputs=request["inputs"],
                request_id=request["request_id"],
                start_claim=claim,
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                run = runner._get_unchecked("g", started["run_id"])
                if run.get("current_node_id") == "wait":
                    break
                time.sleep(0.01)
            self.assertEqual(run.get("current_node_id"), "wait")
            execution = runner._manual_executions[started["run_id"]]
            execution.claim = self.authorities.mark_waiting_recovery(execution.claim)
            recovery_id = "recovery_123456abcdef"
            run.update(
                {
                    "status": "recovering",
                    "recovery": {
                        "recovery_id": recovery_id,
                        "node_id": "wait",
                        "tool": "Click",
                        "arguments": {},
                    },
                    "updated_at": time.time(),
                }
            )
            runner._write_execution("g", run, execution)
            operation = self.authorities.validate_operation_receipt(
                started["run_authority_receipt"],
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id=started["run_id"],
            )
            stop = self.authorities.validate_stop_owner(
                expected_group_id="g",
                expected_actor_id="actor",
                expected_resource_id=started["run_id"],
            )
            return runner, started["run_id"], recovery_id, operation, stop

        runner, run_id, recovery_id, operation, stop = start_recovering("operation-first")
        recovery_path = runner._recovery_path("g", run_id, recovery_id)
        entered = threading.Event()
        release = threading.Event()
        original_atomic_write = runtime_module.atomic_write_text

        def blocking_recovery_write(path, value):
            if Path(path) == recovery_path:
                entered.set()
                self.assertTrue(release.wait(5))
            return original_atomic_write(path, value)

        operation_result: list[dict] = []
        operation_errors: list[BaseException] = []
        cancel_result: list[dict] = []

        def recover() -> None:
            try:
                operation_result.append(
                    runner.submit_recovery_manual(
                        "g",
                        run_id,
                        recovery_id,
                        actor_id="actor",
                        operation_claim=operation,
                        resolution="retry",
                    )
                )
            except BaseException as exc:
                operation_errors.append(exc)

        def cancel() -> None:
            cancel_result.append(runner.cancel_manual_sync("g", run_id, stop_claim=stop))

        with patch("no1.computer_control.runtime.atomic_write_text", side_effect=blocking_recovery_write):
            operation_thread = threading.Thread(target=recover)
            operation_thread.start()
            self.assertTrue(entered.wait(5))
            cancel_thread = threading.Thread(target=cancel)
            cancel_thread.start()
            time.sleep(0.05)
            self.assertTrue(cancel_thread.is_alive())
            release.set()
            operation_thread.join(5)
            cancel_thread.join(5)
        self.assertEqual(operation_errors, [])
        self.assertEqual(operation_result[0]["accepted"], True)
        self.assertTrue(recovery_path.exists())
        self.assertEqual(cancel_result[0]["status"], "cancelled")

        runner, run_id, recovery_id, operation, stop = start_recovering("cancel-first")
        recovery_path = runner._recovery_path("g", run_id, recovery_id)
        entered = threading.Event()
        release = threading.Event()
        original_authority_write = self.authorities._write

        def blocking_cancel_write(path, value):
            if str(value.get("control_state") or "") == "terminating":
                entered.set()
                self.assertTrue(release.wait(5))
            return original_authority_write(path, value)

        operation_result = []
        operation_errors = []
        cancel_result = []
        with patch.object(self.authorities, "_write", side_effect=blocking_cancel_write):
            cancel_thread = threading.Thread(target=cancel)
            cancel_thread.start()
            self.assertTrue(entered.wait(5))
            operation_thread = threading.Thread(target=recover)
            operation_thread.start()
            time.sleep(0.05)
            self.assertTrue(operation_thread.is_alive())
            release.set()
            cancel_thread.join(5)
            operation_thread.join(5)
        self.assertEqual(cancel_result[0]["status"], "cancelled")
        self.assertEqual(operation_result, [])
        self.assertEqual(len(operation_errors), 1)
        self.assertIsInstance(operation_errors[0], PermissionError)
        self.assertFalse(recovery_path.exists())

    def test_tampered_manual_run_scope_is_rejected_by_all_actor_controls(self) -> None:
        workflow = self._workflow("tamper", auto_verify=False)
        request, claim = self._start_claim(workflow, request_id="req-tamper")
        runner = self._runner()
        started = runner.start_manual_sync(
            "g",
            str(workflow["manifest"]["workflow_id"]),
            actor_id="actor",
            version=1,
            inputs=request["inputs"],
            request_id=request["request_id"],
            start_claim=claim,
        )
        original = self._wait_terminal(runner, started["run_id"])
        self.assertEqual(original["status"], "awaiting_verification")
        operation = self.authorities.validate_operation_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        read = self.authorities.validate_read_receipt(
            started["run_authority_receipt"],
            expected_group_id="g",
            expected_actor_id="actor",
            expected_resource_id=started["run_id"],
        )
        authority_path = next(
            (self.store.state_root("g") / "derived-authorities").glob("*.json")
        )
        authority_bytes = authority_path.read_bytes()
        manifest_path = self.store.root("g") / str(original["workflow_id"]) / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        run_path = runner._run_path("g", started["run_id"])
        variants = {
            "workflow": {"workflow_id": "wf_other"},
            "version": {"version": 2},
            "group": {"group_id": "other"},
            "actor": {"actor_id": "other"},
            "run": {"run_id": "other"},
            "anchor": {
                "run_authority": {
                    **original["run_authority"],
                    "workflow_id": "wf_other",
                }
            },
        }
        for label, mutation in variants.items():
            with self.subTest(label=label):
                runner._write_unchecked("g", {**original, **mutation})
                before = run_path.read_bytes()
                operations = (
                    lambda: runner.get_manual_read(
                        "g", started["run_id"], actor_id="actor", read_claim=read
                    ),
                    lambda: runner.get_manual(
                        "g", started["run_id"], actor_id="actor", operation_claim=operation
                    ),
                    lambda: runner.decide_approval_manual(
                        "g",
                        started["run_id"],
                        "approval",
                        actor_id="actor",
                        operation_claim=operation,
                        approved=True,
                    ),
                    lambda: runner.submit_recovery_manual(
                        "g",
                        started["run_id"],
                        actor_id="actor",
                        operation_claim=operation,
                    ),
                    lambda: runner.verify_manual(
                        "g",
                        started["run_id"],
                        actor_id="actor",
                        operation_claim=operation,
                        passed=True,
                        summary="tampered",
                        evidence_ids=[],
                        fingerprint="fp",
                    ),
                )
                for operation_call in operations:
                    with self.assertRaises(PermissionError):
                        operation_call()
                    self.assertEqual(run_path.read_bytes(), before)
                    self.assertEqual(authority_path.read_bytes(), authority_bytes)
                    self.assertEqual(manifest_path.read_bytes(), manifest_bytes)
                self.assertFalse(
                    runner._approval_path("g", started["run_id"], "approval").exists()
                )


if __name__ == "__main__":
    unittest.main()
