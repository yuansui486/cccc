from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch


class _FakeBinaryStdin:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)

    def readline(self) -> str:
        raise AssertionError("text stdin path should not be used when a binary buffer exists")


class _FakeBinaryStdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, _data: str) -> int:
        raise AssertionError("text stdout path should not be used when a binary buffer exists")

    def flush(self) -> None:
        return None


class TestMcpStdioUtf8(unittest.TestCase):
    def test_read_message_uses_binary_utf8_stdin(self) -> None:
        from no1.ports.mcp import main as mcp_main

        stdin = _FakeBinaryStdin(b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"text":"\xe5\xbc\x80"}}\n')
        with patch.object(mcp_main.sys, "stdin", stdin):
            msg = mcp_main._read_message()

        self.assertIsInstance(msg, dict)
        self.assertEqual(str((msg or {}).get("method") or ""), "ping")
        params = (msg or {}).get("params") if isinstance((msg or {}).get("params"), dict) else {}
        self.assertEqual(str(params.get("text") or ""), "开")

    def test_write_message_uses_binary_utf8_stdout(self) -> None:
        from no1.ports.mcp import main as mcp_main

        stdout = _FakeBinaryStdout()
        with patch.object(mcp_main.sys, "stdout", stdout):
            mcp_main._write_message({"jsonrpc": "2.0", "id": 1, "result": {"text": "开始"}})

        payload = stdout.buffer.getvalue().decode("utf-8")
        parsed = json.loads(payload.strip())
        result = parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
        self.assertEqual(str(result.get("text") or ""), "开始")

    def test_text_stream_fallback_still_works_without_binary_buffer(self) -> None:
        from no1.ports.mcp import main as mcp_main

        stdin = io.StringIO('{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
        stdout = io.StringIO()
        with patch.object(mcp_main.sys, "stdin", stdin), patch.object(mcp_main.sys, "stdout", stdout):
            msg = mcp_main._read_message()
            mcp_main._write_message({"jsonrpc": "2.0", "id": 2, "result": {"ok": True}})

        self.assertEqual(str((msg or {}).get("method") or ""), "ping")
        parsed = json.loads(stdout.getvalue().strip())
        result = parsed.get("result") if isinstance(parsed.get("result"), dict) else {}
        self.assertTrue(bool(result.get("ok")))


if __name__ == "__main__":
    unittest.main()
