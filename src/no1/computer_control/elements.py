"""Snapshot element normalization and persistent locator matching."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List


_TEXT_KEYS = ("name", "text", "title", "label", "automation_id", "automationId")


def _first(value: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        child = value.get(key)
        if child is not None and str(child).strip():
            return str(child).strip()
    return ""


def _bounds(value: Dict[str, Any]) -> Dict[str, float] | None:
    raw = value.get("bounds") or value.get("bounding_rect") or value.get("rectangle")
    if isinstance(raw, dict):
        aliases = {"left": "x", "top": "y", "right": "right", "bottom": "bottom"}
        result: Dict[str, float] = {}
        for key, target in aliases.items():
            candidate = raw.get(key, raw.get(target))
            if isinstance(candidate, (int, float)):
                result[target] = float(candidate)
        if "width" in raw and isinstance(raw["width"], (int, float)):
            result["width"] = float(raw["width"])
        if "height" in raw and isinstance(raw["height"], (int, float)):
            result["height"] = float(raw["height"])
        if result:
            return result
    return None


def _walk(value: Any, *, window_name: str = "", parent_name: str = "") -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        current_window = _first(value, "window_name", "window", "windowTitle") or window_name
        name = _first(value, "name", "text", "title", "label")
        control_type = _first(value, "control_type", "controlType", "type", "role")
        has_identity = bool(name or control_type or _first(value, "automation_id", "automationId"))
        if has_identity:
            yield {
                "window_name": current_window,
                "parent_name": parent_name,
                "name": _first(value, "name", "label", "title"),
                "text": _first(value, "text", "value", "name"),
                "control_type": control_type,
                "automation_id": _first(value, "automation_id", "automationId", "id"),
                "class_name": _first(value, "class_name", "className"),
                "process_name": _first(value, "process_name", "processName", "process"),
                "framework_id": _first(value, "framework_id", "frameworkId"),
                "bounds": _bounds(value),
                "monitor": value.get("monitor") if isinstance(value.get("monitor"), int) else None,
                "enabled": value.get("enabled", value.get("is_enabled", True)) is not False,
                "visible": value.get("visible", value.get("is_visible", True)) is not False,
                "focused": value.get("focused", value.get("has_focus", False)) is True,
                "dom": bool(value.get("dom") or value.get("is_dom")),
            }
        next_parent = name or parent_name
        for child in value.values():
            yield from _walk(child, window_name=current_window, parent_name=next_parent)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, window_name=window_name, parent_name=parent_name)


def _payloads(result: Any) -> Iterable[Any]:
    if isinstance(result, dict):
        contents = result.get("content")
        if not isinstance(contents, list):
            yield result
            contents = []
        for content in contents:
            if isinstance(content, dict) and content.get("type") == "text":
                text = str(content.get("text") or "").strip()
                if text:
                    try:
                        yield json.loads(text)
                    except (TypeError, ValueError):
                        # Some Windows-MCP versions return a table preview. It
                        # is intentionally retained as a warning, not guessed
                        # into clickable elements.
                        continue


def normalize_snapshot(result: Any) -> Dict[str, Any]:
    elements: List[Dict[str, Any]] = []
    seen = set()
    focused_window = ""
    for payload in _payloads(result):
        if isinstance(payload, dict):
            focused = payload.get("focused_window") or payload.get("active_window") or payload.get("foreground_window")
            if isinstance(focused, dict):
                focused = _first(focused, "name", "title", "window_name")
            if focused:
                focused_window = str(focused).strip()
        for item in _walk(payload):
            key = tuple(item.get(field) or "" for field in ("window_name", "parent_name", "name", "text", "control_type", "automation_id"))
            bounds = item.get("bounds") or {}
            key += tuple(sorted(bounds.items()))
            if key in seen:
                continue
            seen.add(key)
            item["element_id"] = "el_" + format(len(elements) + 1, "04d")
            elements.append(item)
    return {"elements": elements, "count": len(elements), "focused_window": focused_window, "warnings": [] if elements else ["Snapshot 未返回可解析的 UI 元素"]}


def _match(value: str, expected: str, mode: str) -> bool:
    if not expected:
        return True
    value, expected = str(value or ""), str(expected)
    if mode == "contains":
        return expected.casefold() in value.casefold()
    if mode == "regex":
        try:
            return re.search(expected, value, re.I) is not None
        except re.error:
            return False
    return value.casefold() == expected.casefold()


def resolve_locator(snapshot: Dict[str, Any], locator: Dict[str, Any]) -> Dict[str, Any]:
    candidates = snapshot.get("elements") if isinstance(snapshot, dict) else []
    if not isinstance(candidates, list):
        candidates = []
    mode = str(locator.get("match") or "exact")
    focused_window = str(snapshot.get("focused_window") or "").strip()
    matches: List[Dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        checks = (
            ("window_name", "window_name"), ("process_name", "process_name"),
            ("control_type", "control_type"), ("name", "name"),
            ("text", "text"), ("automation_id", "automation_id"),
            ("class_name", "class_name"), ("framework_id", "framework_id"),
            ("parent_name", "parent_name"),
        )
        if all(_match(str(item.get(field) or ""), str(locator.get(expected) or ""), mode) for field, expected in checks):
            if focused_window and locator.get("window_name") and not _match(focused_window, str(locator.get("window_name")), mode):
                continue
            if locator.get("monitor") is not None and item.get("monitor") not in {None, locator.get("monitor")}:
                continue
            matches.append(item)
    confidence = "high" if len(matches) == 1 and locator.get("automation_id") else "normal" if len(matches) == 1 else "low"
    return {
        "status": "unique" if len(matches) == 1 else "ambiguous" if matches else "not_found",
        "matches": matches,
        "match_count": len(matches),
        "confidence": confidence,
    }


def element_center(element: Dict[str, Any]) -> List[int] | None:
    bounds = element.get("bounds") if isinstance(element, dict) else None
    if not isinstance(bounds, dict):
        return None
    x = bounds.get("x", bounds.get("left"))
    y = bounds.get("y", bounds.get("top"))
    width, height = bounds.get("width"), bounds.get("height")
    if isinstance(x, (int, float)) and isinstance(y, (int, float)) and isinstance(width, (int, float)) and isinstance(height, (int, float)):
        return [round(float(x) + float(width) / 2), round(float(y) + float(height) / 2)]
    return None


def element_at_point(snapshot: Dict[str, Any], point: Any) -> Dict[str, Any] | None:
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    try:
        x, y = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return None
    hits: List[Dict[str, Any]] = []
    for item in snapshot.get("elements", []) if isinstance(snapshot, dict) else []:
        bounds = item.get("bounds") if isinstance(item, dict) else None
        if not isinstance(bounds, dict):
            continue
        left, top = bounds.get("x", bounds.get("left")), bounds.get("y", bounds.get("top"))
        width, height = bounds.get("width"), bounds.get("height")
        if all(isinstance(value, (int, float)) for value in (left, top, width, height)) and float(left) <= x <= float(left) + float(width) and float(top) <= y <= float(top) + float(height):
            hits.append(item)
    return min(hits, key=lambda item: float((item.get("bounds") or {}).get("width") or 10**9) * float((item.get("bounds") or {}).get("height") or 10**9), default=None)


def locator_from_element(element: Dict[str, Any], *, fallback_policy: str = "controlled") -> Dict[str, Any]:
    center = element_center(element)
    return {
        key: value
        for key, value in {
            "window_name": element.get("window_name", ""),
            "process_name": element.get("process_name", ""),
            "control_type": element.get("control_type", ""),
            "name": element.get("name", ""),
            "text": element.get("text", ""),
            "automation_id": element.get("automation_id", ""),
            "class_name": element.get("class_name", ""),
            "framework_id": element.get("framework_id", ""),
            "parent_name": element.get("parent_name", ""),
            "monitor": element.get("monitor"),
            "position_anchor": {"x": center[0], "y": center[1]} if center is not None else None,
            "fallback_policy": fallback_policy,
        }.items()
        if value not in (None, "")
    }
