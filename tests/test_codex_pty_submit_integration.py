"""Opt-in real Codex/ConPTY check using a local fake model and an isolated home.

Set ONECOLLEAGUE_TEST_CODEX_EXE to the native codex.exe to run this test.
No credentials, external model requests, or existing actor sessions are used.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from no1.daemon.messaging import delivery
from no1.runners import pty_win
from no1.util.terminal_render import render_transcript


@pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("ONECOLLEAGUE_TEST_CODEX_EXE"),
    reason="requires opt-in native Windows Codex CLI",
)
def test_real_codex_submits_three_messages_without_accumulating(tmp_path):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            users = [item for item in body.get("input", []) if item.get("role") == "user"]
            text = "\n".join(part.get("text", "") for part in users[-1].get("content", []))
            # New Codex also asks the model to generate a task title. Those
            # auxiliary requests are not duplicate deliveries of the message.
            if text.startswith("PTY_PROBE_"):
                received.append(text)
            answer = '{"title":"PTY probe"}' if body.get("text", {}).get("format", {}).get("type") == "json_schema" else "Received."
            message_id = f"msg_probe_{uuid4().hex}"
            response_id = f"resp_probe_{uuid4().hex}"
            message = {
                "id": message_id, "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": answer, "annotations": []}],
            }
            events = [
                {"type": "response.created", "response": {"id": response_id, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0,
                 "item": {**message, "status": "in_progress", "content": []}},
                {"type": "response.output_text.delta", "item_id": message_id, "output_index": 0,
                 "content_index": 0, "delta": answer},
                {"type": "response.output_item.done", "output_index": 0, "item": message},
                {"type": "response.completed", "response": {
                    "id": response_id, "status": "completed", "output": [message],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }},
            ]
            payload = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    supervisor = pty_win.PtySupervisor()
    options = {
        "sandbox_mode": "danger-full-access", "windows.sandbox": "elevated", "approval_policy": "never",
        "check_for_update_on_startup": False, "model_provider": "diagnostic", "model": "gpt-5.4",
        "model_providers.diagnostic.name": "Diagnostic",
        "model_providers.diagnostic.base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "model_providers.diagnostic.wire_api": "responses",
        "model_providers.diagnostic.requires_openai_auth": False,
    }
    # Production launch normalization must supply compatibility flags. Supplying
    # --no-alt-screen here previously hid the default-launch regression.
    command = [os.environ["ONECOLLEAGUE_TEST_CODEX_EXE"]]
    for name, value in options.items():
        command.extend(["-c", f"{name}={json.dumps(value)}"])
    try:
        session = supervisor.start_actor(
            group_id="probe", actor_id="actor", cwd=tmp_path, command=command,
            env={"CODEX_HOME": str(tmp_path)}, runtime="codex",
        )

        def screen():
            return render_transcript(session.tail_output().decode("utf-8", errors="replace"), compact=True)

        def wait_until(predicate, timeout=15):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.1)
            pytest.fail(f"Codex probe timed out; received={[text[:40] for text in received]}: {screen()[-2000:]}")

        # Codex can briefly render its composer before replacing it with the
        # trust dialog for this fresh directory. Do not submit into that dialog.
        time.sleep(3)
        wait_until(lambda: "Trust this folder?" in screen() or "Ask Codex to do anything" in screen())
        if "Trust this folder?" in screen():
            assert session.write_input(b"\r")
        wait_until(lambda: "Ask Codex to do anything" in screen())
        time.sleep(2)  # Match the startup readiness grace used by normal delivery.
        assert session._win32_input_mode
        payloads = [
            "PTY_PROBE_1 你好",
            "PTY_PROBE_2\n" + "中文长消息，检查自动提交。\n" * 100,
            "PTY_PROBE_3 最后一条",
        ]
        with patch.object(delivery.pty_runner, "SUPERVISOR", supervisor), patch.object(
            delivery, "find_actor", return_value={"submit": "enter"},
        ):
            for index, payload in enumerate(payloads, 1):
                outcome = delivery.pty_submit_text(
                    SimpleNamespace(group_id="probe"), actor_id="actor", text=payload, detailed_result=True,
                )
                assert outcome.accepted
                wait_until(lambda: len(received) >= index)
                # Observe the actual model request, not a successful stdin write
                # or a heuristic over the frequently repainted terminal screen.
        assert received == [payload.rstrip("\n") for payload in payloads]
    finally:
        supervisor.stop_all()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
