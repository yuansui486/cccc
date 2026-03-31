import os
import tempfile
import unittest


class TestInboxReverseLookup(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def test_find_event_with_chat_ack_prefers_recent_tail_scan(self) -> None:
        from cccc.contracts.v1 import ChatMessageData, DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.group import load_group
        from cccc.kernel.ledger import append_event
        from cccc.kernel.inbox import find_event_with_chat_ack

        _, cleanup = self._with_home()
        try:
            create_resp, _ = handle_request(
                DaemonRequest.model_validate({"op": "group_create", "args": {"title": "lookup", "topic": "", "by": "user"}})
            )
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)
            group = load_group(group_id)
            self.assertIsNotNone(group)
            assert group is not None

            target_id = ""
            for idx in range(2000):
                event = append_event(
                    group.ledger_path,
                    kind="chat.message",
                    group_id=group_id,
                    scope_key="",
                    by="user",
                    data=ChatMessageData(text=f"msg {idx}", to=["user"]).model_dump(),
                )
                if idx == 1995:
                    target_id = str(event.get("id") or "")

            self.assertTrue(target_id)
            append_event(
                group.ledger_path,
                kind="chat.ack",
                group_id=group_id,
                scope_key="",
                by="user",
                data={"actor_id": "user", "event_id": target_id},
            )

            reloaded = load_group(group_id)
            self.assertIsNotNone(reloaded)
            assert reloaded is not None

            found, found_ack = find_event_with_chat_ack(reloaded, event_id=target_id, actor_id="user")
            self.assertTrue(found_ack)
            self.assertIsNotNone(found)
            self.assertEqual(str((found or {}).get("id") or ""), target_id)
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
