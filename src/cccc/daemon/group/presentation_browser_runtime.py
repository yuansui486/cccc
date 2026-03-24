"""Daemon-local browser-surface runtime for Presentation slots.

This module intentionally keeps the runtime model narrow:

1. one live browser session per slot
2. one active controller connection at a time
3. frame projection over JSON-lines sockets
4. input relay back into the same Chromium session

The browser session lifetime follows the slot content, not the viewer chrome:
closing the viewer hides it visually, while clear/replace is the destruction
boundary.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ...paths import ensure_home
from ...util.time import utc_now_iso

_FRAME_INTERVAL_SECONDS = 0.35
_SOCKET_READ_TIMEOUT_SECONDS = 0.2
_START_WAIT_TIMEOUT_SECONDS = 20.0


def _ensure_dir(path: Path, mode: int = 0o700) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def _safe_group_token(group_id: str) -> str:
    raw = str(group_id or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    cleaned = cleaned.strip("_")
    return cleaned[:96] or "group"


def _safe_slot_token(slot_id: str) -> str:
    raw = str(slot_id or "").strip().lower().replace("_", "-")
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    cleaned = cleaned.strip("_")
    return cleaned[:96] or "slot"


def _browser_profile_dir(group_id: str, slot_id: str) -> Path:
    root = (
        ensure_home()
        / "state"
        / "presentation_browser"
        / _safe_group_token(group_id)
        / _safe_slot_token(slot_id)
        / "profile"
    )
    _ensure_dir(root, 0o700)
    return root


def _reset_browser_profile_dir(group_id: str, slot_id: str) -> None:
    root = (
        ensure_home()
        / "state"
        / "presentation_browser"
        / _safe_group_token(group_id)
        / _safe_slot_token(slot_id)
    )
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _install_playwright_package() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "playwright>=1.40,<2",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        detail = str(proc.stderr or "").strip() or str(proc.stdout or "").strip() or "pip install playwright failed"
        raise RuntimeError(detail[:1000])


def _install_playwright_chromium() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        detail = str(proc.stderr or "").strip() or str(proc.stdout or "").strip() or "playwright install chromium failed"
        raise RuntimeError(detail[:800])


def _ensure_sync_playwright():
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except Exception:
        _install_playwright_package()
    try:
        from playwright.sync_api import sync_playwright

        return sync_playwright
    except Exception as exc:
        raise RuntimeError(f"failed to initialize Playwright after auto-install: {exc}") from exc


class _PlaywrightBrowserRuntime:
    def __init__(
        self,
        *,
        playwright_cm: Any,
        context: Any,
        page: Any,
        cdp_session: Any,
        width: int,
        height: int,
    ) -> None:
        self._lock = threading.RLock()
        self._playwright_cm = playwright_cm
        self._context = context
        self._page = page
        self._cdp = cdp_session
        self.strategy = "playwright_chromium_cdp"
        self.width = int(width)
        self.height = int(height)
        self._page_history: list[Any] = []
        self._known_page_ids: set[int] = set()
        self._register_page(page)
        try:
            self._context.on("page", self._handle_new_page)
        except Exception:
            pass

    @property
    def page(self) -> Any:
        with self._lock:
            return self._page

    def _is_page_live(self, page: Any) -> bool:
        if page is None:
            return False
        try:
            return not bool(page.is_closed())
        except Exception:
            return False

    def _remember_page(self, page: Any) -> None:
        if not self._is_page_live(page):
            return
        self._page_history = [item for item in self._page_history if item is not page and self._is_page_live(item)]
        self._page_history.append(page)

    def _pop_previous_page(self) -> Any | None:
        while self._page_history:
            candidate = self._page_history.pop()
            if self._is_page_live(candidate):
                return candidate
        return None

    def _register_page(self, page: Any) -> None:
        if page is None:
            return
        page_id = id(page)
        if page_id in self._known_page_ids:
            return
        self._known_page_ids.add(page_id)
        try:
            page.on("close", lambda *_args, _page=page: self._handle_closed_page(_page))
        except Exception:
            pass

    def _bind_cdp(self, page: Any) -> None:
        old_cdp = self._cdp
        self._page = page
        self._cdp = self._context.new_cdp_session(page)
        try:
            self._cdp.send("Page.enable")
        except Exception:
            pass
        try:
            page.set_viewport_size({"width": self.width, "height": self.height})
        except Exception:
            pass
        if old_cdp is not None and old_cdp is not self._cdp:
            try:
                old_cdp.detach()
            except Exception:
                pass

    def _activate_page(self, page: Any, *, remember_previous: bool) -> None:
        if not self._is_page_live(page):
            return
        with self._lock:
            current = self._page
            if remember_previous and current is not None and current is not page and self._is_page_live(current):
                self._remember_page(current)
            self._register_page(page)
            self._bind_cdp(page)

    def _handle_new_page(self, page: Any) -> None:
        if page is None:
            return
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        self._activate_page(page, remember_previous=True)

    def _handle_closed_page(self, page: Any) -> None:
        fallback = None
        with self._lock:
            self._page_history = [item for item in self._page_history if item is not page and self._is_page_live(item)]
            if page is not self._page:
                return
            fallback = self._pop_previous_page()
            if fallback is None:
                for candidate in reversed(list(getattr(self._context, "pages", []) or [])):
                    if candidate is not page and self._is_page_live(candidate):
                        fallback = candidate
                        break
        if fallback is not None:
            self._activate_page(fallback, remember_previous=False)

    def current_url(self) -> str:
        try:
            page = self.page
            return str(getattr(page, "url", "") or "").strip()
        except Exception:
            return ""

    def capture_frame(self) -> bytes:
        with self._lock:
            cdp = self._cdp
        payload = cdp.send(
            "Page.captureScreenshot",
            {
                "format": "jpeg",
                "quality": 60,
                "captureBeyondViewport": False,
                "fromSurface": True,
            },
        )
        data = str((payload or {}).get("data") or "")
        return base64.b64decode(data) if data else b""

    def click(self, *, x: float, y: float, button: str = "left") -> None:
        self.page.mouse.click(float(x), float(y), button=str(button or "left"))

    def scroll(self, *, dx: float, dy: float) -> None:
        self.page.mouse.wheel(float(dx), float(dy))

    def key_press(self, *, key: str) -> None:
        self.page.keyboard.press(str(key or ""))

    def input_text(self, *, text: str) -> None:
        self.page.keyboard.insert_text(str(text or ""))

    def resize(self, *, width: int, height: int) -> None:
        self.width = int(width)
        self.height = int(height)
        self.page.set_viewport_size({"width": self.width, "height": self.height})

    def navigate(self, *, url: str) -> None:
        self.page.goto(str(url or ""), wait_until="domcontentloaded", timeout=30000)

    def refresh(self) -> None:
        self.page.reload(wait_until="domcontentloaded", timeout=30000)

    def back(self) -> None:
        page = self.page
        try:
            result = page.go_back(wait_until="domcontentloaded", timeout=10000)
        except Exception:
            result = None
        if result is not None:
            return
        with self._lock:
            fallback = self._pop_previous_page()
        if fallback is not None:
            self._activate_page(fallback, remember_previous=False)

    def close(self) -> None:
        try:
            if self._cdp is not None:
                self._cdp.detach()
        except Exception:
            pass
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            self._playwright_cm.__exit__(None, None, None)
        except Exception:
            pass


def _launch_browser_surface_runtime(*, group_id: str, slot_id: str, url: str, width: int, height: int) -> _PlaywrightBrowserRuntime:
    sync_playwright = _ensure_sync_playwright()
    playwright_cm = sync_playwright()
    pw = playwright_cm.__enter__()

    def _open_once() -> _PlaywrightBrowserRuntime:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(_browser_profile_dir(group_id, slot_id)),
            headless=True,
            viewport={"width": int(width), "height": int(height)},
        )
        pages = list(getattr(context, "pages", []) or [])
        page = pages[0] if pages else context.new_page()
        page.set_viewport_size({"width": int(width), "height": int(height)})
        cdp_session = context.new_cdp_session(page)
        try:
            cdp_session.send("Page.enable")
        except Exception:
            pass
        if str(url or "").strip():
            page.goto(str(url).strip(), wait_until="domcontentloaded", timeout=30000)
        return _PlaywrightBrowserRuntime(
            playwright_cm=playwright_cm,
            context=context,
            page=page,
            cdp_session=cdp_session,
            width=width,
            height=height,
        )

    try:
        return _open_once()
    except Exception as exc:
        message = str(exc or "")
        needs_install = "Executable doesn't exist" in message or "playwright install" in message
        if not needs_install:
            try:
                playwright_cm.__exit__(None, None, None)
            except Exception:
                pass
            raise
        try:
            _install_playwright_chromium()
            return _open_once()
        except Exception:
            try:
                playwright_cm.__exit__(None, None, None)
            except Exception:
                pass
            raise


class _BrowserSurfaceSession:
    def __init__(self, *, group_id: str, slot_id: str, url: str, width: int, height: int) -> None:
        self.group_id = str(group_id or "").strip()
        self.slot_id = str(slot_id or "").strip()
        self.initial_url = str(url or "").strip()
        self.width = max(640, min(int(width), 2560))
        self.height = max(480, min(int(height), 1600))
        self._lock = threading.Lock()
        self._frame_cond = threading.Condition(self._lock)
        self._commands: "queue.Queue[tuple[str, dict[str, Any], Optional[queue.Queue[dict[str, Any]]]]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"cccc-presentation-browser-{self.group_id[:12]}-{self.slot_id[:12]}",
        )
        self._controller_attached = False
        self._state = "starting"
        self._message = "Preparing browser runtime..."
        self._error: dict[str, Any] = {}
        self._strategy = ""
        self._url = self.initial_url
        self._updated_at = utc_now_iso()
        self._started_at = self._updated_at
        self._last_frame_seq = 0
        self._last_frame_at = ""
        self._last_frame_bytes = b""

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        try:
            self._commands.put_nowait(("close", {}, None))
        except Exception:
            pass
        self._thread.join(timeout=5.0)
        with self._lock:
            self._controller_attached = False
            if self._state not in {"failed", "closed"}:
                self._state = "closed"
                self._message = "Browser surface closed."
                self._updated_at = utc_now_iso()
            self._frame_cond.notify_all()

    def wait_until_started(self, timeout: float = _START_WAIT_TIMEOUT_SECONDS) -> dict[str, Any]:
        deadline = time.time() + max(1.0, float(timeout))
        while time.time() < deadline:
            snapshot = self.snapshot()
            if snapshot["state"] in {"ready", "failed"}:
                return snapshot
            time.sleep(0.05)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active": self._state in {"starting", "ready", "failed"},
                "state": self._state,
                "message": self._message,
                "error": dict(self._error),
                "strategy": self._strategy,
                "url": self._url,
                "width": self.width,
                "height": self.height,
                "started_at": self._started_at,
                "updated_at": self._updated_at,
                "last_frame_seq": self._last_frame_seq,
                "last_frame_at": self._last_frame_at,
                "controller_attached": bool(self._controller_attached),
            }

    def can_attach(self) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            if self._state not in {"starting", "ready"}:
                message = str(self._error.get("message") or self._message or "browser surface is not active")
                return False, {"code": "browser_surface_not_active", "message": message, "details": dict(self._error)}
            if self._controller_attached:
                return False, {
                    "code": "browser_surface_busy",
                    "message": "browser surface already has an active controller",
                    "details": {},
                }
            return True, {}

    def attach_socket(self, sock: socket.socket) -> bool:
        with self._lock:
            if self._controller_attached:
                return False
            self._controller_attached = True
            self._updated_at = utc_now_iso()
        threading.Thread(
            target=self._serve_socket,
            args=(sock,),
            daemon=True,
            name=f"cccc-presentation-browser-stream-{self.group_id[:12]}-{self.slot_id[:12]}",
        ).start()
        return True

    def wait_for_frame(self, *, after_seq: int, timeout: float) -> Optional[dict[str, Any]]:
        deadline = time.time() + max(0.0, float(timeout))
        with self._frame_cond:
            while self._last_frame_seq <= int(after_seq) and not self._stop_event.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._frame_cond.wait(timeout=remaining)
            if self._last_frame_seq <= int(after_seq):
                return None
            return {
                "seq": self._last_frame_seq,
                "captured_at": self._last_frame_at,
                "bytes": bytes(self._last_frame_bytes),
                "width": self.width,
                "height": self.height,
                "url": self._url,
            }

    def submit_command(self, kind: str, payload: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
        reply: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=1)
        self._commands.put((str(kind or "").strip().lower(), dict(payload or {}), reply))
        try:
            result = reply.get(timeout=max(0.1, float(timeout)))
        except queue.Empty as exc:
            raise RuntimeError("browser command timed out") from exc
        if not bool(result.get("ok")):
            raise RuntimeError(str(result.get("message") or "browser command failed"))
        return result

    def _set_state(self, state: str, *, message: str, error: Optional[dict[str, Any]] = None) -> None:
        with self._lock:
            self._state = str(state or self._state)
            self._message = str(message or self._message)
            self._error = dict(error or {})
            self._updated_at = utc_now_iso()
            self._frame_cond.notify_all()

    def _record_frame(self, frame_bytes: bytes) -> None:
        with self._frame_cond:
            self._last_frame_seq += 1
            self._last_frame_bytes = bytes(frame_bytes)
            self._last_frame_at = utc_now_iso()
            self._updated_at = self._last_frame_at
            self._frame_cond.notify_all()

    def _apply_command(self, runtime: Any, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "ping":
            return {"ok": True}
        if kind == "navigate":
            runtime.navigate(url=str(payload.get("url") or "").strip())
        elif kind == "back":
            runtime.back()
        elif kind == "refresh":
            runtime.refresh()
        elif kind == "click":
            runtime.click(
                x=float(payload.get("x") or 0.0),
                y=float(payload.get("y") or 0.0),
                button=str(payload.get("button") or "left"),
            )
        elif kind == "scroll":
            runtime.scroll(
                dx=float(payload.get("dx") or 0.0),
                dy=float(payload.get("dy") or 0.0),
            )
        elif kind == "key":
            runtime.key_press(key=str(payload.get("key") or ""))
        elif kind == "text":
            runtime.input_text(text=str(payload.get("text") or ""))
        elif kind == "resize":
            width = max(640, min(int(payload.get("width") or self.width), 2560))
            height = max(480, min(int(payload.get("height") or self.height), 1600))
            runtime.resize(width=width, height=height)
            self.width = width
            self.height = height
        elif kind == "close":
            self._stop_event.set()
            return {"ok": True}
        else:
            raise RuntimeError(f"unsupported browser command: {kind}")
        with self._lock:
            self._url = str(runtime.current_url() or self._url)
            self._updated_at = utc_now_iso()
        return {"ok": True}

    def _run(self) -> None:
        runtime: Any = None
        try:
            runtime = _launch_browser_surface_runtime(
                group_id=self.group_id,
                slot_id=self.slot_id,
                url=self.initial_url,
                width=self.width,
                height=self.height,
            )
            with self._lock:
                self._strategy = str(getattr(runtime, "strategy", "") or "")
                self._url = str(runtime.current_url() or self.initial_url)
                self._updated_at = utc_now_iso()
            self._set_state("ready", message=f"Browser surface ready ({self._strategy or 'chromium'}).")
            next_frame_at = 0.0
            while not self._stop_event.is_set():
                timeout = max(0.05, min(0.20, next_frame_at - time.time())) if next_frame_at else 0.05
                try:
                    kind, payload, reply = self._commands.get(timeout=timeout)
                except queue.Empty:
                    kind, payload, reply = "", {}, None

                if kind:
                    try:
                        result = self._apply_command(runtime, kind, payload)
                    except Exception as exc:
                        result = {"ok": False, "message": str(exc or "browser command failed")}
                    if reply is not None:
                        try:
                            reply.put_nowait(result)
                        except Exception:
                            pass
                    if kind == "close":
                        break

                now = time.time()
                if next_frame_at and now < next_frame_at:
                    continue
                with self._lock:
                    self._url = str(runtime.current_url() or self._url)
                frame = runtime.capture_frame()
                if frame:
                    self._record_frame(frame)
                next_frame_at = time.time() + _FRAME_INTERVAL_SECONDS
        except Exception as exc:
            self._set_state(
                "failed",
                message=f"Browser surface failed: {exc}",
                error={"code": "browser_surface_runtime_failed", "message": str(exc)},
            )
        finally:
            try:
                if runtime is not None:
                    runtime.close()
            except Exception:
                pass
            with self._lock:
                if self._state not in {"failed", "closed"}:
                    self._state = "closed"
                    self._message = "Browser surface closed."
                    self._updated_at = utc_now_iso()
                self._frame_cond.notify_all()

    def _serve_socket(self, sock: socket.socket) -> None:
        buffer = b""
        last_seq = 0
        sent_state_marker = ""
        try:
            sock.settimeout(_SOCKET_READ_TIMEOUT_SECONDS)
        except Exception:
            pass
        try:
            while not self._stop_event.is_set():
                snapshot = self.snapshot()
                state_marker = json.dumps(
                    {
                        "state": snapshot["state"],
                        "message": snapshot["message"],
                        "url": snapshot["url"],
                        "seq": snapshot["last_frame_seq"],
                        "updated_at": snapshot["updated_at"],
                    },
                    sort_keys=True,
                )
                if state_marker != sent_state_marker:
                    if not _send_json_line(
                        sock,
                        {
                            "t": "state",
                            **snapshot,
                        },
                    ):
                        break
                    sent_state_marker = state_marker
                    if snapshot["state"] == "failed":
                        break

                frame = self.wait_for_frame(after_seq=last_seq, timeout=0.25)
                if frame is not None:
                    if not _send_json_line(
                        sock,
                        {
                            "t": "frame",
                            "seq": frame["seq"],
                            "captured_at": frame["captured_at"],
                            "mime": "image/jpeg",
                            "data_base64": base64.b64encode(frame["bytes"]).decode("ascii"),
                            "width": frame["width"],
                            "height": frame["height"],
                            "url": frame["url"],
                        },
                    ):
                        break
                    last_seq = int(frame["seq"])

                incoming, buffer, disconnected = _recv_json_line_nonblocking(sock, buffer)
                if disconnected:
                    break
                if incoming is not None:
                    kind = str(incoming.get("t") or "").strip().lower()
                    if kind in {"disconnect", "close"}:
                        break
                    try:
                        self.submit_command(kind, incoming, timeout=5.0)
                    except Exception as exc:
                        if not _send_json_line(
                            sock,
                            {
                                "t": "error",
                                "code": "browser_surface_command_failed",
                                "message": str(exc),
                            },
                        ):
                            break
        finally:
            with self._lock:
                self._controller_attached = False
                self._updated_at = utc_now_iso()
                self._frame_cond.notify_all()
            try:
                sock.close()
            except Exception:
                pass


def _send_json_line(sock: socket.socket, obj: dict[str, Any]) -> bool:
    try:
        sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        return True
    except Exception:
        return False


def _recv_json_line_nonblocking(
    sock: socket.socket,
    buffer: bytes,
) -> tuple[Optional[dict[str, Any]], bytes, bool]:
    if b"\n" in buffer:
        line, remainder = buffer.split(b"\n", 1)
        try:
            return json.loads(line.decode("utf-8", errors="replace")), remainder, False
        except Exception:
            return None, remainder, False

    try:
        chunk = sock.recv(65536)
    except socket.timeout:
        return None, buffer, False
    except Exception:
        return None, buffer, True

    if not chunk:
        return None, b"", True

    buffer += chunk
    if len(buffer) > 2_000_000:
        return None, b"", True
    if b"\n" not in buffer:
        return None, buffer, False
    line, remainder = buffer.split(b"\n", 1)
    try:
        return json.loads(line.decode("utf-8", errors="replace")), remainder, False
    except Exception:
        return None, remainder, False


class _BrowserSurfaceManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], _BrowserSurfaceSession] = {}

    def _key(self, *, group_id: str, slot_id: str) -> tuple[str, str]:
        return (str(group_id or "").strip(), str(slot_id or "").strip())

    def open(self, *, group_id: str, slot_id: str, url: str, width: int, height: int) -> dict[str, Any]:
        replacement: Optional[_BrowserSurfaceSession] = None
        previous: Optional[_BrowserSurfaceSession] = None
        key = self._key(group_id=group_id, slot_id=slot_id)
        with self._lock:
            previous = self._sessions.get(key)
            replacement = _BrowserSurfaceSession(group_id=group_id, slot_id=slot_id, url=url, width=width, height=height)
            self._sessions[key] = replacement
        if previous is not None:
            previous.close()
        _reset_browser_profile_dir(group_id, slot_id)
        replacement.start()
        return replacement.wait_until_started()

    def info(self, *, group_id: str, slot_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(self._key(group_id=group_id, slot_id=slot_id))
        if session is None:
            return {
                "active": False,
                "state": "idle",
                "message": "No browser surface session is active for this slot.",
                "error": {},
                "strategy": "",
                "url": "",
                "width": 0,
                "height": 0,
                "started_at": "",
                "updated_at": "",
                "last_frame_seq": 0,
                "last_frame_at": "",
                "controller_attached": False,
            }
        return session.snapshot()

    def close(self, *, group_id: str, slot_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.pop(self._key(group_id=group_id, slot_id=slot_id), None)
        if session is None:
            return {
                "closed": False,
                "browser_surface": self.info(group_id=group_id, slot_id=slot_id),
            }
        session.close()
        return {
            "closed": True,
            "browser_surface": self.info(group_id=group_id, slot_id=slot_id),
        }

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass

    def can_attach(self, *, group_id: str, slot_id: str) -> tuple[bool, dict[str, Any]]:
        with self._lock:
            session = self._sessions.get(self._key(group_id=group_id, slot_id=slot_id))
        if session is None:
            return False, {
                "code": "browser_surface_not_found",
                "message": "no browser surface session is active for this slot",
                "details": {},
            }
        return session.can_attach()

    def attach_socket(self, *, group_id: str, slot_id: str, sock: socket.socket) -> bool:
        with self._lock:
            session = self._sessions.get(self._key(group_id=group_id, slot_id=slot_id))
        if session is None:
            return False
        return session.attach_socket(sock)


_MANAGER = _BrowserSurfaceManager()


def open_browser_surface_session(*, group_id: str, slot_id: str, url: str, width: int, height: int) -> dict[str, Any]:
    return _MANAGER.open(group_id=group_id, slot_id=slot_id, url=url, width=width, height=height)


def get_browser_surface_session_state(*, group_id: str, slot_id: str) -> dict[str, Any]:
    return _MANAGER.info(group_id=group_id, slot_id=slot_id)


def close_browser_surface_session(*, group_id: str, slot_id: str) -> dict[str, Any]:
    return _MANAGER.close(group_id=group_id, slot_id=slot_id)


def close_all_browser_surface_sessions() -> None:
    _MANAGER.close_all()


def can_attach_browser_surface_socket(*, group_id: str, slot_id: str) -> tuple[bool, dict[str, Any]]:
    return _MANAGER.can_attach(group_id=group_id, slot_id=slot_id)


def attach_browser_surface_socket(*, group_id: str, slot_id: str, sock: socket.socket) -> bool:
    return _MANAGER.attach_socket(group_id=group_id, slot_id=slot_id, sock=sock)
