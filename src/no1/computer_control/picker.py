"""Desktop element picker session management.

The web UI talks to the daemon for picker operations because a picker must
retain its lease and native helper state across HTTP requests.  The native
probe is deliberately optional: importing this module is safe on Linux and
on Windows installations without ``comtypes``.  In that case the session
still has a useful lifecycle and reports a structured warning to the UI.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import queue
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .lease import ComputerControlLease, LeaseConflict
from .elements import element_at_point, locator_from_element, normalize_snapshot


_CONTROL_TYPES = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
    50039: "SemanticZoom",
}


class PickerError(RuntimeError):
    """Structured picker failure surfaced by the daemon/API."""

    code = "picker_failed"
    retryable = False

    def __init__(self, message: str, *, code: str = "", retryable: Optional[bool] = None) -> None:
        super().__init__(message)
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = bool(retryable)


def _cursor_position() -> Optional[List[int]]:
    if sys.platform != "win32":
        return None
    try:
        point = ctypes.wintypes.POINT()  # type: ignore[attr-defined]
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return [int(point.x), int(point.y)]
    except Exception:
        return None
    return None


class NativeUIAProbe:
    """Small, dependency-optional UIA ElementFromPoint adapter."""

    available = False

    def __init__(self) -> None:
        self._automation: Any = None
        if sys.platform != "win32":
            return
        try:
            import comtypes.client  # type: ignore

            self._automation = comtypes.client.CreateObject("UIAutomationClient.CUIAutomation")
            self.available = self._automation is not None
        except Exception:
            self._automation = None

    @staticmethod
    def _value(element: Any, property_id: int) -> Any:
        try:
            value = element.GetCurrentPropertyValue(property_id)
            # UIA returns a sentinel for unavailable properties.  Treat it as
            # empty rather than exposing a COM implementation detail.
            if value is None or str(value).startswith("<Unknown"):
                return None
            return value
        except Exception:
            return None


class DesktopHighlightOverlay:
    """Optional click-through highlight rectangle on the real desktop.

    Tk is used only as a lightweight window host; the extended styles make the
    window transparent to mouse/keyboard input and prevent it stealing focus.
    This class never imports Tk on non-Windows systems and is safe to disable
    if a minimal Python distribution has no Tk support.
    """

    def __init__(self) -> None:
        self.available = False
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._root: Any = None

    def start(self) -> bool:
        if sys.platform != "win32" or (self._thread and self._thread.is_alive()):
            return bool(self.available)
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="onecolleague-picker-overlay", daemon=True)
        self._thread.start()
        self._ready.wait(1.0)
        return bool(self.available)

    def _run(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            try:
                root.attributes("-transparentcolor", "black")
            except Exception:
                root.attributes("-alpha", 0.35)
            canvas = tk.Canvas(root, bg="black", highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
            hwnd = int(root.winfo_id())
            try:
                user32 = ctypes.windll.user32
                get_style = user32.GetWindowLongW
                set_style = user32.SetWindowLongW
                style = int(get_style(hwnd, -20))
                # WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE |
                # WS_EX_TOOLWINDOW.  The overlay cannot intercept clicks.
                set_style(hwnd, -20, style | 0x20 | 0x80000 | 0x08000000 | 0x80)
            except Exception:
                pass
            self._root = root
            self.available = True
            self._ready.set()

            def drain() -> None:
                try:
                    while True:
                        item = self._queue.get_nowait()
                        if item is None:
                            root.destroy()
                            return
                        bounds = item
                        if isinstance(bounds, dict):
                            left = int(float(bounds.get("x", bounds.get("left", 0)) or 0))
                            top = int(float(bounds.get("y", bounds.get("top", 0)) or 0))
                            width = max(4, int(float(bounds.get("width", 0) or 0)))
                            height = max(4, int(float(bounds.get("height", 0) or 0)))
                            root.geometry(f"{width}x{height}+{left}+{top}")
                            canvas.delete("all")
                            canvas.create_rectangle(2, 2, width - 2, height - 2, outline="#00c2ff", width=3)
                except queue.Empty:
                    pass
                root.after(30, drain)

            root.after(30, drain)
            root.mainloop()
        except Exception:
            self.available = False
            self._ready.set()
        finally:
            self._root = None

    def update(self, bounds: Optional[Dict[str, Any]]) -> None:
        if self.available and isinstance(bounds, dict):
            self._queue.put(dict(bounds))

    def stop(self) -> None:
        thread = self._thread
        if thread and thread.is_alive():
            self._queue.put(None)
            thread.join(timeout=0.5)
        self._thread = None
        self.available = False

    def element_at(self, point: List[int]) -> Optional[Dict[str, Any]]:
        if not self.available or self._automation is None:
            return None
        try:
            pt = ctypes.wintypes.POINT(int(point[0]), int(point[1]))  # type: ignore[attr-defined]
            element = self._automation.ElementFromPoint(pt)
            if element is None:
                return None
            name = self._value(element, 30005)  # UIA_NamePropertyId
            automation_id = self._value(element, 30011)
            control_type_id = self._value(element, 30003)
            class_name = self._value(element, 30012)
            framework_id = self._value(element, 30024)
            process_id = self._value(element, 30002)
            window_handle = self._value(element, 30020)
            rect = self._value(element, 30001)
            bounds: Optional[Dict[str, float]] = None
            if rect is not None:
                try:
                    if all(hasattr(rect, name) for name in ("left", "top", "right", "bottom")):
                        left, top, right, bottom = (float(getattr(rect, name)) for name in ("left", "top", "right", "bottom"))
                    else:
                        values = list(rect)
                        if len(values) < 4:
                            raise ValueError("invalid UIA rectangle")
                        left, top, right, bottom = (float(v) for v in values[:4])
                    bounds = {"x": left, "y": top, "width": max(0.0, right - left), "height": max(0.0, bottom - top)}
                except Exception:
                    bounds = None
            process_name = ""
            try:
                if process_id:
                    import psutil  # type: ignore

                    process_name = str(psutil.Process(int(process_id)).name() or "")
            except Exception:
                pass
            control_type = _CONTROL_TYPES.get(int(control_type_id), str(control_type_id or "Custom"))
            parent_name = ""
            window_name = ""
            # Walk only a handful of ancestors; this is enough to identify the
            # owning window without making hover events expensive.
            try:
                walker = self._automation.ControlViewWalker
                parent = walker.GetParentElement(element)
                depth = 0
                while parent is not None and depth < 8:
                    ancestor_name = str(self._value(parent, 30005) or "").strip()
                    ancestor_type_id = self._value(parent, 30003)
                    ancestor_type = _CONTROL_TYPES.get(int(ancestor_type_id), "") if ancestor_type_id else ""
                    if not parent_name and ancestor_name:
                        parent_name = ancestor_name
                    if ancestor_type == "Window" and ancestor_name:
                        window_name = ancestor_name
                        break
                    parent = walker.GetParentElement(parent)
                    depth += 1
            except Exception:
                pass
            return {
                "window_name": window_name,
                "parent_name": parent_name,
                "name": str(name or "").strip(),
                "text": str(name or "").strip(),
                "control_type": control_type,
                "automation_id": str(automation_id or "").strip(),
                "class_name": str(class_name or "").strip(),
                "framework_id": str(framework_id or "").strip(),
                "process_name": process_name,
                "process_id": int(process_id) if process_id else None,
                "window_handle": int(window_handle) if window_handle else None,
                "bounds": bounds,
                "monitor": None,
                "enabled": True,
                "visible": True,
                "focused": False,
                "strategy": "uia",
            }
        except Exception:
            return None


# ``element_at`` uses only ``_automation`` and ``_value`` and is intentionally
# shared with the probe.  Keeping one implementation avoids subtle drift in
# UIA property extraction while allowing the overlay class to stay optional.
NativeUIAProbe.element_at = DesktopHighlightOverlay.element_at  # type: ignore[attr-defined]


@dataclass
class PickerSession:
    session_id: str
    group_id: str
    actor_id: str
    hotkey: str = "Ctrl+Shift+L"
    status: str = "active"
    started_at: float = field(default_factory=time.time)
    sequence: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    hovered: Optional[Dict[str, Any]] = None
    locked: Optional[Dict[str, Any]] = None
    samples: List[Dict[str, Any]] = field(default_factory=list)
    warning: str = ""
    overlay_available: bool = False
    last_heartbeat_at: float = 0.0

    def emit(self, kind: str, **data: Any) -> Dict[str, Any]:
        self.sequence += 1
        event = {"seq": self.sequence, "type": kind, "session_id": self.session_id, "ts": time.time(), **data}
        self.events.append(event)
        # A disconnected browser must not grow daemon memory forever.  Keep a
        # replay window large enough for normal SSE reconnects.
        if len(self.events) > 500:
            del self.events[:-500]
        return event

    def public(self) -> Dict[str, Any]:
        public_status = "locked" if self.status == "active" and self.locked else self.status
        return {
            "session_id": self.session_id,
            "group_id": self.group_id,
            "actor_id": self.actor_id,
            "hotkey": self.hotkey,
            "status": public_status,
            "started_at": self.started_at,
            "hovered": self.hovered,
            "locked": self.locked,
            "element": self.locked or self.hovered,
            "candidates": [self.locked or self.hovered] if (self.locked or self.hovered) else [],
            "stability": self.locked.get("stability") if isinstance(self.locked, dict) else None,
            "warning": self.warning,
            "native_available": bool(self.warning == ""),
            # A separate helper can provide a click-through highlight overlay;
            # the UI should show the fallback state until that helper is
            # installed instead of pretending that a browser rectangle is the
            # user's real desktop.
            "overlay_available": bool(self.overlay_available),
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_seq": self.sequence,
        }


class ElementPickerManager:
    """Own picker sessions and the observe-only computer-control lease."""

    def __init__(self, home: Any, lease: ComputerControlLease, *, snapshot_provider: Optional[Callable[[], Any]] = None) -> None:
        self.home = home
        self.lease = lease
        self.snapshot_provider = snapshot_provider
        self.probe = NativeUIAProbe()
        self.overlay = DesktopHighlightOverlay()
        self._sessions: Dict[str, PickerSession] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hotkey_down = False

    def _session(self, session_id: str) -> PickerSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise PickerError(f"拾取会话不存在或已清理：{session_id}", code="picker_session_not_found")
        return session

    def start(self, group_id: str, actor_id: str, *, hotkey: str = "Ctrl+Shift+L", session_id: str = "") -> Dict[str, Any]:
        with self._lock:
            # Completed sessions are retained long enough for an SSE reconnect
            # but do not accumulate forever.  Active sessions are never
            # reaped by this housekeeping pass.
            cutoff = time.time() - 3600
            for old_id, old in list(self._sessions.items()):
                if old.status != "active" and float(old.started_at or 0) < cutoff:
                    self._sessions.pop(old_id, None)
            for existing in self._sessions.values():
                if existing.status == "active":
                    if existing.group_id == group_id and existing.actor_id == actor_id:
                        return existing.public()
                    raise LeaseConflict({"run_id": existing.session_id, "group_id": existing.group_id, "actor_id": existing.actor_id, "observe_only": True})
            sid = session_id.strip() or "pick_" + uuid.uuid4().hex[:14]
            lease = self.lease.acquire(group_id=group_id, actor_id=actor_id or "user", run_id=sid, observe_only=True)
            session = PickerSession(session_id=sid, group_id=group_id, actor_id=actor_id or "user", hotkey=hotkey.strip() or "Ctrl+Shift+L", last_heartbeat_at=time.time())
            session.emit("started", lease=lease, hotkey=session.hotkey, native_available=self.probe.available, overlay_available=False)
            if not self.probe.available:
                session.warning = "当前环境无法使用 Windows UI Automation；可继续使用快照捕获，锁定按钮需要提供候选元素。"
                session.emit("warning", code="native_picker_unavailable", message=session.warning, next_action="安装 comtypes 并在 Windows 桌面会话中重试")
            self._sessions[sid] = session
            if self.probe.available:
                session.overlay_available = self.overlay.start()
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._poll_loop, name="onecolleague-picker", daemon=True)
                self._thread.start()
            return session.public()

    def _poll_loop(self) -> None:
        # UI Automation is a COM API; each native polling thread needs its own
        # apartment.  Failure is harmless because the probe remains optional.
        try:
            import comtypes  # type: ignore

            comtypes.CoInitialize()
            if self.probe.available:
                # Rebind the UIA COM object inside this apartment instead of
                # crossing an apartment boundary with the daemon's instance.
                self.probe = NativeUIAProbe()
        except Exception:
            pass
        while not self._stop.wait(0.15):
            with self._lock:
                active = [s for s in self._sessions.values() if s.status == "active"]
            if not active:
                self._stop.set()
                self.overlay.stop()
                continue
            point = _cursor_position() if self.probe.available else None
            element = self.probe.element_at(point) if point is not None and self.probe.available else None
            for session in active:
                if time.time() - float(session.last_heartbeat_at or 0.0) >= max(2.0, ComputerControlLease.HEARTBEAT_SECONDS / 2):
                    try:
                        self.lease.heartbeat(group_id=session.group_id, actor_id=session.actor_id, run_id=session.session_id)
                        session.last_heartbeat_at = time.time()
                    except PermissionError:
                        with self._lock:
                            session.status = "lease_lost"
                            session.warning = "拾取租约已失效，请重新开始拾取"
                            session.emit("ended", reason="lease_lost", code="computer_control_lease_required")
                if session.status != "active":
                    continue
                if point is not None:
                    self._update_hover(session, point, element)
            # RegisterHotKey needs a dedicated Windows message pump.  The
            # lightweight async-safe equivalent below keeps the helper
            # optional while still honoring Ctrl+Shift+L in a normal desktop
            # session.  It only reacts on the key-down edge.
            pressed = self._hotkey_pressed() if point is not None else False
            if pressed and not self._hotkey_down:
                for session in active:
                    try:
                        self.lock(session.session_id, point=point, element=element)
                    except Exception as exc:
                        with self._lock:
                            session.emit("warning", code="picker_lock_failed", message=str(exc), retryable=True)
            self._hotkey_down = pressed

    @staticmethod
    def _hotkey_pressed() -> bool:
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            return bool(
                (user32.GetAsyncKeyState(0x11) & 0x8000)
                and (user32.GetAsyncKeyState(0x10) & 0x8000)
                and (user32.GetAsyncKeyState(ord("L")) & 0x8000)
            )
        except Exception:
            return False

    @staticmethod
    def _same_element(old: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> bool:
        if not old or not new:
            return old is None and new is None
        keys = ("automation_id", "control_type", "name", "window_handle", "process_id")
        return all(str(old.get(k) or "") == str(new.get(k) or "") for k in keys)

    def _update_hover(self, session: PickerSession, point: List[int], element: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            current_key = element and tuple(str(element.get(k) or "") for k in ("automation_id", "control_type", "name", "window_handle", "process_id"))
            previous_key = session.hovered and tuple(str(session.hovered.get(k) or "") for k in ("automation_id", "control_type", "name", "window_handle", "process_id"))
            if current_key == previous_key and session.hovered is not None:
                return
            if element:
                element = dict(element)
                element["point"] = point
                element["locator_preview"] = locator_from_element(element, fallback_policy="never")
            session.hovered = element
            if element:
                self.overlay.update(element.get("bounds"))
            session.emit("hover", point=point, element=element, candidate_count=1 if element else 0)

    def events(self, session_id: str, *, after_seq: int = 0) -> Dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            return {"session": session.public(), "events": [e for e in session.events if int(e.get("seq") or 0) > int(after_seq)], "next_seq": session.sequence}

    def lock(self, session_id: str, *, point: Optional[List[int]] = None, element: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status != "active":
                raise PickerError("拾取会话已结束，请重新开始", code="picker_session_ended")
            if point is None:
                point = _cursor_position()
            candidate = element if isinstance(element, dict) else session.hovered
            if candidate is None and point:
                candidate = self.probe.element_at(point)
            if candidate is None and point and self.snapshot_provider is not None:
                try:
                    snapshot = normalize_snapshot(self.snapshot_provider())
                    candidate = element_at_point(snapshot, point)
                except Exception:
                    candidate = None
            if candidate is None:
                raise PickerError("当前鼠标位置没有可识别元素，请将鼠标移到目标后重试", code="picker_target_unavailable", retryable=True)
            candidate = dict(candidate)
            candidate["locator_preview"] = locator_from_element(candidate, fallback_policy="never")
            candidate["stability"] = {"level": "medium", "score": 60, "reasons": ["已获取当前元素，等待三次采样确认"]}
            session.locked = candidate
            session.samples = [candidate]
            session.emit("locked", element=candidate, sample_index=1, sample_count=3)
        # Sample on a background-free, cancellable path.  It intentionally has
        # no overall wall-clock deadline; three short samples only stabilize the
        # locator and do not cap the user's subsequent workflow.
        for index, delay in enumerate((0.35, 0.55), start=2):
            time.sleep(delay)
            with self._lock:
                if session.status != "active":
                    break
                point_now = _cursor_position() or point
                sample = self.probe.element_at(point_now) if point_now else None
                if sample is None and point_now and self.snapshot_provider is not None:
                    try:
                        sample = element_at_point(normalize_snapshot(self.snapshot_provider()), point_now)
                    except Exception:
                        sample = None
                if sample:
                    sample = dict(sample)
                    sample["locator_preview"] = locator_from_element(sample, fallback_policy="never")
                session.samples.append(sample or {})
                session.emit("locked", element=sample, sample_index=index, sample_count=3)
        with self._lock:
            samples = [s for s in session.samples if s]
            stable = len(samples) == 3 and all(self._same_element(samples[0], sample) for sample in samples[1:])
            stability_reasons: List[str] = []
            if stable and samples and not any(str(samples[0].get(key) or "").strip() for key in ("automation_id", "name", "text")):
                stable = False
                stability_reasons.append("元素没有可访问名称或 AutomationId")
            if not stable and not stability_reasons:
                stability_reasons.append("元素在采样期间发生变化，请重新锁定")
            if session.locked:
                session.locked["stability"] = {"level": "high" if stable else "low", "score": 95 if stable else 35, "reasons": stability_reasons}
            session.emit("locked", element=session.locked, sample_index=3, sample_count=3, stable=stable)
            return {"session": session.public(), "element": session.locked, "stable": stable}

    def confirm(self, session_id: str, *, locator: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if not session.locked:
                raise PickerError("请先锁定一个元素", code="picker_target_not_locked", retryable=True)
            element = dict(session.locked)
            if locator:
                element["locator_preview"] = locator
            stability = element.get("stability") if isinstance(element.get("stability"), dict) else {}
            if stability.get("level") == "low":
                raise PickerError("元素不稳定，请重新锁定或补充窗口、控件类型等约束", code="picker_target_unstable", retryable=True)
            session.status = "confirmed"
            session.emit("ended", reason="confirmed", element=element)
            try:
                self.lease.release(run_id=session.session_id, force=False)
            except PermissionError:
                # The lease may have expired and been handed to another run;
                # never force-delete that newer owner's lease.
                pass
            self.overlay.stop()
            return {"session": session.public(), "locator": element.get("locator_preview"), "element": element}

    def cancel(self, session_id: str, *, reason: str = "cancelled") -> Dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status == "active":
                session.status = "cancelled"
                session.emit("ended", reason=reason)
                try:
                    self.lease.release(run_id=session.session_id, force=False)
                except PermissionError:
                    pass
                self.overlay.stop()
            return session.public()

    def status(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._session(session_id).public()
