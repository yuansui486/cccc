import os
import tempfile
import unittest


class TestDeliveryStateBehavior(unittest.TestCase):
    def test_turn_context_block_renders_identity_and_message_fields(self) -> None:
        from no1.daemon.messaging.actor_turn_rendering import render_turn_context_block

        rendered = render_turn_context_block(
            group_id="group-1",
            actor_id="agent-1",
            messages=[
                {
                    "id": "event-1",
                    "reply_to": "event-parent",
                    "by": "user-1",
                    "to": ["agent-1", "agent-2"],
                    "turn_grant_receipt": "secret-receipt",
                    "authorization_secret": "secret-key",
                }
            ],
        )

        self.assertIn("group_id=group-1", rendered)
        self.assertIn("actor_id=agent-1", rendered)
        self.assertIn("event_id=event-1", rendered)
        self.assertIn("reply_to=event-parent", rendered)
        self.assertIn("by=user-1", rendered)
        self.assertIn("to=agent-1,agent-2", rendered)
        self.assertNotIn("turn_grant_receipt", rendered)
        self.assertNotIn("authorization_secret", rendered)

    def test_turn_context_block_caps_batch_context_and_allows_empty_context(self) -> None:
        from no1.daemon.messaging.actor_turn_rendering import render_turn_context_block

        rendered = render_turn_context_block(
            group_id="group-1",
            actor_id="agent-1",
            messages=[{"event_id": f"event-{index}"} for index in range(10)],
        )

        self.assertIn("event_id=event-0", rendered)
        self.assertIn("event_id=event-7", rendered)
        self.assertNotIn("event_id=event-8", rendered)
        self.assertEqual(render_turn_context_block(), "")

    def test_turn_context_block_reads_compact_event_data(self) -> None:
        from no1.daemon.messaging.actor_turn_rendering import render_turn_context_block

        rendered = render_turn_context_block(
            group_id="group-1",
            actor_id="agent-1",
            messages=[
                {
                    "id": "event-1",
                    "by": "user-1",
                    "data": {"reply_to": "event-parent", "to": ["agent-1"]},
                }
            ],
        )

        self.assertIn("event_id=event-1", rendered)
        self.assertIn("reply_to=event-parent", rendered)
        self.assertIn("by=user-1", rendered)
        self.assertIn("to=agent-1", rendered)

    def test_reply_reminder_routes_to_named_tools(self) -> None:
        from no1.daemon.messaging.delivery import append_mcp_reply_reminder

        rendered = append_mcp_reply_reminder("[onecolleague] user -> agent: hello")
        self.assertIn("Use onecolleague_message_reply for replies", rendered)
        self.assertIn("use onecolleague_message_send for new messages", rendered)
        self.assertNotIn("If you respond: use MCP", rendered)
        self.assertNotIn("Write-Output", rendered)

    def test_reply_reminder_appends_structured_turn_context(self) -> None:
        from no1.daemon.messaging.delivery import append_mcp_reply_reminder

        rendered = append_mcp_reply_reminder(
            "incoming message",
            group_id="group-1",
            actor_id="agent-1",
            messages=[
                {
                    "event_id": "event-1",
                    "reply_to": "event-parent",
                    "by": "user-1",
                    "to": "agent-1",
                }
            ],
        )

        self.assertIn("[context] OneColleague turn:", rendered)
        self.assertIn("group_id=group-1", rendered)
        self.assertIn("actor_id=agent-1", rendered)
        self.assertIn("event_id=event-1", rendered)
        self.assertIn("Use onecolleague_message_reply for replies", rendered)

    def test_browser_prompt_includes_compact_message_context(self) -> None:
        from no1.daemon.actors.web_model_browser_delivery import build_web_model_browser_turn_prompt

        prompt = build_web_model_browser_turn_prompt(
            {
                "group_id": "group-1",
                "actor_id": "agent-1",
                "delivery_id": "delivery-1",
                "event_ids": ["event-1"],
                "messages": [
                    {
                        "id": "event-1",
                        "by": "user-1",
                        "data": {"reply_to": "event-parent", "to": ["agent-1"]},
                    }
                ],
                "coalesced_text": "[message] user-1 -> agent-1: hello",
            }
        )

        self.assertIn("group_id=group-1", prompt)
        self.assertIn("actor_id=agent-1", prompt)
        self.assertIn("event_id=event-1", prompt)
        self.assertIn("reply_to=event-parent", prompt)
        self.assertIn("to=agent-1", prompt)

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
