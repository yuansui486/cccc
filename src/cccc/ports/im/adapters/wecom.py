"""
WeCom (企业微信) adapter for CCCC IM Bridge.

Uses WeCom AI Bot WebSocket callback for real-time messaging.

Features:
- WebSocket callback subscription (aibot_subscribe / aibot_msg_callback)
- Exponential backoff reconnect (1s-30s, 0-20% jitter)
- Heartbeat (25s interval, 3 consecutive failures → reconnect)
- Message deduplication via _seen_msg_ids
- Streaming reply via aibot_respond_msg
- Rate limiting
"""

from __future__ import annotations

import json
import mimetypes
import os
import random
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from base64 import b64decode
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import IMAdapter, OutboundStreamHandle

# WeCom API limits
WECOM_MAX_MESSAGE_LENGTH = 2048
DEFAULT_MAX_CHARS = 2048
DEFAULT_MAX_LINES = 64

# WebSocket defaults
WECOM_WS_URL = "wss://openws.work.weixin.qq.com"
WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
WS_HEARTBEAT_INTERVAL = 25  # seconds
WS_HEARTBEAT_MAX_FAILURES = 3
WS_RECONNECT_INITIAL = 1.0  # seconds
WS_RECONNECT_MAX = 30.0  # seconds
WS_RECONNECT_JITTER = 0.2  # 0-20%

# Keep the latest callback handle per chat for the lifetime of this bridge
# process. We only need a bounded cache, not a time-based expiry.
REPLY_REF_MAX_ENTRIES = 256
WECOM_MEDIA_CIPHER = "aes-256-cbc"
WECOM_MEDIA_BLOCK_SIZE = 32


class RateLimiter:
    """Simple per-chat rate limiter for WeCom API."""

    def __init__(self, max_per_second: float = 5.0):
        self.min_interval = 1.0 / max_per_second
        self.last_send: Dict[str, float] = {}
        self.lock = threading.Lock()

    def wait_and_acquire(self, chat_id: str) -> None:
        with self.lock:
            now = time.time()
            last = self.last_send.get(chat_id, 0)
            elapsed = now - last
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_send[chat_id] = time.time()


class WecomAdapter(IMAdapter):
    """
    WeCom adapter for CCCC IM Bridge.

    Uses bot_id + secret for authentication over the AI Bot WebSocket.
    Reply delivery is keyed by the latest inbound callback req_id per chat.
    """

    platform = "wecom"

    def __init__(
        self,
        bot_id: str,
        secret: str,
        log_path: Optional[Path] = None,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_lines: int = DEFAULT_MAX_LINES,
        *,
        ws_url: str = "",
        api_base: str = "",
    ):
        self.bot_id = bot_id
        self.secret = secret
        self.ws_url = str(ws_url or "").strip() or WECOM_WS_URL
        self.api_base = (
            str(api_base or "").strip()
            or str(os.getenv("WECOM_API_BASE") or "").strip()
            or WECOM_API_BASE
        ).rstrip("/")
        self.log_path = log_path
        self.max_chars = max_chars
        self.max_lines = max_lines

        # Message queue (thread-safe)
        self._message_queue: List[Dict[str, Any]] = []
        self._queue_lock = threading.Lock()

        # Rate limiter
        self._rate_limiter = RateLimiter(max_per_second=5.0)

        # Connection state
        self._connected = False
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_running = False
        self._ws_app: Any = None  # active WebSocketApp instance
        self._ws_send_lock = threading.Lock()

        # Early failure detection (Feishu pattern)
        self._ws_started = threading.Event()
        self._ws_connect_error: Optional[str] = None

        # Deduplication
        self._seen_msg_ids: Dict[str, float] = {}

        # Reply refs: chat_id → {"req_id": str, "ts": float}
        self._reply_refs: Dict[str, Dict[str, Any]] = {}
        self._reply_lock = threading.Lock()

    def _log(self, msg: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [wecom] {msg}"
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def _store_reply_ref(self, chat_id: str, req_id: str) -> None:
        """Store the latest callback req_id for outbound replies."""
        if not chat_id or not req_id:
            return
        with self._reply_lock:
            self._reply_refs[chat_id] = {
                "req_id": req_id,
                "ts": time.time(),
            }
            if len(self._reply_refs) > REPLY_REF_MAX_ENTRIES:
                sorted_items = sorted(
                    self._reply_refs.items(),
                    key=lambda item: float((item[1] or {}).get("ts") or 0.0),
                )
                self._reply_refs = dict(sorted_items[-REPLY_REF_MAX_ENTRIES:])

    def _get_reply_req_id(self, chat_id: str) -> str:
        """Get the latest callback req_id for the given chat_id."""
        with self._reply_lock:
            entry = self._reply_refs.get(chat_id)
            if not entry:
                return ""
            return str(entry.get("req_id") or "")

    # -- WebSocket send helper --

    def _ws_send(self, payload: Dict[str, Any]) -> bool:
        """Thread-safe send via active WebSocket connection."""
        with self._ws_send_lock:
            ws = self._ws_app
            if not ws:
                return False
            try:
                ws.send(json.dumps(payload))
                return True
            except Exception as e:
                self._log(f"[ws_send] Error: {e}")
                return False

    def _build_subscribe_frame(self) -> Dict[str, Any]:
        return {
            "cmd": "aibot_subscribe",
            "headers": {
                "req_id": f"aibot_subscribe_{int(time.time() * 1000)}",
            },
            "body": {
                "bot_id": self.bot_id,
                "secret": self.secret,
            },
        }

    def _build_reply_frame(self, *, req_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": body,
        }

    def _next_stream_id(self) -> str:
        return f"cccc-wecom-{int(time.time() * 1000)}"

    def _guess_content_type(self, filename: str, media_type: str) -> str:
        if media_type == "image":
            guessed, _ = mimetypes.guess_type(filename or "")
            if guessed and guessed.startswith("image/"):
                return guessed
            return "image/png"
        guessed, _ = mimetypes.guess_type(filename or "")
        return guessed or "application/octet-stream"

    def _build_media_api_url(self, path: str, **query: str) -> str:
        params = {
            "bot_id": self.bot_id,
            "secret": self.secret,
        }
        params.update({k: v for k, v in query.items() if str(v or "").strip()})
        return f"{self.api_base}{path}?{urllib.parse.urlencode(params)}"

    def _decode_media_aes_key(self, raw_key: str) -> bytes:
        trimmed = str(raw_key or "").strip()
        if not trimmed:
            raise ValueError("missing wecom attachment aeskey")

        utf8_key = trimmed.encode("utf-8")
        if len(utf8_key) == 32:
            return utf8_key

        padded = trimmed if trimmed.endswith("=") else f"{trimmed}="
        try:
            decoded = b64decode(padded)
        except Exception as e:
            raise ValueError(f"invalid wecom attachment aeskey: {e}") from e

        if len(decoded) == 32:
            return decoded
        raise ValueError(
            f"invalid wecom attachment aeskey length: utf8={len(utf8_key)} base64={len(decoded)}"
        )

    def _strip_pkcs7_padding(self, raw: bytes, block_size: int = WECOM_MEDIA_BLOCK_SIZE) -> bytes:
        if not raw:
            raise ValueError("empty decrypted media payload")
        pad = raw[-1]
        if pad < 1 or pad > block_size or pad > len(raw):
            raise ValueError("invalid wecom media pkcs7 padding")
        if raw[-pad:] != bytes([pad]) * pad:
            raise ValueError("invalid wecom media pkcs7 padding")
        return raw[:-pad]

    def _decrypt_media_bytes(self, encrypted: bytes, aes_key: str) -> bytes:
        key = self._decode_media_aes_key(aes_key)
        iv = key[:16]
        cmd = [
            "openssl",
            "enc",
            "-d",
            f"-{WECOM_MEDIA_CIPHER}",
            "-nopad",
            "-K",
            key.hex(),
            "-iv",
            iv.hex(),
        ]
        try:
            proc = subprocess.run(
                cmd,
                input=encrypted,
                capture_output=True,
                check=False,
            )
        except Exception as e:
            raise ValueError(f"wecom media decrypt command failed: {e}") from e
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"wecom media decrypt failed: {stderr or f'openssl exit {proc.returncode}'}")
        return self._strip_pkcs7_padding(proc.stdout)

    def _upload_media(self, raw: bytes, filename: str, media_type: str) -> str:
        boundary = "----cccc" + uuid.uuid4().hex
        safe_fn = (filename or "file").replace("\\", "_").replace("/", "_")
        content_type = self._guess_content_type(safe_fn, media_type)

        body = b""
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media"; filename="{safe_fn}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        body += raw
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            self._build_media_api_url("/media/upload", type=media_type),
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:
            raise ValueError(f"upload failed: {e}") from e

        if int(result.get("errcode", 0) or 0) != 0:
            raise ValueError(str(result.get("errmsg") or "upload failed"))

        media_id = str(result.get("media_id") or "").strip()
        if not media_id:
            raise ValueError("upload response missing media_id")
        return media_id

    def _send_media_reply(self, chat_id: str, *, msgtype: str, body: Dict[str, Any]) -> bool:
        req_id = self._get_reply_req_id(chat_id)
        if not req_id:
            self._log(
                f"[send_file] No callback req_id for chat={chat_id}, cannot send media. "
                "Ask the user to send any message in that chat to re-establish outbound replies."
            )
            return False
        return self._ws_send(self._build_reply_frame(req_id=req_id, body={"msgtype": msgtype, **body}))

    # -- WebSocket connection --

    def connect(self) -> bool:
        """
        Initialize connection to WeCom.

        1. Disable proxy environment variables for WebSocket reliability
        2. Start WebSocket listener daemon thread
        3. Wait for authentication confirmation or early failure
        """
        with self._queue_lock:
            self._message_queue.clear()

        self._disable_proxies()

        # Start WebSocket listener
        self._ws_connect_error = None
        self._ws_started.clear()
        self._start_ws_listener()

        # Wait for connection attempt to complete (early failure detection)
        if not self._ws_started.wait(timeout=10.0):
            self._log("[connect] Timeout waiting for WebSocket to start")
            return False

        if self._ws_connect_error:
            self._log(f"[connect] WebSocket connection failed: {self._ws_connect_error}")
            return False

        # Verify thread is alive after brief settle
        time.sleep(0.5)
        if self._ws_thread and not self._ws_thread.is_alive():
            self._log("[connect] WebSocket thread died unexpectedly")
            return False

        self._connected = True
        self._log(f"[connect] Connected (bot_id={self.bot_id[:8]}...)")
        return True

    def _start_ws_listener(self) -> None:
        """Start WebSocket listener daemon thread with reconnect logic."""
        if self._ws_thread and self._ws_thread.is_alive():
            return

        self._ws_running = True

        def ws_loop() -> None:
            try:
                import websocket  # type: ignore[import-untyped]
            except ImportError:
                import sys
                self._log(f"[ws] Missing dependency: websocket-client. Install: {sys.executable} -m pip install websocket-client")
                self._ws_connect_error = "Missing websocket-client"
                self._ws_started.set()
                return

            backoff = WS_RECONNECT_INITIAL
            first_connect = True

            while self._ws_running:
                ws = None
                heartbeat_failures = 0
                awaiting_ping_ack = False
                connected_event = threading.Event()
                subscribe_acked = threading.Event()
                error_holder: List[str] = []

                def on_open(ws_conn: Any) -> None:
                    nonlocal heartbeat_failures
                    heartbeat_failures = 0
                    self._ws_app = ws_conn
                    connected_event.set()
                    self._log("[ws] Connection opened, sending subscribe auth frame")
                    ws_conn.send(json.dumps(self._build_subscribe_frame()))

                def on_message(ws_conn: Any, message: str) -> None:
                    nonlocal heartbeat_failures, awaiting_ping_ack
                    try:
                        data = json.loads(message)
                    except Exception:
                        self._log(f"[ws] Non-JSON message: {message[:200]}")
                        return

                    cmd = str(data.get("cmd") or "")
                    req_id = str((data.get("headers") or {}).get("req_id") or "")

                    if req_id.startswith("ping"):
                        awaiting_ping_ack = False
                        if int(data.get("errcode", 0) or 0) == 0:
                            heartbeat_failures = 0
                        else:
                            heartbeat_failures += 1
                            self._log(f"[ws] Heartbeat ack error: {data.get('errmsg')}")
                    elif cmd == "aibot_msg_callback":
                        heartbeat_failures = 0
                        awaiting_ping_ack = False
                        self._enqueue_message(data)
                    elif cmd == "aibot_event_callback":
                        heartbeat_failures = 0
                        awaiting_ping_ack = False
                        self._log("[ws] Ignoring event callback")
                    elif req_id.startswith("aibot_subscribe"):
                        if int(data.get("errcode", 0) or 0) == 0:
                            heartbeat_failures = 0
                            awaiting_ping_ack = False
                            subscribe_acked.set()
                            self._log("[ws] Subscribe acknowledged")
                        else:
                            errmsg = str(data.get("errmsg") or "subscribe failed")
                            error_holder.append(f"Authentication failed: {errmsg}")
                    else:
                        heartbeat_failures = 0
                        awaiting_ping_ack = False
                        self._log(f"[ws] Unhandled frame: cmd={cmd or '<ack>'} req_id={req_id or '<none>'}")

                def on_error(ws_conn: Any, error: Any) -> None:
                    error_holder.append(str(error))
                    self._log(f"[ws] Error: {error}")

                def on_close(ws_conn: Any, close_status: Any, close_msg: Any) -> None:
                    self._ws_app = None
                    connected_event.set()  # unblock waiters
                    self._log(f"[ws] Connection closed: status={close_status} msg={close_msg}")

                try:
                    ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_open=on_open,
                        on_message=on_message,
                        on_error=on_error,
                        on_close=on_close,
                    )

                    # Run WebSocket in a sub-thread so we can manage heartbeat
                    ws_run_thread = threading.Thread(
                        target=ws.run_forever,
                        kwargs={"ping_interval": 0},  # we handle heartbeat manually
                        daemon=True,
                    )
                    ws_run_thread.start()

                    # Wait for connection
                    connected_event.wait(timeout=10.0)

                    if error_holder:
                        raise ConnectionError(error_holder[0])

                    if not connected_event.is_set():
                        raise ConnectionError("Connection timeout")

                    # Wait for subscribe ack
                    if not subscribe_acked.wait(timeout=5.0):
                        raise ConnectionError("Subscribe ack timeout")

                    # Signal success on first connect
                    if first_connect:
                        self._ws_connect_error = None
                        self._ws_started.set()
                        first_connect = False

                    backoff = WS_RECONNECT_INITIAL  # reset on success
                    self._log("[ws] Subscribed, entering heartbeat loop")

                    # Heartbeat loop (uses _ws_send for thread safety)
                    while self._ws_running and ws_run_thread.is_alive():
                        time.sleep(WS_HEARTBEAT_INTERVAL)
                        if not self._ws_running:
                            break
                        if awaiting_ping_ack:
                            heartbeat_failures += 1
                            awaiting_ping_ack = False
                            self._log(
                                f"[ws] Heartbeat ack timeout ({heartbeat_failures}/{WS_HEARTBEAT_MAX_FAILURES})"
                            )
                            if heartbeat_failures >= WS_HEARTBEAT_MAX_FAILURES:
                                self._log("[ws] Too many heartbeat failures, reconnecting")
                                break
                        ping_req_id = f"ping_{int(time.time() * 1000)}"
                        if self._ws_send({
                            "cmd": "ping",
                            "headers": {"req_id": ping_req_id},
                        }):
                            awaiting_ping_ack = True
                        else:
                            heartbeat_failures += 1
                            awaiting_ping_ack = False
                            self._log(f"[ws] Heartbeat send failed ({heartbeat_failures}/{WS_HEARTBEAT_MAX_FAILURES})")
                            if heartbeat_failures >= WS_HEARTBEAT_MAX_FAILURES:
                                self._log("[ws] Too many heartbeat failures, reconnecting")
                                break

                except Exception as e:
                    err_msg = str(e)
                    self._log(f"[ws] Connection attempt failed: {err_msg}")
                    if first_connect:
                        self._ws_connect_error = err_msg
                        self._ws_started.set()
                        return  # fatal on first connect

                finally:
                    self._ws_app = None
                    if ws:
                        try:
                            ws.close()
                        except Exception:
                            pass

                if not self._ws_running:
                    break

                # Exponential backoff with jitter
                jitter = backoff * random.uniform(0, WS_RECONNECT_JITTER)
                sleep_time = backoff + jitter
                self._log(f"[ws] Reconnecting in {sleep_time:.1f}s")
                time.sleep(sleep_time)
                backoff = min(backoff * 2, WS_RECONNECT_MAX)

            self._log("[ws] Listener stopped")

        self._ws_thread = threading.Thread(target=ws_loop, daemon=True)
        self._ws_thread.start()

    def disconnect(self) -> None:
        """Disconnect from WeCom. Clean up WS thread, token, queues, handles."""
        self._connected = False
        self._ws_running = False

        # Close active WebSocket
        ws = self._ws_app
        if ws:
            try:
                ws.close()
            except Exception:
                pass
            self._ws_app = None

        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=5)

        # Clear state
        with self._queue_lock:
            self._message_queue.clear()
        with self._reply_lock:
            self._reply_refs.clear()

        self._seen_msg_ids.clear()
        self._log("[disconnect] WeCom adapter disconnected")

    # -- Inbound message normalization --

    def _should_enqueue_message(self, conversation_id: str, msg_id: str) -> bool:
        """Check if message should be enqueued (deduplication)."""
        mid = str(msg_id or "").strip()
        if not mid:
            return True

        now = time.time()
        key = f"{conversation_id}:{mid}"

        if key in self._seen_msg_ids:
            self._log(f"[dedup] Skipping duplicate: {key}")
            return False

        self._seen_msg_ids[key] = now

        # Opportunistic pruning (keep memory bounded)
        if len(self._seen_msg_ids) > 2048:
            cutoff = now - 3600.0  # 1h
            self._seen_msg_ids = {k: ts for k, ts in self._seen_msg_ids.items() if ts >= cutoff}
            if len(self._seen_msg_ids) > 4096:
                sorted_items = sorted(self._seen_msg_ids.items(), key=lambda x: x[1])
                self._seen_msg_ids = dict(sorted_items[len(sorted_items) // 2:])

        return True

    def _pick_text(self, *values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _collect_media_payload(self, payload: Dict[str, Any], msg_type: str, content: Any) -> Dict[str, str]:
        media: Dict[str, Any] = {}
        if isinstance(content, dict):
            media.update(content)
        nested = payload.get(msg_type)
        if isinstance(nested, dict):
            media.update(nested)

        media_id = self._pick_text(media.get("media_id"), media.get("mediaId"))
        download_url = self._pick_text(media.get("url"), media.get("download_url"), media.get("downloadUrl"))
        aeskey = self._pick_text(media.get("aeskey"), media.get("aes_key"), media.get("decryption_key"))
        filename = self._pick_text(
            media.get("filename"),
            media.get("file_name"),
            media.get("fileName"),
            media.get("name"),
        )
        content_type = self._pick_text(media.get("content_type"), media.get("contentType"), media.get("mime_type"))
        return {
            "media_id": media_id,
            "download_url": download_url,
            "aeskey": aeskey,
            "filename": filename,
            "content_type": content_type,
        }

    def _build_media_attachment(self, msg_type: str, media_meta: Dict[str, str]) -> Optional[Dict[str, Any]]:
        media_id = media_meta["media_id"]
        download_url = media_meta["download_url"]
        aeskey = media_meta["aeskey"]
        filename = media_meta["filename"]
        content_type = media_meta["content_type"]

        if not (media_id or download_url):
            return None

        if msg_type == "image":
            attachment: Dict[str, Any] = {
                "provider": "wecom",
                "kind": "image",
                "file_name": filename or "image.png",
                "mime_type": content_type or "image/png",
            }
        elif msg_type == "file":
            attachment = {
                "provider": "wecom",
                "kind": "file",
                "file_name": filename or "file",
                "mime_type": content_type or self._guess_content_type(filename or "file", "file"),
            }
        elif msg_type == "video":
            attachment = {
                "provider": "wecom",
                "kind": "video",
                "file_name": filename or "video.mp4",
                "mime_type": content_type or self._guess_content_type(filename or "video.mp4", "file"),
            }
        elif msg_type == "voice":
            attachment = {
                "provider": "wecom",
                "kind": "voice",
                "file_name": filename or "voice.amr",
                "mime_type": content_type or self._guess_content_type(filename or "voice.amr", "file"),
            }
        else:
            return None

        if media_id:
            attachment["media_id"] = media_id
        if download_url:
            attachment["download_url"] = download_url
        if aeskey:
            attachment["aeskey"] = aeskey
            attachment["decryption_key"] = aeskey
        return attachment

    def _enqueue_message(self, data: Dict[str, Any]) -> None:
        """
        Process incoming aibot_msg_callback and enqueue normalized message.

        Supports both the current SDK-style frame shape:
        { cmd, headers: { req_id }, body: { msgid, msgtype, chatid, chattype, ... } }

        and the older action/data shape used by the initial local prototype.
        """
        try:
            req_id = ""
            payload = data.get("data") or {}
            if not payload and str(data.get("cmd") or "") == "aibot_msg_callback":
                req_id = str((data.get("headers") or {}).get("req_id") or "").strip()
                payload = data.get("body") or {}
            if not payload:
                return

            conversation_id = str(
                payload.get("conversation_id")
                or payload.get("chatid")
                or ((payload.get("from") or {}).get("userid") if isinstance(payload.get("from"), dict) else "")
                or ""
            ).strip()
            msg_id = str(payload.get("msg_id") or payload.get("msgid") or "").strip()

            if not self._should_enqueue_message(conversation_id, msg_id):
                return

            if req_id and conversation_id:
                self._store_reply_ref(conversation_id, req_id)

            # Extract text based on msg_type
            msg_type = str(payload.get("msg_type") or payload.get("msgtype") or "text").strip()
            content = payload.get("content") or {}
            media_meta = self._collect_media_payload(payload, msg_type, content)

            if msg_type == "text":
                text = str(
                    content.get("text")
                    or ((payload.get("text") or {}).get("content") if isinstance(payload.get("text"), dict) else "")
                    or ""
                ).strip()
            elif msg_type == "image":
                text = "[image]"
            elif msg_type == "file":
                filename = media_meta["filename"]
                text = f"[file: {filename or 'unknown'}]"
            elif msg_type == "voice":
                text = str(((payload.get("voice") or {}).get("content") if isinstance(payload.get("voice"), dict) else "") or "").strip() or "[voice]"
            elif msg_type == "video":
                text = "[video]"
            elif msg_type == "mixed":
                mixed = (payload.get("mixed") or {}).get("msg_item") if isinstance(payload.get("mixed"), dict) else None
                text_parts: List[str] = []
                mixed_media_types: List[str] = []
                mixed_attachments: List[Dict[str, Any]] = []
                if isinstance(mixed, list):
                    for item in mixed:
                        if not isinstance(item, dict):
                            continue
                        item_type = str(item.get("msgtype") or "").strip()
                        if item_type == "text":
                            part = str(((item.get("text") or {}).get("content") if isinstance(item.get("text"), dict) else "") or "").strip()
                            if part:
                                text_parts.append(part)
                            continue
                        media_meta_item = self._collect_media_payload(item, item_type, item.get(item_type) or {})
                        attachment = self._build_media_attachment(item_type, media_meta_item)
                        if attachment:
                            mixed_attachments.append(attachment)
                            mixed_media_types.append(item_type)
                if text_parts:
                    text = "\n".join(text_parts).strip()
                elif "image" in mixed_media_types:
                    text = "[image]"
                elif mixed_media_types:
                    text = f"[{mixed_media_types[0]}]"
                else:
                    text = "[mixed]"
            else:
                text = f"[{msg_type}]"

            if not text.strip():
                self._log(f"[enqueue] Discarding empty message: msg_type={msg_type}")
                return

            # Sender info
            sender = payload.get("sender") or {}
            if not sender and isinstance(payload.get("from"), dict):
                sender = payload.get("from") or {}
            from_user_id = str(sender.get("user_id") or sender.get("userid") or "").strip()

            # Chat type mapping: single→p2p, group→group
            raw_chat_type = str(payload.get("chat_type") or payload.get("chattype") or "").strip().lower()
            if raw_chat_type == "single":
                chat_type = "p2p"
            elif raw_chat_type == "group":
                chat_type = "group"
            else:
                chat_type = raw_chat_type or "unknown"

            attachments: List[Dict[str, Any]] = []
            attachment = self._build_media_attachment(msg_type, media_meta)
            if attachment:
                attachments.append(attachment)
            if msg_type == "mixed":
                attachments.extend(mixed_attachments)

            # In p2p, always routed; in group, routed if bot is mentioned
            is_at_bot = bool(
                payload.get("is_at_bot")
                or payload.get("is_mention_bot")
                or payload.get("is_at_bot_in_group")
            )
            routed = bool(chat_type == "p2p" or is_at_bot)

            normalized = {
                "chat_id": conversation_id,
                "chat_title": conversation_id,
                "chat_type": chat_type,
                "routed": routed,
                "thread_id": 0,
                "text": text,
                "attachments": attachments,
                "from_user": from_user_id,
                "message_id": msg_id,
                "timestamp": time.time(),
            }

            with self._queue_lock:
                self._message_queue.append(normalized)

            self._log(f"[enqueue] chat={conversation_id} type={msg_type} from={from_user_id[:8] if from_user_id else '?'}")

        except Exception as e:
            self._log(f"[enqueue] Error: {e}")

    def poll(self) -> List[Dict[str, Any]]:
        """Return queued inbound messages."""
        with self._queue_lock:
            messages = list(self._message_queue)
            self._message_queue.clear()
        return messages

    # -- Step 10: send_message --

    def _compose_safe(self, text: str) -> str:
        """Truncate text to WeCom limits."""
        lines = text.split("\n")
        if len(lines) > self.max_lines:
            lines = lines[: self.max_lines]
            lines.append("... (truncated)")
        result = "\n".join(lines)
        if len(result) > self.max_chars:
            result = result[: self.max_chars - 20] + "\n... (truncated)"
        return result

    def send_message(
        self,
        chat_id: str,
        text: str,
        thread_id: Optional[int] = None,
        *,
        mention_user_ids: Optional[List[str]] = None,
    ) -> bool:
        """
        Send a text message via aibot_respond_msg over WebSocket.

        Requires a valid respond_handle captured from inbound aibot_msg_callback.
        Returns False if no handle is available (WeCom conversation-level REST
        API does not map cleanly to conversation_id, so no fallback).
        """
        _ = thread_id
        _ = mention_user_ids

        if not text:
            return True
        if not self._connected:
            return False

        safe_text = self._compose_safe(text)
        self._rate_limiter.wait_and_acquire(chat_id)

        req_id = self._get_reply_req_id(chat_id)
        if not req_id:
            self._log(
                f"[send] No callback req_id for chat={chat_id}, cannot send. "
                "Ask the user to send any message in that chat to re-establish outbound replies."
            )
            return False

        ok = self._ws_send(self._build_reply_frame(
            req_id=req_id,
            body={
                "msgtype": "stream",
                "stream": {
                    "id": self._next_stream_id(),
                    "finish": True,
                    "content": safe_text,
                },
            },
        ))
        if ok:
            self._log(f"[send] Sent via WS respond (chat={chat_id})")
        return ok

    def download_attachment(self, attachment: Dict[str, Any]) -> bytes:
        download_url = str(
            attachment.get("download_url")
            or attachment.get("url")
            or ""
        ).strip()
        decryption_key = str(
            attachment.get("decryption_key")
            or attachment.get("aeskey")
            or attachment.get("aes_key")
            or ""
        ).strip()

        if download_url:
            req = urllib.request.Request(download_url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
            except Exception as e:
                raise ValueError(f"download failed: {e}") from e

            if decryption_key:
                try:
                    return self._decrypt_media_bytes(raw, decryption_key)
                except Exception as e:
                    raise ValueError(f"decrypt failed: {e}") from e
            return raw

        media_id = str(attachment.get("media_id") or "").strip()
        if not media_id:
            raise ValueError("missing wecom attachment media_id or download_url")

        req = urllib.request.Request(
            self._build_media_api_url("/media/get", media_id=media_id),
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except Exception as e:
            raise ValueError(f"download failed: {e}") from e

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

        try:
            raw = file_path.read_bytes()
        except Exception as e:
            self._log(f"[send_file] Read failed: {e}")
            return False

        ext = file_path.suffix.lower()
        is_image = ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
        media_type = "image" if is_image else "file"

        self._rate_limiter.wait_and_acquire(chat_id)

        try:
            media_id = self._upload_media(raw, filename or file_path.name, media_type)
        except Exception as e:
            self._log(f"[send_file] Upload failed: {e}")
            return False

        if is_image:
            ok = self._send_media_reply(
                chat_id,
                msgtype="image",
                body={"image": {"media_id": media_id}},
            )
        else:
            ok = self._send_media_reply(
                chat_id,
                msgtype="file",
                body={"file": {"media_id": media_id, "filename": str(filename or file_path.name or 'file')}},
            )

        if not ok:
            return False

        safe_caption = self._compose_safe(caption)
        if safe_caption:
            if not self.send_message(chat_id, safe_caption):
                self._log(f"[send_file] Media sent but caption follow-up failed (chat={chat_id})")

        return True

    # -- Step 11: Streaming reply --

    def begin_stream(
        self,
        chat_id: str,
        stream_id: str,
        *,
        text: str = "",
        thread_id: Optional[int] = None,
    ) -> Optional[OutboundStreamHandle]:
        """
        Begin a streaming reply via aibot_respond_msg with finish=false.

        Returns an OutboundStreamHandle if a respond_handle is available.
        """
        _ = thread_id

        req_id = self._get_reply_req_id(chat_id)
        if not req_id:
            self._log(f"[stream] No callback req_id for chat={chat_id}")
            return None

        stream_body = {
            "msgtype": "stream",
            "stream": {
                "id": stream_id,
                "finish": False,
                "content": text,
            },
        }
        if text:
            ok = self._ws_send(self._build_reply_frame(req_id=req_id, body=stream_body))
            if not ok:
                self._log(f"[stream] begin_stream WS send failed (chat={chat_id})")
                return None

        out_handle = OutboundStreamHandle(
            stream_id=stream_id,
            platform_handle={"req_id": req_id, "stream_id": stream_id},
        )
        self._log(f"[stream] begin_stream OK (chat={chat_id} stream={stream_id})")
        return out_handle

    def update_stream(
        self,
        handle: OutboundStreamHandle,
        *,
        text: str = "",
        seq: int = 0,
    ) -> bool:
        """Send an intermediate streaming chunk (finish=false)."""
        platform_handle = handle.get("platform_handle", {})
        if not isinstance(platform_handle, dict):
            return False
        req_id = str(platform_handle.get("req_id") or "")
        stream_id = str(platform_handle.get("stream_id") or handle.get("stream_id") or "")
        if not req_id or not stream_id:
            return False
        _ = seq

        return self._ws_send(self._build_reply_frame(
            req_id=req_id,
            body={
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": False,
                    "content": text,
                },
            },
        ))

    def end_stream(
        self,
        handle: OutboundStreamHandle,
        *,
        text: str = "",
    ) -> bool:
        """Finalize a streaming reply (finish=true)."""
        platform_handle = handle.get("platform_handle", {})
        if not isinstance(platform_handle, dict):
            return False
        req_id = str(platform_handle.get("req_id") or "")
        stream_id = str(platform_handle.get("stream_id") or handle.get("stream_id") or "")
        if not req_id or not stream_id:
            return False

        ok = self._ws_send(self._build_reply_frame(
            req_id=req_id,
            body={
                "msgtype": "stream",
                "stream": {
                    "id": stream_id,
                    "finish": True,
                    "content": text,
                },
            },
        ))
        if ok:
            self._log(f"[stream] end_stream OK (stream={handle.get('stream_id', '')})")
        return ok

    # -- Step 13: get_chat_title + enhanced disconnect --

    def get_chat_title(self, chat_id: str) -> str:
        """Current inbound callback does not provide a separate title lookup path."""
        return chat_id
