from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


class TestTurnProvenance(unittest.TestCase):
    def setUp(self) -> None:
        self._old_home = os.environ.get("CCCC_HOME")
        self._home = tempfile.TemporaryDirectory()
        os.environ["CCCC_HOME"] = self._home.name

        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        self.group = create_group(load_registry(), title="turn-provenance", topic="")

    def tearDown(self) -> None:
        self._home.cleanup()
        if self._old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = self._old_home

    def _append(self, *, provenance=None, source_platform: str = ""):
        from no1.contracts.v1 import ChatMessageData
        from no1.kernel.ledger import append_event

        data = ChatMessageData(
            text="message",
            to=["peer1"],
            source_platform=source_platform or None,
            turn_provenance=provenance,
        ).model_dump()
        event = append_event(
            self.group.ledger_path,
            kind="chat.message",
            group_id=self.group.group_id,
            scope_key="",
            by="user",
            data=data,
        )
        return event

    def test_trusted_ingress_mapping_ignores_display_metadata_for_elevation(self) -> None:
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance

        local = build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        self.assertEqual(local.origin, "local_user")
        self.assertTrue(local.fresh_local_request)
        self.assertTrue(str(local.local_request_id or "").startswith("localreq_"))
        self.assertEqual(local.source_transport, "web_user")
        self.assertIsNone(local.source_group_id)

        cli = build_send_turn_provenance({"__turn_ingress": "cli_user", "by": "user"})
        self.assertEqual(cli.origin, "local_user")

        for spoofed in (
            {"__turn_ingress": "web_user", "by": "peer1"},
            {"__turn_ingress": "web_user", "by": "user", "source_platform": "im"},
            {"__turn_ingress": "web_user", "by": "user", "src_group_id": "remote", "src_event_id": "e1"},
            {"by": "user"},
        ):
            with self.subTest(spoofed=spoofed):
                provenance = build_send_turn_provenance(spoofed)
                self.assertEqual(provenance.origin, "untrusted")
                self.assertFalse(provenance.fresh_local_request)

        self.assertEqual(
            build_send_turn_provenance({"__turn_ingress": "actor_mcp", "by": "user"}).origin,
            "local_actor",
        )
        im = build_send_turn_provenance(
            {
                "__turn_ingress": "im",
                "by": "user",
                "source_platform": "dingtalk",
                "source_user_id": "im-user-1",
            }
        )
        self.assertEqual(im.origin, "im")
        self.assertEqual(im.source_transport, "dingtalk")
        self.assertEqual(im.source_peer_id, "im-user-1")
        cross_group = build_send_turn_provenance(
            {
                "__turn_ingress": "cross_group",
                "src_group_id": "remote-group",
                "src_event_id": "remote-event",
                "src_by": "remote-peer",
            }
        )
        self.assertEqual(cross_group.origin, "cross_group")
        bridge = build_send_turn_provenance(
            {
                "__turn_ingress": "group_bridge",
                "source_platform": "Group_Bridge_Session",
                "src_group_id": "remote-group",
                "source_user_id": "remote-peer",
            }
        )
        self.assertEqual(bridge.origin, "group_bridge")
        self.assertEqual(bridge.source_transport, "group_bridge_session")

        incomplete_remote = (
            {"__turn_ingress": "im", "source_platform": "dingtalk"},
            {"__turn_ingress": "im", "source_user_id": "peer"},
            {"__turn_ingress": "cross_group", "src_event_id": "event", "src_by": "peer"},
            {"__turn_ingress": "cross_group", "src_group_id": "group", "src_by": "peer"},
            {"__turn_ingress": "cross_group", "src_group_id": "group", "src_event_id": "event"},
            {"__turn_ingress": "group_bridge", "src_group_id": "group", "source_user_id": "peer"},
            {"__turn_ingress": "group_bridge", "source_platform": "bridge", "source_user_id": "peer"},
            {"__turn_ingress": "group_bridge", "source_platform": "bridge", "src_group_id": "group"},
            {"__turn_ingress": "im", "source_platform": "bad transport", "source_user_id": "peer"},
            {"__turn_ingress": "im", "source_platform": ["dingtalk"], "source_user_id": "peer"},
            {"__turn_ingress": "im", "source_platform": "dingtalk", "source_user_id": "peer\nforged"},
            {
                "__turn_ingress": "cross_group",
                "src_group_id": "group",
                "src_event_id": {"id": "event"},
                "src_by": "peer",
            },
            {
                "__turn_ingress": "group_bridge",
                "source_platform": "bridge",
                "src_group_id": "group\x7f",
                "source_user_id": "peer",
            },
        )
        for incomplete in incomplete_remote:
            with self.subTest(incomplete=incomplete):
                self.assertEqual(build_send_turn_provenance(incomplete).origin, "untrusted")

    def test_reply_current_ingress_decides_origin_and_clears_remote_identity(self) -> None:
        from no1.daemon.messaging.turn_provenance import build_reply_turn_provenance, build_send_turn_provenance

        actor_event = {
            "id": "actor-event",
            "kind": "chat.message",
            "data": {"turn_provenance": build_send_turn_provenance({"__turn_ingress": "actor_mcp"}).model_dump()},
        }
        local_reply = build_reply_turn_provenance(
            actor_event,
            {"__turn_ingress": "web_user", "by": "user"},
        )
        self.assertEqual(local_reply.origin, "local_user")
        self.assertTrue(local_reply.fresh_local_request)
        self.assertEqual(local_reply.parent_event_id, "actor-event")

        for ingress, origin in (
            ("im", "im"),
            ("cross_group", "cross_group"),
            ("group_bridge", "group_bridge"),
        ):
            with self.subTest(origin=origin):
                remote_event = {
                    "id": f"{origin}-event",
                    "kind": "chat.message",
                    "data": {
                        "turn_provenance": build_send_turn_provenance(
                            {
                                "__turn_ingress": ingress,
                                "src_group_id": "remote-group",
                                "src_event_id": "remote-event",
                                "src_by": "remote-peer",
                                "source_platform": "remote-transport",
                                "source_user_id": "remote-user",
                            }
                        ).model_dump()
                    },
                }
                for current_args, expected_origin in (
                    ({"__turn_ingress": "web_user", "by": "user"}, "local_user"),
                    ({"__turn_ingress": "actor_mcp", "by": "peer1"}, "local_actor"),
                ):
                    with self.subTest(origin=origin, expected_origin=expected_origin):
                        reply = build_reply_turn_provenance(remote_event, current_args)
                        self.assertEqual(reply.origin, expected_origin)
                        self.assertEqual(reply.fresh_local_request, expected_origin == "local_user")
                        self.assertEqual(reply.parent_event_id, f"{origin}-event")
                        self.assertIsNone(reply.source_group_id)
                        self.assertIsNone(reply.source_event_id)
                        self.assertIsNone(reply.source_peer_id)
                        self.assertIn(reply.source_transport, {"web_user", "actor_mcp"})

        self.assertIsNone(local_reply.source_group_id)
        self.assertIsNone(local_reply.source_event_id)

    def test_remote_reply_requires_same_complete_current_identity(self) -> None:
        from no1.daemon.messaging.turn_provenance import build_reply_turn_provenance, build_send_turn_provenance

        remote_args = {
            "im": {
                "__turn_ingress": "im",
                "source_platform": "DingTalk",
                "source_user_id": "im-peer",
            },
            "cross_group": {
                "__turn_ingress": "cross_group",
                "src_group_id": "remote-group",
                "src_event_id": "remote-event",
                "src_by": "remote-peer",
            },
            "group_bridge": {
                "__turn_ingress": "group_bridge",
                "source_platform": "Bridge_Session",
                "src_group_id": "remote-group",
                "src_event_id": "remote-event",
                "src_by": "remote-peer",
            },
        }
        for origin, args in remote_args.items():
            parent = {
                "id": f"{origin}-parent",
                "kind": "chat.message",
                "data": {"turn_provenance": build_send_turn_provenance(args).model_dump()},
            }
            with self.subTest(origin=origin, case="same"):
                reply = build_reply_turn_provenance(parent, dict(args))
                self.assertEqual(reply.origin, origin)
                self.assertEqual(reply.parent_event_id, f"{origin}-parent")
                self.assertFalse(reply.fresh_local_request)

            identity_fields = {
                "im": ("source_platform", "source_user_id"),
                "cross_group": ("src_group_id", "src_event_id", "src_by"),
                "group_bridge": ("source_platform", "src_group_id", "src_by"),
            }[origin]
            for field in identity_fields:
                with self.subTest(origin=origin, case="missing", field=field):
                    missing = dict(args)
                    missing.pop(field, None)
                    self.assertEqual(build_reply_turn_provenance(parent, missing).origin, "untrusted")
                with self.subTest(origin=origin, case="mismatch", field=field):
                    mismatch = dict(args)
                    mismatch[field] = "different"
                    self.assertEqual(build_reply_turn_provenance(parent, mismatch).origin, "untrusted")

    def test_reply_legacy_parent_only_trusts_valid_current_ingress(self) -> None:
        from no1.daemon.messaging.turn_provenance import build_reply_turn_provenance

        for data in (
            {"text": "legacy"},
            {
                "source_platform": "dingtalk",
                "source_user_id": "spoofed-user",
                "src_group_id": "spoofed-group",
                "src_event_id": "spoofed-event",
            },
            {"turn_provenance": {"origin": "local_user", "ingress": "broken"}},
            {
                "turn_provenance": {
                    "origin": "local_user",
                    "ingress": "im",
                    "fresh_local_request": True,
                    "local_request_id": "forged",
                }
            },
        ):
            with self.subTest(data=data):
                untrusted = build_reply_turn_provenance(
                    {"id": "legacy-event", "kind": "chat.message", "data": data},
                    {"__turn_ingress": "legacy", "by": "user"},
                )
                self.assertEqual(untrusted.origin, "untrusted")
                self.assertFalse(untrusted.fresh_local_request)
                local = build_reply_turn_provenance(
                    {"id": "legacy-event", "kind": "chat.message", "data": data},
                    {"__turn_ingress": "web_user", "by": "user"},
                )
                self.assertEqual(local.origin, "local_user")
                self.assertTrue(local.fresh_local_request)
                self.assertIsNone(local.source_group_id)

    def test_persisted_provenance_rebuilds_from_ledger(self) -> None:
        from no1.daemon.messaging.turn_provenance import (
            build_reply_turn_provenance,
            build_send_turn_provenance,
            load_event_turn_provenance,
        )
        from no1.kernel.group import load_group

        event = self._append(
            provenance=build_send_turn_provenance(
                {
                    "__turn_ingress": "im",
                    "by": "user",
                    "source_platform": "dingtalk",
                    "source_user_id": "im-user-1",
                }
            )
        )
        reloaded = load_group(self.group.group_id)
        self.assertIsNotNone(reloaded)
        rebuilt = load_event_turn_provenance(reloaded, str(event.get("id") or ""))
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.origin, "im")
        self.assertEqual(rebuilt.source_transport, "dingtalk")
        self.assertEqual(rebuilt.source_peer_id, "im-user-1")

        local_reply = self._append(
            provenance=build_reply_turn_provenance(
                event,
                {"__turn_ingress": "web_user", "by": "user"},
            )
        )
        rebuilt_reply = load_event_turn_provenance(reloaded, str(local_reply["id"]))
        self.assertIsNotNone(rebuilt_reply)
        self.assertEqual(rebuilt_reply.origin, "local_user")
        self.assertTrue(rebuilt_reply.fresh_local_request)
        self.assertEqual(rebuilt_reply.parent_event_id, str(event["id"]))
        self.assertIsNone(rebuilt_reply.source_peer_id)

    def test_corrupt_remote_provenance_reloads_as_untrusted(self) -> None:
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance, load_event_turn_provenance
        from no1.kernel.group import load_group

        event = self._append(
            provenance=build_send_turn_provenance(
                {
                    "__turn_ingress": "cross_group",
                    "src_group_id": "remote-group",
                    "src_event_id": "remote-event",
                    "src_by": "remote-peer",
                }
            )
        )
        lines = self.group.ledger_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            record = json.loads(line)
            if str(record.get("id") or "") != str(event["id"]):
                continue
            provenance = record["data"]["turn_provenance"]
            provenance.pop("source_peer_id", None)
            lines[index] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            break
        self.group.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        reloaded = load_group(self.group.group_id)
        self.assertIsNotNone(reloaded)
        rebuilt = load_event_turn_provenance(reloaded, str(event["id"]))
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.origin, "untrusted")
        self.assertIsNone(rebuilt.source_group_id)

        local_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        lines = self.group.ledger_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            record = json.loads(line)
            if str(record.get("id") or "") != str(local_event["id"]):
                continue
            provenance = record["data"]["turn_provenance"]
            provenance["source_transport"] = "im"
            provenance["source_peer_id"] = "remote-peer"
            lines[index] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            break
        self.group.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        contradictory = load_event_turn_provenance(reloaded, str(local_event["id"]))
        self.assertIsNotNone(contradictory)
        self.assertEqual(contradictory.origin, "untrusted")

    def test_turn_provenance_schema_rejects_cross_origin_identity(self) -> None:
        from no1.contracts.v1 import TurnProvenance

        valid = (
            {
                "origin": "local_user",
                "ingress": "web_user",
                "fresh_local_request": True,
                "local_request_id": "local-1",
                "source_transport": "web_user",
            },
            {"origin": "local_actor", "ingress": "actor_mcp", "source_transport": "actor_mcp"},
            {"origin": "untrusted", "ingress": "untrusted"},
            {"origin": "im", "ingress": "im", "source_transport": "dingtalk", "source_peer_id": "peer"},
            {
                "origin": "cross_group",
                "ingress": "cross_group",
                "source_transport": "cross_group",
                "source_group_id": "group",
                "source_event_id": "event",
                "source_peer_id": "peer",
            },
            {
                "origin": "group_bridge",
                "ingress": "group_bridge",
                "source_transport": "bridge",
                "source_group_id": "group",
                "source_peer_id": "peer",
            },
        )
        for value in valid:
            with self.subTest(valid=value["origin"]):
                self.assertEqual(TurnProvenance.model_validate(value).origin, value["origin"])

        invalid = (
            {**valid[0], "source_transport": "im"},
            {**valid[0], "source_peer_id": "remote"},
            {**valid[1], "source_group_id": "remote"},
            {**valid[2], "source_transport": "im"},
            {**valid[2], "source_event_id": "remote"},
            {**valid[3], "fresh_local_request": True, "local_request_id": "forged"},
            {**valid[3], "source_group_id": "unexpected-group"},
            {**valid[3], "source_event_id": "unexpected-event"},
            {**valid[4], "source_transport": "bridge"},
            {**valid[4], "source_event_id": None},
            {**valid[5], "source_peer_id": None},
        )
        for value in invalid:
            with self.subTest(invalid=value):
                with self.assertRaises(ValueError):
                    TurnProvenance.model_validate(value)

    def test_im_provenance_with_extraneous_identity_reloads_untrusted(self) -> None:
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance, load_event_turn_provenance
        from no1.kernel.group import load_group

        event = self._append(
            provenance=build_send_turn_provenance(
                {
                    "__turn_ingress": "im",
                    "source_platform": "dingtalk",
                    "source_user_id": "peer",
                }
            )
        )
        lines = self.group.ledger_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            record = json.loads(line)
            if str(record.get("id") or "") != str(event["id"]):
                continue
            record["data"]["turn_provenance"]["source_group_id"] = "forged-group"
            record["data"]["turn_provenance"]["source_event_id"] = "forged-event"
            lines[index] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            break
        self.group.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        reloaded = load_group(self.group.group_id)
        self.assertIsNotNone(reloaded)
        provenance = load_event_turn_provenance(reloaded, str(event["id"]))
        self.assertIsNotNone(provenance)
        self.assertEqual(provenance.origin, "untrusted")
        self.assertIsNone(provenance.source_transport)
        self.assertIsNone(provenance.source_group_id)
        self.assertIsNone(provenance.source_event_id)
        self.assertIsNone(provenance.source_peer_id)

    def test_successful_pure_local_batch_issues_exact_grant_and_mixed_clears_it(self) -> None:
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
        )

        first = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"}))
        second = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "cli_user", "by": "user"}))
        ids = [str(first.get("id") or ""), str(second.get("id") or "")]
        grant = finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=ids, now=100.0, ttl_seconds=30.0)
        self.assertIsNotNone(grant)
        self.assertEqual(grant["event_ids"], ids)
        self.assertEqual(grant["group_id"], self.group.group_id)
        self.assertEqual(grant["actor_id"], "peer1")
        self.assertEqual(get_current_turn_grant(self.group, "peer1", now=110.0), grant)

        remote = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "im", "by": "user"}))
        self.assertIsNone(
            finalize_turn_delivery_success(
                self.group,
                actor_id="peer1",
                event_ids=[ids[-1], str(remote.get("id") or "")],
                now=111.0,
            )
        )
        self.assertIsNone(get_current_turn_grant(self.group, "peer1", now=112.0))

    def test_turn_grant_receipt_secret_is_delivered_but_never_persisted(self) -> None:
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_completion_receipt,
            turn_delivery_grant_receipt,
            validate_turn_grant_receipt,
        )
        from no1.util.fs import read_json

        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        attempt = begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(event["id"])],
            binding={"transport": "pty"},
            now=100.0,
        )
        self.assertIsNotNone(attempt)
        secret = str(attempt.get("authorization_secret") or "")
        self.assertGreaterEqual(len(secret), 32)

        state_path = self.group.path / "state" / "turn-grants" / "peer1.json"
        pending_text = state_path.read_text(encoding="utf-8")
        pending_state = read_json(state_path)
        self.assertNotIn(secret, pending_text)
        self.assertNotIn("authorization_secret", pending_text)
        pending = pending_state.get("pending_attempt") or {}
        self.assertTrue((pending.get("authorization_binding") or {}).get("secret_digest"))

        completion = turn_delivery_completion_receipt(attempt)
        receipt = turn_delivery_grant_receipt(attempt)
        self.assertNotIn("authorization_secret", completion or {})
        self.assertEqual((receipt or {}).get("authorization_secret"), secret)
        self.assertIsNotNone(
            finalize_turn_delivery_attempt(
                self.group,
                actor_id="peer1",
                attempt=attempt,
                now=101.0,
                ttl_seconds=30.0,
            )
        )

        current_text = state_path.read_text(encoding="utf-8")
        current = (read_json(state_path).get("current_grant") or {})
        self.assertNotIn(secret, current_text)
        self.assertNotIn("authorization_secret", current_text)
        self.assertEqual(
            validate_turn_grant_receipt(
                self.group,
                "peer1",
                turn_grant_receipt=receipt,
                now=110.0,
            ),
            current,
        )

        disk_only = {
            key: current.get(key)
            for key in (
                "v",
                "issuer_epoch",
                "group_id",
                "actor_id",
                "attempt_id",
                "generation",
                "event_ids",
                "authorization_binding",
            )
        }
        self.assertIsNone(
            validate_turn_grant_receipt(
                self.group,
                "peer1",
                turn_grant_receipt=disk_only,
                now=110.0,
            )
        )
        self.assertIsNone(
            validate_turn_grant_receipt(
                self.group,
                "peer1",
                turn_grant_receipt={**(receipt or {}), "authorization_secret": "forged"},
                now=110.0,
            )
        )
        self.assertIsNone(
            validate_turn_grant_receipt(
                self.group,
                "peer1",
                turn_grant_receipt=receipt,
                now=132.0,
            )
        )

    def test_turn_grant_receipt_exact_identity_rejects_replay_and_cross_binding(self) -> None:
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            turn_delivery_grant_receipt,
            validate_turn_grant_receipt,
        )
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        attempt_a = begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(event["id"])],
            binding={"transport": "pty"},
            now=100.0,
        )
        receipt_a = turn_delivery_grant_receipt(attempt_a)
        finalize_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            attempt=attempt_a,
            now=101.0,
            ttl_seconds=60.0,
        )
        other_group = create_group(load_registry(), title="other-turn-grant", topic="")
        mutations = {
            "issuer": {"issuer_epoch": "daemon_old"},
            "actor": {"actor_id": "peer2"},
            "attempt": {"attempt_id": "turnattempt_old"},
            "generation": {"generation": 99},
            "event_ids": {"event_ids": ["event_old"]},
            "missing_binding": {"binding": {}},
            "changed_binding": {"binding": {"transport": "claude_app"}},
            "authorization_binding_without_transport": {
                "authorization_binding": {
                    key: value
                    for key, value in ((receipt_a or {}).get("authorization_binding") or {}).items()
                    if key != "transport"
                }
            },
            "authorization_binding": {
                "authorization_binding": {
                    **((receipt_a or {}).get("authorization_binding") or {}),
                    "authority_id": "turnauth_old",
                }
            },
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                self.assertIsNone(
                    validate_turn_grant_receipt(
                        self.group,
                        "peer1",
                        turn_grant_receipt={**(receipt_a or {}), **mutation},
                        now=110.0,
                    )
                )
        self.assertIsNone(
            validate_turn_grant_receipt(
                self.group,
                "peer2",
                turn_grant_receipt=receipt_a,
                now=110.0,
            )
        )
        self.assertIsNone(
            validate_turn_grant_receipt(
                other_group,
                "peer1",
                turn_grant_receipt=receipt_a,
                now=110.0,
            )
        )

        attempt_b = begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(event["id"])],
            binding={"transport": "pty"},
            now=111.0,
        )
        receipt_b = turn_delivery_grant_receipt(attempt_b)
        finalize_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            attempt=attempt_b,
            now=112.0,
            ttl_seconds=60.0,
        )
        self.assertIsNone(
            validate_turn_grant_receipt(
                self.group,
                "peer1",
                turn_grant_receipt=receipt_a,
                now=113.0,
            )
        )
        self.assertIsNotNone(
            validate_turn_grant_receipt(
                self.group,
                "peer1",
                turn_grant_receipt=receipt_b,
                now=113.0,
            )
        )

    def test_daemon_issuer_epoch_abandons_persisted_grant_and_pending_attempt(self) -> None:
        from no1.daemon.messaging import turn_provenance as provenance
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance
        from no1.util.fs import read_json

        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        attempt = provenance.begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(event["id"])],
        )
        self.assertIsNotNone(attempt)
        self.assertIsNotNone(
            provenance.finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=attempt)
        )

        state_path = self.group.path / "state" / "turn-grants" / "peer1.json"
        with patch.object(provenance, "_DAEMON_ISSUER_EPOCH", "daemon_new_process"):
            self.assertIsNone(provenance.get_current_turn_grant(self.group, "peer1"))
            state = read_json(state_path)
            self.assertEqual(state.get("invalidated_reason"), "daemon_restart_abandoned")
            self.assertIsNone(state.get("current_grant"))

        pending = provenance.begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(event["id"])],
        )
        self.assertIsNotNone(pending)
        with patch.object(provenance, "_DAEMON_ISSUER_EPOCH", "daemon_third_process"):
            self.assertIsNone(provenance.get_current_turn_grant(self.group, "peer1"))
            state = read_json(state_path)
            self.assertEqual(state.get("invalidated_reason"), "daemon_restart_abandoned")
            self.assertIsNone(state.get("pending_attempt"))
        self.assertIsNone(
            provenance.finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=pending)
        )

    def test_failure_next_generation_and_ttl_leave_no_current_grant(self) -> None:
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
            invalidate_turn_grant,
        )

        event = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"}))
        event_id = str(event.get("id") or "")
        first = finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=[event_id], now=10.0, ttl_seconds=5.0)
        self.assertIsNotNone(first)
        invalidated = invalidate_turn_grant(self.group, "peer1", reason="delivery_failed", now=11.0)
        self.assertGreater(invalidated["generation"], first["generation"])
        self.assertIsNone(get_current_turn_grant(self.group, "peer1", now=12.0))

        second = finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=[event_id], now=20.0, ttl_seconds=5.0)
        self.assertIsNotNone(second)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1", now=25.0))

    def test_claude_queue_acceptance_does_not_sign_and_failure_clears(self) -> None:
        from no1.daemon.messaging.chat_ops import handle_send
        from no1.daemon.messaging.turn_provenance import get_current_turn_grant
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="headless", runtime="claude")

        def send(text: str):
            return handle_send(
                {
                    "group_id": self.group.group_id,
                    "__turn_ingress": "web_user",
                    "by": "user",
                    "text": text,
                    "to": ["peer1"],
                },
                coerce_bool=bool,
                normalize_attachments=lambda _group, _raw: [],
                effective_runner_kind=lambda value: str(value or "pty"),
                auto_wake_recipients=lambda _group, _to, _by: [],
                automation_on_resume=lambda _group: None,
                automation_on_new_message=lambda _group: None,
                clear_pending_system_notifies=lambda _group_id, _kinds: None,
            )

        with patch("no1.daemon.messaging.chat_ops.claude_app_supervisor.actor_running", return_value=True), patch(
            "no1.daemon.messaging.chat_ops.claude_app_supervisor.submit_user_message", return_value=True
        ):
            success = send("delivered")
        self.assertTrue(success.ok)
        event = success.result["event"]
        provenance = event["data"]["turn_provenance"]
        self.assertEqual(provenance["origin"], "local_user")
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        with patch("no1.daemon.messaging.chat_ops.claude_app_supervisor.actor_running", return_value=True), patch(
            "no1.daemon.messaging.chat_ops.claude_app_supervisor.submit_user_message", return_value=False
        ):
            failure = send("not delivered")
        self.assertTrue(failure.ok)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_codex_queue_acceptance_does_not_sign_grant(self) -> None:
        from no1.daemon.messaging.chat_ops import handle_send
        from no1.daemon.messaging.turn_provenance import get_current_turn_grant
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="headless", runtime="codex")
        with patch("no1.daemon.messaging.chat_ops.codex_app_supervisor.actor_running", return_value=True), patch(
            "no1.daemon.messaging.chat_ops.codex_app_supervisor.submit_user_message", return_value=True
        ):
            response = handle_send(
                {
                    "group_id": self.group.group_id,
                    "__turn_ingress": "web_user",
                    "by": "user",
                    "text": "codex delivered",
                    "to": ["peer1"],
                },
                coerce_bool=bool,
                normalize_attachments=lambda _group, _raw: [],
                effective_runner_kind=lambda value: str(value or "pty"),
                auto_wake_recipients=lambda _group, _to, _by: [],
                automation_on_resume=lambda _group: None,
                automation_on_new_message=lambda _group: None,
                clear_pending_system_notifies=lambda _group_id, _kinds: None,
            )

        self.assertTrue(response.ok)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_computer_request_persists_its_local_provenance_identity(self) -> None:
        from no1.daemon.messaging.chat_ops import handle_send
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="headless", runtime="codex")
        with patch("no1.daemon.messaging.chat_ops.codex_app_supervisor.actor_running", return_value=True), patch(
            "no1.daemon.messaging.chat_ops.codex_app_supervisor.submit_user_message", return_value=True
        ):
            response = handle_send(
                {
                    "group_id": self.group.group_id,
                    "__turn_ingress": "web_user",
                    "by": "user",
                    "text": "record a workflow",
                    "to": ["peer1"],
                    "computer_control_request": {
                        "actor_id": "peer1",
                        "mode": "create_and_run",
                    },
                },
                coerce_bool=bool,
                normalize_attachments=lambda _group, _raw: [],
                effective_runner_kind=lambda value: str(value or "pty"),
                auto_wake_recipients=lambda _group, _to, _by: [],
                automation_on_resume=lambda _group: None,
                automation_on_new_message=lambda _group: None,
                clear_pending_system_notifies=lambda _group_id, _kinds: None,
            )
        self.assertTrue(response.ok, getattr(response, "error", None))
        event = response.result["event"]
        request_id = str(event["data"]["computer_control_request"]["request_id"])
        local_request_id = str(event["data"]["turn_provenance"]["local_request_id"])
        request_path = self.group.path / "state" / "computer-control" / "requests.jsonl"
        records = [json.loads(line) for line in request_path.read_text(encoding="utf-8").splitlines()]
        request = next(item for item in records if str(item.get("request_id") or "") == request_id)
        self.assertEqual(request.get("event_id"), event["id"])
        self.assertEqual(request.get("local_request_id"), local_request_id)

    def test_provider_start_boundaries_sign_and_failures_do_not(self) -> None:
        from no1.daemon.claude_app_sessions import ClaudeAppSession, _PendingTurn as ClaudePendingTurn
        from no1.daemon.codex_app_sessions import CodexAppSession, _PendingTurn as CodexPendingTurn
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance, get_current_turn_grant

        codex_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        codex = CodexAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        codex._session_state.thread_id = "thread-1"
        codex._turn_queue.put(
            CodexPendingTurn(text="codex start", event_id=str(codex_event["id"]))
        )
        codex_requests = []

        def accept_codex(_method, params, **_kwargs):
            codex_requests.append(params)
            return {"turn": {"id": "codex-turn-1"}}

        with patch.object(codex, "is_running", side_effect=[True, False, False]), patch.object(
            codex,
            "_request",
            side_effect=accept_codex,
        ), patch.object(codex, "_emit"), patch(
            "no1.daemon.codex_app_sessions.auto_mark_headless_delivery_started"
        ):
            codex._turn_loop()
        codex_grant = get_current_turn_grant(self.group, "peer1")
        self.assertIsNotNone(codex_grant)
        self.assertEqual(codex_grant["event_ids"], [codex_event["id"]])
        codex_text = "\n".join(
            str(item.get("text") or "")
            for item in (codex_requests[0].get("input") or [])
            if isinstance(item, dict)
        )
        self.assertIn("turn_grant_receipt=", codex_text)
        self.assertIn("authorization_secret", codex_text)

        claude_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        claude = ClaudeAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        claude._turn_queue.put(
            ClaudePendingTurn(text="claude start", event_id=str(claude_event["id"]))
        )
        claude_writes = []

        def accept_claude(value):
            claude_writes.append(value)
            return True

        with patch.object(claude, "is_running", side_effect=[True, False]), patch.object(
            claude,
            "_write_stdin",
            side_effect=accept_claude,
        ), patch.object(claude._turn_done, "wait", return_value=True), patch.object(
            claude,
            "_emit",
        ), patch("no1.daemon.claude_app_sessions.auto_mark_headless_delivery_started"):
            claude._turn_loop()
        claude_grant = get_current_turn_grant(self.group, "peer1")
        self.assertIsNotNone(claude_grant)
        self.assertEqual(claude_grant["event_ids"], [claude_event["id"]])
        claude_text = str((((claude_writes[0].get("message") or {}).get("content")) or ""))
        self.assertIn("turn_grant_receipt=", claude_text)
        self.assertIn("authorization_secret", claude_text)

        failed_codex_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        from no1.daemon.messaging.turn_provenance import invalidate_turn_grant

        invalidate_turn_grant(self.group, "peer1", reason="next_delivery")
        failed_codex = CodexAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        failed_codex._session_state.thread_id = "thread-2"
        failed_codex._turn_queue.put(
            CodexPendingTurn(text="codex fails", event_id=str(failed_codex_event["id"]))
        )
        with patch.object(failed_codex, "is_running", side_effect=[True, False]), patch.object(
            failed_codex,
            "_request",
            side_effect=RuntimeError("turn start failed"),
        ), patch.object(failed_codex, "_emit"):
            failed_codex._turn_loop()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_headless_acceptance_write_after_finalize_error_clears_exact_attempt(self) -> None:
        from no1.daemon.claude_app_sessions import ClaudeAppSession, _PendingTurn as ClaudePendingTurn
        from no1.daemon.codex_app_sessions import CodexAppSession, _PendingTurn as CodexPendingTurn
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_attempt as real_finalize,
            get_current_turn_grant,
        )
        from no1.util.fs import read_json

        def write_then_raise(*args, **kwargs):
            real_finalize(*args, **kwargs)
            raise OSError("grant persistence acknowledgement lost")

        codex_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        codex = CodexAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        codex._session_state.thread_id = "thread-uncertain"
        codex._turn_queue.put(CodexPendingTurn(text="codex uncertain", event_id=str(codex_event["id"])))
        with patch.object(codex, "is_running", side_effect=[True, False]), patch.object(
            codex, "_request", return_value={"turn": {"id": "codex-uncertain"}}
        ) as request, patch.object(codex, "_emit") as emit, patch(
            "no1.daemon.codex_app_sessions.finalize_turn_delivery_attempt", side_effect=write_then_raise
        ):
            codex._turn_loop()
        request.assert_called_once()
        self.assertTrue(any(call.args[0] == "headless.turn.failed" for call in emit.call_args_list))
        self.assertEqual(codex._active_turn_id, "")
        self.assertIsNone(codex._session_state.current_task_id)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        claude_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        claude = ClaudeAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        claude._turn_queue.put(ClaudePendingTurn(text="claude uncertain", event_id=str(claude_event["id"])))
        with patch.object(claude, "is_running", return_value=True), patch.object(
            claude, "_write_stdin", return_value=True
        ) as write_stdin, patch.object(claude, "_emit") as emit, patch.object(
            claude, "stop"
        ) as stop, patch(
            "no1.daemon.claude_app_sessions.finalize_turn_delivery_attempt", side_effect=write_then_raise
        ):
            claude._turn_loop()
        write_stdin.assert_called_once()
        stop.assert_called_once_with(persist_actor_stopped=False)
        self.assertTrue(any(call.args[0] == "headless.turn.failed" for call in emit.call_args_list))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        state = read_json(self.group.path / "state" / "turn-grants" / "peer1.json")
        self.assertIsNone(state.get("pending_attempt"))
        self.assertIsNone(state.get("current_grant"))

        failed_codex_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )

        malformed_codex = CodexAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        malformed_codex._session_state.thread_id = "thread-3"
        malformed_codex._turn_queue.put(
            CodexPendingTurn(text="missing turn id", event_id=str(failed_codex_event["id"]))
        )
        with patch.object(malformed_codex, "is_running", side_effect=[True, False]), patch.object(
            malformed_codex,
            "_request",
            return_value={"turn": {}},
        ), patch.object(malformed_codex, "_emit"):
            malformed_codex._turn_loop()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        failed_claude = ClaudeAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        failed_claude._turn_queue.put(
            ClaudePendingTurn(text="claude fails", event_id=str(failed_codex_event["id"]))
        )
        with patch.object(failed_claude, "is_running", side_effect=[True, False]), patch.object(
            failed_claude,
            "_write_stdin",
            return_value=False,
        ), patch.object(failed_claude, "_emit"):
            failed_claude._turn_loop()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_stale_codex_completion_does_not_clear_next_started_grant(self) -> None:
        from no1.daemon.codex_app_sessions import CodexAppSession
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
            invalidate_turn_grant,
        )

        old_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        next_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        self.assertIsNotNone(
            finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=[str(old_event["id"])])
        )
        invalidate_turn_grant(self.group, "peer1", reason="next_delivery_queued")

        session = CodexAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        session._active_turn_id = "old-turn"
        session._active_event_id = str(old_event["id"])
        session._handle_notification("turn/completed", {"turn": {"id": "old-turn", "status": "completed"}})
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        next_grant = finalize_turn_delivery_success(
            self.group,
            actor_id="peer1",
            event_ids=[str(next_event["id"])],
        )
        self.assertIsNotNone(next_grant)
        session._active_turn_id = "next-turn"
        session._active_event_id = str(next_event["id"])
        session._handle_notification("turn/completed", {"turn": {"status": "completed"}})
        self.assertEqual(get_current_turn_grant(self.group, "peer1"), next_grant)
        session._handle_notification("turn/completed", {"turn": {"id": "old-turn", "status": "completed"}})
        self.assertEqual(get_current_turn_grant(self.group, "peer1"), next_grant)

    def test_codex_completion_before_turn_start_response_uses_exact_runtime_id(self) -> None:
        from no1.daemon.codex_app_sessions import CodexAppSession, _PendingTurn
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            get_current_turn_grant,
            invalidate_turn_grant,
        )

        event_b = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        session = CodexAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        session._session_state.thread_id = "thread-b"
        session._turn_queue.put(_PendingTurn(text="turn b", event_id=str(event_b["id"])))

        def old_completion_before_response(_method, _params, **_kwargs):
            session._handle_notification(
                "turn/completed",
                {"turn": {"id": "turn-a", "status": "completed"}},
            )
            return {"turn": {"id": "turn-b"}}

        with patch.object(session, "is_running", side_effect=[True, False, False]), patch.object(
            session, "_request", side_effect=old_completion_before_response
        ), patch.object(session, "_emit"), patch(
            "no1.daemon.codex_app_sessions.auto_mark_headless_delivery_started"
        ):
            session._turn_loop()

        grant_b = get_current_turn_grant(self.group, "peer1")
        self.assertIsNotNone(grant_b)
        self.assertEqual(grant_b["event_ids"], [event_b["id"]])
        self.assertEqual(session._active_turn_id, "turn-b")

        invalidate_turn_grant(self.group, "peer1", reason="test_next_attempt")
        event_c = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        completed = CodexAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        completed._session_state.thread_id = "thread-c"
        completed._turn_queue.put(_PendingTurn(text="turn c", event_id=str(event_c["id"])))

        def current_completion_before_response(_method, _params, **_kwargs):
            completed._handle_notification(
                "turn/completed",
                {"turn": {"id": "turn-c", "status": "completed"}},
            )
            return {"turn": {"id": "turn-c"}}

        with patch.object(completed, "is_running", side_effect=[True, False]), patch.object(
            completed, "_request", side_effect=current_completion_before_response
        ), patch.object(completed, "_emit"):
            completed._turn_loop()

        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertEqual(completed._active_turn_id, "")

        event_d = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        started = CodexAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        started._session_state.thread_id = "thread-d"
        started._turn_queue.put(_PendingTurn(text="turn d", event_id=str(event_d["id"])))

        def started_and_completed_before_response(_method, _params, **_kwargs):
            started._handle_notification("turn/started", {"turn": {"id": "turn-d"}})
            started._handle_notification(
                "turn/completed",
                {"turn": {"id": "turn-d", "status": "completed"}},
            )
            return {"turn": {"id": "turn-d"}}

        with patch.object(started, "is_running", side_effect=[True, False]), patch.object(
            started, "_request", side_effect=started_and_completed_before_response
        ), patch.object(started, "_emit"), patch.object(started._turn_done, "set") as turn_done:
            started._turn_loop()

        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertEqual(started._active_turn_id, "")
        self.assertEqual(started._active_turn_generation, 0)
        self.assertIsNone(started._session_state.current_task_id)
        turn_done.assert_called_once_with()

        event_e = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        terminal_before_started = CodexAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        terminal_before_started._session_state.thread_id = "thread-e"
        terminal_before_started._turn_queue.put(
            _PendingTurn(text="turn e", event_id=str(event_e["id"]))
        )

        def completed_then_started_before_response(_method, _params, **_kwargs):
            terminal_before_started._handle_notification(
                "turn/completed",
                {"turn": {"id": "turn-e", "status": "completed"}},
            )
            self.assertEqual(terminal_before_started._active_turn_id, "")
            self.assertIsNone(terminal_before_started._session_state.current_task_id)
            terminal_before_started._handle_notification("turn/started", {"turn": {"id": "turn-e"}})
            self.assertEqual(terminal_before_started._active_turn_id, "")
            self.assertIsNone(terminal_before_started._session_state.current_task_id)
            return {"turn": {"id": "turn-e"}}

        with patch.object(terminal_before_started, "is_running", side_effect=[True, False]), patch.object(
            terminal_before_started, "_request", side_effect=completed_then_started_before_response
        ), patch.object(terminal_before_started, "_emit") as emit, patch.object(
            terminal_before_started._turn_done, "set"
        ) as turn_done:
            terminal_before_started._turn_loop()

        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertEqual(terminal_before_started._active_turn_id, "")
        self.assertEqual(terminal_before_started._active_turn_generation, 0)
        self.assertIsNone(terminal_before_started._session_state.current_task_id)
        self.assertFalse(any(call.args[0] == "headless.turn.progress" for call in emit.call_args_list))
        turn_done.assert_called_once_with()

    def test_codex_image_fallback_requires_explicit_preaccept_rejection(self) -> None:
        from no1.daemon.codex_app_sessions import (
            CodexAppSession,
            CodexRequestRejectedError,
            _PendingTurn,
        )
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            get_current_turn_grant,
            invalidate_turn_grant,
        )
        from no1.util.fs import read_json

        timeout_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        timeout_session = CodexAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        timeout_session._session_state.thread_id = "thread-image-timeout"
        timeout_session._turn_queue.put(
            _PendingTurn(text="image timeout", event_id=str(timeout_event["id"]))
        )
        requests = []

        def timeout_after_request(method, params, **_kwargs):
            requests.append((method, params))
            raise RuntimeError("codex request timed out: turn/start")

        with patch.object(timeout_session, "is_running", side_effect=[True, False]), patch.object(
            timeout_session,
            "_build_turn_input_items",
            return_value=[{"type": "text", "text": "image timeout"}, {"type": "local_image", "path": "/tmp/x"}],
        ), patch.object(timeout_session, "_request", side_effect=timeout_after_request), patch.object(
            timeout_session, "_emit"
        ), patch.object(timeout_session, "stop") as stop:
            timeout_session._turn_loop()

        self.assertEqual(len(requests), 1)
        stop.assert_called_once_with()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        state = read_json(self.group.path / "state" / "turn-grants" / "peer1.json")
        self.assertIsNone(state.get("pending_attempt"))
        self.assertIsNone(state.get("current_grant"))

        fallback_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        fallback_session = CodexAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        fallback_session._session_state.thread_id = "thread-image-rejected"
        fallback_session._turn_queue.put(
            _PendingTurn(text="image rejected", event_id=str(fallback_event["id"]))
        )
        fallback_requests = []

        def reject_image_then_accept(method, params, **_kwargs):
            fallback_requests.append((method, params))
            if len(fallback_requests) == 1:
                raise CodexRequestRejectedError(
                    method="turn/start",
                    code=-32602,
                    message="local image input is unsupported",
                )
            return {"turn": {"id": "turn-image-text-fallback"}}

        with patch.object(fallback_session, "is_running", side_effect=[True, False, False]), patch.object(
            fallback_session,
            "_build_turn_input_items",
            side_effect=lambda payload, text_override=None: [
                {"type": "text", "text": str(text_override or payload.text)},
                {"type": "local_image", "path": "/tmp/x"},
            ],
        ), patch.object(fallback_session, "_request", side_effect=reject_image_then_accept), patch.object(
            fallback_session, "_emit"
        ), patch("no1.daemon.codex_app_sessions.auto_mark_headless_delivery_started"):
            fallback_session._turn_loop()

        self.assertEqual(len(fallback_requests), 2)
        self.assertTrue(any(item.get("type") == "local_image" for item in fallback_requests[0][1]["input"]))
        first_text = next(item["text"] for item in fallback_requests[0][1]["input"] if item.get("type") == "text")
        second_text = fallback_requests[1][1]["input"][0]["text"]
        self.assertIn("turn_grant_receipt=", first_text)
        self.assertEqual(second_text, first_text)
        self.assertTrue(second_text.endswith("image rejected"))
        self.assertIsNotNone(get_current_turn_grant(self.group, "peer1"))
        invalidate_turn_grant(self.group, "peer1", reason="test_cleanup")

    def test_claude_stream_completion_rotates_transport_before_next_turn(self) -> None:
        from no1.daemon.claude_app_sessions import ClaudeAppSession
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            get_current_turn_grant,
        )

        old_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        next_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )

        class FakeProc:
            def __init__(self) -> None:
                self.stdin = Mock()
                self.stdout = Mock()
                self.stderr = Mock()
                self.pid = 1234
                self.terminate = Mock()
                self.wait = Mock(return_value=0)
                self.kill = Mock()

            def poll(self):
                return None

        session = ClaudeAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        old_proc = FakeProc()
        session._proc = old_proc
        session._running = True
        session._runtime_command = ["claude", "-p"]
        session._transport_generation = 1
        session._proc_generation = 1
        session._accepted_transport_generation = 1
        session._active_turn_id = "old-turn"
        session._active_event_id = str(old_event["id"])
        session._active_turn_generation = 1
        session._active_transport_epoch = 1
        old_attempt = begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(old_event["id"])],
            binding={"transport": "claude_app", "transport_epoch": 1, "turn_generation": 1},
        )
        self.assertIsNotNone(old_attempt)
        old_grant = finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=old_attempt)
        self.assertIsNotNone(old_grant)
        session._active_delivery_attempt = old_attempt
        session._active_grant_generation = int(old_grant["generation"])
        session._handle_event(
            {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}},
            transport_generation=1,
        )
        session._handle_event(
            {"type": "stream_event", "event": {"type": "message_stop"}},
            transport_generation=1,
        )
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertEqual(session._accepted_transport_generation, 0)
        self.assertTrue(session._transport_restart_required)

        new_proc = FakeProc()
        threads = [Mock(), Mock()]
        with patch(
            "no1.daemon.claude_app_sessions.prepare_claude_headless_launch_command",
            return_value=(
                ["claude", "--resume", "provider-session", "-p"],
                {"provider_session_id": "provider-session"},
                "resume",
            ),
        ), patch("no1.daemon.claude_app_sessions.subprocess.Popen", return_value=new_proc), patch(
            "no1.daemon.claude_app_sessions.threading.Thread", side_effect=threads
        ), patch("no1.daemon.claude_app_sessions.time.sleep", return_value=None), patch.object(
            session, "_persist_state"
        ):
            self.assertTrue(session._restart_transport_after_stream_completion())

        old_proc.stdin.close.assert_called_once()
        old_proc.terminate.assert_called_once()
        self.assertIs(session._proc, new_proc)
        self.assertEqual(session._accepted_transport_generation, 2)
        self.assertFalse(session._transport_restart_required)

        session._active_turn_id = "next-turn"
        session._active_event_id = str(next_event["id"])
        session._active_turn_generation = 2
        session._active_transport_epoch = 2
        next_attempt = begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(next_event["id"])],
            binding={"transport": "claude_app", "transport_epoch": 2, "turn_generation": 2},
        )
        self.assertIsNotNone(next_attempt)
        next_grant = finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=next_attempt)
        self.assertIsNotNone(next_grant)
        session._active_delivery_attempt = next_attempt
        session._active_grant_generation = int(next_grant["generation"])

        # The old reader may emit after any amount of time, including after the
        # next grant exists. Its generation is structurally sealed.
        session._handle_event(
            {"type": "result", "subtype": "success"},
            transport_generation=1,
        )
        self.assertEqual(get_current_turn_grant(self.group, "peer1"), next_grant)
        self.assertEqual(session._active_event_id, str(next_event["id"]))

        # If the old provider never emits result, there is no stale FIFO entry
        # to consume the new transport's legitimate completion.
        session._handle_event(
            {"type": "result", "subtype": "success"},
            transport_generation=2,
        )
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertEqual(session._active_event_id, "")

    def test_claude_flush_uncertainty_seals_epoch_before_rotating_or_failing_queue(self) -> None:
        from no1.daemon.claude_app_sessions import ClaudeAppSession, _PendingTurn
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance, get_current_turn_grant
        from no1.kernel.inbox import unread_messages
        from no1.kernel.ledger import read_last_lines

        class FakeStdin:
            def __init__(self, *, fail_flush: bool) -> None:
                self.fail_flush = fail_flush
                self.lines = []

            def write(self, value: str) -> int:
                self.lines.append(value)
                return len(value)

            def flush(self) -> None:
                if self.fail_flush:
                    raise OSError("flush acknowledgement lost")

            def close(self) -> None:
                return None

        class FakeProc:
            def __init__(self, *, fail_flush: bool) -> None:
                self.stdin = FakeStdin(fail_flush=fail_flush)
                self.stdout = []
                self.stderr = []
                self.pid = 1234

            def poll(self):
                return None

        event_a = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        event_b = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        session = ClaudeAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        old_proc = FakeProc(fail_flush=True)
        new_proc = FakeProc(fail_flush=False)
        session._proc = old_proc
        session._running = True
        session._transport_generation = 1
        session._proc_generation = 1
        session._accepted_transport_generation = 1
        session._session_state.session_id = "preserved-session"
        session._turn_queue.put(_PendingTurn(text="turn A", event_id=str(event_a["id"])))
        session._turn_queue.put(_PendingTurn(text="turn B", event_id=str(event_b["id"])))
        rotations = []

        def rotate_after_injecting_old_events() -> bool:
            if not session._transport_restart_required:
                return True
            rotations.append(session._proc_generation)
            self.assertEqual(session._accepted_transport_generation, 0)
            session._last_text_snapshot = "sealed"
            session._current_stream_id = "sealed-stream"
            session._active_tool_activities = {"keep": "activity"}
            session._tool_activity_context = {"keep": {"tool_name": "keep"}}
            emit_count = emit.call_count
            turn_done_count = turn_done.call_count
            old_events = (
                {"type": "assistant", "message": {"id": "old-message", "content": [{"type": "text", "text": "old"}]}},
                {"type": "tool_progress", "tool_use_id": "old-tool", "tool_name": "old", "elapsed_time_seconds": 1},
                {"type": "tool_result", "tool_use_id": "old-tool", "content": "old result"},
                {"type": "tool_use_summary", "summary": "old summary"},
                {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}},
                {"type": "stream_event", "event": {"type": "message_stop"}},
                {"type": "system", "subtype": "init", "session_id": "old-session"},
                {"type": "result", "subtype": "success"},
            )
            for event in old_events:
                session._handle_event(event, transport_generation=1)
            session._stdout_loop(old_proc, 1)
            self.assertEqual(session._last_text_snapshot, "sealed")
            self.assertEqual(session._current_stream_id, "sealed-stream")
            self.assertEqual(session._active_tool_activities, {"keep": "activity"})
            self.assertEqual(session._tool_activity_context, {"keep": {"tool_name": "keep"}})
            self.assertEqual(session._session_state.session_id, "preserved-session")
            self.assertEqual(emit.call_count, emit_count)
            self.assertEqual(turn_done.call_count, turn_done_count)
            session._transport_generation = 2
            session._proc_generation = 2
            session._accepted_transport_generation = 2
            session._transport_restart_required = False
            session._proc = new_proc
            return True

        with patch.object(session, "is_running", side_effect=[True, True, False]), patch.object(
            session, "_restart_transport_after_stream_completion", side_effect=rotate_after_injecting_old_events
        ), patch.object(session, "_persist_state"), patch.object(session, "_emit") as emit, patch.object(
            session._turn_done, "wait", return_value=True
        ), patch.object(session._turn_done, "set") as turn_done, patch(
            "no1.daemon.claude_app_sessions.auto_mark_headless_delivery_started"
        ):
            session._turn_loop()

        self.assertEqual(rotations, [1])
        self.assertEqual(len(old_proc.stdin.lines), 1)
        self.assertEqual(len(new_proc.stdin.lines), 1)
        self.assertIn("turn A", old_proc.stdin.lines[0])
        self.assertIn("turn B", new_proc.stdin.lines[0])
        grant_b = get_current_turn_grant(self.group, "peer1")
        self.assertIsNotNone(grant_b)
        self.assertEqual(grant_b["event_ids"], [event_b["id"]])
        session._handle_event({"type": "result", "subtype": "success"}, transport_generation=2)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        failed_a = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        failed_b = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        failed_c = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        failed = ClaudeAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        failed_proc = FakeProc(fail_flush=True)
        failed._proc = failed_proc
        failed._running = True
        failed._transport_generation = 1
        failed._proc_generation = 1
        failed._accepted_transport_generation = 1
        failed._turn_queue.put(
            _PendingTurn(text="failed A", event_id=str(failed_a["id"]), ts=str(failed_a["ts"]))
        )
        failed._turn_queue.put(
            _PendingTurn(text="queued B", event_id=str(failed_b["id"]), ts=str(failed_b["ts"]))
        )
        failed._turn_queue.put(
            _PendingTurn(text="queued C", event_id=str(failed_c["id"]), ts=str(failed_c["ts"]))
        )
        with patch.object(failed, "is_running", return_value=True), patch.object(
            failed, "_restart_transport_after_stream_completion", return_value=False
        ), patch.object(failed, "_persist_state"), patch.object(failed, "_emit") as failed_emit, patch.object(
            failed, "stop"
        ) as stop:
            failed._turn_loop()

        self.assertEqual(len(failed_proc.stdin.lines), 1)
        self.assertIn("failed A", failed_proc.stdin.lines[0])
        self.assertFalse(any("queued B" in line for line in failed_proc.stdin.lines))
        self.assertFalse(any("queued C" in line for line in failed_proc.stdin.lines))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        stop.assert_called_once_with(persist_actor_stopped=False)
        queued_failures = [
            call
            for call in failed_emit.call_args_list
            if call.args[0] == "headless.turn.failed" and bool(call.args[1].get("queued_not_written"))
        ]
        self.assertEqual(queued_failures, [])
        unread_ids = [
            str(event.get("id") or "")
            for event in unread_messages(self.group, actor_id="peer1", limit=0, kind_filter="chat")
        ]
        self.assertEqual(unread_ids, [str(failed_b["id"]), str(failed_c["id"])])
        read_ids = [
            str((event.get("data") or {}).get("event_id") or "")
            for event in (
                json.loads(line) for line in read_last_lines(self.group.ledger_path, 100)
            )
            if str(event.get("kind") or "") == "chat.read"
        ]
        self.assertEqual(read_ids, [str(failed_a["id"])])

    def test_claude_rotation_failure_rebuilds_unread_turns_in_order_after_restart(self) -> None:
        from no1.daemon.claude_app_sessions import ClaudeAppSession, _PendingTurn
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance, get_current_turn_grant
        from no1.kernel.actors import add_actor
        from no1.kernel.inbox import unread_messages
        from no1.kernel.ledger import append_event

        add_actor(self.group, actor_id="peer1", runner="headless", runtime="claude")
        events = [
            self._append(
                provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            )
            for _ in range(3)
        ]
        session = ClaudeAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        class FlushUnknownStdin:
            def __init__(self) -> None:
                self.lines = []

            def write(self, value: str) -> int:
                self.lines.append(value)
                return len(value)

            def flush(self) -> None:
                raise OSError("flush acknowledgement lost")

        class FakeProc:
            def __init__(self) -> None:
                self.stdin = FlushUnknownStdin()
                self.stdout = []
                self.stderr = []
                self.pid = 1234

            def poll(self):
                return None

        session._proc = FakeProc()
        session._running = True
        session._transport_generation = 1
        session._proc_generation = 1
        session._accepted_transport_generation = 1
        for index, event in enumerate(events):
            session._turn_queue.put(
                _PendingTurn(text=f"turn-{index}", event_id=str(event["id"]), ts=str(event["ts"]))
            )

        with patch.object(session, "is_running", return_value=True), patch.object(
            session, "_restart_transport_after_stream_completion", return_value=False
        ), patch.object(session, "_persist_state"), patch.object(session, "_emit") as emit, patch.object(
            session, "stop"
        ) as stop:
            session._turn_loop()

        self.assertEqual(len(session._proc.stdin.lines), 1)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        stop.assert_called_once_with(persist_actor_stopped=False)
        self.assertFalse(
            any(
                call.args
                and call.args[0] == "headless.turn.failed"
                and bool(call.args[1].get("queued_not_written"))
                for call in emit.call_args_list
            )
        )
        for index in range(2005):
            append_event(
                self.group.ledger_path,
                kind="headless.test.noise",
                group_id=self.group.group_id,
                scope_key="",
                by="other-actor",
                data={"actor_id": "other-actor", "index": index},
            )
        unread = unread_messages(self.group, actor_id="peer1", limit=0, kind_filter="chat")
        self.assertEqual(
            [str(event.get("id") or "") for event in unread],
            [str(events[1]["id"]), str(events[2]["id"])],
        )

        restarted = ClaudeAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        restarted._accepted_transport_generation = 2
        restarted._queue_recovery_turns()
        self.assertEqual(
            [
                str(payload.event_id or "")
                for payload in list(restarted._turn_queue.queue)
                if isinstance(payload, _PendingTurn)
            ],
            [str(events[1]["id"]), str(events[2]["id"])],
        )

        submitted_ids = []

        def record_write(_data):
            submitted_ids.append(str(restarted._active_event_id or ""))
            return True

        def complete_turn():
            restarted._handle_event({"type": "result", "subtype": "success"}, transport_generation=2)
            return True

        with patch.object(restarted, "is_running", side_effect=[True, True, False]), patch.object(
            restarted, "_write_stdin", side_effect=record_write
        ), patch.object(restarted._turn_done, "wait", side_effect=complete_turn), patch.object(
            restarted, "_restart_transport_after_stream_completion", return_value=True
        ), patch.object(restarted, "_persist_state"), patch.object(restarted, "_emit"), patch(
            "no1.daemon.claude_app_sessions.auto_mark_headless_delivery_started"
        ):
            restarted._turn_loop()

        self.assertEqual(
            submitted_ids,
            [str(events[1]["id"]), str(events[2]["id"])],
        )
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        completed_restart = ClaudeAppSession(
            group_id=self.group.group_id,
            actor_id="peer1",
            cwd=self.group.path,
            env={},
        )
        completed_restart._queue_recovery_turns()
        self.assertTrue(completed_restart._turn_queue.empty())
        self.assertEqual(completed_restart._recovery_markers, {})

    def test_tracked_send_rejects_unclosed_actor_ingress_marker(self) -> None:
        from no1.daemon.messaging.chat_ops import handle_tracked_send

        with patch("no1.daemon.messaging.chat_ops.handle_context_sync") as context_sync:
            from no1.contracts.v1 import DaemonResponse

            context_sync.return_value = DaemonResponse(
                ok=True,
                result={"changes": [{"op": "task.create", "task_id": "task-1"}]},
            )
            response = handle_tracked_send(
                {
                    "group_id": self.group.group_id,
                    "__turn_ingress": "actor_mcp",
                    "by": "peer1",
                    "title": "Tracked",
                    "text": "actor work",
                    "to": ["user"],
                },
                coerce_bool=bool,
                normalize_attachments=lambda _group, _raw: [],
                effective_runner_kind=lambda value: str(value or "pty"),
                auto_wake_recipients=lambda _group, _to, _by: [],
                automation_on_resume=lambda _group: None,
                automation_on_new_message=lambda _group: None,
                clear_pending_system_notifies=lambda _group_id, _kinds: None,
            )

        self.assertTrue(response.ok)
        provenance = response.result["event"]["data"]["turn_provenance"]
        self.assertEqual(provenance["origin"], "untrusted")
        self.assertFalse(provenance["fresh_local_request"])

    def test_tracked_send_preserves_closed_actor_ingress(self) -> None:
        from no1.daemon.messaging.chat_ops import handle_tracked_send
        from no1.daemon.messaging.message_admission import close_message_dispatch
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="headless", runtime="codex")
        _op, closed_args = close_message_dispatch(
            "actor_tracked_send",
            {
                "group_id": self.group.group_id,
                "by": "peer1",
                "title": "Tracked",
                "text": "actor work",
                "to": ["user"],
            },
        )

        with patch("no1.daemon.messaging.chat_ops.handle_context_sync") as context_sync:
            from no1.contracts.v1 import DaemonResponse

            context_sync.return_value = DaemonResponse(
                ok=True,
                result={"changes": [{"op": "task.create", "task_id": "task-1"}]},
            )
            response = handle_tracked_send(
                closed_args,
                coerce_bool=bool,
                normalize_attachments=lambda _group, _raw: [],
                effective_runner_kind=lambda value: str(value or "pty"),
                auto_wake_recipients=lambda _group, _to, _by: [],
                automation_on_resume=lambda _group: None,
                automation_on_new_message=lambda _group: None,
                clear_pending_system_notifies=lambda _group_id, _kinds: None,
            )

        self.assertTrue(response.ok)
        provenance = response.result["event"]["data"]["turn_provenance"]
        self.assertEqual(provenance["origin"], "local_actor")
        self.assertFalse(provenance["fresh_local_request"])

    def test_pty_finalize_uses_complete_batch_and_mixed_origin_fails_closed(self) -> None:
        from no1.daemon.messaging import delivery
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance, get_current_turn_grant

        local = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"}))
        remote = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "im", "by": "user"}))
        local_msg = delivery.PendingMessage(
            event_id=str(local["id"]),
            by="user",
            to=["peer1"],
            text="local",
        )
        remote_msg = delivery.PendingMessage(
            event_id=str(remote["id"]),
            by="user",
            to=["peer1"],
            text="remote",
        )
        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery._get_auto_mark_on_delivery", return_value=False
        ):
            delivery._finalize_delivery_success(
                self.group,
                actor_id="peer1",
                chat_total=1,
                deliverable=[local_msg],
                requeue=[],
            )
            self.assertEqual(get_current_turn_grant(self.group, "peer1")["event_ids"], [local["id"]])
            delivery._finalize_delivery_success(
                self.group,
                actor_id="peer1",
                chat_total=2,
                deliverable=[local_msg, remote_msg],
                requeue=[],
            )
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_pty_submit_failure_invalidates_previous_grant(self) -> None:
        from no1.daemon.messaging import delivery
        from no1.daemon.messaging.experience_reminder import ExperienceReminderDecision
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
        )

        event = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"}))
        event_id = str(event["id"])
        self.assertIsNotNone(finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=[event_id]))

        throttle = delivery.DeliveryThrottle()
        experience_decision = ExperienceReminderDecision(
            group_id=self.group.group_id,
            scope_key="",
            actor_id="peer1",
            event_ids=(event_id,),
            due=False,
        )
        with patch.object(delivery, "THROTTLE", throttle), patch(
            "no1.daemon.messaging.delivery.find_actor", return_value={"id": "peer1", "runner": "pty"}
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.is_preamble_sent", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.plan_experience_reminder", return_value=experience_decision
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
        ), patch("no1.daemon.messaging.delivery.pty_submit_text", return_value=False):
            delivery.queue_chat_message(
                self.group,
                actor_id="peer1",
                event_id=event_id,
                by="user",
                to=["peer1"],
                text="delivery fails",
            )
            self.assertFalse(delivery.flush_pending_messages(self.group, actor_id="peer1"))

        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_pty_sync_reply_terminalizes_pending_attempt_before_finalize(self) -> None:
        from no1.daemon.messaging import delivery
        from no1.daemon.messaging.chat_ops import handle_reply
        from no1.daemon.messaging.experience_reminder import ExperienceReminderDecision
        from no1.daemon.messaging.turn_provenance import build_send_turn_provenance, get_current_turn_grant
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        event_id = str(event["id"])
        throttle = delivery.DeliveryThrottle()
        experience_decision = ExperienceReminderDecision(
            group_id=self.group.group_id,
            scope_key="",
            actor_id="peer1",
            event_ids=(event_id,),
            due=False,
        )
        submitted_receipt: dict = {}
        submitted_grant_receipt: dict = {}

        def submit_and_reply(_group, *, actor_id, text, **_kwargs):
            prefix = "[onecolleague] completion_receipt="
            line = next(item for item in str(text or "").splitlines() if item.startswith(prefix))
            receipt = json.loads(line[len(prefix) :])
            submitted_receipt.update(receipt)
            grant_prefix = "[onecolleague] turn_grant_receipt="
            grant_line = next(item for item in str(text or "").splitlines() if item.startswith(grant_prefix))
            submitted_grant_receipt.update(json.loads(grant_line[len(grant_prefix) :]))
            response = handle_reply(
                {
                    "group_id": self.group.group_id,
                    "__turn_ingress": "actor_mcp",
                    "by": actor_id,
                    "reply_to": event_id,
                    "text": "synchronous terminal",
                    "to": ["user"],
                    "completion_receipt": receipt,
                },
                coerce_bool=bool,
                normalize_attachments=lambda _group, _raw: [],
                effective_runner_kind=lambda value: str(value or "pty"),
                auto_wake_recipients=lambda _group, _to, _by: [],
                automation_on_resume=lambda _group: None,
                automation_on_new_message=lambda _group: None,
                clear_pending_system_notifies=lambda _group_id, _kinds: None,
            )
            self.assertTrue(response.ok, getattr(response, "error", None))
            return True

        with patch.object(delivery, "THROTTLE", throttle), patch(
            "no1.daemon.messaging.delivery._get_auto_mark_on_delivery", return_value=False
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.is_preamble_sent", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.plan_experience_reminder", return_value=experience_decision
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
        ), patch("no1.daemon.messaging.delivery.pty_submit_text", side_effect=submit_and_reply):
            delivery.queue_chat_message(
                self.group,
                actor_id="peer1",
                event_id=event_id,
                by="user",
                to=["peer1"],
                text="work",
            )
            self.assertTrue(delivery.flush_pending_messages(self.group, actor_id="peer1"))

        self.assertEqual(submitted_receipt.get("group_id"), self.group.group_id)
        self.assertEqual(submitted_receipt.get("actor_id"), "peer1")
        self.assertEqual(submitted_receipt.get("event_ids"), [event_id])
        self.assertEqual(submitted_receipt.get("binding"), {"transport": "pty"})
        self.assertNotIn("authorization_secret", submitted_receipt)
        self.assertTrue(submitted_grant_receipt.get("authorization_secret"))
        self.assertEqual(submitted_grant_receipt.get("event_ids"), [event_id])
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_pty_accepted_finalize_uncertainty_never_requeues_or_leaves_grant(self) -> None:
        from no1.daemon.messaging import delivery
        from no1.daemon.messaging.experience_reminder import ExperienceReminderDecision
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_attempt as real_finalize,
            get_current_turn_grant,
        )
        from no1.kernel.actors import add_actor
        from no1.kernel.ledger import read_last_lines
        from no1.util.fs import read_json

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")

        def run_sync_case(*, write_before_raise: bool) -> None:
            event = self._append(
                provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            )
            event_id = str(event["id"])
            throttle = delivery.DeliveryThrottle()
            decision = ExperienceReminderDecision(
                group_id=self.group.group_id,
                scope_key="",
                actor_id="peer1",
                event_ids=(event_id,),
                due=False,
            )

            def finalize_then_raise(*args, **kwargs):
                if not write_before_raise:
                    real_finalize(*args, **kwargs)
                raise OSError("grant persistence acknowledgement lost")

            with patch.object(delivery, "THROTTLE", throttle), patch(
                "no1.daemon.messaging.delivery._get_auto_mark_on_delivery", return_value=False
            ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
                "no1.daemon.messaging.delivery.is_preamble_sent", return_value=True
            ), patch(
                "no1.daemon.messaging.delivery.plan_experience_reminder", return_value=decision
            ), patch(
                "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
            ), patch("no1.daemon.messaging.delivery.pty_submit_text", return_value=True) as submit, patch(
                "no1.daemon.messaging.delivery.finalize_turn_delivery_attempt", side_effect=finalize_then_raise
            ):
                delivery.queue_chat_message(
                    self.group,
                    actor_id="peer1",
                    event_id=event_id,
                    by="user",
                    to=["peer1"],
                    text="uncertain accepted",
                )
                self.assertFalse(delivery.flush_pending_messages(self.group, actor_id="peer1"))
                self.assertFalse(throttle.has_pending(self.group.group_id, "peer1"))
            submit.assert_called_once()
            self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
            state = read_json(self.group.path / "state" / "turn-grants" / "peer1.json")
            self.assertIsNone(state.get("pending_attempt"))
            self.assertIsNone(state.get("current_grant"))

        for write_before_raise in (True, False):
            with self.subTest(path="sync", write_before_raise=write_before_raise):
                run_sync_case(write_before_raise=write_before_raise)

        async_event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        async_message = delivery.PendingMessage(
            event_id=str(async_event["id"]),
            by="user",
            to=["peer1"],
            text="async uncertain accepted",
        )
        async_throttle = delivery.DeliveryThrottle()
        async_decision = ExperienceReminderDecision(
            group_id=self.group.group_id,
            scope_key="",
            actor_id="peer1",
            event_ids=(str(async_event["id"]),),
            due=False,
        )
        recorded = threading.Event()
        real_record = delivery._record_uncertain_accepted_delivery

        def write_then_raise(*args, **kwargs):
            real_finalize(*args, **kwargs)
            raise OSError("grant persistence acknowledgement lost")

        def record_and_signal(*args, **kwargs):
            try:
                return real_record(*args, **kwargs)
            finally:
                recorded.set()

        with patch.object(delivery, "THROTTLE", async_throttle), patch(
            "no1.daemon.messaging.delivery.render_system_prompt", return_value=""
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch.object(
            delivery, "PREAMBLE_TO_MESSAGE_DELAY_SECONDS", 0.0
        ), patch("no1.daemon.messaging.delivery.pty_submit_text", return_value=True) as submit, patch(
            "no1.daemon.messaging.delivery.finalize_turn_delivery_attempt", side_effect=write_then_raise
        ), patch(
            "no1.daemon.messaging.delivery._record_uncertain_accepted_delivery", side_effect=record_and_signal
        ):
            delivery._start_async_first_delivery(
                self.group,
                actor_id="peer1",
                messages=[async_message],
                deliverable=[async_message],
                requeue=[],
                message_text="async uncertain accepted",
                chat_total=1,
                actor={"id": "peer1", "runner": "pty"},
                experience_decision=async_decision,
            )
            self.assertTrue(recorded.wait(2.0))
            self.assertFalse(async_throttle.has_pending(self.group.group_id, "peer1"))
        submit.assert_called_once()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertTrue(any("actor.delivery.failed" in line for line in read_last_lines(self.group.ledger_path, 20)))

    def test_pty_delivery_waits_for_submit_and_isolates_uncertain_submit(self) -> None:
        from no1.daemon.messaging import delivery
        from no1.daemon.messaging.experience_reminder import ExperienceReminderDecision
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            get_current_turn_grant,
            invalidate_turn_grant,
        )
        from no1.util.fs import read_json

        def setup_case(text: str):
            event = self._append(
                provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            )
            event_id = str(event["id"])
            throttle = delivery.DeliveryThrottle()
            decision = ExperienceReminderDecision(
                group_id=self.group.group_id,
                scope_key="",
                actor_id="peer1",
                event_ids=(event_id,),
                due=False,
            )
            throttle.queue_message(
                self.group.group_id,
                "peer1",
                event_id=event_id,
                by="user",
                to=["peer1"],
                text=text,
            )
            return event_id, throttle, decision

        event_id, throttle, decision = setup_case("wait for Enter")
        submit_entered = threading.Event()
        release_submit = threading.Event()
        writes = []

        def blocking_write(*, data, **_kwargs):
            writes.append(data)
            if len(writes) == 1:
                return True
            submit_entered.set()
            self.assertTrue(release_submit.wait(2.0))
            return True

        result = {}
        with patch.object(delivery, "THROTTLE", throttle), patch.object(
            delivery, "PTY_SUBMIT_DELAY_SECONDS", 0.0
        ), patch("no1.daemon.messaging.delivery._get_auto_mark_on_delivery", return_value=False), patch(
            "no1.daemon.messaging.delivery.find_actor", return_value={"id": "peer1", "runner": "pty"}
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.is_preamble_sent", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.plan_experience_reminder", return_value=decision
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.write_input", side_effect=blocking_write
        ):
            thread = threading.Thread(
                target=lambda: result.setdefault(
                    "delivered", delivery.flush_pending_messages(self.group, actor_id="peer1")
                )
            )
            thread.start()
            self.assertTrue(submit_entered.wait(2.0))
            state = read_json(self.group.path / "state" / "turn-grants" / "peer1.json")
            self.assertIsInstance(state.get("pending_attempt"), dict)
            self.assertIsNone(state.get("current_grant"))
            self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
            release_submit.set()
            thread.join(2.0)
            self.assertFalse(thread.is_alive())

        self.assertTrue(result.get("delivered"))
        self.assertEqual(len(writes), 2)
        self.assertIsNotNone(get_current_turn_grant(self.group, "peer1"))
        invalidate_turn_grant(self.group, "peer1", reason="test_cleanup")

        for submit_behavior in ("false", "exception"):
            with self.subTest(submit_behavior=submit_behavior):
                _event_id, throttle, decision = setup_case(f"submit {submit_behavior}")
                writes = []

                def uncertain_write(*, data, **_kwargs):
                    writes.append(data)
                    if len(writes) == 1:
                        return True
                    if submit_behavior == "false":
                        return False
                    raise OSError("submit acknowledgement lost")

                with patch.object(delivery, "THROTTLE", throttle), patch.object(
                    delivery, "PTY_SUBMIT_DELAY_SECONDS", 0.0
                ), patch("no1.daemon.messaging.delivery._get_auto_mark_on_delivery", return_value=False), patch(
                    "no1.daemon.messaging.delivery.find_actor", return_value={"id": "peer1", "runner": "pty"}
                ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
                    "no1.daemon.messaging.delivery.is_preamble_sent", return_value=True
                ), patch(
                    "no1.daemon.messaging.delivery.plan_experience_reminder", return_value=decision
                ), patch(
                    "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
                ), patch(
                    "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
                ), patch(
                    "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.write_input", side_effect=uncertain_write
                ), patch(
                    "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.stop_actor"
                ) as stop_actor:
                    self.assertFalse(delivery.flush_pending_messages(self.group, actor_id="peer1"))
                    self.assertFalse(throttle.has_pending(self.group.group_id, "peer1"))

                stop_actor.assert_called_once_with(group_id=self.group.group_id, actor_id="peer1")
                self.assertEqual(len(writes), 2)
                state = read_json(self.group.path / "state" / "turn-grants" / "peer1.json")
                self.assertIsNone(state.get("pending_attempt"))
                self.assertIsNone(state.get("current_grant"))
                self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        for submit_behavior in ("false", "exception"):
            with self.subTest(path="async_first", submit_behavior=submit_behavior):
                async_event = self._append(
                    provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
                )
                async_message = delivery.PendingMessage(
                    event_id=str(async_event["id"]),
                    by="user",
                    to=["peer1"],
                    text=f"async submit {submit_behavior}",
                )
                async_throttle = delivery.DeliveryThrottle()
                async_decision = ExperienceReminderDecision(
                    group_id=self.group.group_id,
                    scope_key="",
                    actor_id="peer1",
                    event_ids=(str(async_event["id"]),),
                    due=False,
                )
                recorded = threading.Event()
                real_record = delivery._record_uncertain_pty_submission
                outcome = delivery.PtySubmitOutcome(
                    False,
                    False,
                    "submit_write_unknown",
                    "submit acknowledgement lost",
                )

                def record_and_signal(*args, **kwargs):
                    try:
                        return real_record(*args, **kwargs)
                    finally:
                        recorded.set()

                with patch.object(delivery, "THROTTLE", async_throttle), patch.object(
                    delivery, "PREAMBLE_TO_MESSAGE_DELAY_SECONDS", 0.0
                ), patch(
                    "no1.daemon.messaging.delivery.render_system_prompt", return_value=""
                ), patch(
                    "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
                ), patch(
                    "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.stop_actor"
                ) as stop_actor, patch(
                    "no1.daemon.messaging.delivery.pty_submit_text", return_value=outcome
                ), patch(
                    "no1.daemon.messaging.delivery._record_uncertain_pty_submission",
                    side_effect=record_and_signal,
                ):
                    delivery._start_async_first_delivery(
                        self.group,
                        actor_id="peer1",
                        messages=[async_message],
                        deliverable=[async_message],
                        requeue=[],
                        message_text=async_message.text,
                        chat_total=1,
                        actor={"id": "peer1", "runner": "pty"},
                        experience_decision=async_decision,
                    )
                    self.assertTrue(recorded.wait(2.0))
                    self.assertFalse(async_throttle.has_pending(self.group.group_id, "peer1"))

                stop_actor.assert_called_once_with(group_id=self.group.group_id, actor_id="peer1")
                self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        with self.subTest(path="async_preamble", submit_behavior="false"):
            preamble_event = self._append(
                provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
            )
            preamble_message = delivery.PendingMessage(
                event_id=str(preamble_event["id"]),
                by="user",
                to=["peer1"],
                text="business payload after preamble",
            )
            preamble_throttle = delivery.DeliveryThrottle()
            preamble_decision = ExperienceReminderDecision(
                group_id=self.group.group_id,
                scope_key="",
                actor_id="peer1",
                event_ids=(str(preamble_event["id"]),),
                due=False,
            )
            order = []
            writes = []
            worker_done = threading.Event()
            real_requeue = preamble_throttle.requeue_front
            real_end = preamble_throttle.end_delivery

            def preamble_write(*, data, **_kwargs):
                writes.append(data)
                return len(writes) == 1

            def stop_and_record(**_kwargs):
                order.append("stop")

            def requeue_and_record(group_id, actor_id, messages):
                order.append("requeue")
                return real_requeue(group_id, actor_id, messages)

            def end_and_signal(group_id, actor_id):
                try:
                    return real_end(group_id, actor_id)
                finally:
                    worker_done.set()

            with patch.object(delivery, "THROTTLE", preamble_throttle), patch.object(
                delivery, "PTY_SUBMIT_DELAY_SECONDS", 0.0
            ), patch(
                "no1.daemon.messaging.delivery.render_system_prompt", return_value="preamble"
            ), patch(
                "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
            ), patch(
                "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.write_input", side_effect=preamble_write
            ), patch(
                "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.stop_actor",
                side_effect=stop_and_record,
            ), patch.object(
                preamble_throttle, "requeue_front", side_effect=requeue_and_record
            ), patch.object(
                preamble_throttle, "end_delivery", side_effect=end_and_signal
            ), patch(
                "no1.daemon.messaging.delivery._finish_delivery_chain"
            ) as finish_chain:
                delivery._start_async_first_delivery(
                    self.group,
                    actor_id="peer1",
                    messages=[preamble_message],
                    deliverable=[preamble_message],
                    requeue=[],
                    message_text=preamble_message.text,
                    chat_total=1,
                    actor={"id": "peer1", "runner": "pty"},
                    experience_decision=preamble_decision,
                )
                self.assertTrue(worker_done.wait(2.0))

            self.assertEqual(order, ["stop", "requeue"])
            self.assertEqual(len(writes), 2)
            self.assertTrue(preamble_throttle.has_pending(self.group.group_id, "peer1"))
            finish_chain.assert_not_called()
            self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_cross_group_dispatch_forces_remote_target_origin(self) -> None:
        from no1.contracts.v1 import DaemonResponse
        from no1.daemon.ops.maintenance_ops import handle_send_cross_group

        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        target = create_group(load_registry(), title="target", topic="")
        calls = []

        def dispatch(op, args):
            calls.append((op, dict(args)))
            event_id = "src-event" if len(calls) == 1 else "dst-event"
            return DaemonResponse(ok=True, result={"event": {"id": event_id}}), False

        response = handle_send_cross_group(
            {
                "group_id": self.group.group_id,
                "dst_group_id": target.group_id,
                "__turn_ingress": "actor_mcp",
                "by": "peer1",
                "text": "relay",
                "to": [],
            },
            dispatch_send=dispatch,
        )
        self.assertTrue(response.ok)
        self.assertEqual(calls[0][1]["__turn_ingress"], "actor_mcp")
        self.assertEqual(calls[1][1]["__turn_ingress"], "cross_group")
        self.assertEqual(calls[1][1]["src_event_id"], "src-event")

    def test_actor_reply_stop_restart_and_process_exit_invalidate(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.actors.actor_exit_ops import persist_actor_process_exit_stopped
        from no1.daemon.messaging.chat_ops import handle_reply
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            get_current_turn_grant,
            turn_delivery_completion_receipt,
        )
        from no1.daemon.server import handle_request
        from no1.kernel.actors import add_actor
        from no1.kernel.group import load_group

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        original = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )

        def issue():
            attempt = begin_turn_delivery_attempt(
                self.group,
                actor_id="peer1",
                event_ids=[str(original["id"])],
                binding={"transport": "pty"},
            )
            self.assertIsNotNone(attempt)
            grant = finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=attempt)
            self.assertIsNotNone(grant)
            return grant, turn_delivery_completion_receipt(attempt)

        _, completion_receipt = issue()
        reply = handle_reply(
            {
                "group_id": self.group.group_id,
                "__turn_ingress": "actor_mcp",
                "by": "peer1",
                "reply_to": original["id"],
                "text": "done",
                "to": ["user"],
                "completion_receipt": completion_receipt,
            },
            coerce_bool=bool,
            normalize_attachments=lambda _group, _raw: [],
            effective_runner_kind=lambda value: str(value or "pty"),
            auto_wake_recipients=lambda _group, _to, _by: [],
            automation_on_resume=lambda _group: None,
            automation_on_new_message=lambda _group: None,
            clear_pending_system_notifies=lambda _group_id, _kinds: None,
        )
        self.assertTrue(reply.ok)
        self.assertEqual(reply.result["event"]["data"]["turn_provenance"]["origin"], "local_actor")
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        self.assertIsNotNone(issue()[0])
        restarted, _ = handle_request(
            DaemonRequest(op="actor_restart", args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"})
        )
        self.assertTrue(restarted.ok, getattr(restarted, "error", None))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        self.assertIsNotNone(issue()[0])
        self.assertTrue(persist_actor_process_exit_stopped(group_id=self.group.group_id, actor_id="peer1", runner="pty"))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_actor_reply_receipt_is_exact_and_generation_safe(self) -> None:
        from no1.daemon.messaging.chat_ops import handle_reply
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            get_current_turn_grant,
            turn_delivery_completion_receipt,
        )
        from no1.kernel.actors import add_actor
        from no1.util.fs import read_json

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        original = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )

        def issue_attempt():
            attempt = begin_turn_delivery_attempt(
                self.group,
                actor_id="peer1",
                event_ids=[str(original["id"])],
                binding={"transport": "pty"},
            )
            self.assertIsNotNone(attempt)
            receipt = turn_delivery_completion_receipt(attempt)
            self.assertIsNotNone(receipt)
            return attempt, receipt

        def reply(receipt, text):
            args = {
                "group_id": self.group.group_id,
                "__turn_ingress": "actor_mcp",
                "by": "peer1",
                "reply_to": original["id"],
                "text": text,
                "to": ["user"],
            }
            if receipt is not None:
                args["completion_receipt"] = receipt
            response = handle_reply(
                args,
                coerce_bool=bool,
                normalize_attachments=lambda _group, _raw: [],
                effective_runner_kind=lambda value: str(value or "pty"),
                auto_wake_recipients=lambda _group, _to, _by: [],
                automation_on_resume=lambda _group: None,
                automation_on_new_message=lambda _group: None,
                clear_pending_system_notifies=lambda _group_id, _kinds: None,
            )
            self.assertTrue(response.ok, getattr(response, "error", None))
            return response.result["event"]

        attempt_a, receipt_a = issue_attempt()
        grant_a = finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=attempt_a)
        self.assertIsNotNone(grant_a)
        self.assertEqual(reply(None, "legacy reply")["data"]["text"], "legacy reply")
        self.assertEqual(get_current_turn_grant(self.group, "peer1"), grant_a)

        for field, forged_value in (
            ("group_id", "other-group"),
            ("actor_id", "other-actor"),
            ("attempt_id", "turnattempt_forged"),
            ("generation", int(receipt_a["generation"]) + 1),
            ("event_ids", ["other-event"]),
            ("binding", {"transport": "other"}),
        ):
            with self.subTest(field=field):
                forged = {**receipt_a, field: forged_value}
                reply(forged, f"forged {field}")
                self.assertEqual(get_current_turn_grant(self.group, "peer1"), grant_a)

        reply(receipt_a, "complete A")
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        attempt_b, receipt_b = issue_attempt()
        reply(receipt_a, "late A while B pending")
        state_path = self.group.path / "state" / "turn-grants" / "peer1.json"
        self.assertEqual(read_json(state_path).get("pending_attempt", {}).get("attempt_id"), attempt_b["attempt_id"])
        grant_b = finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=attempt_b)
        self.assertIsNotNone(grant_b)
        reply(receipt_a, "late A after B grant")
        self.assertEqual(get_current_turn_grant(self.group, "peer1"), grant_b)
        reply(receipt_b, "complete B")
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        attempt_c, receipt_c = issue_attempt()
        reply(receipt_c, "sync C reply before finalize")
        self.assertIsNone(finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=attempt_c))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_actor_and_group_stop_invalidate_current_grants(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
        )
        from no1.daemon.server import handle_request
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        add_actor(self.group, actor_id="peer2", runner="pty", runtime="codex")
        event = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"}))
        for actor_id in ("peer1", "peer2"):
            self.assertIsNotNone(
                finalize_turn_delivery_success(self.group, actor_id=actor_id, event_ids=[str(event["id"])])
            )

        stopped, _ = handle_request(
            DaemonRequest(op="actor_stop", args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"})
        )
        self.assertTrue(stopped.ok, getattr(stopped, "error", None))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertIsNotNone(get_current_turn_grant(self.group, "peer2"))

        group_stopped, _ = handle_request(
            DaemonRequest(op="group_stop", args={"group_id": self.group.group_id, "by": "user"})
        )
        self.assertTrue(group_stopped.ok, getattr(group_stopped, "error", None))
        self.assertIsNone(get_current_turn_grant(self.group, "peer2"))

    def test_stop_restart_intent_revokes_before_fallible_side_effects(self) -> None:
        from dataclasses import replace

        from no1.contracts.v1 import DaemonRequest
        from no1.daemon import server
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
        )
        from no1.daemon.server import handle_request
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        add_actor(self.group, actor_id="peer2", runner="pty", runtime="codex")
        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )

        def issue():
            grant = finalize_turn_delivery_success(
                self.group,
                actor_id="peer1",
                event_ids=[str(event["id"])],
            )
            self.assertIsNotNone(grant)
            return grant

        issue()
        with patch(
            "no1.daemon.actors.actor_lifecycle_ops.update_actor",
            side_effect=OSError("stop config failed"),
        ):
            stopped, _ = handle_request(
                DaemonRequest(
                    op="actor_stop",
                    args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"},
                )
            )
        self.assertFalse(stopped.ok)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        issue()
        deps = server._request_dispatch_deps()
        original_deps = server._REQUEST_DISPATCH_DEPS
        server._REQUEST_DISPATCH_DEPS = replace(
            deps,
            remove_pty_state_if_pid=Mock(side_effect=OSError("state cleanup failed")),
        )
        try:
            with patch("no1.daemon.actors.actor_lifecycle_ops.pty_runner.SUPERVISOR.stop_actor") as runtime_stop:
                stopped, _ = handle_request(
                    DaemonRequest(
                        op="actor_stop",
                        args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"},
                    )
                )
        finally:
            server._REQUEST_DISPATCH_DEPS = original_deps
        self.assertFalse(stopped.ok)
        runtime_stop.assert_called_once()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        issue()
        with patch(
            "no1.daemon.actors.actor_lifecycle_ops.resolve_linked_actor_before_start",
            side_effect=OSError("profile resolve failed"),
        ):
            restarted, _ = handle_request(
                DaemonRequest(
                    op="actor_restart",
                    args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"},
                )
            )
        self.assertFalse(restarted.ok)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        issue()
        with patch(
            "no1.daemon.actors.actor_lifecycle_ops.update_actor",
            side_effect=OSError("restart config failed"),
        ) as update_config, patch(
            "no1.daemon.actors.actor_lifecycle_ops.resolve_linked_actor_before_start"
        ) as resolve_profile:
            restarted, _ = handle_request(
                DaemonRequest(
                    op="actor_restart",
                    args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"},
                )
            )
        self.assertFalse(restarted.ok)
        update_config.assert_called_once()
        resolve_profile.assert_not_called()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        issue()
        with patch("no1.daemon.actors.actor_lifecycle_ops.codex_app_supervisor.stop_actor") as first_stop, patch(
            "no1.daemon.actors.actor_lifecycle_ops.claude_app_supervisor.stop_actor",
            side_effect=OSError("middle supervisor failed"),
        ):
            restarted, _ = handle_request(
                DaemonRequest(
                    op="actor_restart",
                    args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"},
                )
            )
        self.assertFalse(restarted.ok)
        first_stop.assert_called_once()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        surviving = issue()
        original_deps = server._REQUEST_DISPATCH_DEPS
        deps = server._request_dispatch_deps()
        state_cleanup = Mock()
        clear_preamble = Mock()
        throttle_reset = Mock()
        server._REQUEST_DISPATCH_DEPS = replace(
            deps,
            remove_headless_state=state_cleanup,
            remove_pty_state_if_pid=state_cleanup,
            clear_preamble_sent=clear_preamble,
            throttle_reset_actor=throttle_reset,
        )
        try:
            with patch("no1.daemon.actors.actor_lifecycle_ops.update_actor") as update_config, patch(
                "no1.daemon.actors.actor_lifecycle_ops.resolve_linked_actor_before_start"
            ) as resolve_profile, patch(
                "no1.daemon.actors.actor_lifecycle_ops.invalidate_turn_grant"
            ) as invalidate, patch(
                "no1.daemon.actors.actor_lifecycle_ops.codex_app_supervisor.stop_actor"
            ) as stop_codex, patch(
                "no1.daemon.actors.actor_lifecycle_ops.claude_app_supervisor.stop_actor"
            ) as stop_claude, patch(
                "no1.daemon.actors.actor_lifecycle_ops.headless_runner.SUPERVISOR.stop_actor"
            ) as stop_headless, patch(
                "no1.daemon.actors.actor_lifecycle_ops.pty_runner.SUPERVISOR.stop_actor"
            ) as stop_pty:
                denied, _ = handle_request(
                    DaemonRequest(
                        op="actor_stop",
                        args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "peer2"},
                    )
                )
                self.assertFalse(denied.ok)
                self.assertEqual(get_current_turn_grant(self.group, "peer1"), surviving)

                for op in ("actor_stop", "actor_restart"):
                    with self.subTest(op=op):
                        missing, _ = handle_request(
                            DaemonRequest(
                                op=op,
                                args={"group_id": self.group.group_id, "actor_id": "missing", "by": "user"},
                            )
                        )
                        self.assertFalse(missing.ok)

                for side_effect in (
                    update_config,
                    resolve_profile,
                    invalidate,
                    stop_codex,
                    stop_claude,
                    stop_headless,
                    stop_pty,
                    state_cleanup,
                    clear_preamble,
                    throttle_reset,
                ):
                    side_effect.assert_not_called()
        finally:
            server._REQUEST_DISPATCH_DEPS = original_deps

    def test_authoritative_actor_termination_revokes_despite_projection_failures(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.actors.actor_exit_ops import persist_actor_process_exit_stopped
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
        )
        from no1.daemon.server import handle_request
        from no1.kernel.actors import add_actor, update_actor as real_update_actor

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        add_actor(self.group, actor_id="peer2", runner="pty", runtime="codex")
        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )

        self.assertIsNotNone(
            finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=[str(event["id"])])
        )
        with patch(
            "no1.daemon.actors.actor_exit_ops.update_actor",
            side_effect=OSError("projection write failed"),
        ):
            self.assertFalse(
                persist_actor_process_exit_stopped(
                    group_id=self.group.group_id,
                    actor_id="peer1",
                    runner="pty",
                )
            )
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        for actor_id in ("peer1", "peer2"):
            self.assertIsNotNone(
                finalize_turn_delivery_success(self.group, actor_id=actor_id, event_ids=[str(event["id"])])
            )
        def fail_one_update(group, actor_id, patch_doc):
            if actor_id == "peer2":
                raise OSError("peer2 projection write failed")
            return real_update_actor(group, actor_id, patch_doc)

        with patch("no1.daemon.group.group_lifecycle_ops.update_actor", side_effect=fail_one_update):
            stopped, _ = handle_request(
                DaemonRequest(op="group_stop", args={"group_id": self.group.group_id, "by": "user"})
            )
        self.assertTrue(stopped.ok, getattr(stopped, "error", None))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))
        self.assertIsNone(get_current_turn_grant(self.group, "peer2"))

    def test_actor_remove_does_not_leak_grant_to_readded_identity(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
        )
        from no1.daemon.server import handle_request
        from no1.kernel.actors import add_actor
        from no1.kernel.group import load_group

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        self.assertIsNotNone(
            finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=[str(event["id"])])
        )
        removed, _ = handle_request(
            DaemonRequest(
                op="actor_remove",
                args={"group_id": self.group.group_id, "actor_id": "peer1", "by": "user"},
            )
        )
        self.assertTrue(removed.ok, getattr(removed, "error", None))
        reloaded = load_group(self.group.group_id)
        self.assertIsNotNone(reloaded)
        add_actor(reloaded, actor_id="peer1", runner="pty", runtime="codex")
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_partial_group_stop_failure_still_revokes_actor_snapshot(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.messaging.turn_provenance import (
            build_send_turn_provenance,
            finalize_turn_delivery_success,
            get_current_turn_grant,
        )
        from no1.daemon.server import handle_request
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="pty", runtime="codex")
        event = self._append(
            provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"})
        )
        self.assertIsNotNone(
            finalize_turn_delivery_success(self.group, actor_id="peer1", event_ids=[str(event["id"])])
        )
        with patch(
            "no1.daemon.group.group_lifecycle_ops.headless_runner.SUPERVISOR.stop_group",
            side_effect=RuntimeError("partial stop failed"),
        ):
            stopped, _ = handle_request(
                DaemonRequest(op="group_stop", args={"group_id": self.group.group_id, "by": "user"})
            )
        self.assertFalse(stopped.ok)
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_web_model_complete_turn_invalidates_current_grant(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            get_current_turn_grant,
            turn_delivery_completion_receipt,
        )
        from no1.daemon.runner_state_ops import write_headless_state
        from no1.daemon.server import handle_request
        from no1.kernel.actors import add_actor

        add_actor(self.group, actor_id="peer1", runner="headless", runtime="web_model")
        write_headless_state(self.group.group_id, "peer1")
        event = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"}))
        attempt = begin_turn_delivery_attempt(
            self.group,
            actor_id="peer1",
            event_ids=[str(event["id"])],
            binding={"transport": "web_model_pull", "turn_id": "turn-1"},
        )
        self.assertIsNotNone(attempt)
        self.assertIsNotNone(finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=attempt))
        completion_receipt = turn_delivery_completion_receipt(attempt)
        self.assertIsNotNone(completion_receipt)
        completed, _ = handle_request(
            DaemonRequest(
                op="web_model_runtime_complete_turn",
                args={
                    "group_id": self.group.group_id,
                    "actor_id": "peer1",
                    "by": "peer1",
                    "event_ids": [event["id"]],
                    "status": "done",
                    "completion_receipt": completion_receipt,
                },
            )
        )
        self.assertTrue(completed.ok, getattr(completed, "error", None))
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

    def test_codex_and_claude_completion_paths_invalidate_current_grant(self) -> None:
        from no1.daemon.claude_app_sessions import ClaudeAppSession
        from no1.daemon.codex_app_sessions import CodexAppSession
        from no1.daemon.messaging.turn_provenance import (
            begin_turn_delivery_attempt,
            build_send_turn_provenance,
            finalize_turn_delivery_attempt,
            get_current_turn_grant,
        )

        event = self._append(provenance=build_send_turn_provenance({"__turn_ingress": "web_user", "by": "user"}))

        def issue(binding):
            attempt = begin_turn_delivery_attempt(
                self.group,
                actor_id="peer1",
                event_ids=[str(event["id"])],
                binding=binding,
            )
            self.assertIsNotNone(attempt)
            grant = finalize_turn_delivery_attempt(self.group, actor_id="peer1", attempt=attempt)
            self.assertIsNotNone(grant)
            return attempt, grant

        for status in ("completed", "failed", "cancelled"):
            with self.subTest(provider="codex", status=status):
                attempt, grant = issue({"transport": "codex_app", "turn_generation": 1})
                codex = CodexAppSession(
                    group_id=self.group.group_id,
                    actor_id="peer1",
                    cwd=self.group.path,
                    env={},
                )
                codex._active_event_id = str(event["id"])
                codex._active_turn_generation = 1
                codex._active_delivery_attempt = attempt
                codex._active_grant_generation = int(grant["generation"])
                codex._handle_notification("turn/started", {"turn": {"id": f"codex-{status}"}})
                codex._handle_notification(
                    "turn/completed",
                    {"turn": {"id": f"codex-{status}", "status": status}},
                )
                self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        claude = ClaudeAppSession(group_id=self.group.group_id, actor_id="peer1", cwd=self.group.path, env={})
        for subtype in ("success", "error", "interrupted"):
            with self.subTest(provider="claude", subtype=subtype):
                attempt, grant = issue(
                    {"transport": "claude_app", "transport_epoch": 1, "turn_generation": 1}
                )
                claude._active_turn_id = f"claude-{subtype}"
                claude._active_event_id = str(event["id"])
                claude._active_turn_generation = 1
                claude._active_transport_epoch = 1
                claude._active_delivery_attempt = attempt
                claude._active_grant_generation = int(grant["generation"])
                claude._handle_result_event({"type": "result", "subtype": subtype})
                self.assertIsNone(get_current_turn_grant(self.group, "peer1"))

        attempt, grant = issue({"transport": "claude_app", "transport_epoch": 2, "turn_generation": 2})
        claude._active_turn_id = "claude-stream-turn"
        claude._active_event_id = str(event["id"])
        claude._active_turn_generation = 2
        claude._active_transport_epoch = 2
        claude._active_delivery_attempt = attempt
        claude._active_grant_generation = int(grant["generation"])
        claude._complete_turn_from_stream()
        self.assertIsNone(get_current_turn_grant(self.group, "peer1"))


if __name__ == "__main__":
    unittest.main()
