from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

NODE_TYPES = frozenset({"start", "action", "condition", "wait", "loop", "approval", "end"})
TRIGGER_TYPES = frozenset({"interval", "schedule", "at", "cron", "element", "event", "file"})
SECRET_REF_RE = re.compile(r"^\$\{secret:[A-Za-z_][A-Za-z0-9_]*\}$")
TEMPLATE_REF_RE = re.compile(r"^\$\{(?:inputs|steps)\.[A-Za-z0-9_.-]+\}$")


def computer_control_permissions(value: Dict[str, Any]) -> Dict[str, bool]:
    result = {
        "allow_high_risk": value.get("allow_high_risk") is not False,
        "allow_publish": value.get("allow_publish") is not False,
        "allow_trust": value.get("allow_trust") is not False,
        "allow_unattended_triggers": value.get("allow_unattended_triggers") is not False,
    }
    # Keep the legacy helper's exact shape for callers that do not know the
    # newer permission. Request payloads that include the switch get the
    # explicit value; omission remains permissive for backward compatibility.
    if "allow_workflow_edit" in value:
        result["allow_workflow_edit"] = value.get("allow_workflow_edit") is not False
    return result


class SuccessAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(default="result", pattern=r"^(result|steps\.[A-Za-z0-9_.-]+)$")
    path: str = Field(default="", max_length=500)
    operator: Literal["exists", "truthy", "equals", "contains"] = "truthy"
    expected: Any = None


class ElementLocator(BaseModel):
    """Durable identity for a desktop element.

    Snapshot labels and coordinates are observations, not durable selectors.
    ``mcp_label`` is deliberately excluded from this model so it can never be
    persisted by a workflow version.
    """

    selector_version: int = Field(default=1, ge=1, le=10)
    strategy: Literal["uia", "dom", "semantic", "position"] = "uia"
    window_name: str = Field(default="", max_length=500)
    control_type: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=500)
    text: str = Field(default="", max_length=500)
    automation_id: str = Field(default="", max_length=300)
    class_name: str = Field(default="", max_length=300)
    process_name: str = Field(default="", max_length=300)
    framework_id: str = Field(default="", max_length=100)
    parent_name: str = Field(default="", max_length=500)
    match: Literal["exact", "contains", "regex"] = "exact"
    dom: bool = False
    monitor: Optional[int] = Field(default=None, ge=0, le=32)
    observed_bounds: Optional[Dict[str, float]] = None
    dpi: Optional[float] = Field(default=None, gt=0, le=1000)
    capture_fingerprint: str = Field(default="", max_length=200)
    stability: Literal["high", "normal", "low", "unknown"] = "unknown"
    diagnostics: List[str] = Field(default_factory=list, max_length=20)
    position_anchor: Optional[Dict[str, float]] = None
    # New selectors are element-only by default. Legacy workflows can opt in
    # to a one-shot position fallback explicitly.
    fallback_policy: Literal["never", "controlled"] = "never"

    @model_validator(mode="after")
    def require_identity(self) -> "ElementLocator":
        if self.strategy == "position" and self.position_anchor:
            return self
        if not any((self.window_name, self.control_type, self.name, self.text, self.automation_id, self.class_name, self.process_name)):
            raise ValueError("locator requires at least one stable attribute")
        if self.fallback_policy == "controlled" and not self.position_anchor:
            raise ValueError("controlled coordinate fallback requires a position_anchor")
        return self


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    type: Literal["start", "action", "condition", "wait", "loop", "approval", "end"]
    title: str = Field(default="", max_length=200)
    tool: str = Field(default="", max_length=200)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    target: Optional[ElementLocator] = None
    success_condition: Union[str, SuccessAssertion] = ""
    condition: str = Field(default="", max_length=1000)
    # None means wait until the MCP tool returns or the user cancels the run.
    timeout_seconds: Optional[float] = Field(default=None, ge=0)
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    retries: int = Field(default=0, ge=0, le=3)
    adaptive: bool = True
    max_iterations: Optional[int] = Field(default=None, ge=1, le=100)
    position: Dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_kind(self) -> "WorkflowNode":
        if self.type == "action" and not self.tool:
            raise ValueError("action nodes require a tool")
        if self.type == "condition" and not self.condition:
            raise ValueError("condition nodes require a condition")
        if self.type == "loop" and self.max_iterations is None:
            raise ValueError("loop nodes require max_iterations")
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_common_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "next" in value:
            raise ValueError("node.next is not supported; declare routing in top-level edges with source and target")
        if str(value.get("type") or "") == "wait" and "duration" in value:
            raise ValueError("wait nodes use duration_seconds, not duration")
        return value


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=100)
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    branch: Literal["next", "true", "false", "body", "done"] = "next"


class WorkflowTrigger(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1, max_length=100)
    type: Literal["interval", "schedule", "at", "cron", "element", "event", "file"]
    name: str = Field(default="", max_length=200)
    enabled: bool = False
    actor_id: str = Field(default="foreman", min_length=1, max_length=100)
    config: Dict[str, Any] = Field(default_factory=dict)
    inputs: Dict[str, Any] = Field(default_factory=dict)
    cooldown_seconds: int = Field(default=30, ge=0, le=86400)


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Default run inputs. Action arguments reference them with exact ${inputs.name} strings.",
    )
    nodes: List[WorkflowNode] = Field(
        min_length=2,
        max_length=200,
        description="Workflow steps. Routing is declared only in the top-level edges array.",
    )
    edges: List[WorkflowEdge] = Field(
        default_factory=list,
        max_length=800,
        description="Directed routes between nodes using source, target, and an optional branch.",
    )
    triggers: List[WorkflowTrigger] = Field(default_factory=list, max_length=50)
    save_screenshots: bool = True
    auto_verify: bool = True
    # Computer-control workflows are intentionally unbounded by default. A
    # user may still opt into a safety limit by setting this field explicitly.
    max_run_seconds: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        starts = [node.id for node in self.nodes if node.type == "start"]
        ends = [node.id for node in self.nodes if node.type == "end"]
        if len(starts) != 1:
            raise ValueError("workflow requires exactly one start node")
        if not ends:
            raise ValueError("workflow requires at least one end node")
        known = set(ids)
        if len(known) > 1 and not self.edges:
            raise ValueError("workflow requires top-level edges with source and target; node.next is not supported")
        outgoing: Dict[str, List[str]] = {node_id: [] for node_id in ids}
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("edges must reference existing nodes")
            outgoing[edge.source].append(edge.target)
        reachable = set()
        stack = [starts[0]]
        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            stack.extend(outgoing[node_id])
        if reachable != known:
            raise ValueError("all nodes must be reachable from start")
        reverse: Dict[str, List[str]] = {node_id: [] for node_id in ids}
        for edge in self.edges:
            reverse[edge.target].append(edge.source)
        can_end = set()
        stack = list(ends)
        while stack:
            node_id = stack.pop()
            if node_id in can_end:
                continue
            can_end.add(node_id)
            stack.extend(reverse[node_id])
        if known - can_end:
            raise ValueError("every node must be able to reach an end node")
        self._reject_mustache_references(self.model_dump())
        self._reject_plain_secrets(self.model_dump())
        return self

    @classmethod
    def _reject_mustache_references(cls, value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                cls._reject_mustache_references(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._reject_mustache_references(child, f"{path}[{index}]")
        elif isinstance(value, str) and re.search(r"\{\{\s*(?:inputs|steps)\.", value):
            raise ValueError(f"template reference at {path} must use ${{inputs.name}} or ${{steps.node.field}}, not mustache syntax")

    @classmethod
    def _reject_plain_secrets(cls, value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                lowered = str(key).lower()
                if any(token in lowered for token in ("password", "passwd", "secret", "api_key", "token")):
                    if isinstance(child, str) and child and not SECRET_REF_RE.fullmatch(child):
                        raise ValueError(f"sensitive value at {child_path} must use a secret reference")
                cls._reject_plain_secrets(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                cls._reject_plain_secrets(child, f"{path}[{index}]")


class WorkflowCreateRequest(BaseModel):
    definition: WorkflowDefinition


class WorkflowUpdateRequest(BaseModel):
    definition: WorkflowDefinition
    expected_revision: int = Field(ge=1)
    change_note: str = Field(default="", max_length=500)


class WorkflowRunRequest(BaseModel):
    actor_id: str = Field(default="foreman", min_length=1, max_length=100)
    version: Optional[int] = Field(default=None, ge=1)
    inputs: Dict[str, Any] = Field(default_factory=dict)


class TrustRequest(BaseModel):
    version: int = Field(ge=1)
    permissions: List[str] = Field(default_factory=lambda: ["all_windows_mcp_tools"])
