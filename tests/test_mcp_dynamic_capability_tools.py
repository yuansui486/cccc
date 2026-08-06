from __future__ import annotations

import ast
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from no1.ports.mcp.common import MCPError


class TestMcpDynamicCapabilityTools(unittest.TestCase):
    def test_capability_use_rejects_voice_secretary_product_without_enabling_pack(self) -> None:
        from no1.ports.mcp.handlers.onecolleague_capability import capability_use

        with patch(
            "no1.ports.mcp.handlers.onecolleague_capability.capability_state",
            side_effect=AssertionError("product rejection must not read capability state"),
        ), patch(
            "no1.ports.mcp.handlers.onecolleague_capability.capability_enable",
            side_effect=AssertionError("product rejection must not enable a compatibility pack"),
        ), patch("no1.ports.mcp.server.handle_tool_call") as handle_tool_call:
            with self.assertRaises(MCPError) as caught:
                capability_use(
                    group_id="g1",
                    by="voice-secretary",
                    actor_id="voice-secretary",
                    capability_id="",
                    tool_name="onecolleague_voice_secretary_document",
                    tool_arguments={"action": "read_new_input"},
                )

        self.assertEqual(caught.exception.code, "capability_tool_not_found")
        handle_tool_call.assert_not_called()

    def test_list_tools_for_caller_appends_dynamic_specs(self) -> None:
        from no1.ports.mcp.server import list_tools_for_caller

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={
                "visible_tools": ["onecolleague_help", "onecolleague_ext_deadbeef_echo"],
                "dynamic_tools": [
                    {
                        "name": "onecolleague_ext_deadbeef_echo",
                        "description": "echo",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                        "capability_id": "mcp:test-server",
                        "real_tool_name": "echo",
                    }
                ],
            },
        ):
            tools = list_tools_for_caller()
        names = {str(item.get("name") or "") for item in tools if isinstance(item, dict)}
        self.assertIn("onecolleague_help", names)
        self.assertIn("onecolleague_ext_deadbeef_echo", names)

    def test_handle_tool_call_falls_back_to_dynamic_capability_tool_call(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            side_effect=[
                {
                    "admission_fingerprint": "rev-dynamic-1",
                    "dynamic_tools": [{"name": "onecolleague_ext_deadbeef_echo"}],
                },
                {"tool_name": "onecolleague_ext_deadbeef_echo", "result": {"ok": True}},
            ],
        ) as daemon_call:
            result = handle_tool_call("onecolleague_ext_deadbeef_echo", {"message": "hello"})

        self.assertEqual(str(result.get("tool_name") or ""), "onecolleague_ext_deadbeef_echo")
        self.assertEqual(daemon_call.call_count, 2)
        self.assertEqual(daemon_call.call_args_list[0].args[0].get("op"), "capability_state")
        self.assertEqual(daemon_call.call_args_list[1].args[0].get("op"), "capability_tool_call")

    def test_handle_tool_call_keeps_unknown_when_dynamic_not_found(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            side_effect=MCPError("capability_tool_not_found", "not found"),
        ):
            with self.assertRaises(MCPError):
                handle_tool_call("onecolleague_ext_deadbeef_missing", {})

    def test_voice_secretary_document_rejects_mcp_save(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
        ) as daemon_call:
            with self.assertRaises(MCPError) as caught:
                handle_tool_call(
                    "onecolleague_voice_secretary_document",
                    {
                        "action": "save",
                        "document_path": "docs/voice-secretary/notes.md",
                        "new_source": "# Updated\n",
                    },
                )

        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertIn("list|create|read_new_input|archive", caught.exception.message)
        daemon_call.assert_not_called()

    def test_voice_secretary_document_read_new_input_routes_to_daemon(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={"ok": True},
        ) as daemon_call:
            handle_tool_call(
                "onecolleague_voice_secretary_document",
                {
                    "action": "read_new_input",
                },
            )

        req = daemon_call.call_args.args[0]
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(req.get("op"), "assistant_voice_document_input_read")
        self.assertEqual(args.get("by"), "assistant:voice_secretary")

    def test_voice_secretary_composer_submit_prompt_draft_routes_to_daemon(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={"ok": True},
        ) as daemon_call:
            handle_tool_call(
                "onecolleague_voice_secretary_composer",
                {
                    "action": "submit_prompt_draft",
                    "request_id": "voice-prompt-1",
                    "draft_text": "Please review the plan and list concrete risks.",
                },
            )

        req = daemon_call.call_args.args[0]
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(req.get("op"), "assistant_voice_prompt_draft_submit")
        self.assertEqual(args.get("by"), "voice-secretary")
        self.assertEqual(args.get("request_id"), "voice-prompt-1")
        self.assertEqual(args.get("operation"), "")

    def test_voice_secretary_document_list_defaults_to_compact_content(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={"ok": True},
        ) as daemon_call:
            handle_tool_call(
                "onecolleague_voice_secretary_document",
                {
                    "action": "list",
                    "document_path": "docs/voice-secretary/notes.md",
                },
            )

        req = daemon_call.call_args.args[0]
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(req.get("op"), "assistant_voice_document_list")
        self.assertEqual(args.get("document_path"), "docs/voice-secretary/notes.md")
        self.assertFalse(bool(args.get("include_content")))
        self.assertFalse(bool(args.get("include_documents_by_id")))
        self.assertFalse(bool(args.get("include_documents_by_path")))

    def test_voice_secretary_document_list_rejects_content_payload(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
        ) as daemon_call:
            with self.assertRaises(MCPError) as caught:
                handle_tool_call(
                    "onecolleague_voice_secretary_document",
                    {
                        "action": "list",
                        "include_content": True,
                    },
                )

        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertIn("read repository markdown directly", caught.exception.message)
        daemon_call.assert_not_called()

    def test_voice_secretary_document_schema_has_no_save_action(self) -> None:
        from no1.ports.mcp.toolspecs import MCP_TOOLS

        tool = next(item for item in MCP_TOOLS if item.get("name") == "onecolleague_voice_secretary_document")
        properties = ((tool.get("inputSchema") or {}).get("properties") or {})
        actions = set(properties.get("action", {}).get("enum") or [])
        self.assertEqual(actions, {"list", "create", "read_new_input", "archive"})
        self.assertNotIn("content", properties)
        self.assertNotIn("include_content", properties)
        self.assertNotIn("status", properties)

    def test_voice_secretary_document_create_rejects_content_payload(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
        ) as daemon_call:
            with self.assertRaises(MCPError) as caught:
                handle_tool_call(
                    "onecolleague_voice_secretary_document",
                    {
                        "action": "create",
                        "title": "Notes",
                        "content": "# Should not go through MCP\n",
                    },
                )

        self.assertEqual(caught.exception.code, "invalid_request")
        self.assertIn("edit document content directly", caught.exception.message)
        daemon_call.assert_not_called()

    def test_voice_secretary_request_routes_to_daemon(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={"ok": True},
        ) as daemon_call:
            handle_tool_call(
                "onecolleague_voice_secretary_request",
                {
                    "target": "@foreman",
                    "request_text": "Please review this action request.",
                    "summary": "Spoken task detected.",
                    "document_path": "docs/voice-secretary/notes.md",
                    "requires_ack": True,
                },
            )

        req = daemon_call.call_args.args[0]
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(req.get("op"), "assistant_voice_request")
        self.assertEqual(args.get("target"), "@foreman")
        self.assertEqual(args.get("request_text"), "Please review this action request.")
        self.assertEqual(args.get("document_path"), "docs/voice-secretary/notes.md")
        self.assertEqual(args.get("by"), "voice-secretary")

    def test_voice_secretary_request_report_routes_to_feedback_op(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={"ok": True},
        ) as daemon_call:
            handle_tool_call(
                "onecolleague_voice_secretary_request",
                {
                    "action": "report",
                    "request_id": "voice-ask-123",
                    "status": "done",
                    "reply_text": "Handled directly.",
                    "document_path": "docs/voice-secretary/notes.md",
                    "artifact_paths": ["docs/voice-secretary/notes.md", "docs/voice-secretary/report.md"],
                    "source_summary": "Checked current weather provider snapshot.",
                    "checked_at": "2026-04-22T12:00:00Z",
                    "source_urls": ["https://example.com/weather"],
                },
            )

        req = daemon_call.call_args.args[0]
        args = req.get("args") if isinstance(req.get("args"), dict) else {}
        self.assertEqual(req.get("op"), "assistant_voice_instruction_feedback")
        self.assertEqual(args.get("request_id"), "voice-ask-123")
        self.assertEqual(args.get("status"), "done")
        self.assertEqual(args.get("reply_text"), "Handled directly.")
        self.assertEqual(args.get("artifact_paths"), ["docs/voice-secretary/notes.md", "docs/voice-secretary/report.md"])
        self.assertEqual(args.get("source_summary"), "Checked current weather provider snapshot.")
        self.assertEqual(args.get("checked_at"), "2026-04-22T12:00:00Z")
        self.assertEqual(args.get("source_urls"), ["https://example.com/weather"])
        self.assertNotIn("result_summary", args)
        self.assertEqual(args.get("by"), "voice-secretary")

    def test_voice_secretary_request_rejects_non_voice_actor(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"}, clear=False):
            with self.assertRaises(MCPError) as caught:
                handle_tool_call(
                    "onecolleague_voice_secretary_request",
                    {"request_text": "Please review this action request."},
                )

        self.assertEqual(caught.exception.code, "permission_denied")

    def test_voice_secretary_request_requires_explicit_target(self) -> None:
        from no1.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
            "no1.ports.mcp.server._call_daemon_or_raise",
            return_value={"ok": True},
        ) as daemon_call:
            with self.assertRaises(MCPError) as caught:
                handle_tool_call(
                    "onecolleague_voice_secretary_request",
                    {"request_text": "Please review this action request."},
                )

        self.assertEqual(caught.exception.code, "invalid_request")
        daemon_call.assert_not_called()

    def test_list_tools_for_caller_fallback_uses_voice_secretary_surface(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request
        from no1.kernel.group import load_group
        from no1.kernel.voice_secretary_actor import ensure_voice_secretary_actor
        from no1.ports.mcp.server import list_tools_for_caller

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CCCC_HOME": td}, clear=False):
            create_resp, _ = handle_request(
                DaemonRequest.model_validate({"op": "group_create", "args": {"title": "mcp-voice", "topic": "", "by": "user"}})
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
                            "actor_id": "lead",
                            "runtime": "codex",
                            "runner": "headless",
                            "by": "user",
                        },
                    }
                )
            )
            self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

            group = load_group(group_id)
            self.assertIsNotNone(group)
            assert group is not None
            ensure_voice_secretary_actor(group)

            with patch.dict(os.environ, {"CCCC_GROUP_ID": group_id, "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
                "no1.ports.mcp.server._call_daemon_or_raise",
                side_effect=RuntimeError("daemon unavailable"),
            ):
                tools = list_tools_for_caller()

        names = {str(item.get("name") or "") for item in tools if isinstance(item, dict)}
        self.assertIn("onecolleague_help", names)
        self.assertIn("onecolleague_voice_secretary_document", names)
        self.assertIn("onecolleague_voice_secretary_composer", names)
        self.assertIn("onecolleague_voice_secretary_request", names)
        self.assertNotIn("onecolleague_pet_decisions", names)
        self.assertNotIn("onecolleague_message_send", names)
        self.assertNotIn("onecolleague_message_reply", names)

    def test_list_tools_for_caller_fallback_uses_voice_secretary_env_without_actor_doc(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request
        from no1.ports.mcp.server import list_tools_for_caller

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CCCC_HOME": td}, clear=False):
            create_resp, _ = handle_request(
                DaemonRequest.model_validate({"op": "group_create", "args": {"title": "mcp-voice-env", "topic": "", "by": "user"}})
            )
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            with patch.dict(os.environ, {"CCCC_GROUP_ID": group_id, "CCCC_ACTOR_ID": "voice-secretary"}, clear=False), patch(
                "no1.ports.mcp.server._call_daemon_or_raise",
                side_effect=RuntimeError("daemon unavailable"),
            ):
                tools = list_tools_for_caller()

        names = {str(item.get("name") or "") for item in tools if isinstance(item, dict)}
        self.assertIn("onecolleague_help", names)
        self.assertIn("onecolleague_voice_secretary_document", names)
        self.assertIn("onecolleague_voice_secretary_composer", names)
        self.assertIn("onecolleague_voice_secretary_request", names)
        self.assertNotIn("onecolleague_message_send", names)
        self.assertNotIn("onecolleague_message_reply", names)

    def test_full_profile_only_adds_currently_admitted_pack_tools(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="remote")
        with patch.dict(os.environ, {"CCCC_MCP_TOOL_PROFILE": "full"}, clear=False), patch.object(
            mcp_server,
            "_runtime_context",
            return_value=runtime,
        ), patch.object(mcp_server, "load_group", return_value=None), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            return_value={"visible_tools": ["onecolleague_help", "onecolleague_space"]},
        ):
            names = {
                str(item.get("name") or "")
                for item in mcp_server.list_tools_for_caller()
                if isinstance(item, dict)
            }

        self.assertIn("onecolleague_help", names)
        self.assertIn("onecolleague_space", names)
        self.assertNotIn("onecolleague_automation", names)

    def test_every_canonical_tool_has_one_explicit_primary_owner(self) -> None:
        from collections import Counter

        from no1.kernel.capabilities import BUILTIN_CAPABILITY_PACKS
        from no1.ports.mcp.ownership import CANONICAL_MCP_TOOL_NAMES, MCP_TOOL_PRIMARY_OWNERS

        self.assertEqual(len(CANONICAL_MCP_TOOL_NAMES), 58)
        self.assertEqual(set(MCP_TOOL_PRIMARY_OWNERS), set(CANONICAL_MCP_TOOL_NAMES))
        self.assertEqual(
            Counter(owner.kind for owner in MCP_TOOL_PRIMARY_OWNERS.values()),
            {"core": 14, "pack": 24, "product": 14, "disabled": 6},
        )
        overlap = {
            name
            for pack in BUILTIN_CAPABILITY_PACKS.values()
            if isinstance(pack, dict)
            for name in pack.get("tool_names", ())
            if name in MCP_TOOL_PRIMARY_OWNERS and MCP_TOOL_PRIMARY_OWNERS[name].kind == "product"
        }
        self.assertEqual(
            overlap,
            {
                "onecolleague_group_bridge_session_send",
                "onecolleague_group_bridge_remote_send",
                "onecolleague_group_bridge_remote_delivery_status",
                "onecolleague_computer_control_catalog",
                "onecolleague_computer_recording",
                "onecolleague_computer_workflow",
                "onecolleague_computer_run",
            },
        )

    def test_product_tools_are_direct_only_and_nested_calls_probe_no_product_state(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.ownership import MCP_TOOL_PRIMARY_OWNERS

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="local_mcp")
        product_tools = {
            name: owner
            for name, owner in MCP_TOOL_PRIMARY_OWNERS.items()
            if owner.kind == "product"
        }
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "_authorize_registered_surface",
            return_value="product",
        ) as product_guard, patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            side_effect=AssertionError("product ownership must not read capability state"),
        ):
            for name, owner in sorted(product_tools.items()):
                is_computer_control = name.startswith("onecolleague_computer_")
                with self.subTest(path="direct", tool=name):
                    if is_computer_control:
                        with self.assertRaises(MCPError) as caught:
                            mcp_server._issue_tool_call_certificate(name, name, {})
                        self.assertEqual(caught.exception.code, "permission_denied")
                    else:
                        certificate = mcp_server._issue_tool_call_certificate(name, name, {})
                        self.assertEqual(certificate.grant, f"product:{owner.owner_id}")
                product_guard.assert_called_once_with(name)
                product_guard.reset_mock()

                nested_capability = "pack:computer-control-local" if is_computer_control else "pack:group_bridge"
                with self.subTest(path="nested", tool=name), mcp_server.capability_use_nested_builtin_call_scope(
                    nested_capability
                ):
                    if is_computer_control:
                        with patch.object(
                            mcp_server,
                            "_authorize_local_computer_control_tool_call",
                            return_value=("g1", "peer-1"),
                        ):
                            certificate = mcp_server._issue_tool_call_certificate(name, name, {})
                        self.assertEqual(certificate.grant, f"product:{owner.owner_id}")
                    else:
                        with self.assertRaises(MCPError) as caught:
                            mcp_server._issue_tool_call_certificate(name, name, {})
                        self.assertEqual(caught.exception.code, "capability_tool_not_found")
                product_guard.assert_not_called()

    def test_web_model_fixed_pack_fallbacks_list_and_call_without_pack_grants(self) -> None:
        from no1.ports.mcp import server as mcp_server

        fixed = {
            "onecolleague_project_info",
            "onecolleague_capability_state",
            "onecolleague_tracked_send",
            "onecolleague_repo",
            "onecolleague_presentation",
            "onecolleague_memory",
        }
        runtime = SimpleNamespace(group_id="g1", actor_id="web-peer", source="remote")
        actor = {"id": "web-peer", "runtime": "web_model"}
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server, "load_group", return_value=object()
        ), patch.object(mcp_server, "find_actor", return_value=actor), patch.object(
            mcp_server, "get_effective_role", return_value="peer"
        ), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            side_effect=AssertionError("fixed Web Model fallbacks must not require capability grants"),
        ):
            names = {str(item.get("name") or "") for item in mcp_server.list_tools_for_caller()}
            self.assertTrue(fixed <= names)
            for name in fixed:
                with self.subTest(tool=name):
                    certificate = mcp_server._issue_tool_call_certificate(name, name, {})
                    self.assertEqual(certificate.grant, "web-model-fixed")

    def test_web_model_group_bridge_direct_call_is_rejected_before_product_or_daemon_probe(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="web-foreman", source="local_mcp")
        group = object()
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            return_value=group,
        ), patch.object(
            mcp_server,
            "find_actor",
            return_value={"id": "web-foreman", "runtime": "web_model"},
        ), patch.object(mcp_server, "get_effective_role", return_value="foreman"), patch.object(
            mcp_server,
            "_authorize_registered_surface",
            side_effect=AssertionError("caller policy must reject before the Group Bridge product guard"),
        ) as product_guard, patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            side_effect=AssertionError("caller policy must reject before daemon delegation"),
        ) as daemon_call:
            with self.assertRaises(MCPError) as caught:
                mcp_server.handle_tool_call("onecolleague_group_bridge_session_send", {})

        self.assertEqual(caught.exception.code, "permission_denied")
        product_guard.assert_not_called()
        daemon_call.assert_not_called()

    def test_disabled_local_execution_is_never_listed_and_direct_calls_fail_before_actor_probe(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.ownership import disabled_tool_names
        from no1.ports.mcp.toolspecs import legacy_mcp_tool_name

        disabled = set(disabled_tool_names())
        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="remote")
        state = {"visible_tools": ["onecolleague_help", *sorted(disabled)]}
        for profile in ("", "full"):
            with self.subTest(surface="normal", profile=profile or "default"), patch.dict(
                os.environ,
                {"CCCC_MCP_TOOL_PROFILE": profile},
                clear=False,
            ), patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
                mcp_server,
                "load_group",
                return_value=None,
            ), patch.object(mcp_server, "_call_daemon_or_raise", return_value=state):
                names = {str(item.get("name") or "") for item in mcp_server.list_tools_for_caller()}
            self.assertTrue(disabled.isdisjoint(names), disabled.intersection(names))

        web_group = object()
        with patch.dict(os.environ, {"CCCC_MCP_TOOL_PROFILE": "full"}, clear=False), patch.object(
            mcp_server,
            "_runtime_context",
            return_value=runtime,
        ), patch.object(mcp_server, "load_group", return_value=web_group), patch.object(
            mcp_server,
            "find_actor",
            return_value={"id": "peer-1", "runtime": "web_model"},
        ), patch.object(mcp_server, "get_effective_role", return_value="foreman"), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            return_value=state,
        ):
            web_names = {str(item.get("name") or "") for item in mcp_server.list_tools_for_caller()}
        self.assertTrue(disabled.isdisjoint(web_names), disabled.intersection(web_names))

        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            side_effect=AssertionError("disabled direct call must not probe actor state"),
        ), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            side_effect=AssertionError("disabled direct call must not read capability state"),
        ):
            for canonical in sorted(disabled):
                for requested in (canonical, legacy_mcp_tool_name(canonical)):
                    with self.subTest(surface="direct", tool=requested), self.assertRaises(MCPError) as caught:
                        mcp_server.handle_tool_call(requested, {})
                    self.assertEqual(caught.exception.code, "permission_denied")

    def test_voice_product_listing_and_calls_require_fixed_actor_id_even_under_full_profile(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.ownership import MCP_TOOL_PRIMARY_OWNERS

        voice_tools = {
            name
            for name, owner in MCP_TOOL_PRIMARY_OWNERS.items()
            if owner.kind == "product" and owner.owner_id == "voice-secretary"
        }
        fixed = SimpleNamespace(group_id="g1", actor_id="voice-secretary", source="remote")
        marker = SimpleNamespace(group_id="g1", actor_id="voice-marker-alias", source="remote")
        for profile in ("", "full"):
            with self.subTest(actor="fixed", profile=profile or "default"), patch.dict(
                os.environ,
                {"CCCC_MCP_TOOL_PROFILE": profile},
                clear=False,
            ), patch.object(mcp_server, "_runtime_context", return_value=fixed), patch.object(
                mcp_server,
                "load_group",
                return_value=None,
            ), patch.object(mcp_server, "_call_daemon_or_raise", side_effect=RuntimeError("unavailable")):
                fixed_names = {str(item.get("name") or "") for item in mcp_server.list_tools_for_caller()}
            self.assertTrue(voice_tools.issubset(fixed_names))

            with self.subTest(actor="marker", profile=profile or "default"), patch.dict(
                os.environ,
                {"CCCC_MCP_TOOL_PROFILE": profile},
                clear=False,
            ), patch.object(mcp_server, "_runtime_context", return_value=marker), patch.object(
                mcp_server,
                "load_group",
                return_value=object(),
            ), patch.object(
                mcp_server,
                "find_actor",
                return_value={"id": "voice-marker-alias", "runtime": "codex", "internal_kind": "voice_secretary"},
            ), patch.object(mcp_server, "_call_daemon_or_raise", side_effect=RuntimeError("unavailable")):
                marker_names = {str(item.get("name") or "") for item in mcp_server.list_tools_for_caller()}
            self.assertTrue(voice_tools.isdisjoint(marker_names), voice_tools.intersection(marker_names))

        with patch.object(mcp_server, "_runtime_context", return_value=marker), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            side_effect=AssertionError("voice marker rejection must not delegate"),
        ):
            with self.assertRaises(MCPError) as caught:
                mcp_server.handle_tool_call("onecolleague_voice_secretary_request", {"action": "report"})
        self.assertEqual(caught.exception.code, "permission_denied")

    def test_direct_builtin_pack_call_requires_exact_current_grant(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="remote")
        with patch.dict(
            os.environ,
            {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"},
            clear=False,
        ), patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            return_value=None,
        ), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            return_value={"admission_fingerprint": "rev-1", "builtin_tool_grants": {}},
        ) as daemon_call, patch.object(mcp_server, "space_status") as space_status:
            with self.assertRaises(MCPError) as denied:
                mcp_server.handle_tool_call(
                    "onecolleague_space",
                    {"group_id": "g1", "action": "status"},
                )
        self.assertEqual(denied.exception.code, "capability_tool_not_found")
        daemon_call.assert_called_once()
        space_status.assert_not_called()

        with patch.dict(
            os.environ,
            {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"},
            clear=False,
        ), patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            return_value=None,
        ), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            return_value={
                "admission_fingerprint": "rev-1",
                "builtin_tool_grants": {"onecolleague_space": ["pack:space"]},
            },
        ), patch.object(
            mcp_server,
            "space_status",
            return_value={"ok": True},
        ) as space_status:
            result = mcp_server.handle_tool_call(
                "onecolleague_space",
                {"group_id": "g1", "action": "status"},
            )
        self.assertEqual(result, {"ok": True})
        space_status.assert_called_once()

    def test_builtin_pack_admission_fails_closed_on_daemon_failure(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="remote")
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            return_value=None,
        ), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            side_effect=RuntimeError("daemon unavailable"),
        ):
            with self.assertRaises(MCPError) as caught:
                mcp_server.handle_tool_call(
                    "onecolleague_space",
                    {"group_id": "g1", "action": "status"},
                )
        self.assertEqual(caught.exception.code, "capability_tool_not_found")

    def test_nested_builtin_call_cannot_borrow_another_pack_grant(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="remote")
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            return_value=None,
        ), patch.object(
            mcp_server,
            "_call_daemon_or_raise",
            return_value={
                "admission_fingerprint": "rev-1",
                "builtin_tool_grants": {"onecolleague_space": ["pack:space"]},
            },
        ), mcp_server.capability_use_nested_builtin_call_scope("pack:automation"):
            with self.assertRaises(MCPError) as caught:
                mcp_server.handle_tool_call(
                    "onecolleague_space",
                    {"group_id": "g1", "action": "status"},
                )
        self.assertEqual(caught.exception.code, "capability_tool_not_found")

    def test_unauthorized_nested_independent_surface_reads_no_target_state(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="remote")
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            side_effect=AssertionError("nested denial must not inspect target actor state"),
        ), patch.object(
            mcp_server,
            "_authorize_registered_surface",
            side_effect=AssertionError("nested denial must not run the target surface precondition"),
        ) as surface_precondition, mcp_server.capability_use_nested_builtin_call_scope("core"):
            with self.assertRaises(MCPError) as caught:
                mcp_server.handle_tool_call("onecolleague_shell", {"command": "ignored"})
        self.assertEqual(caught.exception.code, "capability_tool_not_found")
        surface_precondition.assert_not_called()

    def test_unclassified_canonical_tool_fails_closed(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="local_mcp")
        future_tool = "onecolleague_future_unclassified"
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            return_value=None,
        ), patch.object(
            mcp_server,
            "_BUILTIN_MCP_TOOL_NAMES",
            frozenset({*mcp_server._BUILTIN_MCP_TOOL_NAMES, future_tool}),
        ), patch.object(mcp_server, "_call_daemon_or_raise") as daemon_call:
            with self.assertRaises(MCPError) as caught:
                mcp_server.handle_tool_call(future_tool, {})
        self.assertEqual(caught.exception.code, "capability_tool_not_found")
        daemon_call.assert_not_called()

        from no1.ports.mcp.ownership import MCP_TOOL_PRIMARY_OWNERS

        self.assertEqual(set(mcp_server._BUILTIN_MCP_TOOL_NAMES), set(MCP_TOOL_PRIMARY_OWNERS))

    def test_tool_call_certificate_rejects_cross_tool_context_and_reuse(self) -> None:
        from no1.ports.mcp import server as mcp_server

        runtime = SimpleNamespace(group_id="g1", actor_id="peer-1", source="local_mcp")
        with patch.object(mcp_server, "_runtime_context", return_value=runtime), patch.object(
            mcp_server,
            "load_group",
            return_value=None,
        ):
            cross_tool = mcp_server._issue_tool_call_certificate("onecolleague_help", "onecolleague_help", {})
            forged = replace(
                cross_tool,
                requested_name="onecolleague_memory",
                canonical_name="onecolleague_memory",
            )
            with self.assertRaises(MCPError) as tool_error:
                mcp_server._route_tool_call(forged, {})
            self.assertEqual(tool_error.exception.code, "permission_denied")

            context_bound = mcp_server._issue_tool_call_certificate("onecolleague_help", "onecolleague_help", {})
            changed = SimpleNamespace(group_id="g1", actor_id="peer-2", source="local_mcp")
            with patch.object(mcp_server, "_runtime_context", return_value=changed):
                with self.assertRaises(MCPError) as context_error:
                    mcp_server._route_tool_call(context_bound, {})
            self.assertEqual(context_error.exception.code, "permission_denied")

            reusable = mcp_server._issue_tool_call_certificate("onecolleague_help", "onecolleague_help", {})
            with patch.object(mcp_server, "_handle_onecolleague_namespace", return_value={"ok": True}):
                self.assertEqual(mcp_server._route_tool_call(reusable, {}), {"ok": True})
                with self.assertRaises(MCPError) as reuse_error:
                    mcp_server._route_tool_call(reusable, {})
            self.assertEqual(reuse_error.exception.code, "permission_denied")

    def test_private_router_and_certificate_seal_have_no_production_bypass(self) -> None:
        import no1

        root = Path(no1.__file__).resolve().parent
        references = []
        router_callers = []
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if path.name != "server.py" and any(
                token in source
                for token in (
                    "_route_tool_call",
                    "_TOOL_CALL_CERTIFICATE_SEAL",
                    "_PENDING_TOOL_CALL_CERTIFICATE",
                )
            ):
                references.append(str(path.relative_to(root)))
            if path == root / "ports" / "mcp" / "server.py":
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if any(
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id == "_route_tool_call"
                        for child in ast.walk(node)
                    ):
                        router_callers.append(node.name)
        self.assertEqual(references, [])
        self.assertEqual(router_callers, ["handle_tool_call"])


if __name__ == "__main__":
    unittest.main()
