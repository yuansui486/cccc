import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.mcp_router_harness import route_tool_call as _route_tool_call


def route_tool_call(name: str, arguments: dict) -> dict:
    group_id = str(arguments.get("group_id") or "").strip()
    actor_id = str(arguments.get("actor_id") or "").strip()
    if not group_id or not actor_id:
        return _route_tool_call(name, arguments)
    from no1.ports.mcp.common import runtime_context_override

    with runtime_context_override(group_id=group_id, actor_id=actor_id, source="local_mcp"):
        return _route_tool_call(name, arguments)


class TestMcpToolBoolCoercion(unittest.TestCase):
    def test_headless_codex_message_send_is_allowed(self) -> None:
        from no1.ports.mcp.handlers import onecolleague_messaging

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "load_group", return_value=_FakeGroup()), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "codex", "runner": "headless"}
        ), patch.object(onecolleague_messaging, "_call_daemon_or_raise", return_value={"ok": True, "kind": "chat.message"}) as call_daemon:
            result = route_tool_call(
                "onecolleague_message_send",
                {"group_id": "g_test", "actor_id": "peer1", "text": "hello", "to": ["user"]},
            )
        self.assertEqual(result.get("kind"), "chat.message")
        self.assertEqual(call_daemon.call_args.args[0]["op"], "actor_message_send")

    def test_headless_codex_message_reply_is_allowed(self) -> None:
        from no1.ports.mcp.handlers import onecolleague_messaging

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "load_group", return_value=_FakeGroup()), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "codex", "runner": "headless"}
        ), patch.object(onecolleague_messaging, "_call_daemon_or_raise", return_value={"ok": True, "kind": "chat.message"}) as call_daemon:
            result = route_tool_call(
                "onecolleague_message_reply",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "reply_to": "ev_1",
                    "text": "hello",
                    "to": ["user"],
                },
            )
        self.assertEqual(result.get("kind"), "chat.message")
        self.assertEqual(call_daemon.call_args.args[0]["op"], "actor_message_reply")

    def test_headless_claude_message_send_is_allowed(self) -> None:
        from no1.ports.mcp.handlers import onecolleague_messaging

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "load_group", return_value=_FakeGroup()), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "claude", "runner": "headless"}
        ), patch.object(onecolleague_messaging, "_call_daemon_or_raise", return_value={"ok": True, "kind": "chat.message"}) as call_daemon:
            result = route_tool_call(
                "onecolleague_message_send",
                {"group_id": "g_test", "actor_id": "peer1", "text": "hello", "to": ["user"]},
            )
        self.assertEqual(result.get("kind"), "chat.message")
        self.assertEqual(call_daemon.call_args.args[0]["op"], "actor_message_send")

    def test_headless_claude_message_reply_is_allowed(self) -> None:
        from no1.ports.mcp.handlers import onecolleague_messaging

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "load_group", return_value=_FakeGroup()), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "claude", "runner": "headless"}
        ), patch.object(onecolleague_messaging, "_call_daemon_or_raise", return_value={"ok": True, "kind": "chat.message"}) as call_daemon:
            result = route_tool_call(
                "onecolleague_message_reply",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "reply_to": "ev_1",
                    "text": "hello",
                    "to": ["user"],
                },
            )
        self.assertEqual(result.get("kind"), "chat.message")
        self.assertEqual(call_daemon.call_args.args[0]["op"], "actor_message_reply")

    def test_file_send_delegates_path_validation_to_closed_daemon_op(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_messaging

        with tempfile.TemporaryDirectory() as td:
            scope_root = os.path.join(td, "scope")
            outside_root = os.path.join(td, "outside")
            os.makedirs(scope_root, exist_ok=True)
            os.makedirs(outside_root, exist_ok=True)
            outside_file = os.path.join(outside_root, "note.txt")
            with open(outside_file, "w", encoding="utf-8") as f:
                f.write("x")
            captured: dict[str, object] = {}

            def _reject_path(req: dict[str, object]) -> dict[str, object]:
                captured.update(req)
                raise mcp_server.MCPError(code="invalid_path", message="path must be under the active scope")

            with patch.object(onecolleague_messaging, "_call_daemon_or_raise", side_effect=_reject_path):
                with self.assertRaises(mcp_server.MCPError) as cm:
                    route_tool_call(
                        "onecolleague_file",
                        {
                            "action": "send",
                            "group_id": "g_test",
                            "actor_id": "peer1",
                            "path": outside_file,
                            "text": "hello",
                        },
                    )
            self.assertEqual(cm.exception.code, "invalid_path")
            self.assertEqual(captured.get("op"), "actor_file_send")
            self.assertEqual((captured.get("args") or {}).get("path"), outside_file)

    def test_file_send_forwards_scope_path_without_adapter_blob_side_effects(self) -> None:
        from no1.kernel.peer_insight import POST_MESSAGE_NUDGE
        from no1.ports.mcp.handlers import onecolleague_messaging

        with tempfile.TemporaryDirectory() as td:
            scope_root = Path(td) / "scope"
            scope_root.mkdir()
            report_path = scope_root / "report.md"
            report_path.write_text("# Report\n\nok\n", encoding="utf-8")
            captured: dict[str, object] = {}

            def _fake_call(payload: dict[str, object]) -> dict[str, object]:
                captured.update(payload)
                return {"ok": True, "event_id": "ev-file"}

            with patch.object(onecolleague_messaging, "_call_daemon_or_raise", side_effect=_fake_call):
                out = route_tool_call(
                    "onecolleague_file",
                    {
                        "action": "send",
                        "group_id": "g_test",
                        "actor_id": "peer1",
                        "path": "report.md",
                        "text": "final report",
                        "to": ["user"],
                    },
                )

            self.assertTrue(out.get("ok"))
            self.assertEqual(
                out.get("post_message_nudge"),
                {"kind": "whole_situation_reconstruction", "message": POST_MESSAGE_NUDGE},
            )
            self.assertEqual(captured.get("op"), "actor_file_send")
            args = captured.get("args")
            self.assertIsInstance(args, dict)
            self.assertEqual(args.get("text"), "final report")
            self.assertEqual(args.get("by"), "peer1")
            self.assertEqual(args.get("to"), ["user"])
            self.assertEqual(args.get("path"), "report.md")
            self.assertNotIn("attachments", args)

    def test_blob_read_reads_blob_attachment_with_limit(self) -> None:
        from no1.kernel.blobs import store_blob_bytes
        from no1.ports.mcp.handlers import onecolleague_messaging

        class _FakeGroup:
            def __init__(self, root: str) -> None:
                self.group_id = "g_test"
                self.path = Path(root)
                self.doc = {}

        with tempfile.TemporaryDirectory() as td:
            group = _FakeGroup(td)
            att = store_blob_bytes(group, data="hello world".encode("utf-8"), filename="note.txt", mime_type="text/plain")

            with patch.object(onecolleague_messaging, "load_group", return_value=group):
                out = onecolleague_messaging.blob_read(group_id="g_test", rel_path=str(att.get("path")), max_bytes=5)

            self.assertEqual(out.get("text"), "hello")
            self.assertTrue(out.get("truncated"))
            self.assertEqual(out.get("bytes"), 11)
            self.assertTrue(str(out.get("path") or "").endswith("note.txt"))

    def test_repo_edit_requires_web_model_actor_even_when_called_directly(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        class _FakeGroup:
            pass

        with runtime_context_override(group_id="g_test", actor_id="peer1", source="local_mcp"):
            with patch.object(mcp_server, "load_group", return_value=_FakeGroup()), patch.object(
                mcp_server, "find_actor", return_value={"id": "peer1", "runtime": "codex", "runner": "headless"}
            ), patch.object(mcp_server, "repo_tool", return_value={"ok": True}) as mock_repo_tool:
                with self.assertRaises(mcp_server.MCPError) as cm:
                    mcp_server.handle_tool_call(
                        "onecolleague_repo_edit",
                        {"action": "write", "path": "notes.txt", "content": "blocked"},
                    )

        self.assertEqual(cm.exception.code, "invalid_actor_runtime")
        mock_repo_tool.assert_not_called()

    def test_repo_edit_allows_web_model_actor(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        class _FakeGroup:
            pass

        with runtime_context_override(group_id="g_test", actor_id="peer1", source="local_mcp"):
            with patch.object(mcp_server, "load_group", return_value=_FakeGroup()), patch.object(
                mcp_server, "find_actor", return_value={"id": "peer1", "runtime": "web_model", "runner": "headless"}
            ), patch.object(mcp_server, "repo_tool", return_value={"ok": True}) as mock_repo_tool:
                result = mcp_server.handle_tool_call(
                    "onecolleague_repo_edit",
                    {"action": "write", "path": "notes.txt", "content": "ok"},
                )

        self.assertEqual(result.get("ok"), True)
        mock_repo_tool.assert_called_once()

    def test_message_send_normalizes_double_escaped_newlines(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_messaging

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "event_id": "ev_test"}

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "_call_daemon_or_raise", side_effect=_fake_call), patch.object(
            onecolleague_messaging, "load_group", return_value=_FakeGroup()
        ), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "codex"}
        ):
            route_tool_call(
                "onecolleague_message_send",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "text": "line1\\nline2\\tindent",
                    "to": ["user"],
                },
            )

        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("text"), "line1\nline2\tindent")

    def test_message_reply_keeps_normal_newlines_idempotent(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_messaging

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "event_id": "ev_test"}

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "_call_daemon_or_raise", side_effect=_fake_call), patch.object(
            onecolleague_messaging, "load_group", return_value=_FakeGroup()
        ), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "claude"}
        ):
            route_tool_call(
                "onecolleague_message_reply",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "reply_to": "ev_1",
                    "text": "line1\nline2",
                    "to": ["user"],
                },
            )

        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("text"), "line1\nline2")

    def test_message_send_keeps_windows_path_for_non_codex_runtime(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_messaging

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "event_id": "ev_test"}

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "_call_daemon_or_raise", side_effect=_fake_call), patch.object(
            onecolleague_messaging, "load_group", return_value=_FakeGroup()
        ), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "claude"}
        ):
            route_tool_call(
                "onecolleague_message_send",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "text": r"C:\\temp\\new",
                    "to": ["user"],
                },
            )

        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("text"), r"C:\\temp\\new")

    def test_message_send_keeps_literal_backslash_n_for_codex_runtime(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_messaging

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "event_id": "ev_test"}

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "_call_daemon_or_raise", side_effect=_fake_call), patch.object(
            onecolleague_messaging, "load_group", return_value=_FakeGroup()
        ), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "codex"}
        ):
            route_tool_call(
                "onecolleague_message_send",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "text": r"literal \\n path C:\\temp\\new",
                    "to": ["user"],
                },
            )

        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("text"), r"literal \\n path C:\\temp\\new")

    def test_message_reply_keeps_literal_backslash_t_for_codex_runtime(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_messaging

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "event_id": "ev_test"}

        class _FakeGroup:
            pass

        with patch.object(onecolleague_messaging, "_call_daemon_or_raise", side_effect=_fake_call), patch.object(
            onecolleague_messaging, "load_group", return_value=_FakeGroup()
        ), patch.object(
            onecolleague_messaging, "find_actor", return_value={"id": "peer1", "runtime": "codex"}
        ):
            route_tool_call(
                "onecolleague_message_reply",
                {
                    "group_id": "g_test",
                    "actor_id": "peer1",
                    "reply_to": "ev_1",
                    "text": r"regex \\t token",
                    "to": ["user"],
                },
            )

        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("text"), r"regex \\t token")


    def test_notify_send_requires_ack_string_false(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_self_actor_id", return_value="peer1"
        ), patch.object(mcp_server, "notify_send", return_value={"ok": True}) as mock_notify_send:
            route_tool_call(
                "onecolleague_notify",
                {
                    "action": "send",
                    "kind": "info",
                    "title": "t",
                    "message": "m",
                    "requires_ack": "false",
                },
            )
            self.assertTrue(mock_notify_send.called)
            kwargs = mock_notify_send.call_args.kwargs
            self.assertEqual(kwargs.get("group_id"), "g_test")
            self.assertEqual(kwargs.get("actor_id"), "peer1")
            self.assertFalse(bool(kwargs.get("requires_ack")))

    def test_terminal_tail_strip_ansi_string_false(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_self_actor_id", return_value="peer1"
        ), patch.object(mcp_server, "terminal_tail", return_value={"ok": True}) as mock_terminal_tail:
            route_tool_call(
                "onecolleague_terminal",
                {
                    "action": "tail",
                    "target_actor_id": "peer2",
                    "strip_ansi": "false",
                },
            )
            self.assertTrue(mock_terminal_tail.called)
            kwargs = mock_terminal_tail.call_args.kwargs
            self.assertEqual(kwargs.get("group_id"), "g_test")
            self.assertEqual(kwargs.get("actor_id"), "peer1")
            self.assertEqual(kwargs.get("target_actor_id"), "peer2")
            self.assertFalse(bool(kwargs.get("strip_ansi")))

    def test_space_artifact_defaults_to_async_wait_false(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_caller_from_by", return_value="peer1"
        ), patch.object(mcp_server, "space_artifact", return_value={"ok": True}) as mock_space_artifact:
            route_tool_call(
                "onecolleague_space",
                {
                    "action": "artifact",
                    "sub_action": "generate",
                    "lane": "work",
                    "kind": "slide_deck",
                },
            )
            kwargs = mock_space_artifact.call_args.kwargs
            self.assertEqual(kwargs.get("group_id"), "g_test")
            self.assertEqual(kwargs.get("by"), "peer1")
            self.assertFalse(bool(kwargs.get("wait")))

    def test_space_list_fresh_bool_coercion_reaches_both_handlers(self) -> None:
        from no1.ports.mcp import server as mcp_server

        cases = (
            ("sources", None, False),
            ("sources", "true", True),
            ("sources", "false", False),
            ("artifact", None, False),
            ("artifact", "true", True),
            ("artifact", "false", False),
        )
        for action, raw_fresh, expected in cases:
            with self.subTest(action=action, fresh=raw_fresh), patch.object(
                mcp_server, "_resolve_group_id", return_value="g_test"
            ), patch.object(mcp_server, "_resolve_caller_from_by", return_value="peer1"), patch.object(
                mcp_server, "space_sources", return_value={"ok": True}
            ) as mock_sources, patch.object(
                mcp_server, "space_artifact", return_value={"ok": True}
            ) as mock_artifact:
                arguments = {
                    "action": action,
                    "sub_action": "list",
                    "lane": "work",
                }
                if raw_fresh is not None:
                    arguments["fresh"] = raw_fresh
                route_tool_call("onecolleague_space", arguments)

                called = mock_sources if action == "sources" else mock_artifact
                self.assertEqual(called.call_args.kwargs.get("fresh"), expected)

    def test_space_handlers_only_send_fresh_for_list_actions(self) -> None:
        from no1.ports.mcp.handlers import onecolleague_space

        captured = []

        def _fake_daemon(req, **_kwargs):
            captured.append(req)
            return {"ok": True}

        with patch.object(onecolleague_space, "_call_daemon_or_raise", side_effect=_fake_daemon):
            onecolleague_space.space_sources(group_id="g_test", by="peer1", action="list", fresh=True)
            onecolleague_space.space_sources(group_id="g_test", by="peer1", action="list", fresh=False)
            onecolleague_space.space_sources(group_id="g_test", by="peer1", action="refresh", fresh=True)
            onecolleague_space.space_artifact(group_id="g_test", by="peer1", action="list", fresh=True)
            onecolleague_space.space_artifact(group_id="g_test", by="peer1", action="list", fresh=False)
            onecolleague_space.space_artifact(
                group_id="g_test",
                by="peer1",
                action="generate",
                kind="report",
                fresh=True,
            )

        args = [req.get("args") if isinstance(req.get("args"), dict) else {} for req in captured]
        self.assertEqual([item.get("fresh") for item in args[:2]], [True, False])
        self.assertNotIn("fresh", args[2])
        self.assertEqual([item.get("fresh") for item in args[3:5]], [True, False])
        self.assertNotIn("fresh", args[5])

    def test_space_artifact_infers_generate_when_action_missing(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_caller_from_by", return_value="peer1"
        ), patch.object(mcp_server, "space_artifact", return_value={"ok": True}) as mock_space_artifact:
            route_tool_call(
                "onecolleague_space",
                {
                    "action": "artifact",
                    "lane": "work",
                    "kind": "study_guide",
                    "save_to_space": "true",
                    "source": "/tmp/notes.md",
                },
            )
            kwargs = mock_space_artifact.call_args.kwargs
            self.assertEqual(kwargs.get("action"), "generate")
            options = kwargs.get("options") if isinstance(kwargs.get("options"), dict) else {}
            self.assertEqual(str(options.get("source") or ""), "/tmp/notes.md")

    def test_space_artifact_top_level_language_is_mapped_into_options(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_caller_from_by", return_value="peer1"
        ), patch.object(mcp_server, "space_artifact", return_value={"ok": True}) as mock_space_artifact:
            route_tool_call(
                "onecolleague_space",
                {
                    "action": "artifact",
                    "lane": "work",
                    "kind": "report",
                    "language": "zh-CN",
                    "source": "/tmp/notes.md",
                },
            )
            kwargs = mock_space_artifact.call_args.kwargs
            options = kwargs.get("options") if isinstance(kwargs.get("options"), dict) else {}
            self.assertEqual(str(options.get("language") or ""), "zh-CN")

    def test_space_artifact_language_infers_from_cjk_source_file(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "zh_notes.md")
            with open(src, "w", encoding="utf-8") as f:
                f.write("这是中文内容\n用于测试语言推断。\n")
            with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
                mcp_server, "_resolve_caller_from_by", return_value="peer1"
            ), patch.object(mcp_server, "space_artifact", return_value={"ok": True}) as mock_space_artifact:
                route_tool_call(
                    "onecolleague_space",
                    {
                        "action": "artifact",
                        "lane": "work",
                        "kind": "report",
                        "source": src,
                    },
                )
                kwargs = mock_space_artifact.call_args.kwargs
                options = kwargs.get("options") if isinstance(kwargs.get("options"), dict) else {}
                self.assertEqual(str(options.get("language") or ""), "zh-CN")

    def test_space_ingest_top_level_fields_auto_pack_payload(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_caller_from_by", return_value="peer1"
        ), patch.object(mcp_server, "space_ingest", return_value={"ok": True}) as mock_space_ingest:
            route_tool_call(
                "onecolleague_space",
                {
                    "action": "ingest",
                    "lane": "work",
                    "source_type": "file",
                    "url": "/tmp/spec.md",
                    "title": "Spec",
                },
            )
            kwargs = mock_space_ingest.call_args.kwargs
            self.assertEqual(str(kwargs.get("kind") or ""), "resource_ingest")
            payload = kwargs.get("payload") if isinstance(kwargs.get("payload"), dict) else {}
            self.assertEqual(str(payload.get("source_type") or ""), "file")
            self.assertEqual(str(payload.get("file_path") or ""), "/tmp/spec.md")
            self.assertEqual(str(payload.get("title") or ""), "Spec")

    def test_space_query_source_ids_option_is_normalized(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "space_query", return_value={"ok": True}
        ) as mock_space_query:
            route_tool_call(
                "onecolleague_space",
                {
                    "action": "query",
                    "lane": "work",
                    "query": "summarize",
                    "options": {"source_ids": [" src_1 ", "src_2"]},
                },
            )
            kwargs = mock_space_query.call_args.kwargs
            options = kwargs.get("options") if isinstance(kwargs.get("options"), dict) else {}
            self.assertEqual(options.get("source_ids"), ["src_1", "src_2"])

    def test_space_query_rejects_top_level_language(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"):
            with self.assertRaises(mcp_server.MCPError) as cm:
                route_tool_call(
                    "onecolleague_space",
                    {
                        "action": "query",
                        "lane": "work",
                        "query": "summarize",
                        "language": "zh-CN",
                    },
                )
        self.assertEqual(cm.exception.code, "invalid_request")
        self.assertIn("language/lang", str(cm.exception.message))

    def test_space_query_rejects_unsupported_options(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"):
            with self.assertRaises(mcp_server.MCPError) as cm:
                route_tool_call(
                    "onecolleague_space",
                    {
                        "action": "query",
                        "lane": "work",
                        "query": "summarize",
                        "options": {"top_k": 5},
                    },
                )
        self.assertEqual(cm.exception.code, "invalid_request")
        self.assertIn("unsupported options", str(cm.exception.message))

    def test_space_artifact_wait_true_uses_extended_daemon_timeout(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_space

        captured = {}

        def _fake_daemon(req, *, timeout_s=60.0):
            captured["req"] = req
            captured["timeout_s"] = float(timeout_s)
            return {"ok": True, "status": "completed"}

        with patch.object(onecolleague_space, "_call_daemon_or_raise", side_effect=_fake_daemon):
            mcp_server.space_artifact(
                group_id="g_test",
                by="peer1",
                action="generate",
                kind="slide_deck",
                wait=True,
                timeout_seconds=120.0,
            )
        self.assertGreaterEqual(float(captured.get("timeout_s") or 0.0), 150.0)

    def test_space_artifact_audio_forces_async_even_if_wait_true(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.handlers import onecolleague_space

        captured = {}

        def _fake_daemon(req, *, timeout_s=60.0):
            captured["req"] = req
            captured["timeout_s"] = float(timeout_s)
            return {"ok": True, "status": "accepted"}

        with patch.object(onecolleague_space, "_call_daemon_or_raise", side_effect=_fake_daemon):
            mcp_server.space_artifact(
                group_id="g_test",
                by="peer1",
                action="generate",
                kind="audio",
                wait=True,
                timeout_seconds=120.0,
            )
        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertFalse(bool(args.get("wait")))
        self.assertGreaterEqual(float(captured.get("timeout_s") or 0.0), 120.0)

    def test_memory_write_routes_to_daemon(self) -> None:
        from no1.ports.mcp import server as mcp_server

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "status": "written"}

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_call_daemon_or_raise", side_effect=_fake_call
        ):
            mcp_server.handle_tool_call(
                "onecolleague_memory",
                {"action": "write", "target": "daily", "date": "2026-03-03", "content": "x"},
            )

        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        self.assertEqual(req.get("op"), "memory_reme_write")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("group_id"), "g_test")
        self.assertEqual(args.get("target"), "daily")
        self.assertEqual(args.get("date"), "2026-03-03")

    def test_memory_get_missing_path_raises_validation_error(self) -> None:
        from no1.ports.mcp import server as mcp_server

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"):
            with self.assertRaises(mcp_server.MCPError) as cm:
                mcp_server.handle_tool_call("onecolleague_memory", {"action": "get"})
        self.assertEqual(cm.exception.code, "validation_error")

    def test_memory_index_sync_routes_to_daemon(self) -> None:
        from no1.ports.mcp import server as mcp_server

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "indexed_files": 2}

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_call_daemon_or_raise", side_effect=_fake_call
        ):
            route_tool_call("onecolleague_memory_admin", {"action": "index_sync", "mode": "rebuild"})
        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        self.assertEqual(req.get("op"), "memory_reme_index_sync")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("group_id"), "g_test")
        self.assertEqual(args.get("mode"), "rebuild")

    def test_memory_context_check_routes_to_daemon(self) -> None:
        from no1.ports.mcp import server as mcp_server

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "needs_compaction": False}

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_call_daemon_or_raise", side_effect=_fake_call
        ):
            route_tool_call(
                "onecolleague_memory_admin",
                {
                    "action": "context_check",
                    "messages": [{"role": "user", "content": "hello"}],
                    "keep_recent_tokens": 2048,
                },
            )
        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        self.assertEqual(req.get("op"), "memory_reme_context_check")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(args.get("group_id"), "g_test")
        messages = args.get("messages") if isinstance(args.get("messages"), list) else []
        self.assertEqual(len(messages), 1)

    def test_memory_daily_flush_coerces_return_prompt_bool(self) -> None:
        from no1.ports.mcp import server as mcp_server

        captured = {}

        def _fake_call(req):
            captured["req"] = req
            return {"ok": True, "status": "silent"}

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_call_daemon_or_raise", side_effect=_fake_call
        ):
            route_tool_call(
                "onecolleague_memory_admin",
                {"action": "daily_flush", "messages": [{"role": "user", "content": "h"}], "return_prompt": "false"},
            )
        req = captured.get("req") if isinstance(captured.get("req"), dict) else {}
        self.assertEqual(req.get("op"), "memory_reme_daily_flush")
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertFalse(bool(args.get("return_prompt")))

if __name__ == "__main__":
    unittest.main()
