import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestPeerInsightAdmission(unittest.TestCase):
    def setUp(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request

        self._home_vars = ("ONECOLLEAGUE_HOME", "CCCC_HOME")
        self._old_homes = {name: os.environ.get(name) for name in self._home_vars}
        self._td = tempfile.TemporaryDirectory()
        for name in self._home_vars:
            os.environ[name] = self._td.name
        self.addCleanup(self._cleanup)

        def call(op: str, args: dict):
            return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))[0]

        self.call = call
        created = call("group_create", {"title": "peer-insight", "by": "user"})
        self.assertTrue(created.ok, created.error)
        self.group_id = str(created.result["group_id"])
        for actor_id in ("peer1", "peer2"):
            added = call(
                "actor_add",
                {
                    "group_id": self.group_id,
                    "actor_id": actor_id,
                    "runtime": "codex",
                    "runner": "headless",
                    "by": "user",
                },
            )
            self.assertTrue(added.ok, added.error)

    def _cleanup(self) -> None:
        for name, old_home in self._old_homes.items():
            if old_home is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_home
        self._td.cleanup()

    def _group(self):
        from no1.kernel.group import load_group

        group = load_group(self.group_id)
        self.assertIsNotNone(group)
        return group

    def _ledger_count(self) -> int:
        from no1.kernel.inbox import iter_events

        return len(list(iter_events(self._group().ledger_path)))

    def _active_scope(self) -> Path:
        root = Path(self._td.name) / "active-scope"
        root.mkdir(exist_ok=True)
        group = self._group()
        group.doc["active_scope_key"] = "scope-peer-insight"
        group.doc["scopes"] = [
            {
                "scope_key": "scope-peer-insight",
                "url": str(root),
                "label": "peer-insight",
                "git_remote": "",
            }
        ]
        group.save()
        return root

    def _destination_group(self) -> str:
        created = self.call("group_create", {"title": "peer-insight-destination", "by": "user"})
        self.assertTrue(created.ok, created.error)
        group_id = str(created.result["group_id"])
        added = self.call(
            "actor_add",
            {
                "group_id": group_id,
                "actor_id": "peer3",
                "runtime": "codex",
                "runner": "headless",
                "by": "user",
            },
        )
        self.assertTrue(added.ok, added.error)
        return group_id

    def test_actor_peer_send_requires_insight_before_wake_or_ledger(self) -> None:
        from no1.kernel.peer_insight import (
            FIRST_PRINCIPLES_OUTCOME_KERNEL,
            PEER_INSIGHT_REQUIRED_ACTION,
            SUPERVISOR_MAGIC_KERNEL,
        )

        before = self._ledger_count()
        with patch("no1.daemon.server.auto_wake_recipients") as wake:
            response = self.call(
                "actor_message_send",
                {"group_id": self.group_id, "by": "peer1", "text": "review", "to": ["peer2"]},
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "peer_insight_required")
        self.assertEqual(response.error.details["new_side_effects"], False)
        self.assertEqual(response.error.details["recommended_action"], PEER_INSIGHT_REQUIRED_ACTION)
        self.assertIn(SUPERVISOR_MAGIC_KERNEL, PEER_INSIGHT_REQUIRED_ACTION)
        self.assertEqual(PEER_INSIGHT_REQUIRED_ACTION.count(FIRST_PRINCIPLES_OUTCOME_KERNEL), 1)
        self.assertIn("Do not repair the draft by adding a postscript", PEER_INSIGHT_REQUIRED_ACTION)
        self.assertIn("Insight is second in the JSON, not second in thought", PEER_INSIGHT_REQUIRED_ACTION)
        self.assertIn("step materially above the work unit being discussed", PEER_INSIGHT_REQUIRED_ACTION)
        wake.assert_not_called()
        self.assertEqual(self._ledger_count(), before)

    def test_post_message_nudge_audits_insight_origin_without_dynamic_scene_logic(self) -> None:
        from no1.kernel.peer_insight import POST_MESSAGE_NUDGE

        self.assertIn("Step outside its mental track now", POST_MESSAGE_NUDGE)
        self.assertIn("fresh owner accountable for the real outcome", POST_MESSAGE_NUDGE)
        self.assertIn("no loyalty to the exchange, its momentum, or its frame", POST_MESSAGE_NUDGE)
        self.assertIn("stayed beside the message instead of rising above its working level", POST_MESSAGE_NUDGE)
        self.assertIn("no higher-order perspective entered the exchange", POST_MESSAGE_NUDGE)
        self.assertIn("whether an unsettled decision needs another independent mind", POST_MESSAGE_NUDGE)
        self.assertIn("If nothing material changes, quietly resume", POST_MESSAGE_NUDGE)

    def test_actor_user_and_human_peer_messages_do_not_require_insight(self) -> None:
        actor = self.call(
            "actor_message_send",
            {"group_id": self.group_id, "by": "peer1", "text": "status", "to": ["user"]},
        )
        human = self.call(
            "user_message_send",
            {"group_id": self.group_id, "by": "user", "text": "please review", "to": ["peer2"]},
        )
        self.assertTrue(actor.ok, actor.error)
        self.assertTrue(human.ok, human.error)

    def test_actor_peer_send_persists_normalized_insight(self) -> None:
        response = self.call(
            "actor_message_send",
            {
                "group_id": self.group_id,
                "by": "peer1",
                "text": "review",
                "insight": "  The current plan may optimize the wrong boundary.  ",
                "to": ["peer2"],
            },
        )
        self.assertTrue(response.ok, response.error)
        self.assertEqual(
            response.result["event"]["data"]["insight"],
            "The current plan may optimize the wrong boundary.",
        )

    def test_tracked_send_missing_insight_does_not_create_task(self) -> None:
        from no1.kernel.context import ContextStorage

        before_ids = [task.id for task in ContextStorage(self._group()).list_tasks()]
        response = self.call(
            "actor_tracked_send",
            {
                "group_id": self.group_id,
                "by": "peer1",
                "title": "Review",
                "text": "review this",
                "to": ["peer2"],
            },
        )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "peer_insight_required")
        self.assertEqual([task.id for task in ContextStorage(self._group()).list_tasks()], before_ids)

    def test_reply_missing_insight_does_not_append_or_ack(self) -> None:
        original = self.call(
            "user_message_send",
            {"group_id": self.group_id, "by": "user", "text": "question", "to": ["peer1"]},
        )
        self.assertTrue(original.ok, original.error)
        peer_message = self.call(
            "user_message_send",
            {"group_id": self.group_id, "by": "user", "text": "peer question", "to": ["peer1", "peer2"]},
        )
        self.assertTrue(peer_message.ok, peer_message.error)
        before = self._ledger_count()
        response = self.call(
            "actor_message_reply",
            {
                "group_id": self.group_id,
                "by": "peer1",
                "reply_to": peer_message.result["event"]["id"],
                "text": "answer",
                "to": ["peer2"],
            },
        )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "peer_insight_required")
        self.assertEqual(self._ledger_count(), before)

    def test_file_missing_insight_precedes_path_and_blob_access(self) -> None:
        with patch("no1.daemon.messaging.chat_ops._open_active_scope_file") as open_file, patch(
            "no1.daemon.messaging.chat_ops.store_blob_bytes"
        ) as store_blob:
            response = self.call(
                "actor_file_send",
                {
                    "group_id": self.group_id,
                    "by": "peer1",
                    "path": "report.txt",
                    "text": "review",
                    "to": ["peer2"],
                },
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "peer_insight_required")
        open_file.assert_not_called()
        store_blob.assert_not_called()

    def test_file_send_rejects_symlink_without_blob_side_effects(self) -> None:
        root = self._active_scope()
        outside = Path(self._td.name) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (root / "report.txt").symlink_to(outside)

        with patch("no1.daemon.messaging.chat_ops.store_blob_bytes") as store_blob:
            response = self.call(
                "actor_file_send",
                {
                    "group_id": self.group_id,
                    "by": "peer1",
                    "path": "report.txt",
                    "text": "report",
                    "to": ["user"],
                },
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "not_found")
        store_blob.assert_not_called()

    def test_file_send_reads_bound_descriptor_after_path_replacement_without_reopen(self) -> None:
        from no1.daemon.messaging import chat_ops
        from no1.kernel.blobs import resolve_blob_attachment_path

        root = self._active_scope()
        report = root / "report.txt"
        replacement = root / "replacement.txt"
        report.write_text("original descriptor bytes", encoding="utf-8")
        replacement.write_text("replacement pathname bytes", encoding="utf-8")
        real_bind = chat_ops.bind_file_descriptor
        real_open = chat_ops.os.open
        leaf_opens = 0

        def replace_then_bind(pending, file_fd, opened_stat):
            chat_ops.os.replace(replacement, report)
            return real_bind(pending, file_fd, opened_stat)

        def track_open(path, *args, **kwargs):
            nonlocal leaf_opens
            if str(path) == "report.txt":
                leaf_opens += 1
            return real_open(path, *args, **kwargs)

        with patch.object(chat_ops, "bind_file_descriptor", side_effect=replace_then_bind), patch.object(
            chat_ops.os, "open", side_effect=track_open
        ):
            response = self.call(
                "actor_file_send",
                {
                    "group_id": self.group_id,
                    "by": "peer1",
                    "path": "report.txt",
                    "text": "report",
                    "to": ["user"],
                },
            )
        self.assertTrue(response.ok, response.error)
        attachment = response.result["event"]["data"]["attachments"][0]
        stored = resolve_blob_attachment_path(self._group(), rel_path=attachment["path"])
        self.assertEqual(stored.read_text(encoding="utf-8"), "original descriptor bytes")
        self.assertEqual(report.read_text(encoding="utf-8"), "replacement pathname bytes")
        self.assertEqual(leaf_opens, 1)

    def test_unknown_actor_closed_ingress_fails_closed(self) -> None:
        before = self._ledger_count()
        response = self.call(
            "actor_message_send",
            {"group_id": self.group_id, "by": "unknown", "text": "hello", "to": ["user"]},
        )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "invalid_actor_sender")
        self.assertEqual(self._ledger_count(), before)

    def test_reply_exact_replay_and_conflict_precede_new_admission(self) -> None:
        from no1.kernel.ledger import append_event
        from no1.kernel.ledger_segments import compress_sealed_segments, rotate_active_ledger

        original = self.call(
            "user_message_send",
            {"group_id": self.group_id, "by": "user", "text": "question", "to": ["peer1"]},
        )
        self.assertTrue(original.ok, original.error)
        payload = {
            "group_id": self.group_id,
            "by": "peer1",
            "reply_to": original.result["event"]["id"],
            "text": "answer",
            "insight": "The answer changes which boundary should own the decision.",
            "to": ["peer2"],
            "client_id": "reply-exact-1",
        }
        first = self.call("actor_message_reply", dict(payload))
        self.assertTrue(first.ok, first.error)
        group = self._group()
        self.assertTrue(rotate_active_ledger(group.path, reason="reply-exact-replay-test")["rotated"])
        self.assertEqual(compress_sealed_segments(group.path, keep_recent=0, force=True)["count"], 1)
        for index in range(801):
            append_event(
                group.ledger_path,
                kind="test.replay_noise",
                group_id=self.group_id,
                scope_key="",
                by="system",
                data={"text": f"replay-window-noise-{index}", "to": ["user"]},
            )
        before = self._ledger_count()
        with patch(
            "no1.daemon.messaging.chat_ops.issue_message_admission",
            side_effect=AssertionError("exact replay and conflict must not run admission"),
        ), patch(
            "no1.daemon.messaging.chat_ops.invalidate_turn_grant_from_completion_receipt",
            side_effect=AssertionError("exact replay and conflict must not invalidate grants"),
        ):
            replay = self.call("actor_message_reply", dict(payload))
            conflict = self.call(
                "actor_message_reply",
                {**payload, "insight": "A different durable message fact."},
            )
        self.assertTrue(replay.ok, replay.error)
        self.assertTrue(replay.result["replayed"])
        self.assertEqual(replay.result["event"]["id"], first.result["event"]["id"])
        self.assertFalse(conflict.ok)
        self.assertEqual(conflict.error.code, "message_replay_conflict")
        self.assertEqual(self._ledger_count(), before)

    def test_reply_replay_is_sender_scoped_and_empty_client_id_always_appends(self) -> None:
        original = self.call(
            "user_message_send",
            {"group_id": self.group_id, "by": "user", "text": "question", "to": ["peer1", "peer2"]},
        )
        self.assertTrue(original.ok, original.error)
        base = {
            "group_id": self.group_id,
            "reply_to": original.result["event"]["id"],
            "text": "answer",
            "to": ["user"],
        }
        peer1 = self.call("actor_message_reply", {**base, "by": "peer1", "client_id": "sender-scoped"})
        peer2 = self.call("actor_message_reply", {**base, "by": "peer2", "client_id": "sender-scoped"})
        self.assertTrue(peer1.ok, peer1.error)
        self.assertTrue(peer2.ok, peer2.error)
        self.assertNotEqual(peer1.result["event"]["id"], peer2.result["event"]["id"])
        self.assertFalse(bool(peer2.result.get("replayed")))

        first = self.call("actor_message_reply", {**base, "by": "peer1"})
        second = self.call("actor_message_reply", {**base, "by": "peer1"})
        self.assertTrue(first.ok, first.error)
        self.assertTrue(second.ok, second.error)
        self.assertNotEqual(first.result["event"]["id"], second.result["event"]["id"])
        self.assertFalse(bool(second.result.get("replayed")))

    def test_tracked_partial_task_is_preserved_when_retry_fails_admission(self) -> None:
        from no1.contracts.v1 import DaemonError, DaemonResponse
        from no1.kernel.context import ContextStorage

        payload = {
            "group_id": self.group_id,
            "by": "peer1",
            "title": "Review boundary",
            "text": "review this",
            "insight": "The task may preserve the wrong authorization owner.",
            "to": ["@peers"],
            "idempotency_key": "partial-peer-insight",
        }
        with patch(
            "no1.daemon.messaging.chat_ops.handle_send",
            return_value=DaemonResponse(
                ok=False,
                error=DaemonError(code="send_failed", message="simulated message failure"),
            ),
        ):
            first = self.call("actor_tracked_send", dict(payload))
        self.assertTrue(first.ok, first.error)
        self.assertTrue(first.result["task_created"])
        self.assertTrue(first.result["partial_failure"])
        task_id = first.result["task_id"]
        before_ids = [task.id for task in ContextStorage(self._group()).list_tasks()]
        retry = self.call("actor_tracked_send", {key: value for key, value in payload.items() if key != "insight"})
        self.assertFalse(retry.ok)
        self.assertEqual(retry.error.code, "peer_insight_required")
        self.assertTrue(retry.error.details["existing_task_preserved"])
        self.assertEqual(retry.error.details["existing_task_id"], task_id)
        self.assertEqual([task.id for task in ContextStorage(self._group()).list_tasks()], before_ids)

    def test_cross_group_source_sender_change_rejects_before_message_side_effects(self) -> None:
        from no1.daemon.ops import maintenance_ops
        from no1.kernel.actors import update_actor

        destination_id = self._destination_group()
        real_issue = maintenance_ops.issue_message_admission

        def issue_then_change_sender(*args, **kwargs):
            pending = real_issue(*args, **kwargs)
            update_actor(self._group(), "peer1", {"title": "changed-after-admission"})
            return pending

        before_source = self._ledger_count()
        with patch.object(maintenance_ops, "issue_message_admission", side_effect=issue_then_change_sender):
            response = self.call(
                "actor_send_cross_group",
                {
                    "group_id": self.group_id,
                    "dst_group_id": destination_id,
                    "by": "peer1",
                    "text": "cross-group review",
                    "insight": "The source sender remains part of the authority fact.",
                    "to": ["peer3"],
                },
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "stale_message_admission")
        self.assertEqual(self._ledger_count(), before_source)

    def test_cross_group_destination_audience_change_rejects_before_message_side_effects(self) -> None:
        from no1.daemon.ops import maintenance_ops
        from no1.kernel.actors import update_actor
        from no1.kernel.group import load_group
        from no1.kernel.inbox import iter_events

        destination_id = self._destination_group()
        real_issue = maintenance_ops.issue_message_admission

        def issue_then_change_audience(*args, **kwargs):
            pending = real_issue(*args, **kwargs)
            destination = load_group(destination_id)
            self.assertIsNotNone(destination)
            update_actor(destination, "peer3", {"title": "changed-after-admission"})
            return pending

        destination = load_group(destination_id)
        self.assertIsNotNone(destination)
        before_source = self._ledger_count()
        before_destination = len(list(iter_events(destination.ledger_path)))
        with patch.object(maintenance_ops, "issue_message_admission", side_effect=issue_then_change_audience):
            response = self.call(
                "actor_send_cross_group",
                {
                    "group_id": self.group_id,
                    "dst_group_id": destination_id,
                    "by": "peer1",
                    "text": "cross-group review",
                    "insight": "The destination audience remains part of the authority fact.",
                    "to": ["peer3"],
                },
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "stale_message_admission")
        self.assertEqual(self._ledger_count(), before_source)
        self.assertEqual(len(list(iter_events(destination.ledger_path))), before_destination)

    def test_peer_messaging_tools_share_runtime_insight_guidance(self) -> None:
        from no1.ports.mcp.toolspecs import MCP_TOOLS

        names = {
            "onecolleague_group_bridge_session_send",
            "onecolleague_group_bridge_remote_send",
            "onecolleague_message_send",
            "onecolleague_tracked_send",
            "onecolleague_message_reply",
            "onecolleague_file",
        }
        specs = {spec["name"]: spec for spec in MCP_TOOLS if spec.get("name") in names}
        self.assertEqual(set(specs), names)
        for name, spec in specs.items():
            properties = spec["inputSchema"]["properties"]
            insight = properties["payload"]["properties"]["insight"] if "payload" in properties else properties["insight"]
            copy = str(insight.get("description") or "").lower()
            self.assertIn("insight is second in the json, not second in thought", copy, name)
            self.assertNotIn("insight is required", copy, name)

    def test_direct_group_bridge_admission_precedes_session_transport(self) -> None:
        args = {
            "group_id": self.group_id,
            "by": "peer1",
            "local_endpoint": "https://local.example.test/api/v1/group-bridge/session",
            "remote_group_id": "remote-group",
            "remote_peer_id": "remote-peer",
            "remote_endpoint": "https://remote.example.test/api/v1/group-bridge/session",
            "client_nonce": "A" * 43,
            "payload": {
                "text": "remote review",
                "format": "plain",
                "priority": "normal",
                "reply_required": False,
                "source_by": "peer1",
            },
        }
        with patch("no1.daemon.group_bridge.ops.send_group_bridge_session_message") as transport:
            response = self.call("actor_group_bridge_session_send", args)
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "peer_insight_required")
        transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
