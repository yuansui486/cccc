"""Opt-in Windows Codex -> browser terminal -> Web API regression test.

Requires ONECOLLEAGUE_TEST_CODEX_EXE, Playwright with Chromium, and web/node_modules.
ONECOLLEAGUE_TEST_CORE_EXE selects a packaged core; otherwise tests source Python.
ONECOLLEAGUE_TEST_BROWSER_EXE optionally selects an installed Chromium executable.
All application state and model traffic are isolated; no real model API is used.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

import pytest


def _wait(predicate, label, *, pump=None, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if pump:
            pump(100)
        else:
            time.sleep(0.1)
    raise AssertionError(f"Timed out: {label}")


@pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("ONECOLLEAGUE_TEST_CODEX_EXE"),
    reason="requires opt-in native Windows Codex CLI",
)
def test_web_messages_survive_reconnect_and_remote_resume(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    repo = Path(__file__).resolve().parents[1]
    xterm = repo / "web/node_modules/@xterm/xterm/lib/xterm.js"
    if not xterm.is_file():
        pytest.skip("requires npm -C web install")
    codex = Path(os.environ["ONECOLLEAGUE_TEST_CODEX_EXE"]).resolve()
    core = Path(os.environ.get("ONECOLLEAGUE_TEST_CORE_EXE", sys.executable)).resolve()
    received = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            users = [item for item in body.get("input", []) if item.get("role") == "user"]
            text = "\n".join(part.get("text", "") for part in users[-1].get("content", [])) if users else ""
            if "WEB_PROBE_" in text and not text.startswith("Generate a concise, single-line task title"):
                received.append(text)
            message = {
                "id": "msg_" + uuid4().hex, "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "Received.", "annotations": []}],
            }
            events = [
                {"type": "response.output_item.added", "output_index": 0, "item": {**message, "content": []}},
                {"type": "response.output_text.delta", "item_id": message["id"], "output_index": 0,
                 "content_index": 0, "delta": "Received."},
                {"type": "response.output_item.done", "output_index": 0, "item": message},
                {"type": "response.completed", "response": {
                    "id": "resp_" + uuid4().hex, "status": "completed", "output": [message],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                }},
            ]
            payload = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    home, work, codex_home = (tmp_path / name for name in ("state", "work", "codex"))
    for directory in (home, work, codex_home):
        directory.mkdir()
    for key, value in {
        "ONECOLLEAGUE_HOME": str(home), "CCCC_HOME": str(home), "CODEX_HOME": str(codex_home),
        "PATH": str(codex.parent) + os.pathsep + os.environ.get("PATH", ""),
    }.items():
        monkeypatch.setenv(key, value)
    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    options = {
        "sandbox_mode": "danger-full-access", "windows.sandbox": "elevated", "approval_policy": "never",
        "check_for_update_on_startup": False, "model_provider": "diagnostic", "model": "gpt-5.4",
        "model_providers.diagnostic.name": "Diagnostic",
        "model_providers.diagnostic.base_url": f"http://127.0.0.1:{model.server_port}/v1",
        "model_providers.diagnostic.wire_api": "responses", "model_providers.diagnostic.requires_openai_auth": False,
        f"projects.{json.dumps(str(work))}.trust_level": "trusted",
    }
    (codex_home / "config.toml").write_text(
        "\n".join(f"{key} = {json.dumps(value)}" for key, value in options.items()), encoding="utf-8",
    )
    from no1.daemon.server import DaemonPaths, call_daemon
    from no1.daemon.runtime_session_ops import read_runtime_session, record_codex_app_thread_runtime_session
    from no1.kernel.actors import add_actor
    from no1.kernel.group import attach_scope_to_group, create_group
    from no1.kernel.registry import load_registry
    from no1.kernel.scope import detect_scope

    def command(module, *args):
        if core.stem.lower() in {"python", "python3"}:
            return [str(core), "-m", module, *args]
        return [str(core), "--internal-module", module, "--", *args]

    paths = DaemonPaths(home=home)
    processes = []
    log = (tmp_path / "process.log").open("w", encoding="utf-8")

    def start(module, *args):
        proc = subprocess.Popen(command(module, *args), cwd=home, env=os.environ.copy(),
                                stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(proc)
        return proc

    def rpc(op, **args):
        response = call_daemon({"op": op, "args": args}, paths=paths, timeout_s=30)
        assert response.get("ok"), response.get("error")
        return response.get("result") or {}

    try:
        start("no1.daemon_main", "run")
        _wait(lambda: paths.addr_path.exists(), "daemon startup")
        group = attach_scope_to_group(load_registry(), create_group(load_registry(), title="Browser probe"), detect_scope(work))
        gid = group.group_id
        # Deliberately use the default command: the product must add its own
        # compatibility option, including for a persisted pre-upgrade actor.
        add_actor(group, actor_id="probe", command=[str(codex), "--dangerously-bypass-approvals-and-sandbox"],
                  env={"CODEX_HOME": str(codex_home)}, runner="pty", runtime="codex", runtime_state_source="app_server")
        with socket.socket() as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            port = port_probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        start("no1.ports.web.main", "--serve-child", "--host", "127.0.0.1", "--port", str(port))

        def web_ready():
            try:
                with urlopen(base + "/api/v1/groups", timeout=1) as response:
                    return response.status == 200
            except OSError:
                return False

        _wait(web_ready, "web startup")
        with playwright.sync_playwright() as driver:
            browser_path = os.environ.get("ONECOLLEAGUE_TEST_BROWSER_EXE")
            browser = driver.chromium.launch(headless=True, **({"executable_path": browser_path} if browser_path else {}))
            page = browser.new_page()
            # A small terminal host exercises the real WebSocket and chat HTTP
            # endpoints without depending on account/onboarding UI preferences.
            # Navigate over the real loopback connection before installing the
            # host, so Chromium's local-network checks know the origin's address.
            page.goto(base + "/api/v1/groups")
            page.set_content('<div id="terminal"></div>')
            page.add_script_tag(path=str(xterm))
            page.evaluate("window.term = new Terminal({cols:120,rows:40}); term.open(document.getElementById('terminal'));")

            def attach():
                page.evaluate("""url => {
                    window.attached = false;
                    window.terminalError = null;
                    window.ws = new WebSocket(url);
                    ws.binaryType = 'arraybuffer';
                    ws.onerror = () => { window.terminalError = 'WebSocket connection failed'; };
                    const decoder = new TextDecoder();
                    ws.onmessage = event => {
                        if (typeof event.data === 'string') { terminalError = event.data; return; }
                        const bytes = new Uint8Array(event.data);
                        if (bytes[0] === 49) term.write(decoder.decode(bytes.slice(1), {stream:true}));
                        if (bytes[0] === 51) attached = JSON.parse(decoder.decode(bytes.slice(1))).terminal_writable;
                    };
                    window.inputListener?.dispose();
                    window.inputListener = term.onData(data => {
                        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({t:'i',d:data}));
                    });
                }""", f"ws://127.0.0.1:{port}/api/v1/groups/{gid}/actors/probe/term?mode=control")
                page.wait_for_function("window.attached || window.terminalError", timeout=15000)
                assert page.evaluate("window.attached"), page.evaluate("window.terminalError")

            rpc("actor_start", group_id=gid, actor_id="probe")
            attach()  # Connect immediately during startup, not after a fixed startup grace.
            payloads = ["WEB_PROBE_1 你好", "WEB_PROBE_2\n" + "中文长消息。\n" * 100, "WEB_PROBE_3 重连后", "WEB_PROBE_4 恢复后"]

            def send(index):
                result = page.evaluate("""async ({url, text}) => {
                    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({text,by:'user',to:['probe']})});
                    return response.json();
                }""", {"url": f"{base}/api/v1/groups/{gid}/send", "text": payloads[index]})
                assert result.get("ok"), result.get("error")
                _wait(lambda: len(received) >= index + 1, f"model request {index + 1}", pump=page.wait_for_timeout)

            send(0)
            send(1)
            page.evaluate("ws.close()")
            page.wait_for_function("ws.readyState === WebSocket.CLOSED")
            page.wait_for_timeout(300)
            attach()
            send(2)
            page.wait_for_timeout(500)
            rpc("actor_stop", group_id=gid, actor_id="probe")
            page.wait_for_function("ws.readyState === WebSocket.CLOSED")
            rollouts = list((codex_home / "sessions").rglob("*.jsonl"))
            assert rollouts, "expected materialized conversation"
            thread_id = json.loads(rollouts[0].read_text(encoding="utf-8").splitlines()[0])["payload"]["id"]
            # Model an existing saved conversation without relying on the
            # unrelated fresh-thread discovery mechanism.
            record_codex_app_thread_runtime_session(
                group_id=gid, actor_id="probe", cwd=work, runner="pty", provider_thread_id=thread_id,
                command=["codex", "app-server", "--listen", "ws://127.0.0.1:12345"], captured_from="integration_fixture",
            )
            rpc("actor_start", group_id=gid, actor_id="probe")
            attach()
            assert read_runtime_session(gid, "probe").get("provider_thread_id") == thread_id
            send(3)
            page.wait_for_timeout(1000)
            assert len(received) == len(payloads)
            for index, (actual, expected) in enumerate(zip(received, payloads), 1):
                assert expected.rstrip("\n") in actual
                assert actual.count(f"WEB_PROBE_{index}") == 1
                assert all(f"WEB_PROBE_{other}" not in actual for other in range(1, 5) if other != index)
            assert "[Pasted Content" not in page.evaluate("term.buffer.active.getLine(term.buffer.active.cursorY + term.buffer.active.baseY)?.translateToString() || ''")
            browser.close()
    finally:
        try:
            call_daemon({"op": "shutdown"}, paths=paths, timeout_s=5)
        except Exception:
            pass
        for proc in reversed(processes):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)
        log.close()
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=2)
