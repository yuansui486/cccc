import type { Edge, Node } from "@xyflow/react";

export type WorkflowNodeKind =
  | "start"
  | "action"
  | "condition"
  | "wait"
  | "loop"
  | "approval"
  | "end";

export type WorkflowNodeModel = {
  id: string;
  type: WorkflowNodeKind;
  title: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  target?: {
    window_name?: string;
    control_type?: string;
    name?: string;
    text?: string;
    automation_id?: string;
    class_name?: string;
    process_name?: string;
    framework_id?: string;
    parent_name?: string;
    fallback_policy?: "never" | "controlled";
    match?: "exact" | "contains" | "regex";
    dom?: boolean;
    monitor?: number;
    position_anchor?: { x?: number; y?: number };
  };
  success_condition?:
    | string
    | {
        source: string;
        path?: string;
        operator: "exists" | "truthy" | "equals" | "contains";
        expected?: unknown;
      };
  condition?: string;
  timeout_seconds?: number | null;
  duration_seconds?: number | null;
  retries?: number;
  adaptive?: boolean;
  max_iterations?: number;
  position?: { x: number; y: number };
};

export type WorkflowEdgeModel = {
  id: string;
  source: string;
  target: string;
  branch?: "next" | "true" | "false" | "body" | "done";
};

export type WorkflowDefinition = {
  name: string;
  description: string;
  nodes: WorkflowNodeModel[];
  edges: WorkflowEdgeModel[];
  triggers: Record<string, unknown>[];
  inputs: Record<string, unknown>;
  save_screenshots: boolean;
  max_run_seconds: number | null;
  auto_verify?: boolean;
};

export type ToolSchema = {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, ToolSchema>;
  required?: string[];
  enum?: unknown[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  items?: ToolSchema;
  oneOf?: ToolSchema[];
  anyOf?: ToolSchema[];
  additionalProperties?: boolean | ToolSchema;
};

export type ToolCatalogItem = {
  name: string;
  description?: string;
  inputSchema?: ToolSchema;
  annotations?: Record<string, unknown>;
};

export type CanvasNode = Node<{ model: WorkflowNodeModel }>;
export type CanvasEdge = Edge<{ branch?: string }>;

export function isValidNodePosition(
  value: unknown,
): value is { x: number; y: number } {
  if (!value || typeof value !== "object") return false;
  const position = value as { x?: unknown; y?: unknown };
  return (
    typeof position.x === "number" &&
    Number.isFinite(position.x) &&
    typeof position.y === "number" &&
    Number.isFinite(position.y)
  );
}

export function layoutWorkflowNodes(
  nodes: WorkflowNodeModel[],
  edges: WorkflowEdgeModel[],
): WorkflowNodeModel[] {
  if (nodes.length === 0) return nodes;
  const ids = new Set(nodes.map((node) => node.id));
  const incoming = new Map<string, number>();
  const outgoing = new Map<string, string[]>();
  for (const node of nodes) {
    incoming.set(node.id, 0);
    outgoing.set(node.id, []);
  }
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue;
    outgoing.get(edge.source)!.push(edge.target);
    incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1);
  }
  const depth = new Map<string, number>();
  const queue = nodes
    .filter((node) => (incoming.get(node.id) || 0) === 0)
    .map((node) => node.id);
  if (queue.length === 0) queue.push(nodes[0].id);
  for (const id of queue) depth.set(id, 0);
  for (let index = 0; index < queue.length; index += 1) {
    const source = queue[index];
    for (const target of outgoing.get(source) || []) {
      depth.set(
        target,
        Math.max(depth.get(target) || 0, (depth.get(source) || 0) + 1),
      );
      const nextIncoming = (incoming.get(target) || 0) - 1;
      incoming.set(target, nextIncoming);
      if (nextIncoming === 0) queue.push(target);
    }
  }
  const maxDepth = Math.max(0, ...depth.values());
  let fallbackDepth = maxDepth + 1;
  for (const node of nodes) {
    if (!depth.has(node.id)) depth.set(node.id, fallbackDepth++);
  }
  const rows = new Map<number, number>();
  return nodes.map((node) => {
    const column = depth.get(node.id) || 0;
    const row = rows.get(column) || 0;
    rows.set(column, row + 1);
    return { ...node, position: { x: 80 + column * 260, y: 80 + row * 150 } };
  });
}

export function normalizeDefinition(
  value: Record<string, unknown>,
): WorkflowDefinition {
  const rawNodes = Array.isArray(value.nodes) ? value.nodes : [];
  const rawEdges = Array.isArray(value.edges) ? value.edges : [];
  const nodes = rawNodes.map((item, index) => {
    const node = (
      item && typeof item === "object" ? item : {}
    ) as Partial<WorkflowNodeModel>;
    return {
      ...node,
      id: String(node.id || `step-${index + 1}`),
      type: node.type || "action",
      title: String(node.title || node.tool || "未命名步骤"),
      arguments:
        node.arguments && typeof node.arguments === "object"
          ? node.arguments
          : {},
      position: isValidNodePosition(node.position) ? node.position : undefined,
    } as WorkflowNodeModel;
  });
  const edges = rawEdges.map((item, index) => {
    const edge = (
      item && typeof item === "object" ? item : {}
    ) as Partial<WorkflowEdgeModel>;
    return {
      id: String(edge.id || `edge-${index + 1}`),
      source: String(edge.source || ""),
      target: String(edge.target || ""),
      branch: edge.branch || "next",
    };
  });
  const laidOutNodes = nodes.every((node) => isValidNodePosition(node.position))
    ? nodes
    : layoutWorkflowNodes(nodes, edges);
  return {
    name:
      typeof value.name === "string" && value.name.trim()
        ? value.name
        : "未命名流程",
    description: typeof value.description === "string" ? value.description : "",
    nodes: laidOutNodes,
    edges,
    triggers: Array.isArray(value.triggers)
      ? (value.triggers as Record<string, unknown>[])
      : [],
    inputs:
      value.inputs && typeof value.inputs === "object"
        ? (value.inputs as Record<string, unknown>)
        : {},
    save_screenshots: value.save_screenshots !== false,
    max_run_seconds:
      typeof value.max_run_seconds === "number" ? value.max_run_seconds : null,
    auto_verify: value.auto_verify !== false,
  };
}
export function serializeDefinition(
  definition: WorkflowDefinition,
): Record<string, unknown> {
  return {
    ...definition,
    nodes: definition.nodes.map((node) => {
      const clean = { ...node };
      if (clean.type !== "action") {
        delete clean.tool;
        delete clean.arguments;
        delete clean.success_condition;
      }
      if (clean.type !== "condition") delete clean.condition;
      if (clean.type !== "loop") delete clean.max_iterations;
      if (clean.type !== "wait") delete clean.duration_seconds;
      return clean;
    }),
  };
}
