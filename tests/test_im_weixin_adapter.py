from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class TestWeixinAdapterOutbound(unittest.TestCase):
    def _adapter(self):
        from cccc.ports.im.adapters.weixin import WeixinAdapter

        adapter = WeixinAdapter(command=["node", "fake-sidecar.mjs"])
        adapter._connected = True
        writes: list[dict] = []

        def fake_write(payload: dict) -> bool:
            writes.append(payload)
            return True

        adapter._write_json = fake_write  # type: ignore[method-assign]
        return adapter, writes

    def test_send_message_uses_reply_and_clears_pending_ref(self) -> None:
        adapter, writes = self._adapter()
        adapter._reply_refs["chat-1"] = "req-1"
        adapter._outbound_ready_chats.add("chat-1")

        ok = adapter.send_message("chat-1", "hello")

        self.assertTrue(ok)
        self.assertEqual(writes, [{
            "type": "cmd",
            "cmd": "reply",
            "request_id": "req-1",
            "chat_id": "chat-1",
            "text": "hello",
        }])
        self.assertNotIn("chat-1", adapter._reply_refs)

    def test_send_message_uses_send_for_seen_chat_without_pending_request(self) -> None:
        adapter, writes = self._adapter()
        adapter._outbound_ready_chats.add("chat-1")

        ok = adapter.send_message("chat-1", "hello")

        self.assertTrue(ok)
        self.assertEqual(writes, [{
            "type": "cmd",
            "cmd": "send",
            "chat_id": "chat-1",
            "text": "hello",
        }])

    def test_send_message_falls_back_to_send_after_reply_is_consumed(self) -> None:
        adapter, writes = self._adapter()
        adapter._reply_refs["chat-1"] = "req-1"
        adapter._outbound_ready_chats.add("chat-1")

        first_ok = adapter.send_message("chat-1", "reply once")
        second_ok = adapter.send_message("chat-1", "follow up")

        self.assertTrue(first_ok)
        self.assertTrue(second_ok)
        self.assertEqual(writes, [
            {
                "type": "cmd",
                "cmd": "reply",
                "request_id": "req-1",
                "chat_id": "chat-1",
                "text": "reply once",
            },
            {
                "type": "cmd",
                "cmd": "send",
                "chat_id": "chat-1",
                "text": "follow up",
            },
        ])

    def test_send_message_returns_false_when_no_outbound_context_exists(self) -> None:
        adapter, writes = self._adapter()

        ok = adapter.send_message("chat-1", "hello")

        self.assertFalse(ok)
        self.assertEqual(writes, [])

    def test_send_file_uses_reply_file_and_clears_pending_ref(self) -> None:
        adapter, writes = self._adapter()
        adapter._reply_refs["chat-1"] = "req-1"
        adapter._outbound_ready_chats.add("chat-1")

        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "note.txt"
            file_path.write_text("hello", encoding="utf-8")

            ok = adapter.send_file(
                "chat-1",
                file_path=file_path,
                filename="note.txt",
                caption="caption",
            )

        self.assertTrue(ok)
        self.assertEqual(writes, [{
            "type": "cmd",
            "cmd": "reply_file",
            "request_id": "req-1",
            "chat_id": "chat-1",
            "file_path": str(file_path.resolve()),
            "filename": "note.txt",
            "caption": "caption",
        }])
        self.assertNotIn("chat-1", adapter._reply_refs)

    def test_send_file_uses_send_file_for_seen_chat_without_pending_request(self) -> None:
        adapter, writes = self._adapter()
        adapter._outbound_ready_chats.add("chat-1")

        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "note.txt"
            file_path.write_text("hello", encoding="utf-8")

            ok = adapter.send_file(
                "chat-1",
                file_path=file_path,
                filename="note.txt",
                caption="caption",
            )

        self.assertTrue(ok)
        self.assertEqual(writes, [{
            "type": "cmd",
            "cmd": "send_file",
            "chat_id": "chat-1",
            "file_path": str(file_path.resolve()),
            "filename": "note.txt",
            "caption": "caption",
        }])


if __name__ == "__main__":
    unittest.main()
