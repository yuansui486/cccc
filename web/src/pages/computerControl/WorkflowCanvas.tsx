import React, { memo, useCallback, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  type Connection,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Bot, CheckCircle2, Circle, GitBranch, Hourglass, MousePointer2, RefreshCcw, ShieldQuestion, type LucideIcon } from "lucide-react";
import type { CanvasEdge, CanvasNode, ToolCatalogItem, WorkflowNodeKind } from "./types";

const kindMeta: Record<WorkflowNodeKind, { label: string; color: string; icon: LucideIcon }> = {
  start: { label: "开始", color: "#059669", icon: Circle },
  action: { label: "操作", color: "#2563eb", icon: MousePointer2 },
  condition: { label: "判断", color: "#d97706", icon: GitBranch },
  wait: { label: "等待", color: "#7c3aed", icon: Hourglass },
  loop: { label: "循环", color: "#0891b2", icon: RefreshCcw },
  approval: { label: "确认", color: "#dc2626", icon: ShieldQuestion },
  end: { label: "结束", color: "#475569", icon: CheckCircle2 },
};

const WorkflowNodeCard = memo(function WorkflowNodeCard({ data, selected }: NodeProps<CanvasNode>) {
  const meta = kindMeta[data.model.type];
  const Icon = meta.icon;
  return (
    <div className={`w-[190px] rounded-md border bg-[var(--color-bg-primary)] shadow-sm ${selected ? "border-[var(--color-accent-primary)] ring-2 ring-[var(--color-accent-primary)]/15" : "border-[var(--color-border)]"}`}>
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-2 !bg-white" />
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2" style={{ color: meta.color }}>
        <Icon size={14} />
        <span className="text-[11px] font-semibold">{meta.label}</span>
      </div>
      <div className="px-3 py-2.5">
        <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">{data.model.title || meta.label}</div>
        {data.model.tool && <div className="mt-1 flex items-center gap-1 truncate text-[11px] text-[var(--color-text-secondary)]"><Bot size={11} />{data.model.tool}</div>}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-2 !bg-white" />
    </div>
  );
});

const nodeTypes = { workflow: WorkflowNodeCard };

type Props = {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  onNodesChange: (changes: Parameters<NonNullable<React.ComponentProps<typeof ReactFlow<CanvasNode, CanvasEdge>>["onNodesChange"]>>[0]) => void;
  onEdgesChange: (changes: Parameters<NonNullable<React.ComponentProps<typeof ReactFlow<CanvasNode, CanvasEdge>>["onEdgesChange"]>>[0]) => void;
  onConnect: (connection: Connection) => void;
  onSelect: (nodeId: string) => void;
  onToolDrop: (tool: ToolCatalogItem, position: { x: number; y: number }) => void;
};

export function WorkflowCanvas({ nodes, edges, onNodesChange, onEdgesChange, onConnect, onSelect, onToolDrop }: Props) {
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<CanvasNode, CanvasEdge> | null>(null);
  const handleDrop = useCallback((event: React.DragEvent, screenToFlowPosition: (point: { x: number; y: number }) => { x: number; y: number }) => {
    event.preventDefault();
    const raw = event.dataTransfer.getData("application/onecolleague-windows-mcp-tool");
    if (!raw) return;
    try {
      onToolDrop(JSON.parse(raw) as ToolCatalogItem, screenToFlowPosition({ x: event.clientX, y: event.clientY }));
    } catch {
      return;
    }
  }, [onToolDrop]);

  return (
    <ReactFlow<CanvasNode, CanvasEdge>
      nodes={nodes}
      edges={edges.map((edge) => ({ ...edge, markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 } }))}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onInit={setFlowInstance}
      onNodeClick={(_, node) => onSelect(node.id)}
      onPaneClick={() => onSelect("")}
      onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; }}
      onDrop={(event) => handleDrop(event, flowInstance?.screenToFlowPosition || ((point) => point))}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      minZoom={0.35}
      maxZoom={1.6}
      className="bg-[var(--color-bg-secondary)]"
    >
      <Background gap={20} size={1} color="var(--color-border)" />
      <Controls showInteractive={false} />
      <MiniMap pannable zoomable className="!hidden !border !border-[var(--color-border)] !bg-[var(--color-bg-primary)] lg:!block" nodeColor={(node) => {
        const model = (node.data as { model?: { type?: WorkflowNodeKind } }).model;
        return model?.type ? kindMeta[model.type].color : "#64748b";
      }} />
    </ReactFlow>
  );
}
