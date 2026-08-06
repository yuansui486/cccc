import os
import tempfile
import unittest


class TestDeliveryStateBehavior(unittest.TestCase):
    def test_reply_reminder_routes_to_named_tools(self) -> None:
        from no1.daemon.messaging.delivery import append_mcp_reply_reminder

        rendered = append_mcp_reply_reminder("[onecolleague] user -> agent: hello")
        self.assertIn("Use onecolleague_message_reply for replies", rendered)
        self.assertIn("use onecolleague_message_send for new messages", rendered)
        self.assertNotIn("If you respond: use MCP", rendered)
        self.assertNotIn("Write-Output", rendered)

    def test_should_deliver_message_respects_idle_and_paused_semantics(self) -> None:
        from no1.daemon.messaging.delivery import should_deliver_message
        from no1.kernel.group import create_group, set_group_state
        from no1.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        try:
            td = td_ctx.__enter__()
            os.environ["CCCC_HOME"] = td
            reg = load_registry()
            group = create_group(reg, title="delivery-state")

            # active: allow chat + notify
            self.assertTrue(should_deliver_message(group, "chat.message"))
            self.assertTrue(should_deliver_message(group, "system.notify"))

            # idle: allow chat + notify; block other kinds
            group = set_group_state(group, state="idle")
            self.assertTrue(should_deliver_message(group, "chat.message"))
            self.assertTrue(should_deliver_message(group, "system.notify"))
            self.assertFalse(should_deliver_message(group, "chat.ack"))

            # paused: block all PTY delivery
            group = set_group_state(group, state="paused")
            self.assertFalse(should_deliver_message(group, "chat.message"))
            self.assertFalse(should_deliver_message(group, "system.notify"))

            # stopped: block all PTY delivery
            group.doc["state"] = "stopped"
            group.save()
            self.assertFalse(should_deliver_message(group, "chat.message"))
            self.assertFalse(should_deliver_message(group, "system.notify"))
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home
            td_ctx.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
