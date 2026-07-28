from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import sys
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple


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

_INTERACTIVE_TYPES = {
    "button",
    "checkbox",
    "combobox",
    "edit",
    "hyperlink",
    "listitem",
    "menuitem",
    "radiobutton",
    "scrollbar",
    "slider",
    "spinner",
    "tabitem",
    "treeitem",
    "datagrid",
    "dataitem",
    "document",
    "splitbutton",
}


def create_uia_automation() -> Any:
    """Create UI Automation through the system type library, not a ProgID.

    Some normal Windows installations expose the COM class only by CLSID.  A
    generated type library also gives comtypes the IUIAutomation interface
    needed for this non-IDispatch COM server.
    """
    import comtypes.client  # type: ignore

    module = comtypes.client.GetModule("UIAutomationCore.dll")
    automation_class = getattr(module, "CUIAutomation8", None) or module.CUIAutomation
    return comtypes.client.CreateObject(automation_class, interface=module.IUIAutomation)


def foreground_window() -> Dict[str, Any]:
    """Return a cheap, non-persistent identity for the foreground window."""
    if sys.platform != "win32":
        return {"name": "", "handle": None, "process_id": None}
    try:
        user32 = ctypes.windll.user32
        handle = int(user32.GetForegroundWindow() or 0)
        if not handle:
            return {"name": "", "handle": None, "process_id": None}
        length = max(0, int(user32.GetWindowTextLengthW(handle)))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, buffer, len(buffer))
        process_id = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        return {"name": str(buffer.value or "").strip(), "handle": handle, "process_id": int(process_id.value or 0) or None}
    except Exception:
        return {"name": "", "handle": None, "process_id": None}


def opened_windows() -> List[Dict[str, Any]]:
    if sys.platform != "win32":
        return []
    result: List[Dict[str, Any]] = []
    try:
        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def visit(handle: int, _param: int) -> bool:
            length = max(0, int(user32.GetWindowTextLengthW(handle)))
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            name = str(buffer.value or "").strip()
            if not name:
                return True
            process_id = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
            result.append(
                {
                    "name": name,
                    "handle": int(handle),
                    "process_id": int(process_id.value or 0) or None,
                    "visible": bool(user32.IsWindowVisible(handle)),
                }
            )
            return True

        callback = callback_type(visit)
        user32.EnumWindows(callback, 0)
    except Exception:
        return []
    return result


def window_names_match(observed: str, current: str) -> bool:
    left, right = str(observed or "").strip().casefold(), str(current or "").strip().casefold()
    if not left or not right:
        return not left and not right
    if left == right:
        return True

    def decorated(base: str, title: str) -> bool:
        if not title.startswith(base) or len(title) == len(base):
            return False
        return title[len(base)] in " \t-—–|·:：[（([{/\\"

    return decorated(left, right) or decorated(right, left)


class NativeWindowObserver:
    """Read one live UIA window without depending on Windows-MCP internals."""

    available = sys.platform == "win32"

    @staticmethod
    def _value(element: Any, property_id: int) -> Any:
        try:
            value = element.GetCurrentPropertyValue(property_id)
            if value is None or str(value).startswith("<Unknown"):
                return None
            return value
        except Exception:
            return None

    @classmethod
    def _process_name(cls, process_id: Any) -> str:
        try:
            if process_id:
                import psutil  # type: ignore

                return str(psutil.Process(int(process_id)).name() or "")
        except Exception:
            pass
        return ""

    @classmethod
    def _bounds(cls, element: Any) -> Optional[Dict[str, float]]:
        rect = cls._value(element, 30001)  # UIA_BoundingRectanglePropertyId
        if rect is None:
            return None
        try:
            if all(hasattr(rect, key) for key in ("left", "top", "right", "bottom")):
                left, top, right, bottom = (float(getattr(rect, key)) for key in ("left", "top", "right", "bottom"))
            else:
                values = list(rect)
                if len(values) < 4:
                    return None
                left, top, width, height = (float(value) for value in values[:4])
                return {"x": left, "y": top, "width": max(0.0, width), "height": max(0.0, height)}
            return {
                "x": left,
                "y": top,
                "width": max(0.0, right - left),
                "height": max(0.0, bottom - top),
            }
        except Exception:
            return None

    @classmethod
    def _element(cls, element: Any, *, window_name: str, process_name: str, parent_name: str) -> Dict[str, Any]:
        name = str(cls._value(element, 30005) or "").strip()
        control_type_id = cls._value(element, 30003)
        try:
            control_type = _CONTROL_TYPES.get(int(control_type_id), str(control_type_id or "Custom"))
        except (TypeError, ValueError):
            control_type = "Custom"
        bounds = cls._bounds(element)
        item: Dict[str, Any] = {
            "window_name": window_name,
            "parent_name": parent_name,
            "name": name,
            "text": name,
            "control_type": control_type,
            "automation_id": str(cls._value(element, 30011) or "").strip(),
            "class_name": str(cls._value(element, 30012) or "").strip(),
            "framework_id": str(cls._value(element, 30024) or "").strip(),
            "process_name": process_name,
            "process_id": int(cls._value(element, 30002) or 0) or None,
            "bounds": bounds,
            "enabled": cls._value(element, 30010) is not False,
            "visible": cls._value(element, 30022) is not True,
            "focused": cls._value(element, 30008) is True,
            "password": cls._value(element, 30019) is True,
            "strategy": "uia",
            "provider": "native_uia",
        }
        if bounds:
            item["observed_point"] = [
                round(float(bounds["x"]) + float(bounds["width"]) / 2),
                round(float(bounds["y"]) + float(bounds["height"]) / 2),
            ]
        item["interactive"] = control_type.casefold().replace(" ", "") in _INTERACTIVE_TYPES
        return item

    @classmethod
    def _children(cls, walker: Any, parent: Any) -> Iterable[Any]:
        try:
            child = walker.GetFirstChildElement(parent)
        except Exception:
            child = None
        while child:
            yield child
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                child = None

    @classmethod
    def _select_window(cls, automation: Any, target_window: str, process_name: str) -> Tuple[Any, str, str]:
        foreground = foreground_window()
        handle = foreground.get("handle")
        if handle and (not target_window or window_names_match(target_window, str(foreground.get("name") or ""))):
            try:
                element = automation.ElementFromHandle(int(handle))
                if element is not None:
                    name = str(cls._value(element, 30005) or foreground.get("name") or "").strip()
                    pid = cls._value(element, 30002) or foreground.get("process_id")
                    candidate_process = cls._process_name(pid)
                    if not process_name or candidate_process.casefold() == process_name.casefold():
                        return element, name, candidate_process
            except Exception:
                pass

        exact_native: List[Tuple[Dict[str, Any], str]] = []
        contains_native: List[Tuple[Dict[str, Any], str]] = []
        for candidate in opened_windows():
            name = str(candidate.get("name") or "")
            candidate_process = cls._process_name(candidate.get("process_id"))
            if process_name and candidate_process.casefold() != process_name.casefold():
                continue
            value = (candidate, candidate_process)
            if name.casefold() == target_window.casefold():
                exact_native.append(value)
            elif target_window and window_names_match(target_window, name):
                contains_native.append(value)
        native_matches = exact_native or contains_native
        visible_matches = [item for item in native_matches if item[0].get("visible")]
        if len(visible_matches) == 1:
            native_matches = visible_matches
        if len(native_matches) == 1:
            candidate, candidate_process = native_matches[0]
            try:
                element = automation.ElementFromHandle(int(candidate["handle"]))
                if element is not None:
                    return element, str(candidate["name"]), candidate_process
            except Exception:
                pass

        root = automation.GetRootElement()
        walker = automation.ControlViewWalker
        exact: List[Tuple[Any, str, str]] = []
        contains: List[Tuple[Any, str, str]] = []
        for candidate in cls._children(walker, root):
            name = str(cls._value(candidate, 30005) or "").strip()
            pid = cls._value(candidate, 30002)
            candidate_process = cls._process_name(pid)
            if process_name and candidate_process.casefold() != process_name.casefold():
                continue
            if not target_window:
                continue
            if name.casefold() == target_window.casefold():
                exact.append((candidate, name, candidate_process))
            elif window_names_match(target_window, name):
                contains.append((candidate, name, candidate_process))
        matches = exact or contains
        if len(matches) != 1:
            return None, "", ""
        return matches[0]

    def observe(self, *, target_window: str = "", process_name: str = "", max_elements: int = 2500) -> Dict[str, Any]:
        observation_id = "obs_" + uuid.uuid4().hex[:16]
        captured_at = time.time()
        focused = foreground_window()
        base: Dict[str, Any] = {
            "observation_id": observation_id,
            "captured_at": captured_at,
            "provider": "native_uia",
            "focused_window": str(focused.get("name") or ""),
            "target_window": target_window,
            "elements": [],
            "count": 0,
            "warnings": [],
        }
        if sys.platform != "win32":
            base["warnings"] = ["原生 UIA 观察仅支持 Windows"]
            base["diagnostics"] = {"code": "native_uia_unavailable", "retryable": False}
            return base

        initialized = False
        try:
            import comtypes  # type: ignore
            import comtypes.client  # type: ignore

            comtypes.CoInitialize()
            initialized = True
            automation = create_uia_automation()
            window, resolved_window, resolved_process = self._select_window(automation, target_window, process_name)
            if window is None:
                base["warnings"] = ["未能唯一找到目标窗口"]
                base["diagnostics"] = {
                    "code": "target_window_not_found",
                    "target_window": target_window,
                    "retryable": True,
                }
                return base

            base["target_window"] = resolved_window
            walker = automation.ControlViewWalker
            queue: List[Tuple[Any, str]] = [(child, resolved_window) for child in self._children(walker, window)]
            elements: List[Dict[str, Any]] = []
            truncated = False
            while queue:
                element, parent_name = queue.pop(0)
                item = self._element(
                    element,
                    window_name=resolved_window,
                    process_name=resolved_process,
                    parent_name=parent_name,
                )
                next_parent = str(item.get("name") or parent_name)
                if item.get("name") or item.get("automation_id") or item.get("control_type") != "Custom":
                    item["element_id"] = "uia_" + format(len(elements) + 1, "04d")
                    item["selector_version"] = 1
                    item["stability"] = "high" if item.get("automation_id") and resolved_process else "normal" if item.get("name") else "low"
                    item["diagnostics"] = [] if item["stability"] != "low" else ["元素缺少名称和 AutomationId"]
                    elements.append(item)
                if len(elements) >= max(1, int(max_elements)):
                    truncated = True
                    break
                queue.extend((child, next_parent) for child in self._children(walker, element))
            base["elements"] = elements
            base["count"] = len(elements)
            if truncated:
                base["warnings"].append(f"目标窗口元素超过 {max_elements} 个，已截断观察结果")
            if not elements:
                base["warnings"].append("目标窗口没有暴露可解析的 UIA 元素")
            base["window_element_counts"] = {resolved_window: len(elements)}
            base["target_window_element_count"] = len(elements)
            base["diagnostics"] = {
                "code": "ok" if elements else "target_window_elements_unavailable",
                "target_window": resolved_window,
                "target_window_element_count": len(elements),
                "retryable": not bool(elements),
            }
            return base
        except Exception as exc:
            base["warnings"] = [f"原生 UIA 观察失败：{exc}"]
            base["diagnostics"] = {"code": "native_uia_unavailable", "message": str(exc), "retryable": True}
            return base
        finally:
            if initialized:
                try:
                    import comtypes  # type: ignore

                    comtypes.CoUninitialize()
                except Exception:
                    pass


def merge_observations(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Merge a target-window fallback without inventing Windows-MCP labels."""
    primary_elements = [item for item in primary.get("elements", []) if isinstance(item, dict)]
    fallback_elements = [item for item in fallback.get("elements", []) if isinstance(item, dict)]
    if not fallback_elements:
        result = dict(primary)
        result["fallback_used"] = False
        diagnostics = dict(result.get("diagnostics") or {})
        diagnostics["native_uia"] = fallback.get("diagnostics") or {}
        result["diagnostics"] = diagnostics
        result["warnings"] = list(primary.get("warnings") or []) + list(fallback.get("warnings") or [])
        return result

    target = str(fallback.get("target_window") or "")
    retained = [item for item in primary_elements if not target or not window_names_match(target, str(item.get("window_name") or ""))]
    result = dict(primary)
    result["elements"] = retained + fallback_elements
    result["count"] = len(result["elements"])
    result["provider"] = "windows_mcp+native_uia"
    result["fallback_used"] = True
    result["target_window"] = target
    result["target_window_element_count"] = len(fallback_elements)
    counts = dict(primary.get("window_element_counts") or {})
    counts[target] = len(fallback_elements)
    result["window_element_counts"] = counts
    result["warnings"] = list(primary.get("warnings") or []) + list(fallback.get("warnings") or [])
    diagnostics = dict(primary.get("diagnostics") or {})
    diagnostics.update({"code": "ok", "fallback_provider": "native_uia", "target_window_element_count": len(fallback_elements)})
    result["diagnostics"] = diagnostics
    return result


class ElementObservationService:
    """Add target-window freshness and native UIA fallback to a Snapshot."""

    def __init__(self, native: Optional[NativeWindowObserver] = None) -> None:
        self.native = native or NativeWindowObserver()
        self._lock = threading.RLock()
        self._last: Dict[str, Any] = {}

    def current_foreground_window(self) -> Dict[str, Any]:
        value = foreground_window()
        return {"focused_window": value.get("name"), **value}

    @staticmethod
    def _target_count(snapshot: Dict[str, Any], target_window: str) -> int:
        return sum(
            1
            for item in snapshot.get("elements", [])
            if isinstance(item, dict) and window_names_match(target_window, str(item.get("window_name") or ""))
        )

    def _remember(self, observation: Dict[str, Any]) -> None:
        value = {
            "observation_id": observation.get("observation_id"),
            "observation_updated_at": observation.get("captured_at"),
            "observation_provider": observation.get("provider"),
            "observation_target_window": observation.get("target_window"),
            "observation_element_count": observation.get("target_window_element_count", observation.get("count", 0)),
            "snapshot_health": observation.get("snapshot_health"),
            "fallback_used": bool(observation.get("fallback_used")),
        }
        with self._lock:
            self._last = value

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._last)

    def invalidate(self) -> None:
        with self._lock:
            self._last = {}

    def enhance_sync(self, snapshot: Dict[str, Any], locator: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        locator = locator if isinstance(locator, dict) else {}
        result = dict(snapshot)
        result.setdefault("observation_id", "obs_" + uuid.uuid4().hex[:16])
        result.setdefault("captured_at", time.time())
        result.setdefault("provider", "windows-mcp")
        result.setdefault("fallback_used", False)
        target_window = str(locator.get("window_name") or "").strip()
        process_name = str(locator.get("process_name") or "").strip()
        result["target_window"] = target_window
        target_count = self._target_count(result, target_window) if target_window else int(result.get("count") or 0)
        result["target_window_element_count"] = target_count
        result["snapshot_health"] = "target_available" if target_count else "target_elements_unavailable" if target_window else "observed"

        strategy = str(locator.get("strategy") or "uia").casefold()
        if target_window and target_count == 0 and strategy != "position":
            fallback = self.native.observe(target_window=target_window, process_name=process_name)
            result = merge_observations(result, fallback)
            result["observation_id"] = str(snapshot.get("observation_id") or result.get("observation_id") or "obs_" + uuid.uuid4().hex[:16])
            result["captured_at"] = float(snapshot.get("captured_at") or result.get("captured_at") or time.time())
            result["snapshot_health"] = "target_available" if int(result.get("target_window_element_count") or 0) else "target_elements_unavailable"
        self._remember(result)
        return result

    async def enhance(self, snapshot: Dict[str, Any], locator: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # UIA providers can block while an application builds its accessibility
        # tree.  Keep the runner loop and its independent lease heartbeat live.
        return await asyncio.to_thread(self.enhance_sync, snapshot, locator)
