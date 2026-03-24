import unittest
from unittest.mock import patch


class TestMcpTaskUpdateStatusWrapper(unittest.TestCase):
    def test_task_update_with_status_batches_update_and_move(self) -> None:
        from cccc.ports.mcp import server as mcp_server
        from cccc.ports.mcp.handlers import context as mcp_context

        captured = {}

        def _fake_context_sync(*, group_id, ops, dry_run=False, if_version=None, by=None):
            captured["group_id"] = group_id
            captured["ops"] = ops
            captured["dry_run"] = dry_run
            captured["if_version"] = if_version
            captured["by"] = by
            return {"ok": True}

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_self_actor_id", return_value="peer1"
        ), patch.object(mcp_context, "context_sync", side_effect=_fake_context_sync):
            out = mcp_server.handle_tool_call(
                "cccc_task",
                {
                    "action": "update",
                    "task_id": "T123",
                    "title": "Ship the patch",
                    "status": "done",
                },
            )

        self.assertTrue(bool(out.get("ok")))
        self.assertEqual(captured.get("group_id"), "g_test")
        self.assertEqual(captured.get("by"), "peer1")
        self.assertEqual(
            captured.get("ops"),
            [
                {"op": "task.update", "task_id": "T123", "title": "Ship the patch"},
                {"op": "task.move", "task_id": "T123", "status": "done"},
            ],
        )

    def test_task_update_with_status_only_degenerates_to_move(self) -> None:
        from cccc.ports.mcp import server as mcp_server
        from cccc.ports.mcp.handlers import context as mcp_context

        captured = {}

        def _fake_context_sync(*, group_id, ops, dry_run=False, if_version=None, by=None):
            captured["group_id"] = group_id
            captured["ops"] = ops
            captured["by"] = by
            return {"ok": True}

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_self_actor_id", return_value="peer1"
        ), patch.object(mcp_context, "context_sync", side_effect=_fake_context_sync):
            out = mcp_server.handle_tool_call(
                "cccc_task",
                {
                    "action": "update",
                    "task_id": "T123",
                    "status": "active",
                },
            )

        self.assertTrue(bool(out.get("ok")))
        self.assertEqual(captured.get("group_id"), "g_test")
        self.assertEqual(captured.get("by"), "peer1")
        self.assertEqual(
            captured.get("ops"),
            [{"op": "task.move", "task_id": "T123", "status": "active"}],
        )

    def test_task_update_without_status_stays_plain_patch(self) -> None:
        from cccc.ports.mcp import server as mcp_server
        from cccc.ports.mcp.handlers import context as mcp_context

        captured = {}

        def _fake_context_sync(*, group_id, ops, dry_run=False, if_version=None, by=None):
            captured["group_id"] = group_id
            captured["ops"] = ops
            captured["by"] = by
            return {"ok": True}

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_self_actor_id", return_value="peer1"
        ), patch.object(mcp_context, "context_sync", side_effect=_fake_context_sync):
            out = mcp_server.handle_tool_call(
                "cccc_task",
                {
                    "action": "update",
                    "task_id": "T123",
                    "notes": "Need one more repro step.",
                },
            )

        self.assertTrue(bool(out.get("ok")))
        self.assertEqual(captured.get("group_id"), "g_test")
        self.assertEqual(captured.get("by"), "peer1")
        self.assertEqual(
            captured.get("ops"),
            [{"op": "task.update", "task_id": "T123", "notes": "Need one more repro step."}],
        )


if __name__ == "__main__":
    unittest.main()
