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
import gc
import os
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .lease import ComputerControlLease, LeaseConflict
from .elements import element_at_point, locator_from_element, normalize_snapshot
from .observation import (
    UIAUnavailableError,
    create_uia_automation,
    desktop_session_available,
    diagnose_uia_readiness,
)
from ..util.process import is_frozen_executable


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
        self.diagnostics: Dict[str, Any] = {
            "available": False,
            "code": "native_uia_starting",
            "layer": "uia",
            "message": "正在启动 Windows 原生元素拾取",
            "retryable": True,
            "runtime": {
                "platform": sys.platform,
                "frozen": is_frozen_executable(),
                "executable": sys.executable,
            },
            "checks": {
                "interactive_desktop": desktop_session_available(),
                "automation_created": False,
            },
        }
        if sys.platform != "win32":
            self.diagnostics.update(
                code="native_uia_platform_unsupported",
                layer="platform",
                message="原生元素拾取仅支持 Windows",
                retryable=False,
            )
            return
        try:
            self._automation = create_uia_automation()
            self.available = self._automation is not None
            if self.available:
                self.diagnostics["checks"]["automation_created"] = True
                self.diagnostics.update(
                    available=True,
                    code="ok",
                    message="Windows 原生元素拾取可用",
                    retryable=False,
                )
        except UIAUnavailableError as exc:
            self.diagnostics.update(exc.diagnostics())
            self.diagnostics["available"] = False
            self._automation = None
        except Exception as exc:
            self.diagnostics.update(
                code="native_uia_probe_failed",
                layer="com",
                message="Windows UI Automation 启动失败",
                detail=f"{type(exc).__name__}: {exc}",
                next_action="复制诊断信息并重新启动 OneColleague",
            )
            self._automation = None

    def close(self) -> None:
        """Release COM references while the owner apartment is still active."""
        self.available = False
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
            runtime_id: Optional[List[int]] = None
            try:
                raw_runtime_id = element.GetRuntimeId()
                if raw_runtime_id is not None:
                    runtime_id = [int(value) for value in list(raw_runtime_id)]
            except Exception:
                runtime_id = None
            bounds: Optional[Dict[str, float]] = None
            if rect is not None:
                try:
                    if all(hasattr(rect, attr) for attr in ("left", "top", "right", "bottom")):
                        left, top, right, bottom = (
                            float(getattr(rect, attr)) for attr in ("left", "top", "right", "bottom")
                        )
                        bounds = {
                            "x": left,
                            "y": top,
                            "width": max(0.0, right - left),
                            "height": max(0.0, bottom - top),
                        }
                    else:
                        values = list(rect)
                        if len(values) < 4:
                            raise ValueError("invalid UIA rectangle")
                        left, top, width, height = (float(value) for value in values[:4])
                        bounds = {
                            "x": left,
                            "y": top,
                            "width": max(0.0, width),
                            "height": max(0.0, height),
                        }
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
            try:
                walker = self._automation.ControlViewWalker
                parent = walker.GetParentElement(element)
                depth = 0
                while parent is not None and depth < 32:
                    ancestor_name = str(self._value(parent, 30005) or "").strip()
                    ancestor_type_id = self._value(parent, 30003)
                    ancestor_type = _CONTROL_TYPES.get(int(ancestor_type_id), "") if ancestor_type_id else ""
                    if not parent_name and ancestor_name:
                        parent_name = ancestor_name
                    if ancestor_type == "Window" and ancestor_name:
                        window_name = ancestor_name
                        if not window_handle:
                            window_handle = self._value(parent, 30020)
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
                "runtime_id": runtime_id,
                "bounds": bounds,
                "monitor": None,
                "enabled": True,
                "visible": True,
                "focused": False,
                "strategy": "uia",
            }
        except Exception:
            return None


class UnavailableUIAProbe:
    """Non-COM placeholder used before or after the picker owner thread."""

    available = False
    diagnostics = {
        "available": False,
        "code": "native_uia_unavailable",
        "layer": "uia",
        "message": "Windows 原生元素拾取不可用",
        "retryable": True,
    }

    @staticmethod
    def element_at(point: List[int]) -> Optional[Dict[str, Any]]:
        del point
        return None


def _initialize_com() -> Any:
    if sys.platform != "win32":
        return None
    import comtypes  # type: ignore

    comtypes.CoInitialize()
    return comtypes


def _prepare_com_runtime() -> None:
    """Import comtypes on the long-lived manager thread.

    comtypes initializes the thread that imports it for the first time and
    registers process-exit cleanup for that apartment. Importing it first on a
    short-lived picker worker leaves that cleanup attached to the wrong
    thread, which can surface as RPC_E_DISCONNECTED after a session ends.
    """
    if sys.platform != "win32":
        return
    try:
        import comtypes  # type: ignore  # noqa: F401
    except Exception:
        pass


def _uninitialize_com(comtypes_module: Any) -> None:
    if comtypes_module is not None:
        comtypes_module.CoUninitialize()


class DesktopHighlightOverlay:
    """Native click-through highlight rectangle owned by one Win32 thread."""

    def __init__(self) -> None:
        self.available = False
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._hwnd: int = 0
        self._wndproc: Any = None
        self.error = ""

    def start(self) -> bool:
        if sys.platform != "win32" or (self._thread and self._thread.is_alive()):
            return bool(self.available)
        self._ready.clear()
        self.error = ""
        self._thread = threading.Thread(target=self._run, name="onecolleague-picker-overlay", daemon=True)
        self._thread.start()
        self._ready.wait(1.0)
        return bool(self.available)

    def _run(self) -> None:
        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
            ]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", ctypes.c_void_p),
                ("fErase", ctypes.wintypes.BOOL),
                ("rcPaint", ctypes.wintypes.RECT),
                ("fRestore", ctypes.wintypes.BOOL),
                ("fIncUpdate", ctypes.wintypes.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        user32.RegisterClassW.restype = ctypes.c_ushort
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.UnregisterClassW.restype = ctypes.wintypes.BOOL
        user32.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        user32.DestroyWindow.restype = ctypes.wintypes.BOOL
        user32.DestroyWindow.argtypes = [ctypes.wintypes.HWND]
        user32.CreateWindowExW.restype = ctypes.wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.wintypes.HWND,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.SetWindowPos.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.InvalidateRect.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p, ctypes.wintypes.BOOL]
        user32.PostMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        user32.BeginPaint.restype = ctypes.c_void_p
        user32.BeginPaint.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.EndPaint.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_void_p]
        gdi32.GetStockObject.restype = ctypes.c_void_p
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.CreatePen.restype = ctypes.c_void_p
        gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.Rectangle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        class_name = f"OneColleagueElementOverlay_{id(self):x}"
        wndproc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.wintypes.HWND,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )

        def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == 0x0084:  # WM_NCHITTEST
                return -1  # HTTRANSPARENT
            if message == 0x0021:  # WM_MOUSEACTIVATE
                return 3  # MA_NOACTIVATE
            if message == 0x0014:  # WM_ERASEBKGND
                return 1
            if message == 0x000F:  # WM_PAINT
                paint = PAINTSTRUCT()
                rect = ctypes.wintypes.RECT()
                hdc = user32.BeginPaint(hwnd, ctypes.byref(paint))
                try:
                    user32.GetClientRect(hwnd, ctypes.byref(rect))
                    user32.FillRect(hdc, ctypes.byref(rect), gdi32.GetStockObject(4))  # BLACK_BRUSH
                    pen = gdi32.CreatePen(0, 3, 0x00FFC200)  # RGB(0, 194, 255)
                    old_pen = gdi32.SelectObject(hdc, pen)
                    old_brush = gdi32.SelectObject(hdc, gdi32.GetStockObject(5))  # NULL_BRUSH
                    gdi32.Rectangle(hdc, 1, 1, max(2, rect.right - 1), max(2, rect.bottom - 1))
                    gdi32.SelectObject(hdc, old_brush)
                    gdi32.SelectObject(hdc, old_pen)
                    gdi32.DeleteObject(pen)
                finally:
                    user32.EndPaint(hwnd, ctypes.byref(paint))
                return 0
            if message == 0x0010:  # WM_CLOSE
                user32.DestroyWindow(hwnd)
                return 0
            if message == 0x0002:  # WM_DESTROY
                user32.PostQuitMessage(0)
                return 0
            return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))

        try:
            self._wndproc = wndproc_type(window_proc)
            instance = kernel32.GetModuleHandleW(None)
            window_class = WNDCLASSW(
                0,
                ctypes.cast(self._wndproc, ctypes.c_void_p),
                0,
                0,
                instance,
                None,
                None,
                gdi32.GetStockObject(4),
                None,
                class_name,
            )
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError()
            ex_style = 0x00000020 | 0x00080000 | 0x08000000 | 0x00000080 | 0x00000008
            hwnd = user32.CreateWindowExW(
                ex_style,
                class_name,
                "",
                0x80000000,
                0,
                0,
                1,
                1,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError()
            self._hwnd = int(hwnd)
            user32.SetLayeredWindowAttributes(hwnd, 0, 255, 1)  # black color key
            self.available = True
            self._ready.set()
            message = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self.error = str(exc)
            self.available = False
            self._ready.set()
        finally:
            self._hwnd = 0
            self.available = False
            try:
                user32.UnregisterClassW(class_name, kernel32.GetModuleHandleW(None))
            except Exception:
                pass
            self._wndproc = None

    def update(self, bounds: Optional[Dict[str, Any]]) -> None:
        hwnd = self._hwnd
        if self.available and hwnd and isinstance(bounds, dict):
            left = int(float(bounds.get("x", bounds.get("left", 0)) or 0))
            top = int(float(bounds.get("y", bounds.get("top", 0)) or 0))
            width = max(4, int(float(bounds.get("width", 0) or 0)))
            height = max(4, int(float(bounds.get("height", 0) or 0)))
            user32 = ctypes.windll.user32
            user32.SetWindowPos(hwnd, ctypes.wintypes.HWND(-1), left, top, width, height, 0x0010 | 0x0040)
            user32.InvalidateRect(hwnd, None, True)

    def stop(self) -> None:
        thread = self._thread
        hwnd = self._hwnd
        if thread and thread.is_alive() and hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
            thread.join(timeout=2.0)
        self._thread = None
        self.available = False


def diagnose_native_picker() -> Dict[str, Any]:
    """Run the same native prerequisites used by a live picker session."""
    diagnostics = diagnose_uia_readiness()
    checks = dict(diagnostics.get("checks") or {})
    checks["overlay"] = False
    diagnostics["checks"] = checks
    diagnostics["overlay_available"] = False
    diagnostics["uia_available"] = bool(diagnostics.get("available"))
    if not diagnostics.get("available"):
        return diagnostics

    overlay = DesktopHighlightOverlay()
    try:
        available = overlay.start()
        checks["overlay"] = bool(available)
        diagnostics["overlay_available"] = bool(available)
        if not available:
            diagnostics.update(
                available=False,
                code="native_picker_overlay_unavailable",
                layer="overlay",
                message="Windows UI Automation 可用，但元素高亮覆盖层无法启动",
                detail=str(overlay.error or "覆盖层启动失败"),
                next_action="确认 OneColleague 运行在已登录且未锁定的桌面会话中",
                retryable=True,
            )
    finally:
        overlay.stop()
    return diagnostics

@dataclass
class PickerSession:
    session_id: str
    group_id: str
    actor_id: str
    hotkey: str = "Ctrl+Shift+LeftClick"
    status: str = "active"
    started_at: float = field(default_factory=time.time)
    sequence: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    hovered: Optional[Dict[str, Any]] = None
    locked: Optional[Dict[str, Any]] = None
    samples: List[Dict[str, Any]] = field(default_factory=list)
    warning: str = ""
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    native_available: bool = False
    overlay_available: bool = False
    last_heartbeat_at: float = 0.0
    heartbeat_stop: threading.Event = field(default_factory=threading.Event, repr=False)
    heartbeat_thread: Optional[threading.Thread] = field(default=None, repr=False)

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
            "diagnostics": dict(self.diagnostics),
            "native_available": self.native_available,
            # A separate helper can provide a click-through highlight overlay;
            # the UI should show the fallback state until that helper is
            # installed instead of pretending that a browser rectangle is the
            # user's real desktop.
            "overlay_available": bool(self.overlay_available),
            "last_heartbeat_at": self.last_heartbeat_at,
            "last_seq": self.sequence,
        }


@dataclass
class PickerThreadRequest:
    """A synchronous API request executed by the UIA owner thread."""

    kind: str
    session_id: str
    point: Optional[List[int]]
    supplied_element: Optional[Dict[str, Any]]
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[BaseException] = None


class ElementPickerManager:
    """Own picker sessions and the observe-only computer-control lease."""

    def __init__(
        self,
        home: Any,
        lease: ComputerControlLease,
        *,
        snapshot_provider: Optional[Callable[[], Any]] = None,
        probe_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        _prepare_com_runtime()
        self.home = home
        self.lease = lease
        self.snapshot_provider = snapshot_provider
        self.probe: Any = UnavailableUIAProbe()
        self._probe_factory = probe_factory or NativeUIAProbe
        self.overlay = DesktopHighlightOverlay()
        self._sessions: Dict[str, PickerSession] = {}
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._requests: "queue.Queue[PickerThreadRequest]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._gesture_down = False

    def _session(self, session_id: str) -> PickerSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise PickerError(f"拾取会话不存在或已清理：{session_id}", code="picker_session_not_found")
        return session

    def start(self, group_id: str, actor_id: str, *, hotkey: str = "Ctrl+Shift+LeftClick", session_id: str = "") -> Dict[str, Any]:
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
            session = PickerSession(
                session_id=sid,
                group_id=group_id,
                actor_id=actor_id or "user",
                hotkey=hotkey.strip() or "Ctrl+Shift+LeftClick",
                last_heartbeat_at=time.time(),
                diagnostics={
                    "available": False,
                    "code": "native_uia_starting",
                    "layer": "uia",
                    "message": "正在启动 Windows 原生元素拾取",
                    "retryable": True,
                },
            )
            session.emit("started", lease=lease, hotkey=session.hotkey, native_available=False, overlay_available=False)
            self._sessions[sid] = session
            try:
                self._start_session_heartbeat(session)
            except Exception:
                self._sessions.pop(sid, None)
                raise
            if self._thread is None or not self._thread.is_alive():
                self._wake.clear()
                self._thread = threading.Thread(target=self._poll_loop, name="onecolleague-picker", daemon=True)
                self._thread.start()
            self._wake.set()
            return session.public()

    def _start_session_heartbeat(self, session: PickerSession) -> None:
        def heartbeat_loop() -> None:
            interval = max(0.01, float(self.lease.HEARTBEAT_SECONDS) / 2.0)
            while not session.heartbeat_stop.wait(interval):
                try:
                    self.lease.heartbeat(
                        group_id=session.group_id,
                        actor_id=session.actor_id,
                        run_id=session.session_id,
                    )
                    session.last_heartbeat_at = time.time()
                except Exception:
                    with self._lock:
                        if session.status == "active":
                            session.status = "lease_lost"
                            session.warning = "拾取租约已失效，请重新开始拾取"
                            session.emit("ended", reason="lease_lost", code="computer_control_lease_required")
                    self._wake.set()
                    return

        thread = threading.Thread(
            target=heartbeat_loop,
            name=f"onecolleague-picker-lease-{session.session_id[:20]}",
            daemon=True,
        )
        session.heartbeat_thread = thread
        try:
            thread.start()
        except Exception:
            session.heartbeat_thread = None
            self.lease.release(run_id=session.session_id, force=False)
            raise

    @staticmethod
    def _stop_session_heartbeat(session: PickerSession) -> None:
        session.heartbeat_stop.set()
        thread = session.heartbeat_thread
        session.heartbeat_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _poll_loop(self) -> None:
        comtypes_module: Any = None
        com_initialized = False
        probe: Any = UnavailableUIAProbe()
        probe_error = ""
        normal_exit = False
        try:
            try:
                comtypes_module = _initialize_com()
                com_initialized = comtypes_module is not None
                if sys.platform != "win32" or com_initialized:
                    probe = self._probe_factory()
            except Exception as exc:
                probe_error = str(exc)
                probe = UnavailableUIAProbe()
            with self._lock:
                self.probe = probe
                active_at_start = [session for session in self._sessions.values() if session.status == "active"]
                for session in active_at_start:
                    session.native_available = bool(probe.available)
                    session.diagnostics = dict(getattr(probe, "diagnostics", {}) or {})
                    if not probe.available:
                        session.warning = (
                            "当前环境无法使用 Windows UI Automation；可继续使用快照捕获，"
                            "锁定按钮需要提供候选元素。"
                        )
                        session.emit(
                            "warning",
                            code="native_picker_unavailable",
                            message=session.warning,
                            detail=str(session.diagnostics.get("detail") or probe_error),
                            layer=str(session.diagnostics.get("layer") or "uia"),
                            next_action=str(session.diagnostics.get("next_action") or "复制诊断信息并重试"),
                        )
            if probe.available and active_at_start:
                overlay_available = self.overlay.start()
                with self._lock:
                    for session in active_at_start:
                        session.overlay_available = overlay_available
                        checks = dict(session.diagnostics.get("checks") or {})
                        checks["overlay"] = overlay_available
                        session.diagnostics["checks"] = checks
                        session.diagnostics["overlay_available"] = overlay_available
                        if not overlay_available:
                            session.emit(
                                "warning",
                                code="native_picker_overlay_unavailable",
                                layer="overlay",
                                message="元素可以读取，但桌面高亮框不可用",
                                detail=str(self.overlay.error or "覆盖层启动失败"),
                                next_action="复制诊断信息并重新启动 OneColleague",
                                retryable=True,
                            )

            # A new worker may start while the user is still releasing the
            # previous gesture. Seed the edge detector from physical state so
            # that this does not immediately lock the next session.
            self._gesture_down = self._lock_gesture_pressed()

            while True:
                self._process_thread_requests(probe)
                with self._lock:
                    active = [session for session in self._sessions.values() if session.status == "active"]
                    if not active:
                        # Complete teardown while holding the same lock used by
                        # start(). A new session either joins this worker before
                        # this point or starts a fresh worker after teardown.
                        self.overlay.stop()
                        self.probe = UnavailableUIAProbe()
                        if self._thread is threading.current_thread():
                            self._thread = None
                        normal_exit = True
                if normal_exit:
                    break
                point = _cursor_position()
                element = probe.element_at(point) if point is not None and probe.available else None
                for session in active:
                    if session.status != "active":
                        continue
                    if point is not None:
                        self._update_hover(session, point, element)
                pressed = self._lock_gesture_pressed() if point is not None else False
                if pressed and not self._gesture_down:
                    for session in active:
                        try:
                            self._lock_on_owner(
                                session.session_id,
                                probe=probe,
                                point=point,
                                supplied_element=element,
                            )
                        except Exception as exc:
                            with self._lock:
                                session.emit("warning", code="picker_lock_failed", message=str(exc), retryable=True)
                self._gesture_down = pressed
                self._wake.wait(0.05)
                self._wake.clear()
        except Exception as exc:
            probe_error = str(exc)
        finally:
            if not normal_exit:
                with self._lock:
                    abandoned = [session for session in self._sessions.values() if session.status == "active"]
                    for session in abandoned:
                        session.status = "failed"
                        session.warning = "元素拾取线程意外结束，请重新开始拾取"
                        if probe_error:
                            session.warning += f"：{probe_error[:200]}"
                        session.emit("ended", reason="picker_thread_unavailable", code="picker_thread_unavailable")
                    # Exceptional teardown is atomic with start(). A normal
                    # exit already did this before publishing _thread=None and
                    # must never touch resources owned by a replacement worker.
                    self.overlay.stop()
                    self.probe = UnavailableUIAProbe()
                    if self._thread is threading.current_thread():
                        self._thread = None
                for session in abandoned:
                    self._stop_session_heartbeat(session)
                    try:
                        self.lease.release(run_id=session.session_id, force=False)
                    except (OSError, PermissionError):
                        pass
                thread_error = PickerError(
                    "元素拾取线程已经结束，请重新开始拾取",
                    code="picker_thread_unavailable",
                    retryable=True,
                )
                while True:
                    try:
                        request = self._requests.get_nowait()
                    except queue.Empty:
                        break
                    request.error = thread_error
                    request.done.set()
            if com_initialized:
                try:
                    close_probe = getattr(probe, "close", None)
                    if callable(close_probe):
                        close_probe()
                except Exception:
                    pass
                probe = UnavailableUIAProbe()
                # comtypes may retain cyclic wrapper objects after the last
                # Python reference is cleared. Release them inside the same
                # apartment before CoUninitialize, never on a later worker.
                gc.collect()
                try:
                    _uninitialize_com(comtypes_module)
                except Exception:
                    pass

    @staticmethod
    def _lock_gesture_pressed() -> bool:
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            left_button = int(user32.GetAsyncKeyState(0x01))
            return bool(
                (user32.GetAsyncKeyState(0x11) & 0x8000)
                and (user32.GetAsyncKeyState(0x10) & 0x8000)
                and (left_button & 0x8001)
            )
        except Exception:
            return False

    def _process_thread_requests(self, probe: Any) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                if request.kind != "lock":
                    raise PickerError(f"不支持的拾取请求：{request.kind}", code="picker_request_invalid")
                request.result = self._lock_on_owner(
                    request.session_id,
                    probe=probe,
                    point=request.point,
                    supplied_element=request.supplied_element,
                )
            except BaseException as exc:
                request.error = exc
            finally:
                request.done.set()

    @staticmethod
    def _runtime_id(element: Optional[Dict[str, Any]]) -> tuple[str, ...]:
        if not element:
            return ()
        value = element.get("runtime_id")
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        if value is None or value == "":
            return ()
        return (str(value),)

    @staticmethod
    def _normalized_bounds(element: Optional[Dict[str, Any]]) -> Optional[tuple[float, float, float, float]]:
        bounds = element.get("bounds") if isinstance(element, dict) else None
        if not isinstance(bounds, dict):
            return None
        try:
            x = float(bounds.get("x", bounds.get("left")))
            y = float(bounds.get("y", bounds.get("top")))
            width = float(bounds.get("width"))
            height = float(bounds.get("height"))
        except (TypeError, ValueError):
            return None
        return (x, y, x + width, y + height)

    @classmethod
    def _bounds_moved(cls, reference: Dict[str, Any], candidate: Dict[str, Any], *, tolerance: float = 4.0) -> bool:
        old = cls._normalized_bounds(reference)
        new = cls._normalized_bounds(candidate)
        if old is None or new is None:
            return old != new
        old_center = ((old[0] + old[2]) / 2, (old[1] + old[3]) / 2)
        new_center = ((new[0] + new[2]) / 2, (new[1] + new[3]) / 2)
        old_size = (old[2] - old[0], old[3] - old[1])
        new_size = (new[2] - new[0], new[3] - new[1])
        deltas = [abs(left - right) for left, right in zip(old, new)]
        deltas.extend(abs(left - right) for left, right in zip(old_center, new_center))
        deltas.extend(abs(left - right) for left, right in zip(old_size, new_size))
        return max(deltas, default=0.0) > tolerance

    @staticmethod
    def _same_element(old: Optional[Dict[str, Any]], new: Optional[Dict[str, Any]]) -> bool:
        if not old or not new:
            return old is None and new is None
        old_runtime_id = ElementPickerManager._runtime_id(old)
        new_runtime_id = ElementPickerManager._runtime_id(new)
        if old_runtime_id or new_runtime_id:
            if old_runtime_id != new_runtime_id:
                return False
        keys = (
            "automation_id",
            "control_type",
            "name",
            "window_name",
            "window_handle",
            "process_name",
            "process_id",
        )
        if not all(str(old.get(key) or "") == str(new.get(key) or "") for key in keys):
            return False
        return not ElementPickerManager._bounds_moved(old, new)

    @classmethod
    def _assess_stability(cls, samples: List[Dict[str, Any]]) -> tuple[bool, List[str]]:
        if len(samples) != 3 or any(not sample for sample in samples):
            return False, ["未能完成三次元素采样"]
        reference = samples[0]
        runtime_ids = [cls._runtime_id(sample) for sample in samples]
        if any(runtime_ids) and not all(runtime_id == runtime_ids[0] for runtime_id in runtime_ids[1:]):
            return False, ["元素 RuntimeId 发生变化"]
        identity_keys = (
            "automation_id",
            "control_type",
            "name",
            "window_name",
            "window_handle",
            "process_name",
            "process_id",
        )
        if any(
            str(sample.get(key) or "") != str(reference.get(key) or "")
            for sample in samples[1:]
            for key in identity_keys
        ):
            return False, ["元素在采样期间发生变化，请重新锁定"]
        if any(cls._bounds_moved(reference, sample) for sample in samples[1:]):
            return False, ["元素边界在采样期间移动"]
        if cls._normalized_bounds(reference) is None:
            return False, ["元素缺少可验证边界"]
        match_counts = [int(sample.get("_picker_match_count") or 0) for sample in samples]
        if any(count > 1 for count in match_counts):
            return False, ["检测到同名元素，无法确认唯一目标"]
        if not any(str(reference.get(key) or "").strip() for key in ("automation_id", "name", "text")):
            return False, ["元素没有可访问名称或 AutomationId"]
        stable_runtime = bool(runtime_ids[0]) and all(runtime_ids)
        stable_automation_id = bool(str(reference.get("automation_id") or "").strip())
        stable_native_context = bool(reference.get("window_handle") and reference.get("process_id"))
        stable_snapshot_context = bool(match_counts and all(count == 1 for count in match_counts))
        if not (stable_runtime or stable_automation_id or stable_native_context or stable_snapshot_context):
            return False, ["同名元素缺少稳定身份"]
        return True, []

    @classmethod
    def _hover_key(cls, element: Optional[Dict[str, Any]]) -> Any:
        if not element:
            return None
        bounds = cls._normalized_bounds(element)
        rounded_bounds = tuple(round(value, 1) for value in bounds) if bounds else ()
        return (
            cls._runtime_id(element),
            str(element.get("automation_id") or ""),
            str(element.get("control_type") or ""),
            str(element.get("name") or ""),
            str(element.get("window_handle") or ""),
            str(element.get("process_id") or ""),
            rounded_bounds,
        )

    def _update_hover(self, session: PickerSession, point: List[int], element: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            current_key = self._hover_key(element)
            previous_key = self._hover_key(session.hovered)
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

    def _snapshot_element_at(self, point: List[int]) -> Optional[Dict[str, Any]]:
        if self.snapshot_provider is None:
            return None
        try:
            snapshot = normalize_snapshot(self.snapshot_provider())
            candidate = element_at_point(snapshot, point)
        except Exception:
            return None
        if not isinstance(candidate, dict):
            return None
        candidate = dict(candidate)
        name = str(candidate.get("name") or candidate.get("text") or "")
        control_type = str(candidate.get("control_type") or "")
        window_name = str(candidate.get("window_name") or "")
        matches = [
            item
            for item in snapshot.get("elements", [])
            if isinstance(item, dict)
            and str(item.get("name") or item.get("text") or "") == name
            and str(item.get("control_type") or "") == control_type
            and str(item.get("window_name") or "") == window_name
        ]
        candidate["_picker_match_count"] = len(matches)
        return candidate

    @staticmethod
    def _center_point(element: Dict[str, Any]) -> Optional[List[int]]:
        bounds = ElementPickerManager._normalized_bounds(element)
        if bounds is None:
            return None
        return [int(round((bounds[0] + bounds[2]) / 2)), int(round((bounds[1] + bounds[3]) / 2))]

    def _sample_element(self, probe: Any, point: Optional[List[int]]) -> Optional[Dict[str, Any]]:
        if point is None:
            return None
        candidate = probe.element_at(point) if probe.available else None
        if candidate is None:
            candidate = self._snapshot_element_at(point)
        if not isinstance(candidate, dict):
            return None
        result = dict(candidate)
        result["locator_preview"] = locator_from_element(result, fallback_policy="never")
        return result

    def lock(self, session_id: str, *, point: Optional[List[int]] = None, element: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status != "active":
                raise PickerError("拾取会话已结束，请重新开始", code="picker_session_ended")
            if self._thread is None or not self._thread.is_alive():
                raise PickerError("元素拾取线程不可用，请重新开始拾取", code="picker_thread_unavailable", retryable=True)
        request = PickerThreadRequest(
            kind="lock",
            session_id=session_id,
            point=list(point) if point is not None else None,
            supplied_element=dict(element) if isinstance(element, dict) else None,
        )
        self._requests.put(request)
        self._wake.set()
        request.done.wait()
        if request.error is not None:
            raise request.error
        return request.result

    def _lock_on_owner(
        self,
        session_id: str,
        *,
        probe: Any,
        point: Optional[List[int]],
        supplied_element: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status != "active":
                raise PickerError("拾取会话已结束，请重新开始", code="picker_session_ended")
            cached = dict(session.hovered) if isinstance(session.hovered, dict) else None
        locked_point = list(point) if point is not None else _cursor_position()
        candidate = dict(supplied_element) if isinstance(supplied_element, dict) else cached
        if candidate is None:
            candidate = self._sample_element(probe, locked_point)
        if candidate is not None and locked_point is None:
            locked_point = self._center_point(candidate)
        if candidate is None:
            raise PickerError(
                "当前鼠标位置没有可识别元素，请将鼠标移到目标后重试",
                code="picker_target_unavailable",
                retryable=True,
            )
        candidate = dict(candidate)
        candidate["locator_preview"] = locator_from_element(candidate, fallback_policy="never")
        candidate["stability"] = {"level": "medium", "score": 60, "reasons": ["已获取当前元素，等待三次采样确认"]}
        with self._lock:
            session.locked = candidate
            session.samples = [candidate]
            session.emit("locked", element=candidate, sample_index=1, sample_count=3)
        for index, delay in enumerate((0.35, 0.55), start=2):
            time.sleep(delay)
            with self._lock:
                if session.status != "active":
                    break
            sample = self._sample_element(probe, locked_point)
            with self._lock:
                session.samples.append(sample or {})
                session.emit("locked", element=sample, sample_index=index, sample_count=3)
        with self._lock:
            samples = list(session.samples)
            stable, stability_reasons = self._assess_stability(samples)
            if session.locked:
                session.locked["stability"] = {
                    "level": "high" if stable else "low",
                    "score": 95 if stable else 35,
                    "reasons": stability_reasons,
                }
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
            public = session.public()
        self._stop_session_heartbeat(session)
        try:
            self.lease.release(run_id=session.session_id, force=False)
        except (OSError, PermissionError):
            # The lease may have expired and been handed to another run;
            # never force-delete that newer owner's lease.
            pass
        self._wake.set()
        return {"session": public, "locator": element.get("locator_preview"), "element": element}

    def cancel(self, session_id: str, *, reason: str = "cancelled") -> Dict[str, Any]:
        with self._lock:
            session = self._session(session_id)
            if session.status == "active":
                session.status = "cancelled"
                session.emit("ended", reason=reason)
                should_release = True
            else:
                should_release = False
            public = session.public()
        if should_release:
            self._stop_session_heartbeat(session)
            try:
                self.lease.release(run_id=session.session_id, force=False)
            except (OSError, PermissionError):
                pass
            self._wake.set()
        return public

    def status(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._session(session_id).public()
