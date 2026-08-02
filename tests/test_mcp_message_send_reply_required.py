import os
import tempfile
import unittest
from unittest.mock import patch

from tests.mcp_router_harness import route_tool_call

# Env vars that _resolve_group_id / _resolve_self_actor_id read at runtime.
# Tests must isolate from the host environment to avoid group_id_mismatch.
_CLEAN_ENV = {"CCCC_GROUP_ID": "", "CCCC_ACTOR_ID": ""}


class TestMcpMessageSendReplyRequired(unittest.TestCase):
    def assert_post_message_nudge(self, result: dict) -> None:
        from no1.kernel.peer_insight import POST_MESSAGE_NUDGE

        self.assertEqual(
            result.get("post_message_nudge"),
            {"kind": "whole_situation_reconstruction", "message": POST_MESSAGE_NUDGE},
        )

    def test_message_send_coerces_reply_required_string(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp import common as mcp_common

        captured = {}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"event_id": "ev_test"}}

        with patch.dict(os.environ, _CLEAN_ENV, clear=False), \
             patch.object(mcp_common, "call_daemon", side_effect=_fake_call_daemon):
            out = mcp_server.handle_tool_call(
                "onecolleague_message_send",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "text": "hello",
                    "to": ["user"],
                    "reply_required": "true",
                },
            )

        self.assertEqual(out.get("event_id"), "ev_test")
        self.assert_post_message_nudge(out)
        req = captured.get("req") or {}
        self.assertEqual(req.get("op"), "actor_message_send")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertTrue(args.get("reply_required") is True)
        self.assertFalse(any(str(key).startswith("__") for key in args))

    def test_message_send_passes_refs(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp import common as mcp_common

        captured = {}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"event_id": "ev_test"}}

        refs = [{"kind": "presentation_ref", "slot_id": "slot-2", "label": "P2", "locator_label": "PDF p.12"}]

        with patch.dict(os.environ, _CLEAN_ENV, clear=False), \
             patch.object(mcp_common, "call_daemon", side_effect=_fake_call_daemon):
            out = mcp_server.handle_tool_call(
                "onecolleague_message_send",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "text": "hello",
                    "to": ["user"],
                    "refs": refs,
                },
            )

        self.assertEqual(out.get("event_id"), "ev_test")
        self.assert_post_message_nudge(out)
        req = captured.get("req") or {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("refs"), refs)

    def test_message_send_uses_cross_group_op_for_explicit_destination(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp import common as mcp_common

        captured = {}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"src_event": {"id": "src-1"}, "dst_event": {"id": "dst-1"}}}

        with patch.dict(os.environ, _CLEAN_ENV, clear=False), \
             patch.object(mcp_common, "call_daemon", side_effect=_fake_call_daemon):
            out = mcp_server.handle_tool_call(
                "onecolleague_message_send",
                {
                    "group_id": "g_runtime",
                    "dst_group_id": "g_selected",
                    "actor_id": "peer1",
                    "text": "hello",
                    "to": ["@foreman"],
                },
            )

        self.assertEqual((out.get("dst_event") or {}).get("id"), "dst-1")
        self.assert_post_message_nudge(out)
        req = captured.get("req") or {}
        self.assertEqual(req.get("op"), "actor_send_cross_group")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("group_id"), "g_runtime")
        self.assertEqual(args.get("dst_group_id"), "g_selected")
        self.assertEqual(args.get("to"), ["@foreman"])
        self.assertFalse(any(str(key).startswith("__") for key in args))

    def test_message_reply_passes_refs(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp import common as mcp_common

        captured = {}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"event_id": "ev_test"}}

        refs = [{"kind": "presentation_ref", "slot_id": "slot-4", "label": "P4", "locator_label": "Web"}]

        with patch.dict(os.environ, _CLEAN_ENV, clear=False), \
             patch.object(mcp_common, "call_daemon", side_effect=_fake_call_daemon):
            out = mcp_server.handle_tool_call(
                "onecolleague_message_reply",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "event_id": "ev_1",
                    "text": "reply",
                    "refs": refs,
                },
            )

        self.assertEqual(out.get("event_id"), "ev_test")
        self.assert_post_message_nudge(out)
        req = captured.get("req") or {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("refs"), refs)

    def test_message_reply_passes_completion_receipt(self) -> None:
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        captured = {}
        receipt = {"v": 1, "attempt_id": "attempt-1", "generation": 7}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"event_id": "ev_test"}}

        with patch.dict(os.environ, _CLEAN_ENV, clear=False), patch.object(
            mcp_common, "call_daemon", side_effect=_fake_call_daemon
        ):
            mcp_server.handle_tool_call(
                "onecolleague_message_reply",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "event_id": "ev_1",
                    "text": "reply",
                    "completion_receipt": receipt,
                },
            )

        args = (captured.get("req") or {}).get("args") or {}
        self.assertEqual(args.get("completion_receipt"), receipt)

    def test_runtime_complete_turn_passes_completion_receipt(self) -> None:
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        captured = {}
        receipt = {"v": 1, "attempt_id": "attempt-2", "generation": 8}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"status": "done"}}

        with patch.dict(os.environ, _CLEAN_ENV, clear=False), patch.object(
            mcp_common, "call_daemon", side_effect=_fake_call_daemon
        ):
            route_tool_call(
                "onecolleague_runtime_complete_turn",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "event_ids": ["ev_1"],
                    "status": "done",
                    "completion_receipt": receipt,
                },
            )

        req = captured.get("req") or {}
        self.assertEqual(req.get("op"), "web_model_runtime_complete_turn")
        self.assertEqual((req.get("args") or {}).get("completion_receipt"), receipt)

    def test_tracked_send_passes_task_contract_args(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp import common as mcp_common

        captured = {}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"task_id": "T001", "message_sent": True}}

        checklist = [{"text": "Check"}, {"text": "Report", "status": "pending"}]
        with patch.dict(os.environ, _CLEAN_ENV, clear=False), \
             patch.object(mcp_common, "call_daemon", side_effect=_fake_call_daemon):
            out = mcp_server.handle_tool_call(
                "onecolleague_tracked_send",
                {
                    "group_id": "g_test",
                    "actor_id": "foreman",
                    "title": "Review PR",
                    "text": "Please review this PR and report evidence.",
                    "to": "reviewer",
                    "outcome": "Review findings reported.",
                    "checklist": checklist,
                    "idempotency_key": "req-1",
                },
            )

        self.assertEqual(out.get("task_id"), "T001")
        self.assert_post_message_nudge(out)
        req = captured.get("req") or {}
        self.assertEqual(req.get("op"), "actor_tracked_send")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("to"), ["reviewer"])
        self.assertEqual(args.get("title"), "Review PR")
        self.assertEqual(args.get("checklist"), checklist)
        self.assertTrue(args.get("reply_required"))
        self.assertFalse(any(str(key).startswith("__") for key in args))

    def test_post_message_nudge_is_omitted_for_partial_or_unsent_results(self) -> None:
        from no1.ports.mcp.handlers.onecolleague_messaging import _with_post_message_nudge

        partial = {"task_id": "T001", "partial_failure": True, "message_sent": False}
        unsent = {"task_id": "T002", "message_sent": False}
        self.assertIs(_with_post_message_nudge(partial), partial)
        self.assertIs(_with_post_message_nudge(unsent), unsent)

    def test_tracked_send_persists_actor_provenance_through_daemon(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        def _local_call_daemon(req):
            response, _ = handle_request(DaemonRequest.model_validate(req))
            return response.model_dump(exclude_none=True)

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {**_CLEAN_ENV, "CCCC_HOME": td},
            clear=False,
        ):
            created, _ = handle_request(
                DaemonRequest(op="group_create", args={"title": "tracked-provenance", "by": "user"})
            )
            self.assertTrue(created.ok, getattr(created, "error", None))
            group_id = str((created.result or {}).get("group_id") or "")
            added, _ = handle_request(
                DaemonRequest(
                    op="actor_add",
                    args={
                        "group_id": group_id,
                        "actor_id": "foreman1",
                        "runtime": "codex",
                        "runner": "headless",
                        "by": "user",
                    },
                )
            )
            self.assertTrue(added.ok, getattr(added, "error", None))

            with patch.object(mcp_common, "call_daemon", side_effect=_local_call_daemon):
                result = mcp_server.handle_tool_call(
                    "onecolleague_tracked_send",
                    {
                        "group_id": group_id,
                        "actor_id": "foreman1",
                        "title": "Tracked request",
                        "text": "Record this task",
                        "to": ["user"],
                    },
                )

        event = result.get("event") if isinstance(result.get("event"), dict) else {}
        provenance = (event.get("data") or {}).get("turn_provenance") or {}
        self.assertEqual(provenance.get("origin"), "local_actor")
        self.assertFalse(bool(provenance.get("fresh_local_request")))

    def test_message_send_allows_codex_headless_actor(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        captured = {}

        def _fake_call_daemon(req):
            captured["req"] = req
            return {"ok": True, "result": {"event_id": "ev_headless"}}

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {**_CLEAN_ENV, "CCCC_HOME": td}, clear=False):
            create_resp, _ = handle_request(
                DaemonRequest.model_validate({"op": "group_create", "args": {"title": "headless-send", "topic": "", "by": "user"}})
            )
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add_resp, _ = handle_request(
                DaemonRequest.model_validate(
                    {
                        "op": "actor_add",
                        "args": {
                            "group_id": group_id,
                            "actor_id": "peer1",
                            "runtime": "codex",
                            "runner": "headless",
                            "by": "user",
                        },
                    }
                )
            )
            self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

            with patch.object(mcp_common, "call_daemon", side_effect=_fake_call_daemon):
                out = mcp_server.handle_tool_call(
                    "onecolleague_message_send",
                    {
                        "group_id": group_id,
                        "actor_id": "peer1",
                        "text": "hello",
                        "to": ["user"],
                    },
                )

        self.assertEqual(out.get("event_id"), "ev_headless")
        req = captured.get("req") or {}
        self.assertEqual(req.get("op"), "actor_message_send")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("group_id"), group_id)
        self.assertEqual(args.get("by"), "peer1")
        self.assertFalse(any(str(key).startswith("__") for key in args))


if __name__ == "__main__":
    unittest.main()
