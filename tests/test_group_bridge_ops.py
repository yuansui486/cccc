from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import pickle
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch


class TestGroupBridgeOps(unittest.TestCase):
    def setUp(self) -> None:
        from no1.daemon.group_bridge.identity import get_group_bridge_identity

        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.home = self.root / "local"
        self.remote_home = self.root / "remote"
        self.endpoint = "https://local.example.test/api/v1/group-bridge/session"
        self.remote_endpoint = "https://remote.example.test/api/v1/group-bridge/session"
        self._env = patch.dict(
            os.environ,
            {
                "CCCC_GROUP_BRIDGE_LOCAL_ENDPOINT": self.endpoint,
                "CCCC_HOME": str(self.home),
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._td.cleanup)
        self.local_identity = get_group_bridge_identity(home=self.home)
        self.remote_identity = get_group_bridge_identity(home=self.remote_home)
        self.authorized = True

        def authorize(**kwargs: object) -> dict[str, object] | None:
            if not self.authorized:
                return None
            return {
                "remote_endpoint": kwargs["remote_endpoint"],
                "remote_group_id": kwargs["remote_group_id"],
                "remote_peer_id": kwargs["remote_peer_id"],
                "access_level": "messages",
                "status": "active",
            }

        self._authorize = patch(
            "no1.daemon.group_bridge.session.authorize_remote_principal",
            side_effect=authorize,
        )
        self._addresses = patch(
            "no1.daemon.group_bridge.session.resolve_peer_multiaddrs",
            return_value=("/dns4/remote.example.test/tcp/443/https",),
        )
        self._authorize.start()
        self._addresses.start()
        self.addCleanup(self._authorize.stop)
        self.addCleanup(self._addresses.stop)

    def _signed_envelope(
        self,
        *,
        issued_at: str | None = None,
        target_group_id: str = "local-group",
        text: str = "verified remote message",
        insight: str | None = None,
        source_by: str = "",
    ) -> dict[str, object]:
        from no1.contracts.v1.group_bridge import GroupBridgeSessionMessage
        from no1.daemon.group_bridge import identity, session

        envelope = session._signed_message(
            nonce=session.new_group_bridge_session_nonce(),
            source_group_id="remote-group",
            source_endpoint=self.remote_endpoint,
            target_group_id=target_group_id,
            target_peer_id=self.local_identity.peer_id,
            target_endpoint=self.endpoint,
            payload=GroupBridgeSessionMessage(
                text=text,
                format="markdown",
                priority="attention",
                reply_required=True,
                insight=insight,
                source_by=source_by,
            ),
            home=self.remote_home,
        ).model_dump()
        if issued_at is not None:
            envelope["issued_at"] = issued_at
            unsigned = {key: value for key, value in envelope.items() if key != "signature"}
            envelope["signature"] = identity.sign_group_bridge_payload(
                identity.canonical_payload_bytes(unsigned),
                home=self.remote_home,
            )
        return envelope

    @staticmethod
    def _receive_args(envelope: dict[str, object]) -> dict[str, object]:
        return {
            "group_id": "local-group",
            "envelope": envelope,
        }

    @staticmethod
    def _send_args() -> dict[str, object]:
        return {
            "group_id": "local-group",
            "local_endpoint": "https://local.example.test/api/v1/group-bridge/session",
            "remote_group_id": "remote-group",
            "remote_peer_id": "remote-peer",
            "remote_endpoint": "https://remote.example.test/api/v1/group-bridge/session",
            "client_nonce": "A" * 43,
            "payload": {
                "text": "hello",
                "format": "plain",
                "priority": "normal",
                "reply_required": False,
            },
        }

    @staticmethod
    def _subprocess_send_script() -> str:
        return """
import json
import os
from no1.contracts.v1 import DaemonRequest
from no1.contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
from no1.daemon import server
from no1.daemon.group_bridge import ops

envelope = GroupBridgeSignedMessageEnvelope.model_validate(json.loads(os.environ["NO1_TEST_ENVELOPE"]))
args = ops._local_send_args(envelope, envelope.payload.model_dump(), os.environ["NO1_TEST_DELIVERY_ID"])
response, should_stop = server.handle_request(DaemonRequest(op="send", args=args))
print(json.dumps({"response": response.model_dump(), "should_stop": should_stop}, separators=(",", ":")))
"""

    def _subprocess_send_env(self, envelope: dict[str, object], delivery_id: str) -> dict[str, str]:
        env = os.environ.copy()
        env["NO1_TEST_ENVELOPE"] = json.dumps(envelope, separators=(",", ":"))
        env["NO1_TEST_DELIVERY_ID"] = delivery_id
        return env

    @staticmethod
    def _ledger_events_with_id(ledger_path: Path, event_id: str) -> list[dict[str, object]]:
        from no1.kernel.ledger_segments import iter_source_lines, list_ledger_sources

        events: list[dict[str, object]] = []
        for source in list_ledger_sources(ledger_path.parent):
            source_path = source.get("abs_path")
            if not isinstance(source_path, Path):
                continue
            for line in iter_source_lines(source_path):
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                if isinstance(value, dict) and value.get("id") == event_id:
                    events.append(value)
        return events

    def test_closed_ops_and_outbound_safe_projection(self) -> None:
        from no1.daemon.group_bridge import ops
        from no1.daemon.group_bridge.session import GroupBridgeSessionError

        dispatch = Mock()
        self.assertIsNone(ops.try_handle_group_bridge_op("unknown", {}, dispatch_send=dispatch))
        expected = {
            "status": "accepted",
            "request_fingerprint": "f" * 64,
            "remote_event_id": "remote-event",
            "attempt": 1,
        }
        with patch.object(ops, "send_group_bridge_session_message", return_value=expected) as send:
            response = ops.try_handle_group_bridge_op(
                "group_bridge_session_send",
                self._send_args(),
                dispatch_send=dispatch,
            )
        self.assertIsNotNone(response)
        assert response is not None
        self.assertTrue(response.ok)
        self.assertEqual(response.result, {"session": expected})
        send.assert_called_once()
        self.assertNotIn("home", send.call_args.kwargs)
        self.assertNotIn("http_post", send.call_args.kwargs)
        dispatch.assert_not_called()

        for changed in (
            {key: value for key, value in self._send_args().items() if key != "payload"},
            {**self._send_args(), "attachments": []},
        ):
            with self.subTest(keys=sorted(changed)):
                with patch.object(ops, "send_group_bridge_session_message") as send:
                    response = ops.try_handle_group_bridge_op(
                        "group_bridge_session_send",
                        changed,
                        dispatch_send=dispatch,
                    )
                assert response is not None
                self.assertFalse(response.ok)
                self.assertEqual(response.error.code, "invalid_request")
                send.assert_not_called()

        unavailable = GroupBridgeSessionError(
            "transport_unavailable",
            "endpointless Group Bridge transport is unavailable",
        )
        endpointless = {**self._send_args(), "remote_endpoint": ""}
        with patch.object(ops, "send_group_bridge_session_message", side_effect=unavailable):
            response = ops.try_handle_group_bridge_op(
                "group_bridge_session_send",
                endpointless,
                dispatch_send=dispatch,
            )
        assert response is not None and response.error is not None
        self.assertEqual(response.error.code, "transport_unavailable")
        self.assertEqual(response.error.details, {"retriable": False})

    def test_signed_receive_is_downgraded_once_and_duplicate_replays_receipt(self) -> None:
        from no1.contracts.v1 import DaemonResponse
        from no1.daemon.group_bridge import ops
        from no1.daemon.messaging.turn_provenance import INGRESS_GROUP_BRIDGE, TRUSTED_INGRESS_ARG

        envelope = self._signed_envelope(
            insight="Remote ownership remains authoritative.",
            source_by="remote-peer",
        )
        dispatched: list[dict[str, object]] = []

        def dispatch_send(args: dict[str, object]):
            dispatched.append(dict(args))
            return DaemonResponse(ok=True, result={"event": {"id": dispatched[0]["client_id"]}}), False

        first = ops.try_handle_group_bridge_op(
            "group_bridge_session_receive",
            self._receive_args(envelope),
            dispatch_send=dispatch_send,
        )
        second = ops.try_handle_group_bridge_op(
            "group_bridge_session_receive",
            self._receive_args(envelope),
            dispatch_send=dispatch_send,
        )
        assert first is not None and second is not None
        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        first_receipt = first.result["receipt"]
        second_receipt = second.result["receipt"]
        for field in ("request_nonce_hash", "request_fingerprint", "remote_event_id", "status"):
            self.assertEqual(first_receipt[field], second_receipt[field])
        self.assertEqual(len(dispatched), 1)

        delivery = dispatched[0]
        self.assertEqual(
            set(delivery),
            {
                "group_id",
                "text",
                "insight",
                "format",
                "priority",
                "reply_required",
                "collaboration_required",
                "by",
                "to",
                "attachments",
                "refs",
                "path",
                "quote_text",
                "source_platform",
                "source_user_id",
                "src_group_id",
                "src_event_id",
                "src_by",
                "remote_reply_to",
                "client_id",
                TRUSTED_INGRESS_ARG,
                ops._GROUP_BRIDGE_DELIVERY_CLAIM_ARG,
            },
        )
        self.assertEqual(delivery["group_id"], "local-group")
        self.assertEqual(delivery["to"], ["user"])
        self.assertEqual(delivery["by"], "system")
        self.assertEqual(delivery["source_user_id"], self.remote_identity.peer_id)
        self.assertEqual(delivery["src_group_id"], "remote-group")
        self.assertEqual(delivery["source_platform"], "group_bridge_session")
        self.assertEqual(delivery["insight"], "Remote ownership remains authoritative.")
        self.assertEqual(delivery["src_by"], "remote-peer")
        self.assertEqual(delivery["remote_reply_to"], ["remote-peer"])
        self.assertEqual(delivery[TRUSTED_INGRESS_ARG], INGRESS_GROUP_BRIDGE)
        self.assertEqual(delivery["src_event_id"], delivery["client_id"])
        self.assertTrue(str(delivery["client_id"]).startswith("gbs_"))
        self.assertEqual(delivery["attachments"], [])
        self.assertEqual(delivery["refs"], [])
        self.assertEqual(delivery["path"], "")
        self.assertEqual(delivery["quote_text"], "")
        self.assertNotIn("computer_control_request", delivery)
        self.assertNotIn(envelope["nonce"], repr(first.result))

    def test_receive_failures_never_reach_local_dispatch(self) -> None:
        from no1.daemon.group_bridge import ops, session

        dispatch = Mock()
        cases: list[tuple[str, dict[str, object], str, object | None]] = []

        self.authorized = False
        cases.append(("authorization", self._signed_envelope(), "unauthorized", None))
        self.authorized = True

        bad_signature = self._signed_envelope()
        bad_signature["signature"] = ("B" * 86) + "=="
        cases.append(("signature", bad_signature, "invalid_signature", None))

        bad_target = self._signed_envelope()
        bad_target["target_group_id"] = "other-group"
        cases.append(("target", bad_target, "invalid_target", None))

        stale = self._signed_envelope(issued_at="2000-01-01T00:00:00Z")
        cases.append(("freshness", stale, "stale_request", None))

        capacity = self._signed_envelope()
        cases.append(("capacity", capacity, "session_capacity", 0))

        for name, envelope, expected, capacity_limit in cases:
            with self.subTest(name=name):
                self.authorized = name != "authorization"
                capacity_patch = (
                    patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", capacity_limit)
                    if capacity_limit is not None
                    else patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", session._MAX_ENTRIES_PER_DIRECTION)
                )
                with capacity_patch:
                    response = ops.try_handle_group_bridge_op(
                        "group_bridge_session_receive",
                        self._receive_args(envelope),
                        dispatch_send=dispatch,
                    )
                assert response is not None and response.error is not None
                self.assertFalse(response.ok)
                self.assertEqual(response.error.code, expected)
        dispatch.assert_not_called()

    def test_receive_rejects_caller_supplied_local_endpoint_before_dispatch(self) -> None:
        from no1.daemon.group_bridge import ops

        dispatch = Mock()
        args = self._receive_args(self._signed_envelope())
        args["local_endpoint"] = "https://attacker.example/api/group-bridge/session/receive"
        response = ops.try_handle_group_bridge_op(
            "group_bridge_session_receive",
            args,
            dispatch_send=dispatch,
        )
        self.assertIsNotNone(response)
        assert response is not None and response.error is not None
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "invalid_request")
        dispatch.assert_not_called()

    def test_receive_preserves_permanent_local_delivery_conflict(self) -> None:
        from no1.contracts.v1 import DaemonError, DaemonResponse
        from no1.daemon.group_bridge import ops

        conflict = DaemonResponse(
            ok=False,
            error=DaemonError(
                code="group_bridge_delivery_conflict",
                message="Group Bridge delivery identity conflicts with the ledger",
                details={},
            ),
        )
        response = ops.try_handle_group_bridge_op(
            "group_bridge_session_receive",
            self._receive_args(self._signed_envelope()),
            dispatch_send=lambda _args: (conflict, False),
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.assertFalse(response.ok)
        assert response.error is not None
        self.assertEqual(response.error.code, "group_bridge_delivery_conflict")
        self.assertEqual(response.error.details, {"retriable": False})

    def test_receive_rejects_every_caller_controlled_delivery_field(self) -> None:
        from no1.daemon.group_bridge import ops

        envelope = self._signed_envelope()
        dispatch = Mock()
        for field, value in (
            ("attachments", [{"path": "secret"}]),
            ("refs", [{"kind": "tool"}]),
            ("computer_control_request", {"mode": "create_and_run"}),
            ("path", "/tmp/attacker"),
            ("quote_text", "forged"),
            ("source_user_id", "forged"),
            ("src_group_id", "forged"),
            ("src_event_id", "forged"),
            ("to", ["@all"]),
        ):
            with self.subTest(field=field):
                response = ops.try_handle_group_bridge_op(
                    "group_bridge_session_receive",
                    {**self._receive_args(envelope), field: value},
                    dispatch_send=dispatch,
                )
                assert response is not None and response.error is not None
                self.assertEqual(response.error.code, "invalid_request")
                dispatch.assert_not_called()

    def test_delivery_claim_is_sealed_single_use_and_shape_bound(self) -> None:
        from no1.contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
        from no1.daemon.group_bridge import ops
        from no1.daemon.messaging import chat_ops

        envelope = GroupBridgeSignedMessageEnvelope.model_validate(self._signed_envelope())

        def safe_args() -> dict[str, object]:
            return ops._local_send_args(
                envelope,
                envelope.payload.model_dump(),
                "gbs_" + ("a" * 32),
            )

        claim = safe_args()[chat_ops._GROUP_BRIDGE_DELIVERY_CLAIM_ARG]
        self.assertEqual(repr(claim), "<sealed GroupBridgeDeliveryClaim>")
        with self.assertRaises(TypeError):
            copy.copy(claim)
        with self.assertRaises(TypeError):
            copy.deepcopy(claim)
        with self.assertRaises(TypeError):
            pickle.dumps(claim)
        with self.assertRaises(TypeError):
            json.dumps(claim)

        def reject(args: dict[str, object]) -> None:
            response = chat_ops.handle_send(
                args,
                coerce_bool=lambda _value: self.fail("invalid claim reached bool coercion"),
                normalize_attachments=lambda _group, _raw: self.fail("invalid claim reached attachments"),
                effective_runner_kind=lambda _kind: self.fail("invalid claim reached delivery planning"),
                auto_wake_recipients=lambda _group, _to, _by: self.fail("invalid claim reached wake"),
                automation_on_resume=lambda _group: self.fail("invalid claim reached resume"),
                automation_on_new_message=lambda _group: self.fail("invalid claim reached automation"),
                clear_pending_system_notifies=lambda _group_id, _kinds: self.fail(
                    "invalid claim reached notifications"
                ),
            )
            self.assertFalse(response.ok)
            self.assertEqual(response.error.code, "invalid_group_bridge_delivery")

        forged = safe_args()
        forged[chat_ops._GROUP_BRIDGE_DELIVERY_CLAIM_ARG] = chat_ops._GroupBridgeDeliveryClaim()
        reject(forged)
        reject({**safe_args(), chat_ops._GROUP_BRIDGE_DELIVERY_CLAIM_ARG: object()})

        removal_fields = set(chat_ops._GROUP_BRIDGE_DELIVERY_FIELDS) - {
            chat_ops._GROUP_BRIDGE_DELIVERY_CLAIM_ARG
        }
        for field in sorted(removal_fields):
            with self.subTest(kind="missing", field=field):
                changed = safe_args()
                changed.pop(field)
                reject(changed)

        shifts = {
            "group_id": "",
            "text": "",
            "format": "html",
            "priority": "urgent",
            "reply_required": 1,
            "collaboration_required": True,
            "by": self.remote_identity.peer_id,
            "to": ["@all"],
            "attachments": [{"path": "secret"}],
            "refs": [{"kind": "tool"}],
            "path": "/tmp/attacker",
            "quote_text": "forged",
            "source_platform": "web_user",
            "source_user_id": "",
            "src_group_id": "",
            "src_event_id": "gbs_invalid",
            "client_id": "gbs_" + ("b" * 32),
            "__turn_ingress": "web_user",
        }
        for field, value in shifts.items():
            with self.subTest(kind="value", field=field):
                changed = safe_args()
                changed[field] = value
                reject(changed)
        for name, updates in (
            ("group", {"group_id": "other-group"}),
            ("text", {"text": "other verified text"}),
            ("source-peer", {"source_user_id": "other-peer"}),
            ("source-group", {"src_group_id": "other-remote-group"}),
            (
                "delivery",
                {
                    "src_event_id": "gbs_" + ("c" * 32),
                    "client_id": "gbs_" + ("c" * 32),
                },
            ),
        ):
            with self.subTest(kind="bound-facts", field=name):
                changed = safe_args()
                changed.update(updates)
                reject(changed)

        for name, updates in (
            ("group", {"group_id": "other-group"}),
            ("text", {"text": "intercepted text"}),
            ("format", {"format": "plain"}),
            ("priority", {"priority": "normal"}),
            ("reply-required", {"reply_required": False}),
            ("source-peer", {"source_user_id": "other-peer"}),
            ("source-group", {"src_group_id": "other-remote-group"}),
            (
                "delivery",
                {
                    "src_event_id": "gbs_" + ("d" * 32),
                    "client_id": "gbs_" + ("d" * 32),
                },
            ),
        ):
            with self.subTest(kind="captured-spent", field=name):
                original = safe_args()
                reject({**original, **updates})
                accepted, error = chat_ops._consume_valid_group_bridge_delivery(original)
                self.assertFalse(accepted)
                self.assertIsNotNone(error)

        pid_bound = safe_args()
        with patch.object(chat_ops.os, "getpid", return_value=os.getpid() + 1):
            reject(pid_bound)
        accepted, error = chat_ops._consume_valid_group_bridge_delivery(pid_bound)
        self.assertTrue(accepted)
        self.assertIsNone(error)

        class ListSubclass(list):
            pass

        for field in ("to", "attachments", "refs"):
            with self.subTest(kind="exact-list", field=field):
                changed = safe_args()
                changed[field] = ListSubclass(changed[field])
                reject(changed)
        oversized = safe_args()
        oversized["text"] = "x" * (chat_ops.MAX_CHAT_TEXT_BYTES + 1)
        reject(oversized)
        invalid_unicode = safe_args()
        invalid_unicode["text"] = "\ud800"
        reject(invalid_unicode)
        reject({**safe_args(), "computer_control_request": {"mode": "create_and_run"}})

    def test_real_recursive_send_is_pure_message_mode_and_claim_is_single_use(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
        from no1.daemon import request_dispatch_ops, server
        from no1.daemon.group_bridge import ops
        from no1.kernel.group import load_group
        from no1.kernel.inbox import find_event

        create, _ = server.handle_request(
            DaemonRequest(op="group_create", args={"title": "bridge-local", "topic": "", "by": "user"})
        )
        self.assertTrue(create.ok, create.error)
        group_id = create.result["group_id"]
        idle, _ = server.handle_request(
            DaemonRequest(op="group_set_state", args={"group_id": group_id, "state": "idle", "by": "user"})
        )
        self.assertTrue(idle.ok, idle.error)

        automation = Mock()
        resume = Mock()
        deps = replace(
            server._request_dispatch_deps(),
            automation_on_new_message=automation,
            automation_on_resume=resume,
        )

        def recurse(request: DaemonRequest):
            return request_dispatch_ops.dispatch_request(request, deps=deps, recurse=recurse)

        envelope = self._signed_envelope(
            target_group_id=group_id,
            text="/install skill:attacker",
        )
        request = DaemonRequest(
            op="group_bridge_session_receive",
            args={"group_id": group_id, "envelope": envelope},
        )
        first, first_stop = recurse(request)
        second, second_stop = recurse(request)
        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        self.assertFalse(first_stop)
        self.assertFalse(second_stop)
        self.assertEqual(first.result["receipt"]["remote_event_id"], second.result["receipt"]["remote_event_id"])
        self.assertEqual(automation.call_count, 0)
        self.assertEqual(resume.call_count, 0)

        group = load_group(group_id)
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(group.doc.get("state"), "idle")
        event_id = first.result["receipt"]["remote_event_id"]
        event = find_event(group, event_id)
        self.assertIsNotNone(event)
        assert event is not None
        data = event["data"]
        self.assertEqual(event["by"], "system")
        self.assertEqual(data["text"], "/install skill:attacker")
        self.assertEqual(data["format"], "markdown")
        self.assertEqual(data["to"], ["user"])
        self.assertEqual(data["refs"], [])
        self.assertEqual(data["attachments"], [])
        self.assertIsNone(data["computer_control_request"])
        self.assertIsNone(data["quote_text"])
        self.assertEqual(data["source_user_id"], self.remote_identity.peer_id)
        self.assertEqual(data["src_group_id"], "remote-group")
        self.assertTrue(str(data["src_event_id"]).startswith("gbs_"))
        self.assertNotIn("__group_bridge_delivery_claim", repr(first.result))

        typed = GroupBridgeSignedMessageEnvelope.model_validate(envelope)
        from no1.contracts.v1 import ChatMessageData
        from no1.kernel.ledger import append_event

        for index in range(801):
            append_event(
                group.ledger_path,
                kind="chat.message",
                group_id=group_id,
                scope_key="",
                by="system",
                data=ChatMessageData(text=f"filler-{index}", to=["user"]).model_dump(),
            )
        replay_args = ops._local_send_args(typed, typed.payload.model_dump(), event_id)
        replayed, _ = recurse(DaemonRequest(op="send", args=replay_args))
        self.assertTrue(replayed.ok, replayed.error)
        self.assertTrue(replayed.result["replayed"])
        self.assertEqual(replayed.result["event"], event)
        self.assertEqual(len(self._ledger_events_with_id(group.ledger_path, event_id)), 1)

        leaked_args = ops._local_send_args(typed, typed.payload.model_dump(), "gbs_" + ("b" * 32))
        accepted, _ = recurse(DaemonRequest(op="send", args=leaked_args))
        rejected, _ = recurse(DaemonRequest(op="send", args=leaked_args))
        self.assertTrue(accepted.ok, accepted.error)
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.error.code, "invalid_group_bridge_delivery")

        ordinary, _ = recurse(
            DaemonRequest(
                op="send",
                args={
                    "group_id": group_id,
                    "by": "user",
                    "to": ["user"],
                    "text": "/install skill:ordinary",
                },
            )
        )
        self.assertTrue(ordinary.ok, ordinary.error)
        self.assertEqual(automation.call_count, 1)
        self.assertEqual(resume.call_count, 1)
        group = load_group(group_id)
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(group.doc.get("state"), "active")
        ordinary_event = ordinary.result["event"]
        self.assertEqual(ordinary_event["data"]["text"], "/install skill:ordinary")
        self.assertEqual(ordinary_event["data"]["format"], "plain")
        self.assertEqual(ordinary_event["data"]["refs"][0]["title"], "slash_command")

    def test_replayed_event_retries_local_delivery_after_append_crash_prefix(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon import server
        from no1.daemon.group_bridge import ops
        from no1.daemon.messaging import chat_ops
        from no1.kernel.group import load_group

        created, _ = server.handle_request(
            DaemonRequest(op="group_create", args={"title": "bridge-replay-delivery", "topic": "", "by": "user"})
        )
        self.assertTrue(created.ok, created.error)
        group = load_group(created.result["group_id"])
        self.assertIsNotNone(group)
        assert group is not None
        envelope = self._signed_envelope(target_group_id=group.group_id)
        from no1.contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
        typed = GroupBridgeSignedMessageEnvelope.model_validate(envelope)
        delivery_id = "gbs_" + ("a" * 32)
        first_args = ops._local_send_args(typed, typed.payload.model_dump(), delivery_id)
        first, _ = server.handle_request(DaemonRequest(op="send", args=first_args))
        self.assertTrue(first.ok, first.error)

        # The event already exists, simulating a retry after a crash between
        # the event-once append and completion of the local delivery attempt.
        notify = Mock()
        retry_args = ops._local_send_args(typed, typed.payload.model_dump(), delivery_id)
        with patch.object(chat_ops, "_notify_headless_targets", notify):
            replay, _ = server.handle_request(DaemonRequest(op="send", args=retry_args))
        self.assertTrue(replay.ok, replay.error)
        self.assertTrue(replay.result["replayed"])
        self.assertTrue(replay.result["message_sent"])
        self.assertEqual(replay.result["event"], first.result["event"])
        notify.assert_called_once()
    def test_delivery_id_survives_index_loss_restart_and_process_competition(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
        from no1.daemon import server
        from no1.daemon.group_bridge import ops
        from no1.kernel.group import load_group

        created, _ = server.handle_request(
            DaemonRequest(op="group_create", args={"title": "bridge-restart", "topic": "", "by": "user"})
        )
        self.assertTrue(created.ok, created.error)
        group_id = created.result["group_id"]
        envelope = self._signed_envelope(target_group_id=group_id)
        typed = GroupBridgeSignedMessageEnvelope.model_validate(envelope)
        delivery_id = "gbs_" + ("e" * 32)
        first_args = ops._local_send_args(typed, typed.payload.model_dump(), delivery_id)
        first, _ = server.handle_request(DaemonRequest(op="send", args=first_args))
        self.assertTrue(first.ok, first.error)

        group = load_group(group_id)
        self.assertIsNotNone(group)
        assert group is not None
        index_path = group.path / "state" / "ledger" / "index.sqlite3"
        for path in (index_path, Path(f"{index_path}-wal"), Path(f"{index_path}-shm")):
            path.unlink(missing_ok=True)
        index_path.write_bytes(b"not-a-sqlite-index")

        completed = subprocess.run(
            [sys.executable, "-c", self._subprocess_send_script()],
            cwd=Path(__file__).resolve().parents[1],
            env=self._subprocess_send_env(envelope, delivery_id),
            check=True,
            capture_output=True,
            text=True,
        )
        restarted = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertFalse(restarted["should_stop"])
        self.assertTrue(restarted["response"]["ok"])
        self.assertTrue(restarted["response"]["result"]["replayed"])
        self.assertEqual(restarted["response"]["result"]["event"], first.result["event"])
        self.assertEqual(len(self._ledger_events_with_id(group.ledger_path, delivery_id)), 1)

        competing_id = "gbs_" + ("f" * 32)
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", self._subprocess_send_script()],
                cwd=Path(__file__).resolve().parents[1],
                env=self._subprocess_send_env(envelope, competing_id),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results: list[dict[str, object]] = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout.strip().splitlines()[-1]))
        self.assertTrue(all(result["response"]["ok"] for result in results))
        competing_events = [result["response"]["result"]["event"] for result in results]
        self.assertEqual(competing_events[0], competing_events[1])
        self.assertEqual({event["id"] for event in competing_events}, {competing_id})
        replay_count = sum(bool(result["response"]["result"].get("replayed")) for result in results)
        self.assertEqual(replay_count, 1)
        self.assertEqual(len(self._ledger_events_with_id(group.ledger_path, competing_id)), 1)

    def test_delivery_id_is_atomic_across_threads_and_rejects_fact_conflicts(self) -> None:
        from no1.contracts.v1 import ChatMessageData, DaemonRequest
        from no1.contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
        from no1.daemon import server
        from no1.daemon.group_bridge import ops
        from no1.kernel.group import load_group
        from no1.kernel.ledger import LedgerEventConflictError, append_event_once

        created, _ = server.handle_request(
            DaemonRequest(op="group_create", args={"title": "bridge-race", "topic": "", "by": "user"})
        )
        self.assertTrue(created.ok, created.error)
        group_id = created.result["group_id"]
        envelope = self._signed_envelope(target_group_id=group_id)
        typed = GroupBridgeSignedMessageEnvelope.model_validate(envelope)
        delivery_id = "gbs_" + ("c" * 32)
        barrier = threading.Barrier(3)
        responses: list[object] = []

        def send_once() -> None:
            args = ops._local_send_args(typed, typed.payload.model_dump(), delivery_id)
            barrier.wait(timeout=10)
            response, _ = server.handle_request(DaemonRequest(op="send", args=args))
            responses.append(response)

        threads = [threading.Thread(target=send_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(response.ok for response in responses))
        self.assertEqual(responses[0].result["event"], responses[1].result["event"])
        self.assertEqual({response.result["event"]["id"] for response in responses}, {delivery_id})
        self.assertEqual(sum(bool(response.result.get("replayed")) for response in responses), 1)

        group = load_group(group_id)
        self.assertIsNotNone(group)
        assert group is not None
        self.assertEqual(len(self._ledger_events_with_id(group.ledger_path, delivery_id)), 1)

        conflict_id = "gbs_" + ("d" * 32)
        conflict, replayed = append_event_once(
            group.ledger_path,
            event_id=conflict_id,
            kind="chat.message",
            group_id=group_id,
            scope_key="",
            by="system",
            data=ChatMessageData(text="conflicting local fact", to=["user"]).model_dump(),
        )
        self.assertFalse(replayed)
        conflict_args = ops._local_send_args(typed, typed.payload.model_dump(), conflict_id)
        rejected, _ = server.handle_request(DaemonRequest(op="send", args=conflict_args))
        self.assertFalse(rejected.ok)
        self.assertEqual(rejected.error.code, "group_bridge_delivery_conflict")
        self.assertEqual(self._ledger_events_with_id(group.ledger_path, conflict_id), [conflict])

        with self.assertRaises(LedgerEventConflictError):
            append_event_once(
                group.ledger_path,
                event_id=conflict_id,
                kind="chat.message",
                group_id=group_id,
                scope_key="different-scope",
                by="system",
                data=conflict["data"],
            )
        self.assertEqual(self._ledger_events_with_id(group.ledger_path, conflict_id), [conflict])

    def test_delivery_id_falls_back_to_ledger_for_logically_corrupt_index(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.contracts.v1.group_bridge import GroupBridgeSignedMessageEnvelope
        from no1.daemon import server
        from no1.daemon.group_bridge import ops
        from no1.kernel import ledger_index
        from no1.kernel.group import load_group
        from no1.kernel.ledger_segments import compress_sealed_segments, rotate_active_ledger

        created, _ = server.handle_request(
            DaemonRequest(op="group_create", args={"title": "bridge-index", "topic": "", "by": "user"})
        )
        self.assertTrue(created.ok, created.error)
        group_id = created.result["group_id"]
        envelope = self._signed_envelope(target_group_id=group_id)
        typed = GroupBridgeSignedMessageEnvelope.model_validate(envelope)
        group = load_group(group_id)
        self.assertIsNotNone(group)
        assert group is not None
        index_path = group.path / "state" / "ledger" / "index.sqlite3"

        def send(delivery_id: str):
            args = ops._local_send_args(typed, typed.payload.model_dump(), delivery_id)
            response, _ = server.handle_request(DaemonRequest(op="send", args=args))
            self.assertTrue(response.ok, response.error)
            return response

        remapped_id = "gbs_" + ("1" * 32)
        remapped = send(remapped_id)
        self.assertTrue(rotate_active_ledger(group.path, reason="idempotency-test")["rotated"])
        self.assertEqual(compress_sealed_segments(group.path, keep_recent=0, force=True)["count"], 1)
        ledger_index.catch_up_ledger_index(group.ledger_path, force_rebuild=True)
        with sqlite3.connect(str(index_path)) as conn:
            replacement = "gbs_" + ("2" * 32)
            conn.execute("UPDATE events SET event_id = ? WHERE event_id = ?", (replacement, remapped_id))
            conn.execute("UPDATE event_search SET event_id = ? WHERE event_id = ?", (replacement, remapped_id))
        remapped_replay = send(remapped_id)
        self.assertTrue(remapped_replay.result["replayed"])
        self.assertEqual(remapped_replay.result["event"], remapped.result["event"])
        self.assertEqual(self._ledger_events_with_id(group.ledger_path, remapped_id), [remapped.result["event"]])
        self.assertEqual(ledger_index.lookup_event_by_id(group.ledger_path, remapped_id), remapped.result["event"])

        other_line_id = "gbs_" + ("5" * 32)
        send(other_line_id)
        wrong_line_id = "gbs_" + ("3" * 32)
        wrong_line = send(wrong_line_id)
        with sqlite3.connect(str(index_path)) as conn:
            source = conn.execute(
                "SELECT source_path, line_no, offset_bytes FROM events WHERE event_id = ?",
                (other_line_id,),
            ).fetchone()
            self.assertIsNotNone(source)
            conn.execute(
                "UPDATE events SET source_path = ?, line_no = ?, offset_bytes = ? WHERE event_id = ?",
                (*source, wrong_line_id),
            )
        wrong_line_replay = send(wrong_line_id)
        self.assertTrue(wrong_line_replay.result["replayed"])
        self.assertEqual(wrong_line_replay.result["event"], wrong_line.result["event"])
        self.assertEqual(self._ledger_events_with_id(group.ledger_path, wrong_line_id), [wrong_line.result["event"]])

        empty_line_no = len(group.ledger_path.read_text(encoding="utf-8").splitlines()) + 1
        with group.ledger_path.open("ab") as handle:
            empty_offset = handle.tell()
            handle.write(b"\n")
        ledger_index.catch_up_ledger_index(group.ledger_path)

        empty_line_id = "gbs_" + ("4" * 32)
        empty_line = send(empty_line_id)
        with sqlite3.connect(str(index_path)) as conn:
            conn.execute(
                "UPDATE events SET line_no = ?, offset_bytes = ? WHERE event_id = ?",
                (empty_line_no, empty_offset, empty_line_id),
            )
        empty_line_replay = send(empty_line_id)
        self.assertTrue(empty_line_replay.result["replayed"])
        self.assertEqual(empty_line_replay.result["event"], empty_line.result["event"])
        self.assertEqual(self._ledger_events_with_id(group.ledger_path, empty_line_id), [empty_line.result["event"]])

    def test_delivery_id_rejects_duplicate_facts_across_all_ledger_sources(self) -> None:
        from no1.contracts.v1 import ChatMessageData, DaemonRequest
        from no1.daemon import server
        from no1.kernel import ledger_index
        from no1.kernel.group import load_group
        from no1.kernel.ledger import LedgerEventConflictError, append_event_once
        from no1.kernel.ledger_segments import compress_sealed_segments, rotate_active_ledger

        created, _ = server.handle_request(
            DaemonRequest(op="group_create", args={"title": "bridge-duplicates", "topic": "", "by": "user"})
        )
        self.assertTrue(created.ok, created.error)
        group_id = created.result["group_id"]
        group = load_group(group_id)
        self.assertIsNotNone(group)
        assert group is not None
        event_data = ChatMessageData(text="duplicate source fact", to=["user"]).model_dump()

        def append_unique(event_id: str) -> dict[str, object]:
            event, replayed = append_event_once(
                group.ledger_path,
                event_id=event_id,
                kind="chat.message",
                group_id=group_id,
                scope_key="",
                by="system",
                data=event_data,
            )
            self.assertFalse(replayed)
            return event

        def append_raw(event: dict[str, object]) -> None:
            with group.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")

        def assert_duplicate_conflict(event_id: str, event: dict[str, object]) -> None:
            ledger_index.catch_up_ledger_index(group.ledger_path, force_rebuild=True)
            self.assertEqual(ledger_index.lookup_event_by_id(group.ledger_path, event_id), event)
            with self.assertRaises(LedgerEventConflictError):
                append_event_once(
                    group.ledger_path,
                    event_id=event_id,
                    kind="chat.message",
                    group_id=group_id,
                    scope_key="",
                    by="system",
                    data=event_data,
                )
            self.assertEqual(self._ledger_events_with_id(group.ledger_path, event_id), [event, event])

        active_id = "gbs_" + ("6" * 32)
        active_event = append_unique(active_id)
        append_raw(active_event)
        assert_duplicate_conflict(active_id, active_event)

        sealed_active_id = "gbs_" + ("7" * 32)
        sealed_active_event = append_unique(sealed_active_id)
        self.assertTrue(rotate_active_ledger(group.path, reason="sealed-active-test")["rotated"])
        compress_sealed_segments(group.path, keep_recent=0, force=True)
        append_raw(sealed_active_event)
        assert_duplicate_conflict(sealed_active_id, sealed_active_event)

        sealed_id = "gbs_" + ("8" * 32)
        sealed_event = append_unique(sealed_id)
        self.assertTrue(rotate_active_ledger(group.path, reason="sealed-first-test")["rotated"])
        compress_sealed_segments(group.path, keep_recent=0, force=True)
        append_raw(sealed_event)
        self.assertTrue(rotate_active_ledger(group.path, reason="sealed-second-test")["rotated"])
        compress_sealed_segments(group.path, keep_recent=0, force=True)
        assert_duplicate_conflict(sealed_id, sealed_event)

    def test_delivery_id_rejects_json_type_equivalent_fact_tampering(self) -> None:
        from no1.contracts.v1 import ChatMessageData, DaemonRequest
        from no1.daemon import server
        from no1.kernel.group import load_group
        from no1.kernel.ledger import LedgerEventConflictError, append_event_once

        created, _ = server.handle_request(
            DaemonRequest(op="group_create", args={"title": "bridge-json-types", "topic": "", "by": "user"})
        )
        self.assertTrue(created.ok, created.error)
        group_id = created.result["group_id"]
        group = load_group(group_id)
        self.assertIsNotNone(group)
        assert group is not None
        event_data = ChatMessageData(text="type exact fact", to=["user"]).model_dump()
        mutations = (
            ("top-level bool", "v", True),
            ("top-level float", "v", 1.0),
            ("nested int", "reply_required", 0),
            ("nested float", "reply_required", 0.0),
        )

        for index, (label, field, replacement) in enumerate(mutations, start=1):
            with self.subTest(case=label):
                event_id = f"gbs_{index:032x}"
                _, replayed = append_event_once(
                    group.ledger_path,
                    event_id=event_id,
                    kind="chat.message",
                    group_id=group_id,
                    scope_key="",
                    by="system",
                    data=event_data,
                )
                self.assertFalse(replayed)
                lines = group.ledger_path.read_text(encoding="utf-8").splitlines()
                for line_index, raw_line in enumerate(lines):
                    persisted = json.loads(raw_line)
                    if persisted.get("id") != event_id:
                        continue
                    if field == "v":
                        persisted["v"] = replacement
                    else:
                        persisted["data"][field] = replacement
                    lines[line_index] = json.dumps(persisted, ensure_ascii=False)
                    break
                else:
                    self.fail(f"missing persisted event: {event_id}")
                group.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tampered_bytes = group.ledger_path.read_bytes()

                with self.assertRaises(LedgerEventConflictError):
                    append_event_once(
                        group.ledger_path,
                        event_id=event_id,
                        kind="chat.message",
                        group_id=group_id,
                        scope_key="",
                        by="system",
                        data=event_data,
                    )
                self.assertEqual(group.ledger_path.read_bytes(), tampered_bytes)
                persisted = self._ledger_events_with_id(group.ledger_path, event_id)
                self.assertEqual(len(persisted), 1)
                actual = persisted[0]["v"] if field == "v" else persisted[0]["data"][field]
                self.assertIs(type(actual), type(replacement))

    def test_delivery_id_fails_closed_for_malformed_ledger_sources(self) -> None:
        from no1.contracts.v1 import ChatMessageData, DaemonRequest
        from no1.daemon import server
        from no1.kernel.group import load_group
        from no1.kernel.ledger import LedgerEventSourceError, append_event_once
        from no1.kernel.ledger_segments import (
            compress_sealed_segments,
            list_sealed_segments,
            rotate_active_ledger,
        )

        event_data = ChatMessageData(text="malformed source fact", to=["user"]).model_dump()

        def create_group(label: str):
            created, _ = server.handle_request(
                DaemonRequest(op="group_create", args={"title": label, "topic": "", "by": "user"})
            )
            self.assertTrue(created.ok, created.error)
            group = load_group(created.result["group_id"])
            self.assertIsNotNone(group)
            return group

        def append_unique(group, event_id: str) -> None:
            _, replayed = append_event_once(
                group.ledger_path,
                event_id=event_id,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="system",
                data=event_data,
            )
            self.assertFalse(replayed)

        for index, layout in enumerate(("active", "sealed-plain", "sealed-gzip"), start=10):
            with self.subTest(layout=layout):
                group = create_group(f"bridge-malformed-{layout}")
                event_id = f"gbs_{index:032x}"
                append_unique(group, event_id)
                source_path = group.ledger_path
                if layout != "active":
                    self.assertTrue(rotate_active_ledger(group.path, reason=layout)["rotated"])
                    if layout == "sealed-gzip":
                        compress_sealed_segments(group.path, keep_recent=0, force=True)
                    source_path = list_sealed_segments(group.path)[0]["abs_path"]
                if layout == "sealed-gzip":
                    with gzip.open(source_path, "rb") as handle:
                        source_bytes = handle.read()
                    with gzip.open(source_path, "wb") as handle:
                        handle.write(source_bytes[:-2] + b"\n")
                else:
                    source_bytes = source_path.read_bytes()
                    source_path.write_bytes(source_bytes[:-2] + b"\n")
                source_before = source_path.read_bytes()
                active_before = group.ledger_path.read_bytes()

                with self.assertRaises(LedgerEventSourceError):
                    append_event_once(
                        group.ledger_path,
                        event_id=event_id,
                        kind="chat.message",
                        group_id=group.group_id,
                        scope_key="",
                        by="system",
                        data=event_data,
                    )
                self.assertEqual(source_path.read_bytes(), source_before)
                self.assertEqual(group.ledger_path.read_bytes(), active_before)

        group = create_group("bridge-malformed-unrelated")
        group.ledger_path.write_bytes(b'{"id":"unrelated"\n')
        source_before = group.ledger_path.read_bytes()
        with self.assertRaises(LedgerEventSourceError):
            append_unique(group, "gbs_" + ("e" * 32))
        self.assertEqual(group.ledger_path.read_bytes(), source_before)

        invalid_sources = (
            ("non-object", b"[]\n"),
            ("duplicate-key", b'{"id":"first","id":"second"}\n'),
            ("non-finite", b'{"value":NaN}\n'),
            ("invalid-utf8", b'{"id":"bad-\xff"}\n'),
            ("unterminated", b'{"id":"unterminated"}'),
        )
        for index, (label, source_bytes) in enumerate(invalid_sources, start=20):
            with self.subTest(invalid_source=label):
                group = create_group(f"bridge-invalid-{label}")
                group.ledger_path.write_bytes(source_bytes)
                with self.assertRaises(LedgerEventSourceError):
                    append_unique(group, f"gbs_{index:032x}")
                self.assertEqual(group.ledger_path.read_bytes(), source_bytes)

        group = create_group("bridge-truncated-gzip")
        truncated_id = "gbs_" + ("f" * 32)
        append_unique(group, truncated_id)
        self.assertTrue(rotate_active_ledger(group.path, reason="truncated-gzip")["rotated"])
        compress_sealed_segments(group.path, keep_recent=0, force=True)
        source_path = list_sealed_segments(group.path)[0]["abs_path"]
        source_bytes = source_path.read_bytes()[:-8]
        source_path.write_bytes(source_bytes)
        with self.assertRaises(LedgerEventSourceError):
            append_unique(group, truncated_id)
        self.assertEqual(source_path.read_bytes(), source_bytes)

        group = create_group("bridge-blank-source")
        group.ledger_path.write_bytes(b"\n \t\n")
        append_unique(group, "gbs_" + ("0" * 32))

    def test_delivery_id_source_scan_bounds_plain_and_gzip_records(self) -> None:
        from no1.contracts.v1 import ChatMessageData, DaemonRequest
        from no1.daemon import server
        from no1.kernel import ledger as ledger_module
        from no1.kernel.group import load_group
        from no1.kernel.ledger import LedgerEventSourceError, MAX_EVENT_BYTES, append_event_once
        from no1.kernel.ledger_segments import (
            compress_sealed_segments,
            list_sealed_segments,
            load_ledger_manifest,
            rotate_active_ledger,
        )

        event_data = ChatMessageData(text="bounded source fact", to=["user"]).model_dump()

        def create_group(label: str):
            created, _ = server.handle_request(
                DaemonRequest(op="group_create", args={"title": label, "topic": "", "by": "user"})
            )
            self.assertTrue(created.ok, created.error)
            group = load_group(created.result["group_id"])
            self.assertIsNotNone(group)
            return group

        def append_unique(group, event_id: str) -> None:
            _, replayed = append_event_once(
                group.ledger_path,
                event_id=event_id,
                kind="chat.message",
                group_id=group.group_id,
                scope_key="",
                by="system",
                data=event_data,
            )
            self.assertFalse(replayed)

        def json_record(size: int) -> bytes:
            prefix = b'{"padding":"'
            suffix = b'"}'
            self.assertGreaterEqual(size, len(prefix) + len(suffix))
            return prefix + (b"x" * (size - len(prefix) - len(suffix))) + suffix

        for compressed in (False, True):
            for record_size in (MAX_EVENT_BYTES, MAX_EVENT_BYTES + 1):
                with self.subTest(compressed=compressed, record_size=record_size):
                    group = create_group(f"bridge-source-bound-{compressed}-{record_size}")
                    group.ledger_path.write_bytes(json_record(record_size) + b"\n")
                    source_path = group.ledger_path
                    if compressed:
                        self.assertTrue(rotate_active_ledger(group.path, reason="bounded-source")["rotated"])
                        compress_sealed_segments(group.path, keep_recent=0, force=True)
                        source_path = list_sealed_segments(group.path)[0]["abs_path"]
                    source_before = source_path.read_bytes()
                    active_before = group.ledger_path.read_bytes()
                    event_id = "gbs_" + hashlib.sha256(
                        f"{compressed}:{record_size}".encode("ascii")
                    ).hexdigest()[:32]

                    if record_size == MAX_EVENT_BYTES:
                        append_unique(group, event_id)
                        if compressed:
                            self.assertEqual(source_path.read_bytes(), source_before)
                            self.assertNotEqual(group.ledger_path.read_bytes(), active_before)
                        else:
                            self.assertTrue(group.ledger_path.read_bytes().startswith(source_before))
                        self.assertEqual(len(self._ledger_events_with_id(group.ledger_path, event_id)), 1)
                    else:
                        with self.assertRaises(LedgerEventSourceError):
                            append_unique(group, event_id)
                        self.assertEqual(source_path.read_bytes(), source_before)
                        self.assertEqual(group.ledger_path.read_bytes(), active_before)

        group = create_group("bridge-source-gzip-bomb")
        expanded = b"x" * (MAX_EVENT_BYTES * 32)
        compressed_bytes = gzip.compress(expanded)
        self.assertLess(len(compressed_bytes), MAX_EVENT_BYTES // 16)
        group.ledger_path.write_bytes(expanded)
        self.assertTrue(rotate_active_ledger(group.path, reason="bounded-gzip-bomb")["rotated"])
        compress_sealed_segments(group.path, keep_recent=0, force=True)
        source_path = list_sealed_segments(group.path)[0]["abs_path"]
        self.assertLess(source_path.stat().st_size, MAX_EVENT_BYTES // 16)
        source_before = source_path.read_bytes()
        active_before = group.ledger_path.read_bytes()
        with patch("no1.kernel.ledger_segments.open_ledger_source_text") as unbounded_reader:
            with self.assertRaises(LedgerEventSourceError):
                append_unique(group, "gbs_" + ("b" * 32))
        unbounded_reader.assert_not_called()
        self.assertEqual(source_path.read_bytes(), source_before)
        self.assertEqual(group.ledger_path.read_bytes(), active_before)

        group = create_group("bridge-source-orphan-gzip-bomb")
        segment_dir = group.path / "state" / "ledger" / "segments"
        orphan_path = segment_dir / "ledger.20260101T000000Z.000001.jsonl.gz"
        orphan_path.write_bytes(gzip.compress(expanded))
        source_before = orphan_path.read_bytes()
        active_before = group.ledger_path.read_bytes()
        with patch("no1.kernel.ledger_segments.open_ledger_source_text") as unbounded_reader:
            with self.assertRaises(LedgerEventSourceError):
                append_unique(group, "gbs_" + ("e" * 32))
        unbounded_reader.assert_not_called()
        manifest = load_ledger_manifest(group.path)
        self.assertEqual(len(manifest["segments"]), 1)
        self.assertEqual(manifest["segments"][0]["path"], str(orphan_path.relative_to(group.path)))
        self.assertEqual(manifest["segments"][0]["line_count"], 0)
        self.assertEqual(orphan_path.read_bytes(), source_before)
        self.assertEqual(group.ledger_path.read_bytes(), active_before)

        group = create_group("bridge-source-known-line-count")
        group.ledger_path.write_bytes(b'{}\n{}\n')
        self.assertTrue(rotate_active_ledger(group.path, reason="known-line-count")["rotated"])
        with patch("no1.kernel.ledger_segments.open_ledger_source_text") as unbounded_reader:
            manifest = load_ledger_manifest(group.path)
        unbounded_reader.assert_not_called()
        self.assertEqual(manifest["segments"][0]["line_count"], 2)
        compress_sealed_segments(group.path, keep_recent=0, force=True)
        with patch("no1.kernel.ledger_segments.open_ledger_source_text") as unbounded_reader:
            manifest = load_ledger_manifest(group.path)
        unbounded_reader.assert_not_called()
        self.assertTrue(manifest["segments"][0]["compressed"])
        self.assertEqual(manifest["segments"][0]["line_count"], 2)

        group = create_group("bridge-source-interrupted-compression")
        segment_dir = group.path / "state" / "ledger" / "segments"
        plain_path = segment_dir / "ledger.20260101T000000Z.000001.jsonl"
        gzip_path = plain_path.with_name(plain_path.name + ".gz")
        plain_path.write_bytes(expanded)
        gzip_path.write_bytes(gzip.compress(expanded))
        plain_before = plain_path.read_bytes()
        gzip_before = gzip_path.read_bytes()
        active_before = group.ledger_path.read_bytes()
        with patch("no1.kernel.ledger_segments.open_ledger_source_text") as unbounded_reader:
            with self.assertRaises(LedgerEventSourceError):
                append_unique(group, "gbs_" + ("f" * 32))
        unbounded_reader.assert_not_called()
        manifest = load_ledger_manifest(group.path)
        self.assertEqual(len(manifest["segments"]), 1)
        self.assertEqual(manifest["segments"][0]["path"], str(gzip_path.relative_to(group.path)))
        self.assertTrue(manifest["segments"][0]["compressed"])
        self.assertEqual(plain_path.read_bytes(), plain_before)
        self.assertEqual(gzip_path.read_bytes(), gzip_before)
        self.assertEqual(group.ledger_path.read_bytes(), active_before)

        group = create_group("bridge-source-directory-error")
        ledger_path = group.path / "ledger.jsonl"
        segment_dir = group.path / "state" / "ledger" / "segments"
        active_before = ledger_path.read_bytes()
        original_iterdir = Path.iterdir

        def fail_segment_enumeration(path: Path):
            if path == segment_dir:
                raise OSError("segment directory is unavailable")
            return original_iterdir(path)

        with patch.object(Path, "iterdir", autospec=True, side_effect=fail_segment_enumeration):
            with self.assertRaises(LedgerEventSourceError):
                append_event_once(
                    ledger_path,
                    event_id="gbs_" + ("1" * 32),
                    kind="chat.message",
                    group_id=group.group_id,
                    scope_key="",
                    by="system",
                    data=event_data,
                )
        self.assertEqual(ledger_path.read_bytes(), active_before)

        class OneReadHandle:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb) -> None:
                return None

            def readline(self, limit: int) -> bytes:
                self.calls.append(limit)
                if len(self.calls) > 1:
                    self.fail("source reader continued after overflow")
                return b"x" * limit

            def fail(self, message: str) -> None:
                raise AssertionError(message)

        for compressed in (False, True):
            with self.subTest(no_further_read=compressed):
                group = create_group(f"bridge-source-read-sentinel-{compressed}")
                first = group.path / ("first.jsonl.gz" if compressed else "first.jsonl")
                second = group.path / "second.jsonl"
                first.write_bytes(b"placeholder")
                second.write_bytes(b'{"id":"must-not-be-read"}\n')
                source_before = first.read_bytes()
                active_before = group.ledger_path.read_bytes()
                handle = OneReadHandle()

                def open_source(path: Path, mode: str):
                    self.assertEqual(mode, "rb")
                    if path != first:
                        self.fail("source reader opened a later segment after overflow")
                    return handle

                sources = [
                    {"abs_path": first},
                    {"abs_path": second},
                ]
                opener_patch = (
                    patch.object(ledger_module.gzip, "open", side_effect=open_source)
                    if compressed
                    else patch.object(ledger_module, "open", side_effect=open_source, create=True)
                )
                source_paths = [source["abs_path"] for source in sources]
                with patch.object(ledger_module, "_ledger_source_paths", return_value=source_paths), opener_patch:
                    with self.assertRaises(LedgerEventSourceError):
                        append_unique(group, "gbs_" + (("d" if compressed else "c") * 32))
                self.assertEqual(handle.calls, [MAX_EVENT_BYTES + 1])
                self.assertEqual(first.read_bytes(), source_before)
                self.assertEqual(group.ledger_path.read_bytes(), active_before)

    def test_request_dispatch_injects_only_recursive_send(self) -> None:
        from no1.contracts.v1 import DaemonRequest, DaemonResponse
        from no1.daemon import request_dispatch_ops, server

        recurse = Mock(return_value=(DaemonResponse(ok=True, result={"event": {"id": "event-1"}}), False))

        def group_bridge_handler(op: str, args: dict[str, object], *, dispatch_send):
            self.assertEqual(op, "group_bridge_session_receive")
            self.assertEqual(args, {"probe": True})
            nested = dispatch_send({"group_id": "verified-group", "to": ["user"]})
            self.assertTrue(nested[0].ok)
            return DaemonResponse(ok=True, result={"routed": True})

        with patch.object(request_dispatch_ops, "try_handle_group_bridge_op", side_effect=group_bridge_handler):
            response, should_stop = request_dispatch_ops.dispatch_request(
                DaemonRequest(op="group_bridge_session_receive", args={"probe": True}),
                deps=server._request_dispatch_deps(),
                recurse=recurse,
            )
        self.assertTrue(response.ok)
        self.assertFalse(should_stop)
        recurse.assert_called_once_with(
            DaemonRequest(op="send", args={"group_id": "verified-group", "to": ["user"]})
        )


if __name__ == "__main__":
    unittest.main()
