from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def classify_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    lowered = str(name or "").strip().lower()
    args = arguments if isinstance(arguments, dict) else {}
    action = str(args.get("action") or args.get("operation") or args.get("mode") or "").lower()
    if any(token in lowered for token in ("powershell", "registry")):
        return "high"
    if "filesystem" in lowered or lowered in {"file", "files"}:
        return "low" if action in {"read", "list", "find", "search", "exists", "stat"} else "high"
    if "process" in lowered:
        return "low" if action in {"list", "get", "inspect"} else "high"
    if any(token in lowered for token in ("click", "type", "keyboard", "mouse", "clipboard", "window")):
        return "medium"
    return "low"


def annotate_catalog(tools: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [{**tool, "risk_level": classify_tool(str(tool.get("name") or ""))} for tool in tools if isinstance(tool, dict)]


def workflow_risk(definition: Dict[str, Any]) -> Dict[str, Any]:
    high = []
    medium = []
    for node in definition.get("nodes", []) if isinstance(definition.get("nodes"), list) else []:
        if not isinstance(node, dict) or str(node.get("type") or "") != "action":
            continue
        risk = classify_tool(str(node.get("tool") or ""), node.get("arguments") if isinstance(node.get("arguments"), dict) else {})
        item = {"node_id": str(node.get("id") or ""), "title": str(node.get("title") or ""), "tool": str(node.get("tool") or ""), "risk": risk}
        if risk == "high":
            high.append(item)
        elif risk == "medium":
            medium.append(item)
    return {"level": "high" if high else "medium" if medium else "low", "high_risk_nodes": high, "medium_risk_nodes": medium}
