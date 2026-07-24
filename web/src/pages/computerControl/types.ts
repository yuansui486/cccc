import type { Edge, Node } from "@xyflow/react";

export type WorkflowNodeKind = "start" | "action" | "condition" | "wait" | "loop" | "approval" | "end";

export type WorkflowNodeModel = {
  id: string;
  type: WorkflowNodeKind;
  title: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  success_condition?: string;
  condition?: string;
  timeout_seconds?: number;
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
  max_run_seconds: number;
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
};

export type ToolCatalogItem = {
  name: string;
  description?: string;
  inputSchema?: ToolSchema;
  annotations?: Record<string, unknown>;
};

export type CanvasNode = Node<{ model: WorkflowNodeModel }>;
export type CanvasEdge = Edge<{ branch?: string }>;

export function normalizeDefinition(value: Record<string, unknown>): WorkflowDefinition {
  const nodes = Array.isArray(value.nodes) ? value.nodes : [];
  const edges = Array.isArray(value.edges) ? value.edges : [];
  return {
    name: typeof value.name === "string" && value.name.trim() ? value.name : "未命名流程",
    description: typeof value.description === "string" ? value.description : "",
    nodes: nodes.map((item, index) => {
      const node = (item && typeof item === "object" ? item : {}) as Partial<WorkflowNodeModel>;
      return {
        ...node,
        id: String(node.id || `step-${index + 1}`),
        type: node.type || "action",
        title: String(node.title || node.tool || "未命名步骤"),
        arguments: node.arguments && typeof node.arguments === "object" ? node.arguments : {},
        position: node.position || { x: 100 + index * 220, y: 120 },
      } as WorkflowNodeModel;
    }),
    edges: edges.map((item, index) => {
      const edge = (item && typeof item === "object" ? item : {}) as Partial<WorkflowEdgeModel>;
      return {
        id: String(edge.id || `edge-${index + 1}`),
        source: String(edge.source || ""),
        target: String(edge.target || ""),
        branch: edge.branch || "next",
      };
    }),
    triggers: Array.isArray(value.triggers) ? (value.triggers as Record<string, unknown>[]) : [],
    inputs: value.inputs && typeof value.inputs === "object" ? (value.inputs as Record<string, unknown>) : {},
    save_screenshots: value.save_screenshots !== false,
    max_run_seconds: typeof value.max_run_seconds === "number" ? value.max_run_seconds : 1800,
  };
}

export function serializeDefinition(definition: WorkflowDefinition): Record<string, unknown> {
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
      return clean;
    }),
  };
}
