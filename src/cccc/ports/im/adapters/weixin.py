"""
Weixin (personal WeChat) adapter for CCCC IM Bridge.

This adapter talks to a local Node.js sidecar process over JSON Lines.
The sidecar hosts `weixin-agent-sdk` and turns WeChat messages into a
simple stdin/stdout protocol that fits the Python bridge model.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import IMAdapter

DEFAULT_MAX_CHARS = 4000
DEFAULT_MAX_LINES = 64


class WeixinAdapter(IMAdapter):
    """Personal WeChat adapter backed by a local Node sidecar."""

    platform = "weixin"

    def __init__(
        self,
        *,
        command: List[str],
        account_id: str = "",
        log_path: Optional[Path] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_lines: int = DEFAULT_MAX_LINES,
    ):
        self.command = list(command)
        self.account_id = str(account_id or "").strip()
        self.log_path = log_path
        self.max_chars = int(max_chars)
        self.max_lines = int(max_lines)

        self._proc: Optional[subprocess.Popen[str]] = None
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stdin_lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._message_queue: List[Dict[str, Any]] = []
        self._reply_refs: Dict[str, str] = {}
        self._outbound_ready_chats: set[str] = set()
        self._connected = False
        self._ready = threading.Event()
        self._connect_error: Optional[str] = None

    def _log(self, msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [weixin] {msg}"
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def _compose_safe(self, text: str) -> str:
        lines = str(text or "").split("\n")
        if len(lines) > self.max_lines:
            lines = lines[: self.max_lines]
            lines.append("... (truncated)")
        out = "\n".join(lines)
        if len(out) > self.max_chars:
            out = out[: self.max_chars - 20] + "\n... (truncated)"
        return out

    def _write_json(self, payload: Dict[str, Any]) -> bool:
        proc = self._proc
        if not proc or not proc.stdin or proc.poll() is not None:
            return False
        line = json.dumps(payload, ensure_ascii=False)
        with self._stdin_lock:
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
                return True
            except Exception as e:
                self._log(f"[stdio] Failed to write command: {e}")
                return False

    def _handle_event(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("event") or "").strip().lower()
        if kind == "ready":
            self._connect_error = None
            self._ready.set()
            self._log("[connect] sidecar ready")
            return
        if kind == "error":
            message = str(event.get("message") or "sidecar error").strip()
            self._connect_error = message
            self._ready.set()
            self._log(f"[sidecar] error: {message}")
            return
        if kind == "log":
            message = str(event.get("message") or "").strip()
            if message:
                self._log(f"[sidecar] {message}")
            return
        if kind != "message":
            self._log(f"[sidecar] ignored event={kind or '<unknown>'}")
            return

        chat_id = str(event.get("chat_id") or "").strip()
        request_id = str(event.get("request_id") or event.get("message_id") or "").strip()
        text = str(event.get("text") or "").strip()
        if not chat_id or not request_id:
            self._log("[sidecar] malformed message event: missing chat_id/request_id")
            return

        self._reply_refs[chat_id] = request_id
        self._outbound_ready_chats.add(chat_id)
        attachment = event.get("attachment")
        attachments: List[Dict[str, Any]] = []
        if isinstance(attachment, dict):
            attachments.append(dict(attachment))

        normalized = {
            "chat_id": chat_id,
            "chat_title": str(event.get("chat_title") or chat_id),
            "chat_type": str(event.get("chat_type") or "p2p"),
            "routed": True,
            "thread_id": 0,
            "text": text,
            "attachments": attachments,
            "from_user": str(event.get("from_user") or ""),
            "message_id": request_id,
            "timestamp": float(event.get("timestamp") or time.time()),
        }
        with self._queue_lock:
            self._message_queue.append(normalized)

    def _stdout_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    self._log(f"[sidecar] non-json stdout: {line[:200]}")
                    continue
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("type") or "") != "event":
                    continue
                self._handle_event(payload)
        finally:
            if not self._ready.is_set() and self._connect_error is None:
                self._connect_error = "sidecar stdout closed before ready"
                self._ready.set()

    def _stderr_loop(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for raw_line in proc.stderr:
            line = raw_line.rstrip()
            if line:
                self._log(f"[stderr] {line}")

    def connect(self) -> bool:
        if not self.command:
            self._log("[connect] missing weixin sidecar command")
            return False

        self._disable_proxies()
        self._ready.clear()
        self._connect_error = None

        env = None
        if self.account_id:
            import os

            env = dict(os.environ)
            env["CCCC_IM_WEIXIN_ACCOUNT_ID"] = self.account_id

        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except Exception as e:
            self._log(f"[connect] failed to start sidecar {shlex.join(self.command)}: {e}")
            return False

        self._stdout_thread = threading.Thread(target=self._stdout_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

        if not self._ready.wait(timeout=15.0):
            self._log("[connect] timeout waiting for weixin sidecar readiness")
            self.disconnect()
            return False
        if self._connect_error:
            self._log(f"[connect] sidecar failed: {self._connect_error}")
            self.disconnect()
            return False
        if self._proc and self._proc.poll() is not None:
            self._log("[connect] sidecar exited unexpectedly")
            self.disconnect()
            return False

        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False
        proc = self._proc

        if proc is not None:
            try:
                self._write_json({"type": "cmd", "cmd": "shutdown"})
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proc = None

        with self._queue_lock:
            self._message_queue.clear()
        self._reply_refs.clear()
        self._outbound_ready_chats.clear()

    def poll(self) -> List[Dict[str, Any]]:
        with self._queue_lock:
            items = list(self._message_queue)
            self._message_queue.clear()
        return items

    def send_message(
        self,
        chat_id: str,
        text: str,
        thread_id: Optional[int] = None,
        *,
        mention_user_ids: Optional[List[str]] = None,
    ) -> bool:
        _ = thread_id
        _ = mention_user_ids

        if not text:
            return True
        if not self._connected:
            return False

        chat_id = str(chat_id or "").strip()
        request_id = self._reply_refs.get(chat_id, "")
        if not request_id:
            if chat_id not in self._outbound_ready_chats:
                self._log(f"[send] no outbound context for chat={chat_id}")
                return False
            return self._write_json(
                {
                    "type": "cmd",
                    "cmd": "send",
                    "chat_id": chat_id,
                    "text": self._compose_safe(text),
                }
            )

        ok = self._write_json(
            {
                "type": "cmd",
                "cmd": "reply",
                "request_id": request_id,
                "chat_id": chat_id,
                "text": self._compose_safe(text),
            }
        )
        if ok:
            self._reply_refs.pop(chat_id, None)
        return ok

    def download_attachment(self, attachment: Dict[str, Any]) -> bytes:
        file_path = str(attachment.get("file_path") or "").strip()
        if not file_path:
            raise ValueError("missing weixin attachment file_path")
        return Path(file_path).read_bytes()

    def send_file(
        self,
        chat_id: str,
        *,
        file_path: Path,
        filename: str,
        caption: str = "",
        thread_id: Optional[int] = None,
        mention_user_ids: Optional[List[str]] = None,
    ) -> bool:
        _ = thread_id
        _ = mention_user_ids

        if not self._connected:
            return False

        chat_id = str(chat_id or "").strip()
        request_id = self._reply_refs.get(chat_id, "")
        payload = {
            "type": "cmd",
            "chat_id": chat_id,
            "file_path": str(file_path.expanduser().resolve()),
            "filename": str(filename or file_path.name),
            "caption": self._compose_safe(caption),
        }

        if request_id:
            ok = self._write_json(
                {
                    **payload,
                    "cmd": "reply_file",
                    "request_id": request_id,
                }
            )
            if ok:
                self._reply_refs.pop(chat_id, None)
            return ok

        if chat_id not in self._outbound_ready_chats:
            self._log(f"[send_file] no outbound context for chat={chat_id}")
            return False

        return self._write_json(
            {
                **payload,
                "cmd": "send_file",
            }
        )

    def get_chat_title(self, chat_id: str) -> str:
        return chat_id
