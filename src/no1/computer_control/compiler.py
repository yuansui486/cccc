"""User-facing workflow compilation and diagnostics.

All workflow sources (manual edits, recordings, and AI repairs) pass through
this small deterministic compiler before they are persisted or executed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .mcp import validate_workflow_tools
from .models import WorkflowDefinition


def _diagnostic(code: str, message: str, *, path: str = "", severity: str = "error", next_action: str = "") -> Dict[str, Any]:
    return {"code": code, "path": path, "message": message, "severity": severity, "next_action": next_action}


def compile_workflow(definition: WorkflowDefinition | Dict[str, Any], catalog: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Validate and normalize a workflow without mutating the caller.

    The return value is intentionally JSON serializable so both API clients
    and agents can render the same field-level diagnostics.
    """

    diagnostics: List[Dict[str, Any]] = []
    try:
        value = definition.model_dump(mode="json") if isinstance(definition, WorkflowDefinition) else dict(definition)
        model = WorkflowDefinition.model_validate(value)
    except Exception as exc:
        diagnostics.append(_diagnostic("workflow_schema_invalid", str(exc), next_action="修正标记的字段后重新编译"))
        return {"valid": False, "definition": None, "diagnostics": diagnostics}

    normalized = model.model_dump(mode="json")
    if catalog:
        try:
            validate_workflow_tools(model, catalog)
        except Exception as exc:
            message = str(exc)
            diagnostics.append(_diagnostic("tool_schema_invalid", message, next_action="重新读取工具目录并修正步骤参数"))
        # Also retain per-step paths for form highlighting. The aggregate
        # validator above remains the compatibility gate.
        schemas = {str(item.get("name") or ""): item.get("inputSchema") for item in catalog if isinstance(item, dict)}
        from .mcp import validate_arguments_against_schema
        for index, node in enumerate(model.nodes):
            if node.type != "action" or node.tool not in schemas:
                continue
            args = {key: value for key, value in node.arguments.items() if key != "coordinate_fallback"}
            try:
                validate_arguments_against_schema(node.tool, args, schemas.get(node.tool) if isinstance(schemas.get(node.tool), dict) else {})
            except Exception as exc:
                diagnostics.append(_diagnostic("field_invalid", str(exc), path=f"nodes[{index}].arguments", next_action="按字段提示修正参数"))

    for index, node in enumerate(model.nodes):
        path = f"nodes[{index}]"
        if node.type != "action":
            continue
        if node.timeout_seconds is not None and node.timeout_seconds <= 0:
            diagnostics.append(_diagnostic("invalid_timeout", "操作超时必须为空或大于 0；为空表示一直等待直到取消", path=f"{path}.timeout_seconds"))
        if node.tool.lower() in {"click", "type"}:
            target = node.target
            args = node.arguments
            if target is not None and target.fallback_policy == "never" and any(key in args for key in ("loc", "x", "y")):
                diagnostics.append(_diagnostic("coordinate_not_allowed", "元素优先步骤不能保存绝对坐标；请重新捕获元素", path=f"{path}.arguments", next_action="点击“实时捕获元素”"))
            if target is not None and target.stability == "low":
                diagnostics.append(_diagnostic("low_stability", "该元素稳定性较低，运行前会重新观察；建议补充窗口、控件类型或父级约束", path=f"{path}.target", severity="warning"))
            if target is None and any(key in args for key in ("loc", "x", "y")) and args.get("coordinate_fallback") is not True:
                diagnostics.append(_diagnostic("coordinate_requires_opt_in", "坐标只能作为显式的一次性兜底", path=f"{path}.arguments", next_action="打开“允许一次性位置兜底”"))

    return {"valid": not any(item["severity"] == "error" for item in diagnostics), "definition": normalized, "diagnostics": diagnostics}


def compile_or_raise(definition: WorkflowDefinition | Dict[str, Any], catalog: Sequence[Dict[str, Any]] = ()) -> WorkflowDefinition:
    result = compile_workflow(definition, catalog)
    if not result["valid"]:
        first = next((item for item in result["diagnostics"] if item.get("severity") == "error"), None)
        raise ValueError(str(first.get("message") if first else "工作流编译失败"))
    return WorkflowDefinition.model_validate(result["definition"])
