from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from cccc.ports.mcp.common import MCPError


class TestMcpDynamicCapabilityTools(unittest.TestCase):
    def test_list_tools_for_caller_appends_dynamic_specs(self) -> None:
        from cccc.ports.mcp.server import list_tools_for_caller

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"}, clear=False), patch(
            "cccc.ports.mcp.server._call_daemon_or_raise",
            return_value={
                "visible_tools": ["cccc_help", "cccc_ext_deadbeef_echo"],
                "dynamic_tools": [
                    {
                        "name": "cccc_ext_deadbeef_echo",
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
        self.assertIn("cccc_help", names)
        self.assertIn("cccc_ext_deadbeef_echo", names)

    def test_handle_tool_call_falls_back_to_dynamic_capability_tool_call(self) -> None:
        from cccc.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"}, clear=False), patch(
            "cccc.ports.mcp.server._call_daemon_or_raise",
            return_value={"tool_name": "cccc_ext_deadbeef_echo", "result": {"ok": True}},
        ) as daemon_call:
            result = handle_tool_call("cccc_ext_deadbeef_echo", {"message": "hello"})

        self.assertEqual(str(result.get("tool_name") or ""), "cccc_ext_deadbeef_echo")
        daemon_call.assert_called_once()

    def test_handle_tool_call_keeps_unknown_when_dynamic_not_found(self) -> None:
        from cccc.ports.mcp.server import handle_tool_call

        with patch.dict(os.environ, {"CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer-1"}, clear=False), patch(
            "cccc.ports.mcp.server._call_daemon_or_raise",
            side_effect=MCPError("capability_tool_not_found", "not found"),
        ):
            with self.assertRaises(MCPError):
                handle_tool_call("cccc_ext_deadbeef_missing", {})

    def test_list_tools_for_caller_fallback_uses_pet_minimal_surface(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.group import load_group
        from cccc.kernel.pet_actor import ensure_pet_actor
        from cccc.ports.mcp.server import list_tools_for_caller

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CCCC_HOME": td}, clear=False):
            create_resp, _ = handle_request(
                DaemonRequest.model_validate({"op": "group_create", "args": {"title": "mcp-pet", "topic": "", "by": "user"}})
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
                            "actor_id": "peer-1",
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
            ensure_pet_actor(group)

            with patch.dict(os.environ, {"CCCC_GROUP_ID": group_id, "CCCC_ACTOR_ID": "pet-peer"}, clear=False), patch(
                "cccc.ports.mcp.server._call_daemon_or_raise",
                side_effect=RuntimeError("daemon unavailable"),
            ):
                tools = list_tools_for_caller()

        names = {str(item.get("name") or "") for item in tools if isinstance(item, dict)}
        self.assertIn("cccc_help", names)
        self.assertIn("cccc_context_get", names)
        self.assertIn("cccc_agent_state", names)
        self.assertIn("cccc_pet_decisions", names)
        self.assertNotIn("cccc_message_send", names)
        self.assertNotIn("cccc_message_reply", names)
        self.assertNotIn("cccc_file", names)


if __name__ == "__main__":
    unittest.main()

