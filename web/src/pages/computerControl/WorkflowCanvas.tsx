import React, { memo, useCallback, useEffect, useState } from "react";
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
  const cardStyle: React.CSSProperties = {
    borderColor: selected ? "var(--color-accent-primary)" : "var(--color-border)",
    boxShadow: selected
      ? "0 0 0 2px var(--color-bg-primary), 0 0 0 4px color-mix(in srgb, var(--color-accent-primary) 28%, transparent), 0 14px 30px rgba(15, 23, 42, 0.14)"
      : "0 10px 26px rgba(15, 23, 42, 0.10)",
  };
  const handleStyle: React.CSSProperties = {
    background: "var(--color-bg-primary)",
    borderColor: meta.color,
    boxShadow: `0 0 0 2px var(--color-bg-primary), 0 0 0 3px ${meta.color}`,
  };
  return (
    <div
      className="group relative w-[216px] overflow-visible rounded-xl border bg-[var(--color-bg-primary)] transition-[box-shadow,transform] duration-150 hover:-translate-y-px"
      style={cardStyle}
    >
      <div className="pointer-events-none absolute inset-y-2 left-0.5 w-1 rounded-full" style={{ backgroundColor: meta.color }} />
      <Handle type="target" position={Position.Left} className="!z-10 !h-3 !w-3 !border-2" style={handleStyle} />
      <div className="flex items-center gap-2 border-b border-[var(--glass-border-subtle)] px-3.5 py-2.5" style={{ color: meta.color }}>
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-black/[0.04] dark:bg-white/[0.06]">
          <Icon size={14} strokeWidth={2.2} />
        </span>
        <span className="text-[11px] font-semibold">{meta.label}</span>
        <span className="ml-auto text-[10px] font-medium text-[var(--color-text-muted)]">步骤</span>
      </div>
      <div className="px-3.5 py-3">
        <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{data.model.title || meta.label}</div>
        {data.model.tool && (
          <div className="mt-2 flex min-w-0 items-center gap-1.5 rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--color-bg-secondary)] px-2 py-1 text-[11px] text-[var(--color-text-secondary)]">
            <Bot size={11} className="shrink-0" />
            <span className="truncate">{data.model.tool}</span>
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!z-10 !h-3 !w-3 !border-2" style={handleStyle} />
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
  fitViewKey?: string;
};

export function WorkflowCanvas({ nodes, edges, onNodesChange, onEdgesChange, onConnect, onSelect, onToolDrop, fitViewKey }: Props) {
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<CanvasNode, CanvasEdge> | null>(null);

  useEffect(() => {
    if (!flowInstance || !fitViewKey) return;
    requestAnimationFrame(() => flowInstance.fitView({ padding: 0.25, duration: 160 }));
  }, [fitViewKey, flowInstance]);
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
      edges={edges.map((edge) => ({
        ...edge,
        style: { stroke: "var(--color-accent-primary)", strokeWidth: 2.25, strokeLinecap: "round" },
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: "var(--color-accent-primary)" },
      }))}
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
      className="bg-[var(--color-bg-secondary)] [&_.react-flow__edge-path]:drop-shadow-[0_1px_1px_rgba(15,23,42,0.12)]"
    >
      <Background gap={24} size={1} color="var(--color-border)" />
      <Controls
        position="bottom-left"
        showInteractive={false}
        className="!bottom-4 !left-4 !m-0 !overflow-hidden !rounded-xl !border !border-[var(--glass-border-subtle)] !bg-[var(--color-bg-primary)] !shadow-lg [&>button]:!h-9 [&>button]:!w-9 [&>button]:!border-0 [&>button]:!bg-transparent [&>button]:!text-[var(--color-text-secondary)] [&>button]:hover:!bg-[var(--color-bg-secondary)] [&>button]:hover:!text-[var(--color-text-primary)]"
      />
      <MiniMap
        pannable
        zoomable
        nodeBorderRadius={6}
        className="!right-4 !top-4 !hidden !h-[132px] !w-[196px] !overflow-hidden !rounded-xl !border !border-[var(--glass-border-subtle)] !bg-[var(--color-bg-primary)] !shadow-lg lg:!block"
        nodeColor={(node) => {
          const model = (node.data as { model?: { type?: WorkflowNodeKind } }).model;
          return model?.type ? kindMeta[model.type].color : "#64748b";
        }}
        nodeStrokeColor="var(--color-bg-primary)"
      />
    </ReactFlow>
  );
}
