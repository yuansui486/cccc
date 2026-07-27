"""Snapshot normalization and element-first locator matching.

Windows-MCP has returned several equivalent Snapshot shapes over time: a
structured MCP result, JSON encoded text, and a human-readable ``UI Tree``.
This module deliberately normalizes all of them into one small, serializable
shape.  ``mcp_label`` is assigned for the current Snapshot only; it must never
be copied into a workflow locator.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Set, Tuple


_INTERACTIVE_CONTROL_TYPES = {
    "button", "edit", "textbox", "input", "checkbox", "radio", "combobox",
    "listitem", "menuitem", "link", "tab", "slider", "treeitem", "hyperlink",
}


def _first(value: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        child = value.get(key)
        if child is not None and str(child).strip():
            return str(child).strip()
    return ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def _bounds(value: Dict[str, Any]) -> Dict[str, float] | None:
    """Normalize Win32/UIA/DOM rectangle aliases, including list rectangles."""
    raw = value.get("bounds") or value.get("bounding_rect") or value.get("boundingRectangle") or value.get("rectangle")
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        nums = [_number(item) for item in raw[:4]]
        if all(item is not None for item in nums):
            x, y, width, height = [float(item) for item in nums]  # type: ignore[arg-type]
            return {"x": x, "y": y, "width": width, "height": height}
    if isinstance(raw, str):
        nums = re.findall(r"-?\d+(?:\.\d+)?", raw)
        if len(nums) >= 4:
            return {"x": float(nums[0]), "y": float(nums[1]), "width": float(nums[2]), "height": float(nums[3])}
    if not isinstance(raw, dict):
        # Some adapters place bounds directly on the element.
        raw = value
    result: Dict[str, float] = {}
    for key, aliases in {
        "x": ("x", "left"),
        "y": ("y", "top"),
        "width": ("width", "w"),
        "height": ("height", "h"),
        "right": ("right",),
        "bottom": ("bottom",),
    }.items():
        for alias in aliases:
            number = _number(raw.get(alias)) if isinstance(raw, dict) else None
            if number is not None:
                result[key] = number
                break
    if "width" not in result and "right" in result and "x" in result:
        result["width"] = result["right"] - result["x"]
    if "height" not in result and "bottom" in result and "y" in result:
        result["height"] = result["bottom"] - result["y"]
    return result or None


def _decode(value: Any, *, max_depth: int = 5) -> Any:
    """Decode nested JSON strings without attempting to parse ordinary text."""
    current = value
    for _ in range(max_depth):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text or text[0] not in "[{\"":
            return current
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return current
        if decoded == current:
            return current
        current = decoded
    return current


def _snapshot_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "value", "raw", "output"):
            if isinstance(value.get(key), str):
                return str(value[key])
    return ""


def _focused_from_text(text: str) -> str:
    match = re.search(r"Focused Window\s*:\s*.*?\n[-\s]+\n(?P<row>[^\n]+)", text, re.I | re.S)
    if not match:
        return ""
    row = match.group("row").strip()
    # The table has depth/status columns separated by at least two spaces.
    title = re.split(r"\s{2,}(?=-?\d+\s)", row, maxsplit=1)[0].strip()
    return title or row


def _parse_ui_tree(text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Parse the textual tree printed by Windows-MCP.

    The parser intentionally accepts extra decorations and unknown metadata;
    this keeps it useful across MCP releases and non-English control names.
    """
    elements: List[Dict[str, Any]] = []
    focused_window = _focused_from_text(text)
    current_window = focused_window
    current_process = ""
    current_framework = ""
    parent_by_level: Dict[int, str] = {}
    coordinate_re = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")
    quoted_re = re.compile(r'[\"“](.*?)[\"”]')
    metadata_re = re.compile(r"\[\s*([A-Za-z][\w -]*)\s*:\s*([^\]]*)\]")
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        # Header context is useful when a tree omits window/process on each row.
        context_match = re.match(r"(?:Window|窗口)\s*:\s*(.+)$", stripped, re.I)
        if context_match:
            current_window = context_match.group(1).strip()
            continue
        context_match = re.match(r"(?:Process|进程)\s*:\s*(.+)$", stripped, re.I)
        if context_match:
            current_process = context_match.group(1).strip()
            continue
        context_match = re.match(r"(?:Framework|框架)\s*:\s*(.+)$", stripped, re.I)
        if context_match:
            current_framework = context_match.group(1).strip()
            continue
        coordinate = coordinate_re.search(stripped)
        if coordinate is None:
            continue
        x, y = float(coordinate.group(1)), float(coordinate.group(2))
        body = stripped[coordinate.end():].strip(" │├└─-\t")
        # Ignore cursor/screenshot metadata that happens to contain coordinates.
        if body.lower().startswith(("cursor position", "screenshot size", "screenshot region")):
            continue
        metadata = {key.casefold().replace(" ", "_"): value.strip() for key, value in metadata_re.findall(body)}
        body_without_metadata = metadata_re.sub("", body).strip()
        quoted = quoted_re.search(body_without_metadata)
        name = quoted.group(1).strip() if quoted else ""
        before_name = body_without_metadata[: quoted.start()].strip() if quoted else body_without_metadata
        # Control type is the visible role before the quoted label. Keep the
        # source language; matching is case-insensitive and consumers can show it.
        control_type = before_name.strip(" :/\\")
        if not control_type:
            control_type = metadata.get("control_type", metadata.get("role", ""))
        action = str(metadata.get("action") or "").strip().casefold()
        if not name and not control_type and not metadata:
            continue
        prefix = line[: coordinate.start()]
        level = len(re.match(r"^[\s│|]*", prefix).group(0)) if re.match(r"^[\s│|]*", prefix) else 0
        level = min(level // 2, 64)
        parent_name = parent_by_level.get(max((item for item in parent_by_level if item < level), default=-1), "")
        parent_by_level[level] = name or control_type
        for old_level in list(parent_by_level):
            if old_level > level:
                parent_by_level.pop(old_level, None)
        item: Dict[str, Any] = {
            "window_name": current_window,
            "parent_name": parent_name,
            "name": name,
            "text": metadata.get("value", name),
            "control_type": control_type,
            "process_name": current_process,
            "framework_id": current_framework,
            "bounds": {"x": x, "y": y, "width": 0.0, "height": 0.0},
            "observed_point": [round(x), round(y)],
            "visible": True,
            "enabled": metadata.get("enabled", "true").casefold() not in {"false", "0", "disabled"},
            "focused": any(key in body_without_metadata.casefold() for key in ("focused", "focus", "焦点")),
            "dom": "dom" in body_without_metadata.casefold() or metadata.get("mode", "").casefold() == "dom",
            "_interactive": bool(action and action not in {"none", "read"})
            or control_type.casefold().replace(" ", "") in _INTERACTIVE_CONTROL_TYPES,
            "action": action,
        }
        for source, target in (("automation_id", "automation_id"), ("automationid", "automation_id"), ("class", "class_name"), ("class_name", "class_name"), ("framework_id", "framework_id"), ("process", "process_name")):
            if source in metadata and not item.get(target):
                item[target] = metadata[source]
        for key in ("checked", "selected", "expanded", "toggle_state", "password"):
            if key in metadata:
                item[key] = metadata[key]
        elements.append(item)
    return elements, {"focused_window": focused_window, "source": "ui_tree"}


def _walk(value: Any, *, window_name: str = "", parent_name: str = "", process_name: str = "", framework_id: str = "", dom: bool = False) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        # MCP content blocks are envelopes, not UI elements. Their text is
        # parsed separately by ``_payloads``.
        if str(value.get("type") or "").casefold() in {"text", "image", "resource", "resource_link"}:
            return
        current_window = _first(value, "window_name", "window", "windowTitle", "title_bar") or window_name
        if not current_window and isinstance(value.get("focused_window"), str):
            current_window = str(value["focused_window"]).strip()
        current_process = _first(value, "process_name", "processName", "process", "exe") or process_name
        current_framework = _first(value, "framework_id", "frameworkId") or framework_id
        current_dom = bool(value.get("dom") or value.get("is_dom") or dom)
        name = _first(value, "name", "text", "title", "label")
        control_type = _first(value, "control_type", "controlType", "type", "role", "localized_control_type")
        automation_id = _first(value, "automation_id", "automationId", "automationID", "id")
        bounds = _bounds(value)
        has_identity = bool(
            name
            or control_type
            or automation_id
            or (
                bounds
                and any(key in value for key in ("action", "interactive", "is_interactive", "element_id", "automation_id", "automationId"))
            )
        )
        if has_identity:
            action = _first(value, "action", "default_action", "pattern")
            yield {
                "window_name": current_window,
                "parent_name": parent_name,
                "name": _first(value, "name", "label", "title"),
                "text": _first(value, "text", "value", "name"),
                "control_type": control_type,
                "automation_id": automation_id,
                "class_name": _first(value, "class_name", "className", "class"),
                "process_name": current_process,
                "framework_id": current_framework,
                "bounds": bounds,
                "observed_point": value.get("observed_point") or value.get("point"),
                "dpi": _number(value.get("dpi")),
                "monitor": int(value["monitor"]) if isinstance(value.get("monitor"), (int, str)) and str(value.get("monitor")).lstrip("-").isdigit() else None,
                "enabled": value.get("enabled", value.get("is_enabled", True)) is not False,
                "visible": value.get("visible", value.get("is_visible", True)) is not False,
                "focused": value.get("focused", value.get("has_focus", False)) is True,
                "dom": current_dom,
                "action": action,
                "value": value.get("value"),
                "toggle_state": value.get("toggle_state", value.get("toggleState")),
                "_interactive": bool(action)
                or bool(value.get("interactive", value.get("is_interactive", False)))
                or control_type.casefold().replace(" ", "") in _INTERACTIVE_CONTROL_TYPES,
            }
        next_parent = name or parent_name
        skip = {"bounds", "bounding_rect", "boundingRectangle", "rectangle", "content", "structuredContent", "structured_content"}
        for key, child in value.items():
            if key in skip:
                continue
            yield from _walk(child, window_name=current_window, parent_name=next_parent, process_name=current_process, framework_id=current_framework, dom=current_dom)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, window_name=window_name, parent_name=parent_name, process_name=process_name, framework_id=framework_id, dom=dom)


def _payloads(result: Any) -> Iterable[Any]:
    """Yield structured payloads and raw UI Tree text from any MCP envelope."""
    visited: Set[Tuple[str, int]] = set()

    def visit(value: Any, depth: int = 0) -> Iterable[Any]:
        if depth > 6:
            return
        if isinstance(value, str):
            decoded = _decode(value)
            if decoded is not value and decoded != value:
                yield from visit(decoded, depth + 1)
            else:
                if "UI Tree" in value or "Focused Window" in value or re.search(r"\(-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\)", value):
                    yield value
            return
        if isinstance(value, (dict, list)):
            marker = ("dict" if isinstance(value, dict) else "list", id(value))
            if marker in visited:
                return
            visited.add(marker)
        if isinstance(value, dict):
            yield value
            for key in ("structuredContent", "structured_content", "result", "payload", "data", "output"):
                if key in value:
                    yield from visit(value[key], depth + 1)
            content = value.get("content")
            if content is not None:
                yield from visit(content, depth + 1)
        elif isinstance(value, list):
            yield value
            for child in value:
                if isinstance(child, dict) and (child.get("type") == "text" or isinstance(child.get("text"), str)):
                    yield from visit(child.get("text", ""), depth + 1)
                else:
                    yield from visit(child, depth + 1)

    yield from visit(result)


def normalize_snapshot(result: Any) -> Dict[str, Any]:
    elements: List[Dict[str, Any]] = []
    seen: Set[Tuple[Any, ...]] = set()
    focused_window = ""
    warnings: List[str] = []
    source_kinds: Set[str] = set()
    for payload in _payloads(result):
        decoded = _decode(payload)
        if isinstance(decoded, str):
            parsed, metadata = _parse_ui_tree(decoded)
            source_kinds.add("ui_tree")
            if metadata.get("focused_window"):
                focused_window = str(metadata["focused_window"])
            candidates: Iterable[Dict[str, Any]] = parsed
        else:
            if isinstance(decoded, dict):
                focused = decoded.get("focused_window") or decoded.get("active_window") or decoded.get("foreground_window")
                if isinstance(focused, dict):
                    focused = _first(focused, "name", "title", "window_name")
                if focused:
                    focused_window = str(focused).strip()
            candidates = _walk(decoded)
        for item in candidates:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            bounds = normalized.get("bounds") if isinstance(normalized.get("bounds"), dict) else None
            key = tuple(str(normalized.get(field) or "") for field in ("window_name", "parent_name", "name", "text", "control_type", "automation_id", "process_name"))
            key += tuple(sorted((bounds or {}).items()))
            if key in seen:
                continue
            seen.add(key)
            normalized.pop("_interactive", None)
            normalized["element_id"] = "el_" + format(len(elements) + 1, "04d")
            normalized["selector_version"] = 1
            normalized["strategy"] = "dom" if normalized.get("dom") else "uia"
            normalized["capture_fingerprint"] = ""
            stable_fields = sum(bool(normalized.get(field)) for field in ("window_name", "control_type", "name", "automation_id", "process_name", "framework_id"))
            normalized["stability"] = "high" if stable_fields >= 4 else "normal" if stable_fields >= 2 else "low"
            normalized["stability_reasons"] = [] if stable_fields >= 2 else ["可访问名称或窗口约束不足"]
            normalized["diagnostics"] = list(normalized["stability_reasons"])
            if bool(item.get("_interactive")):
                normalized["mcp_label"] = sum(1 for previous in elements if previous.get("mcp_label") is not None)
            elements.append(normalized)
    if not elements:
        warnings.append("Snapshot 未返回可解析的 UI 元素")
        if source_kinds:
            warnings.append("已收到 UI Tree 文本，但其中没有带坐标或控件信息的元素行")
    return {
        "elements": elements,
        "count": len(elements),
        "focused_window": focused_window,
        "warnings": warnings,
        "diagnostics": {
            "source": sorted(source_kinds) or ["structured"],
            "element_count": len(elements),
            "focused_window": focused_window,
            "message": warnings[0] if warnings else "已解析当前桌面元素",
        },
    }


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
    locator = locator if isinstance(locator, dict) else {}
    mode = str(locator.get("match") or "exact")
    focused_window = str(snapshot.get("focused_window") or "").strip() if isinstance(snapshot, dict) else ""
    if isinstance(locator.get("mcp_label"), int):
        matches = [item for item in candidates if isinstance(item, dict) and item.get("mcp_label") == locator.get("mcp_label")]
    else:
        checks = (
            ("window_name", "window_name"), ("process_name", "process_name"),
            ("control_type", "control_type"), ("name", "name"),
            ("text", "text"), ("automation_id", "automation_id"),
            ("class_name", "class_name"), ("framework_id", "framework_id"),
            ("parent_name", "parent_name"),
        )
        def matches_fields(item: Dict[str, Any], fields: Iterable[Tuple[str, str]]) -> bool:
            return all(_match(str(item.get(field) or ""), str(locator.get(expected) or ""), mode) for field, expected in fields)

        def apply_context(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            result: List[Dict[str, Any]] = []
            for item in items:
                if focused_window and locator.get("window_name") and not _match(focused_window, str(locator.get("window_name")), mode):
                    continue
                if locator.get("monitor") is not None and item.get("monitor") not in {None, locator.get("monitor")}:
                    continue
                if locator.get("dom") is True and item.get("dom") is not True:
                    continue
                result.append(item)
            return result

        valid_candidates = [item for item in candidates if isinstance(item, dict)]
        # Stable AutomationId identity wins over a stale visible name/text.
        # Only fall back to the broader locator when the strong identity is no
        # longer present in the live tree.
        if locator.get("automation_id"):
            identity_checks = [
                pair for pair in (("automation_id", "automation_id"), ("framework_id", "framework_id"), ("process_name", "process_name"), ("control_type", "control_type"))
                if locator.get(pair[1])
            ]
            strong = apply_context([item for item in valid_candidates if matches_fields(item, identity_checks)])
            if strong:
                matches = strong
            else:
                matches = apply_context([item for item in valid_candidates if matches_fields(item, checks)])
        else:
            matches = apply_context([item for item in valid_candidates if matches_fields(item, checks)])
    if len(matches) == 1:
        stable = matches[0].get("stability")
        confidence = "high" if locator.get("automation_id") and locator.get("process_name") else "normal"
        if stable == "low":
            confidence = "low"
    else:
        confidence = "low"
    status = "unique" if len(matches) == 1 else "ambiguous" if matches else "not_found"
    code = {"unique": "ok", "ambiguous": "target_ambiguous", "not_found": "target_not_found"}[status]
    next_action = {
        "ok": "可执行目标操作",
        "target_ambiguous": "补充窗口、控件类型、父级或 AutomationId 约束后重新观察",
        "target_not_found": "重新观察桌面并确认目标窗口/元素仍然存在",
    }[code]
    return {
        "status": status,
        "matches": matches,
        "match_count": len(matches),
        "candidate_count": len(matches),
        "confidence": confidence,
        "diagnostics": {"code": code, "message": next_action, "next_action": next_action, "retryable": True},
    }


def element_center(element: Dict[str, Any]) -> List[int] | None:
    if not isinstance(element, dict):
        return None
    point = element.get("observed_point") or element.get("point")
    if isinstance(point, (list, tuple)) and len(point) >= 2 and all(_number(item) is not None for item in point[:2]):
        return [round(float(point[0])), round(float(point[1]))]
    bounds = element.get("bounds")
    if not isinstance(bounds, dict):
        return None
    x, y = bounds.get("x", bounds.get("left")), bounds.get("y", bounds.get("top"))
    width, height = bounds.get("width"), bounds.get("height")
    if all(_number(value) is not None for value in (x, y, width, height)):
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
        if all(_number(value) is not None for value in (left, top, width, height)):
            right, bottom = float(left) + float(width), float(top) + float(height)
            if (float(width) == 0 and abs(x - float(left)) <= 8 and abs(y - float(top)) <= 8) or (float(left) <= x <= right and float(top) <= y <= bottom):
                hits.append(item)
    return min(hits, key=lambda item: float((item.get("bounds") or {}).get("width") or 10**9) * float((item.get("bounds") or {}).get("height") or 10**9), default=None)


def locator_from_element(element: Dict[str, Any], *, fallback_policy: str = "never") -> Dict[str, Any]:
    """Create a durable locator, intentionally excluding the ephemeral MCP label."""
    center = element_center(element)
    observed_bounds = element.get("bounds") if isinstance(element.get("bounds"), dict) else None
    return {
        key: value
        for key, value in {
            "selector_version": 1,
            "strategy": "dom" if element.get("dom") else "uia",
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
            "observed_bounds": observed_bounds,
            "dpi": element.get("dpi"),
            "capture_fingerprint": element.get("capture_fingerprint", ""),
            "stability": element.get("stability", "normal"),
            "diagnostics": element.get("diagnostics", element.get("stability_reasons", [])),
            # Retained as an observation for legacy clients; runtime only uses
            # it when fallback_policy is explicitly controlled.
            "position_anchor": {"x": center[0], "y": center[1]} if center is not None else None,
            "fallback_policy": fallback_policy,
        }.items()
        if value not in (None, "")
    }
