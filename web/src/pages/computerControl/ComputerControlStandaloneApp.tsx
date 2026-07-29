import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";
import {
  AlarmClock,
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  CircleStop,
  Copy,
  GitBranch,
  Hourglass,
  Laptop,
  LoaderCircle,
  MousePointer2,
  Play,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
  Undo2,
  Upload,
  Wrench,
  X,
} from "lucide-react";
import {
  computerControlApi,
  type ComputerControlRequest,
  type ComputerControlLease,
  type ComputerControlSettings,
  type ComputerSetup,
  type ElementPickerElement,
  type ElementPickerSession,
  type OptimizationProposal,
  type WorkflowManifest,
  type WorkflowRecord,
  type WorkflowVersion,
} from "../../services/api/computerControl";
import { SchemaForm } from "./SchemaForm";
import { TriggerEditor } from "./TriggerEditor";
import { toolDescription, toolName } from "./localization";
import type {
  CanvasEdge,
  CanvasNode,
  ToolCatalogItem,
  WorkflowDefinition,
  WorkflowNodeKind,
  WorkflowNodeModel,
} from "./types";
import { normalizeDefinition, serializeDefinition } from "./types";
import { WorkflowCanvas } from "./WorkflowCanvas";
import { COMPUTER_CONTROL_TAB } from "../../utils/appTabs";
import { useUIStore } from "../../stores";
import { computerControlProviderLabel, computerControlSessionDetails, formatComputerControlTime, latestComputerControlObservation, newestComputerControlObservation, setupErrorMessage, shouldRefreshComputerControlAfterSseTransition } from "./statusPresentation";

type Section = "tools" | "steps" | "properties" | "runs";
type Snapshot = { nodes: CanvasNode[]; edges: CanvasEdge[] };

let generatedId = 0;

function nextId(prefix: string): string {
  generatedId += 1;
  return `${prefix}-${generatedId}`;
}

function initialDefinition(): WorkflowDefinition {
  return normalizeDefinition({
    name: "新电脑流程",
    description: "",
    nodes: [
      {
        id: "start",
        type: "start",
        title: "开始",
        position: { x: 80, y: 160 },
      },
      { id: "end", type: "end", title: "结束", position: { x: 500, y: 160 } },
    ],
    edges: [
      { id: "start-end", source: "start", target: "end", branch: "next" },
    ],
    triggers: [],
    inputs: {},
    save_screenshots: true,
    max_run_seconds: null,
    auto_verify: true,
  });
}

function phaseLabel(phase: string): string {
  return (
    (
      {
        checking: "正在检查",
        downloading: "正在准备组件",
        initializing: "正在启动服务",
        verifying: "正在验证工具",
        ready: "已就绪",
        failed: "安装失败",
        not_started: "等待安装",
      } as Record<string, string>
    )[phase] || phase
  );
}

function nodeLabel(kind: WorkflowNodeKind): string {
  return (
    {
      start: "开始",
      action: "执行操作",
      condition: "条件判断",
      wait: "等待",
      loop: "循环",
      approval: "人工确认",
      end: "结束",
    } as Record<WorkflowNodeKind, string>
  )[kind];
}

function runStatusLabel(status: unknown): string {
  return (
    (
      {
        running: "正在回放",
        exploring: "正在探路",
        awaiting_verification: "等待验证",
        verified: "验证通过",
        published: "已发布并信任",
        verification_failed: "验证未通过",
        completed: "运行成功",
        failed: "运行失败",
        cancelled: "已停止",
        recovering: "正在自适应恢复",
        pending_approval: "等待用户确认",
      } as Record<string, string>
    )[String(status || "")] || "状态未知"
  );
}

function friendlyError(message: unknown): string {
  const text = String(message || "");
  const rules: Array<[RegExp, string]> = [
    [
      /workflow version is not trusted|workflow_not_trusted/i,
      "当前工作流版本尚未发布并信任。",
    ],
    [
      /computer control is currently occupied|computer control is already in use|computer_control_busy/i,
      "另一项电脑控制任务正在运行，请先停止当前占用后再试。",
    ],
    [
      /windows-mcp exited/i,
      "Windows-MCP 连接意外中断，系统已尝试恢复。为避免重复操作，请确认桌面状态后重试。",
    ],
    [
      /revision conflict|revision_conflict/i,
      "工作流已被其他操作更新，请刷新后再保存。",
    ],
    [
      /approval_required|需要用户批准/i,
      "该流程包含高风险操作，需要用户确认后才能继续。",
    ],
    [
      /Either loc or label must be provided|缺少操作目标/i,
      "该步骤没有设置可用的点击或输入目标，请在步骤属性中设置位置或稳定元素。",
    ],
    [
      /Status Code:\s*[1-9]|退出状态码/i,
      "系统命令执行失败，请查看该步骤的错误说明。",
    ],
    [
      /恢复通知无法投递/i,
      "无法联系执行智能体进行自适应恢复，请确认智能体正在运行。",
    ],
  ];
  return (
    rules.find(([pattern]) => pattern.test(text))?.[1] ||
    text ||
    "操作未完成，请稍后重试。"
  );
}

function toCanvas(definition: WorkflowDefinition): Snapshot {
  const nodeIds = new Set(definition.nodes.map((node) => node.id));
  return {
    nodes: definition.nodes.map((model) => ({
      id: model.id,
      type: "workflow",
      position: model.position || { x: 80, y: 80 },
      data: { model },
    })),
    edges: definition.edges
      .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.branch && edge.branch !== "next" ? edge.branch : undefined,
        data: { branch: edge.branch || "next" },
      })),
  };
}

function fromCanvas(
  base: WorkflowDefinition,
  nodes: CanvasNode[],
  edges: CanvasEdge[],
): WorkflowDefinition {
  return {
    ...base,
    nodes: nodes.map((node) => ({
      ...node.data.model,
      position: node.position,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      branch: (edge.data?.branch || "next") as "next",
    })),
  };
}

function cloneSnapshot(nodes: CanvasNode[], edges: CanvasEdge[]): Snapshot {
  return { nodes: structuredClone(nodes), edges: structuredClone(edges) };
}

export function ComputerControlWorkspace({ groupId, activeTab, groupLabelById }: { groupId: string; activeTab: string; groupLabelById?: Record<string, string> }) {
  const sseStatus = useUIStore((state) => state.sseStatus);
  const sseDisconnectedSinceRefresh = React.useRef(sseStatus === "disconnected");
  const [setup, setSetup] = useState<ComputerSetup>({
    phase: "not_started",
    version: "",
  });
  const [tools, setTools] = useState<ToolCatalogItem[]>([]);
  const [toolSearch, setToolSearch] = useState("");
  const [workflows, setWorkflows] = useState<WorkflowManifest[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [record, setRecord] = useState<WorkflowRecord | null>(null);
  const [definition, setDefinition] =
    useState<WorkflowDefinition>(initialDefinition);
  const initialCanvas = useMemo(() => toCanvas(initialDefinition()), []);
  const [nodes, setNodes] = useState<CanvasNode[]>(initialCanvas.nodes);
  const [edges, setEdges] = useState<CanvasEdge[]>(initialCanvas.edges);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [history, setHistory] = useState<Snapshot[]>([]);
  const [runs, setRuns] = useState<Record<string, unknown>[]>([]);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [proposals, setProposals] = useState<OptimizationProposal[]>([]);
  const [requests, setRequests] = useState<ComputerControlRequest[]>([]);
  const [section, setSection] = useState<Section>("steps");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [trustDialogOpen, setTrustDialogOpen] = useState(false);
  const [triggerDialogOpen, setTriggerDialogOpen] = useState(false);
  const [settings, setSettings] = useState<ComputerControlSettings>({
    auto_publish_and_trust: true,
  });
  const [lease, setLease] = useState<ComputerControlLease>({ active: false });
  const activeGroupIdRef = React.useRef(groupId);
  activeGroupIdRef.current = groupId;
  const refreshInFlight = React.useRef<Promise<void> | null>(null);
  const refreshQueued = React.useRef(false);
  const [pickerElements, setPickerElements] = useState<Array<Record<string, unknown>>>([]);
  const [pickerBusy, setPickerBusy] = useState(false);
  const [pickerStatus, setPickerStatus] = useState("");
  const [pickerSession, setPickerSession] = useState<ElementPickerSession | null>(null);

  const refresh = useCallback(async () => {
    refreshQueued.current = true;
    if (refreshInFlight.current) return refreshInFlight.current;
    const task = (async () => {
      while (refreshQueued.current) {
        refreshQueued.current = false;
        const requestedGroupId = activeGroupIdRef.current;
        if (!requestedGroupId) continue;
        const [
          status,
          workflowResponse,
          runResponse,
          requestResponse,
          settingsResponse,
          leaseResponse,
        ] = await Promise.all([
          computerControlApi.status(),
          computerControlApi.workflows(requestedGroupId),
          computerControlApi.runs(requestedGroupId),
          computerControlApi.requests(requestedGroupId),
          computerControlApi.settings(requestedGroupId),
          computerControlApi.leaseStatus(),
        ]);
        if (activeGroupIdRef.current !== requestedGroupId) {
          refreshQueued.current = true;
          continue;
        }
        if (status.ok) setSetup(status.result);
        if (workflowResponse.ok) {
          setWorkflows(workflowResponse.result.workflows || []);
          const first = workflowResponse.result.workflows?.[0];
          if (first) setSelectedId((current) => current || first.workflow_id);
        }
        if (runResponse.ok) setRuns(runResponse.result.runs || []);
        if (requestResponse.ok) setRequests(requestResponse.result.requests || []);
        if (settingsResponse.ok) setSettings(settingsResponse.result);
        if (leaseResponse.ok) setLease(leaseResponse.result);
      }
    })();
    refreshInFlight.current = task;
    try {
      await task;
    } finally {
      if (refreshInFlight.current === task) refreshInFlight.current = null;
    }
  }, []);

  const loadCatalog = useCallback(async () => {
    const response = await computerControlApi.catalog();
    if (response.ok)
      setTools((response.result.tools || []) as ToolCatalogItem[]);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setBusy("ensure");
      const response = await computerControlApi.ensure();
      if (!cancelled && response.ok) {
        setSetup(response.result);
        if (response.result.phase === "ready") await loadCatalog();
      }
      if (!cancelled) setBusy("");
      await refresh();
    })();
    return () => {
      cancelled = true;
    };
  }, [loadCatalog, refresh]);

  useEffect(() => {
    if (setup.phase === "ready") {
      return;
    }
    if (setup.phase === "failed") return;
    const timer = window.setInterval(() => {
      void computerControlApi.status().then((response) => {
        if (response.ok) {
          setSetup(response.result);
          if (response.result.phase === "ready" && tools.length === 0)
            void loadCatalog();
        }
      });
    }, 1200);
    return () => window.clearInterval(timer);
  }, [loadCatalog, setup.phase, tools.length]);

  useEffect(() => {
    setSelectedId("");
    setRecord(null);
    setWorkflows([]);
    setRuns([]);
    setRequests([]);
    setVersions([]);
    setProposals([]);
    setTriggerDialogOpen(false);
  }, [groupId]);

  useEffect(() => {
    if (!groupId || !selectedId) return undefined;
    let cancelled = false;
    void Promise.all([
      computerControlApi.workflow(groupId, selectedId),
      computerControlApi.versions(groupId, selectedId),
      computerControlApi.proposals(groupId, selectedId),
    ]).then(([response, versionResponse, proposalResponse]) => {
      if (cancelled) return;
      if (!response.ok) {
        setMessage(friendlyError(response.error.message));
        return;
      }
      if (versionResponse.ok)
        setVersions(versionResponse.result.versions || []);
      if (proposalResponse.ok)
        setProposals(proposalResponse.result.proposals || []);
      const nextDefinition = normalizeDefinition(response.result.definition);
      const canvas = toCanvas(nextDefinition);
      setRecord(response.result);
      setDefinition(nextDefinition);
      setNodes(canvas.nodes);
      setEdges(canvas.edges);
      setSelectedNodeId("");
      setHistory([]);
    });
    return () => {
      cancelled = true;
    };
  }, [groupId, selectedId]);

  useEffect(() => {
    if (activeTab !== COMPUTER_CONTROL_TAB || !groupId) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [activeTab, groupId, refresh]);

  useEffect(() => {
    if (sseStatus === "disconnected") sseDisconnectedSinceRefresh.current = true;
    if (groupId && shouldRefreshComputerControlAfterSseTransition(sseDisconnectedSinceRefresh.current, sseStatus, activeTab === COMPUTER_CONTROL_TAB)) {
      sseDisconnectedSinceRefresh.current = false;
      void refresh();
    }
  }, [activeTab, groupId, refresh, sseStatus]);

  const selectedManifest = useMemo(
    () => workflows.find((item) => item.workflow_id === selectedId),
    [selectedId, workflows],
  );
  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId),
    [nodes, selectedNodeId],
  );
  const selectedTool = useMemo(
    () => tools.find((tool) => tool.name === selectedNode?.data.model.tool),
    [selectedNode, tools],
  );
  const sessionDetails = useMemo(() => computerControlSessionDetails(setup), [setup]);
  const runObservation = useMemo(() => latestComputerControlObservation(runs), [runs]);
  const currentObservation = useMemo(
    () => newestComputerControlObservation(sessionDetails.observation, runObservation),
    [runObservation, sessionDetails.observation],
  );

  async function captureElements() {
    if (!groupId) return;
    setPickerBusy(true);
    setPickerStatus("正在读取当前界面元素…");
    const response = await computerControlApi.captureElements(groupId);
    setPickerBusy(false);
    if (!response.ok) {
      setPickerStatus(friendlyError(response.error.message));
      return;
    }
    setPickerElements(response.result.elements || []);
    setPickerStatus(response.result.elements?.length ? "请选择一个稳定元素" : "当前界面没有可解析元素");
  }

  async function startPicker() {
    if (!groupId || pickerBusy || pickerSession) return;
    setPickerBusy(true);
    setPickerStatus("正在启动桌面元素拾取器。请把鼠标移动到目标控件上，再按 Ctrl+Shift+鼠标左键锁定。\u2026");
    const response = await computerControlApi.pickerStart(groupId, { node_id: selectedNode?.id });
    setPickerBusy(false);
    if (!response.ok) {
      // Keep the snapshot picker available on older daemons.
      setPickerStatus(`${friendlyError(response.error.message)} 可改用“读取当前快照”。`);
      return;
    }
    setPickerSession(response.result);
    setPickerStatus("拾取器已启动：移动鼠标观察元素，按 Ctrl+Shift+鼠标左键锁定。");
  }

  async function lockPicker() {
    if (!groupId || !pickerSession || pickerBusy) return;
    setPickerBusy(true);
    const response = await computerControlApi.pickerLock(groupId, pickerSession.session_id);
    setPickerBusy(false);
    if (!response.ok) {
      setPickerStatus(friendlyError(response.error.message));
      return;
    }
    setPickerSession(response.result);
    if (response.result.element) {
      await chooseElement(response.result.element);
      setPickerStatus("元素已锁定，正在进行稳定性采样。请确认属性后保存。");
    } else setPickerStatus("未锁定到唯一元素，请移动到控件中心后重试。");
  }

  async function confirmPicker() {
    if (!groupId || !pickerSession || pickerBusy) return;
    setPickerBusy(true);
    const response = await computerControlApi.pickerConfirm(groupId, pickerSession.session_id, pickerSession.element?.locator);
    setPickerBusy(false);
    if (!response.ok) {
      setPickerStatus(friendlyError(response.error.message));
      return;
    }
    setPickerSession(response.result);
    if (response.result.element) await chooseElement(response.result.element);
    setPickerStatus("元素定位已确认。运行时会重新观察并匹配该元素。");
  }

  async function stopPicker() {
    if (!groupId || !pickerSession) return;
    setPickerBusy(true);
    await computerControlApi.pickerStop(groupId, pickerSession.session_id);
    setPickerBusy(false);
    setPickerSession(null);
    setPickerStatus("元素拾取器已关闭。");
  }

  useEffect(() => {
    if (!groupId || !pickerSession || ["ended", "confirmed", "failed"].includes(pickerSession.status)) return undefined;
    const timer = window.setInterval(() => {
      void computerControlApi.pickerSession(groupId, pickerSession.session_id).then((response) => {
        if (!response.ok) return;
        setPickerSession(response.result);
        if (response.result.element && (response.result.status === "locked" || response.result.status === "confirmed")) {
          setPickerElements((items) => items.length ? items : [response.result.element as ElementPickerElement]);
        }
      });
    }, 850);
    return () => window.clearInterval(timer);
  }, [groupId, pickerSession]);

  async function chooseElement(element: Record<string, unknown>) {
    if (!selectedNode) return;
    const target = {
      window_name: String(element.window_name || ""),
      control_type: String(element.control_type || ""),
      name: String(element.name || ""),
      text: String(element.text || ""),
      automation_id: String(element.automation_id || ""),
      class_name: String(element.class_name || ""),
      process_name: String(element.process_name || ""),
      framework_id: String(element.framework_id || ""),
      parent_name: String(element.parent_name || ""),
      monitor: typeof element.monitor === "number" ? element.monitor : undefined,
      fallback_policy: "never" as const,
      strategy: String(element.strategy || "uia"),
      selector_version: typeof element.selector_version === "number" ? element.selector_version : 1,
      observed_bounds: element.bounds as Record<string, number> | undefined,
      stability: String(element.stability || element.stability_level || "normal"),
      capture_fingerprint: String(element.capture_fingerprint || ""),
      mcp_label: typeof element.mcp_label === "number" ? element.mcp_label : undefined,
    };
    updateNode(selectedNode.id, {
      target,
      arguments: Object.fromEntries(Object.entries(selectedNode.data.model.arguments || {}).filter(([key]) => !["loc", "x", "y"].includes(key))),
    });
    const validation = await computerControlApi.validateElement(groupId, target);
    setPickerStatus(validation.ok
      ? validation.result.valid
        ? `已定位：${String(element.name || element.text || element.control_type || "元素")}（${validation.result.confidence || "一般"}稳定性）`
        : `元素当前${validation.result.status === "ambiguous" ? "有多个匹配" : "未找到"}，运行时会先重新观察`
      : friendlyError(validation.error.message));
  }
  const loadToolSchema = useCallback(
    async (toolNameValue: string) => {
      if (!toolNameValue) return;
      const current = tools.find((tool) => tool.name === toolNameValue);
      if (
        current &&
        Object.prototype.hasOwnProperty.call(current, "inputSchema")
      )
        return;
      const response = await computerControlApi.catalog(toolNameValue);
      if (!response.ok || !response.result.tool) return;
      const detailed = response.result.tool as ToolCatalogItem;
      setTools((items) =>
        items.map((tool) =>
          tool.name === toolNameValue ? { ...tool, ...detailed } : tool,
        ),
      );
    },
    [tools],
  );

  useEffect(() => {
    const toolNameValue = selectedNode?.data.model.tool || "";
    if (selectedNode?.data.model.type !== "action" || !toolNameValue)
      return undefined;
    const timer = window.setTimeout(
      () => void loadToolSchema(toolNameValue),
      0,
    );
    return () => window.clearTimeout(timer);
  }, [loadToolSchema, selectedNode]);
  const filteredTools = useMemo(() => {
    const search = toolSearch.trim().toLowerCase();
    return tools.filter(
      (tool) =>
        !search ||
        `${tool.name} ${toolName(tool.name)} ${tool.description || ""}`
          .toLowerCase()
          .includes(search),
    );
  }, [toolSearch, tools]);
  const setupReady = setup.phase === "ready";
  const isTrusted = Boolean(selectedManifest?.effective_version);
  const canLinearReorder =
    edges.length === Math.max(0, nodes.length - 1) &&
    edges.every((edge) => !edge.data?.branch || edge.data.branch === "next");

  function remember() {
    setHistory((items) => [...items.slice(-29), cloneSnapshot(nodes, edges)]);
  }

  function undo() {
    const previous = history[history.length - 1];
    if (!previous) return;
    setNodes(previous.nodes);
    setEdges(previous.edges);
    setHistory((items) => items.slice(0, -1));
    setSelectedNodeId("");
  }

  function updateNode(nodeId: string, patch: Partial<WorkflowNodeModel>) {
    remember();
    setNodes((items) =>
      items.map((node) =>
        node.id === nodeId
          ? { ...node, data: { model: { ...node.data.model, ...patch } } }
          : node,
      ),
    );
  }

  function addNode(
    kind: WorkflowNodeKind,
    tool?: ToolCatalogItem,
    position?: { x: number; y: number },
  ) {
    remember();
    const id = nextId(kind);
    const model: WorkflowNodeModel = {
      id,
      type: kind,
      title: tool ? toolName(tool.name) : nodeLabel(kind),
      position: position || {
        x: 260 + nodes.length * 25,
        y: 120 + nodes.length * 30,
      },
    };
    if (kind === "action") {
      model.tool = tool?.name || tools[0]?.name || "";
      model.arguments = {};
      model.timeout_seconds = null;
      model.retries = 0;
      model.adaptive = true;
    }
    if (kind === "condition") model.condition = "steps.previous.success";
    if (kind === "wait") model.duration_seconds = 1;
    if (kind === "loop") model.max_iterations = 3;
    const end = nodes.find((node) => node.data.model.type === "end");
    const incoming = end
      ? edges.find((edge) => edge.target === end.id)
      : undefined;
    setNodes((items) => {
      const next = [...items];
      const endIndex = next.findIndex((node) => node.data.model.type === "end");
      next.splice(endIndex >= 0 ? endIndex : next.length, 0, {
        id,
        type: "workflow",
        position: model.position!,
        data: { model },
      });
      return next;
    });
    if (end && incoming) {
      setEdges((items) => [
        ...items.filter((edge) => edge.id !== incoming.id),
        {
          id: `${incoming.source}-${id}`,
          source: incoming.source,
          target: id,
          data: { branch: "next" },
        },
        {
          id: `${id}-${end.id}`,
          source: id,
          target: end.id,
          data: { branch: "next" },
        },
      ]);
    }
    setSelectedNodeId(id);
    setSection("properties");
  }

  function removeSelectedNode() {
    if (
      !selectedNode ||
      selectedNode.data.model.type === "start" ||
      selectedNode.data.model.type === "end"
    )
      return;
    remember();
    const incoming = edges.filter((edge) => edge.target === selectedNode.id);
    const outgoing = edges.filter((edge) => edge.source === selectedNode.id);
    const replacements = incoming.flatMap((left) =>
      outgoing.map(
        (right) =>
          ({
            id: nextId(`${left.source}-${right.target}`),
            source: left.source,
            target: right.target,
            data: { branch: left.data?.branch || "next" },
          }) as CanvasEdge,
      ),
    );
    setNodes((items) => items.filter((node) => node.id !== selectedNode.id));
    setEdges((items) => [
      ...items.filter(
        (edge) =>
          edge.source !== selectedNode.id && edge.target !== selectedNode.id,
      ),
      ...replacements,
    ]);
    setSelectedNodeId("");
  }

  function moveNode(nodeId: string, direction: -1 | 1) {
    const index = nodes.findIndex((node) => node.id === nodeId);
    const target = index + direction;
    if (
      !canLinearReorder ||
      index <= 0 ||
      target <= 0 ||
      target >= nodes.length - 1
    )
      return;
    remember();
    const reordered = [...nodes];
    [reordered[index], reordered[target]] = [
      reordered[target],
      reordered[index],
    ];
    setNodes(reordered);
    setEdges(
      reordered.slice(0, -1).map((node, edgeIndex) => ({
        id: nextId(`${node.id}-${reordered[edgeIndex + 1].id}`),
        source: node.id,
        target: reordered[edgeIndex + 1].id,
        data: { branch: "next" },
      })),
    );
  }

  async function beginSetup(kind: "ensure" | "repair" | "upgrade" | "restart") {
    if (lease.active) {
      setSection("runs");
      setMessage("电脑控制任务正在运行，请先停止当前运行再管理 Windows-MCP。");
      return;
    }
    setBusy(kind);
    const response =
      kind === "repair"
        ? await computerControlApi.repair()
        : kind === "upgrade"
          ? await computerControlApi.upgrade()
          : kind === "restart"
            ? await computerControlApi.restartSession()
          : await computerControlApi.ensure();
    if (response.ok) {
      setSetup((current) => ({
        ...current,
        ...response.result,
        phase: response.result.phase || response.result.setup_phase || current.phase,
        session_started_at: response.result.session_started_at || response.result.started_at || current.session_started_at,
      }));
      setMessage(
        kind === "upgrade" ? "正在检查 Windows-MCP 更新"
          : kind === "restart" ? "Windows-MCP 会话已重启，正在刷新工具和运行状态"
            : kind === "repair" ? "正在修复 Windows-MCP 安装"
              : "正在准备 Windows-MCP",
      );
      if (kind === "restart") {
        await Promise.all([loadCatalog(), refresh()]);
      }
    } else setMessage(friendlyError(response.error.message));
    setBusy("");
  }

  function resetEditor() {
    const next = initialDefinition();
    const canvas = toCanvas(next);
    setRecord(null);
    setDefinition(next);
    setNodes(canvas.nodes);
    setEdges(canvas.edges);
    setSelectedNodeId("");
    setHistory([]);
    setVersions([]);
    setProposals([]);
  }

  async function deleteWorkflow() {
    if (!selectedManifest || busy) return;
    if (
      !window.confirm(
        `确定删除工作流“${selectedManifest.name}”吗？此操作不可撤销。`,
      )
    )
      return;
    setBusy("delete");
    const response = await computerControlApi.deleteWorkflow(
      groupId,
      selectedManifest.workflow_id,
    );
    if (response.ok) {
      setSelectedId("");
      resetEditor();
      setMessage("工作流已删除");
      await refresh();
    } else {
      setMessage(friendlyError(response.error.message));
    }
    setBusy("");
  }
  async function createWorkflow() {
    setBusy("create");
    const response = await computerControlApi.createWorkflow(
      groupId,
      serializeDefinition(initialDefinition()),
    );
    if (response.ok) {
      setSelectedId(response.result.manifest.workflow_id);
      await refresh();
    } else setMessage(response.error.message);
    setBusy("");
  }

  async function save() {
    if (!record) return;
    setBusy("save");
    const current = fromCanvas(definition, nodes, edges);
    const response = await computerControlApi.updateWorkflow(
      groupId,
      record.manifest.workflow_id,
      serializeDefinition(current),
      record.manifest.revision,
    );
    if (response.ok) {
      const next = normalizeDefinition(response.result.definition);
      setRecord(response.result);
      setDefinition(next);
      setMessage(
        settings.auto_publish_and_trust
          ? "已保存，并自动发布和信任新版本"
          : "已保存为新版本",
      );
      setHistory([]);
      await refresh();
    } else setMessage(response.error.message);
    setBusy("");
  }

  async function publish() {
    if (!selectedManifest) return;
    setBusy("publish");
    const response = await computerControlApi.publish(
      groupId,
      selectedManifest.workflow_id,
      selectedManifest.current_version,
    );
    if (response.ok) {
      setMessage("当前版本已发布");
      await refresh();
    } else setMessage(response.error.message);
    setBusy("");
  }

  async function run() {
    if (!selectedManifest) return;
    if (lease.active) {
      setSection("runs");
      setMessage("电脑当前正在执行其他任务，请先在运行面板中停止占用。");
      return;
    }
    setBusy("run");
    const response = await computerControlApi.run(
      groupId,
      selectedManifest.workflow_id,
      "foreman",
      selectedManifest.effective_version || undefined,
    );
    if (response.ok) {
      setMessage("运行已启动");
      await refresh();
      setSection("runs");
    } else setMessage(response.error.message);
    setBusy("");
  }

  async function updateAutoTrust(
    enabled: boolean,
    authorizeCurrentFingerprint = false,
  ) {
    setBusy("settings");
    const response = await computerControlApi.updateSettings(
      groupId,
      enabled,
      authorizeCurrentFingerprint,
    );
    if (response.ok) {
      setSettings(response.result);
      setMessage(
        authorizeCurrentFingerprint
          ? "已重新授权当前 Windows-MCP 工具版本"
          : enabled
            ? "新版本将自动发布并信任"
            : "已关闭自动发布和信任",
      );
      await refresh();
    } else setMessage(friendlyError(response.error.message));
    setBusy("");
  }

  async function trust() {
    if (!selectedManifest) return;
    setBusy("trust");
    const response = await computerControlApi.trust(
      groupId,
      selectedManifest.workflow_id,
      selectedManifest.current_version,
    );
    if (response.ok) {
      setMessage("当前版本已受信");
      await refresh();
    } else setMessage(response.error.message);
    setBusy("");
  }

  async function rollback(version: number) {
    if (!selectedManifest || version === selectedManifest.current_version)
      return;
    setBusy("rollback");
    const response = await computerControlApi.rollback(
      groupId,
      selectedManifest.workflow_id,
      version,
    );
    if (response.ok) {
      setMessage(
        `已从版本 ${version} 创建新的可编辑版本，发布和信任状态已撤销。`,
      );
      await refresh();
    } else {
      setMessage(friendlyError(response.error.message));
    }
    setBusy("");
  }

  async function decideProposal(
    proposalId: string,
    decision: "accept" | "reject",
  ) {
    if (!selectedManifest) return;
    setBusy("proposal");
    const response = await computerControlApi.decideProposal(
      groupId,
      selectedManifest.workflow_id,
      proposalId,
      decision,
    );
    if (response.ok) {
      setMessage(
        decision === "accept"
          ? "优化建议已生成新的待发布版本。"
          : "优化建议已拒绝。",
      );
      await refresh();
      const next = await computerControlApi.proposals(
        groupId,
        selectedManifest.workflow_id,
      );
      if (next.ok) setProposals(next.result.proposals || []);
    } else {
      setMessage(friendlyError(response.error.message));
    }
    setBusy("");
  }

  async function interruptLease(emergency = false) {
    const runId = String(lease.lease?.run_id || "");
    if (!runId) return;
    if (emergency && !window.confirm("紧急停止会立即终止当前 Windows-MCP 会话，可能使正在进行的操作结果未知。确定继续吗？")) return;
    if (!emergency && !window.confirm("确定停止当前电脑控制任务吗？")) return;
    setBusy("stop");
    const response = await computerControlApi.interruptLease(runId, emergency);
    setMessage(response.ok ? (emergency ? "已紧急停止当前电脑控制任务。" : "已停止当前电脑控制任务。") : friendlyError(response.error.message));
    await refresh();
    setBusy("");
  }

  async function resolveRunRecovery(runId: string, resolution: "reobserve" | "retry" | "skip" | "cancel") {
    setBusy(`recovery-${runId}`);
    const response = await computerControlApi.resolveRecovery(groupId, runId, {
      resolution,
      idempotency_key: `${runId}-${resolution}-${Date.now()}`,
    });
    setMessage(response.ok ? (resolution === "cancel" ? "已停止恢复并取消运行。" : "已提交恢复意图，运行器会自动重新观察并继续。") : friendlyError(response.error.message));
    await refresh();
    setBusy("");
  }

  async function decideRunApproval(
    runId: string,
    nodeId: string,
    approved: boolean,
  ) {
    setBusy("approval");
    const response = await computerControlApi.decideRunApproval(
      groupId,
      runId,
      nodeId,
      approved,
    );
    setMessage(
      response.ok
        ? approved
          ? "已批准，工作流将继续执行。"
          : "已拒绝，工作流将停止。"
        : friendlyError(response.error.message),
    );
    await refresh();
    setBusy("");
  }

  async function decideRequest(requestId: string, approved: boolean) {
    setBusy("request-approval");
    const response = approved
      ? await computerControlApi.approveRequest(groupId, requestId)
      : await computerControlApi.rejectRequest(groupId, requestId);
    setMessage(
      response.ok
        ? approved
          ? "已授予本次运行的一次性授权。"
          : "已拒绝本次电脑控制请求。"
        : friendlyError(response.error.message),
    );
    await refresh();
    setBusy("");
  }

  function onConnect(connection: Connection) {
    if (
      !connection.source ||
      !connection.target ||
      connection.source === connection.target
    )
      return;
    remember();
    setEdges((items) =>
      addEdge(
        {
          ...connection,
          id: nextId(`${connection.source}-${connection.target}`),
          data: { branch: "next" },
        },
        items,
      ),
    );
  }

  const runsPanel = (
    <div className="h-full overflow-auto p-4">
      {lease.active && lease.lease && (
        <div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/8 p-3 text-xs">
          <div className="flex items-start gap-2">
            <CircleStop size={16} className="mt-0.5 shrink-0 text-amber-600" />
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-amber-800 dark:text-amber-200">电脑正在被占用</div>
              <div className="mt-1 text-amber-900/80 dark:text-amber-100/80">
                工作组：{groupLabelById?.[String(lease.lease.group_id || "")] || String(lease.lease.group_id || "未知")} · 智能体：{String(lease.run?.actor_id || lease.lease.actor_id || "未知")}
              </div>
              <div className="mt-0.5 truncate text-amber-900/80 dark:text-amber-100/80">
                工作流：{String(lease.workflow?.name || lease.run?.workflow_id || "控制层任务")} · 当前步骤：{String(lease.run?.current_node_id || "准备中")}
              </div>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <button
                className="rounded border border-amber-600/40 px-2 py-1 text-amber-800 disabled:opacity-40 dark:text-amber-200"
                disabled={Boolean(busy)}
                onClick={() => void interruptLease(false)}
              >
                停止
              </button>
              <button
                className="rounded border border-red-500/50 px-2 py-1 text-red-700 disabled:opacity-40 dark:text-red-300"
                disabled={Boolean(busy)}
                onClick={() => void interruptLease(true)}
              >
                紧急停止
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">运行时间线</h2>
        <button
          title="刷新运行记录"
          className="rounded-md p-1.5 hover:bg-black/5"
          onClick={() => void refresh()}
        >
          <RefreshCw size={15} />
        </button>
      </div>
      {runs.length === 0 ? (
        <div className="rounded-md border border-dashed border-[var(--color-border)] p-5 text-center text-xs text-[var(--color-text-secondary)]">
          暂无运行记录
        </div>
      ) : (
        <div className="space-y-2">
          {runs.map((run) => {
            const events = Array.isArray(run.events)
              ? run.events.filter((item): item is Record<string, unknown> =>
                  Boolean(item && typeof item === "object"),
                )
              : [];
            const current =
              [...events].reverse().find((item) => item.status === "running") ||
              events[events.length - 1];
            const error =
              run.error && typeof run.error === "object"
                ? (run.error as Record<string, unknown>)
                : null;
            const recovery =
              run.recovery && typeof run.recovery === "object"
                ? (run.recovery as Record<string, unknown>)
                : null;
            return (
              <div
                key={String(run.run_id)}
                className="rounded-md border border-[var(--color-border)] p-3 text-xs"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold">
                    {runStatusLabel(run.status)}
                  </span>
                  <span className="truncate text-[var(--color-text-secondary)]">
                    {current ? String(current.node_id || "") : "尚未开始步骤"}
                  </span>
                </div>
                <div className="mt-1 text-[var(--color-text-secondary)]">
                  执行智能体：{String(run.actor_id || "未记录")} · 已完成{" "}
                  {events.filter((item) => item.status === "completed").length}{" "}
                  个步骤
                </div>
                {error && (
                  <div className="mt-2 rounded bg-red-500/8 px-2 py-1.5 text-red-700 dark:text-red-300">
                    {friendlyError(error.message)}
                  </div>
                )}
                {recovery && (
                  <div className="mt-2 rounded bg-amber-500/8 px-2 py-1.5 text-amber-800 dark:text-amber-200">
                    自适应恢复：
                    {recovery.delivery_status === "delivered"
                      ? "已通知执行智能体，等待修正"
                      : recovery.delivery_status === "failed"
                        ? "通知投递失败"
                        : "正在准备恢复信息"}
                  </div>
                )}
                {run.status === "recovering" && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 rounded bg-sky-500/8 px-2 py-1.5">
                    <span className="mr-auto text-sky-800 dark:text-sky-200">请选择处理方式</span>
                    <button className="rounded border border-sky-500/40 px-2 py-1 text-sky-700 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void resolveRunRecovery(String(run.run_id), "reobserve")}>重新观察</button>
                    <button className="rounded border border-[var(--color-border)] px-2 py-1 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void resolveRunRecovery(String(run.run_id), "retry")}>重试</button>
                    <button className="rounded border border-amber-500/40 px-2 py-1 text-amber-700 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void resolveRunRecovery(String(run.run_id), "skip")}>跳过</button>
                    <button className="rounded border border-red-500/40 px-2 py-1 text-red-600 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void resolveRunRecovery(String(run.run_id), "cancel")}>停止</button>
                  </div>
                )}
                {run.status === "waiting_approval" &&
                  Boolean(run.current_node_id) && (
                    <div className="mt-2 flex items-center gap-2 rounded bg-amber-500/8 px-2 py-1.5">
                      <span className="flex-1 text-amber-800 dark:text-amber-200">
                        此步骤要求人工确认
                      </span>
                      <button
                        className="text-emerald-700 disabled:opacity-40"
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void decideRunApproval(
                            String(run.run_id),
                            String(run.current_node_id),
                            true,
                          )
                        }
                      >
                        批准
                      </button>
                      <button
                        className="text-red-600 disabled:opacity-40"
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void decideRunApproval(
                            String(run.run_id),
                            String(run.current_node_id),
                            false,
                          )
                        }
                      >
                        拒绝
                      </button>
                    </div>
                  )}
                {run.status === "running" && String(lease.lease?.run_id || "") === String(run.run_id || "") && (
                  <button
                    className="mt-2 inline-flex items-center gap-1 rounded border border-red-500/40 px-2 py-1 text-red-600"
                    onClick={() => void interruptLease(true)}
                  >
                    <CircleStop size={13} />
                    紧急停止
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  return (
    <main className="flex h-full min-h-0 flex-col bg-[var(--color-bg-primary)] text-[var(--color-text-primary)]">
      <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border)] px-4 py-3 md:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Laptop size={22} />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">电脑控制</div>
            <div className="truncate text-xs text-[var(--color-text-secondary)]">
              工作组 {groupId || "未选择"}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            title="撤销"
            className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-[var(--color-border)] disabled:opacity-40"
            onClick={undo}
            disabled={history.length === 0}
          >
            <Undo2 size={15} />
          </button>
          <button
            className="inline-flex h-9 items-center gap-1 rounded-md border border-[var(--color-border)] px-3 text-sm disabled:opacity-50"
            onClick={() => void save()}
            disabled={!record || Boolean(busy)}
          >
            <Save size={15} />
            保存
          </button>
          <button
            className="hidden h-9 items-center gap-1 rounded-md border border-[var(--color-border)] px-3 text-sm disabled:opacity-50 sm:inline-flex"
            onClick={() => void publish()}
            disabled={!selectedManifest || Boolean(busy)}
          >
            <Upload size={15} />
            发布
          </button>
          <button
            className="hidden h-9 items-center gap-1 rounded-md border border-emerald-500/40 px-3 text-sm text-emerald-700 disabled:opacity-50 sm:inline-flex"
            onClick={() => setTrustDialogOpen(true)}
            disabled={!selectedManifest || Boolean(busy)}
          >
            <ShieldCheck size={15} />
            信任
          </button>
          <button
            className="inline-flex h-9 items-center gap-1 rounded-md border border-[var(--color-border)] px-3 text-sm disabled:opacity-50"
            onClick={() => setTriggerDialogOpen(true)}
            disabled={!record || Boolean(busy)}
            title={!record ? "请先选择或创建工作流" : "配置元素、定时、周期和一次性触发"}
          >
            <AlarmClock size={15} />
            自动触发
          </button>
          <button
            className="inline-flex h-9 items-center gap-1 rounded-md bg-[var(--color-accent-primary)] px-3 text-sm text-white disabled:opacity-50"
            onClick={() => void run()}
            disabled={!setupReady || !isTrusted || Boolean(busy)}
          >
            <Play size={15} />
            运行
          </button>
        </div>
      </header>

      {message && (
        <div className="mx-4 mt-3 flex items-center justify-between rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm md:mx-6">
          <span>{message}</span>
          <button aria-label="关闭" onClick={() => setMessage("")}>
            <X size={15} />
          </button>
        </div>
      )}

      <div className="mx-4 mt-3 flex items-center gap-3 rounded-md border border-[var(--color-border)] px-3 py-2 text-xs md:mx-6">
        {setup.phase === "ready" ? (
          <Check size={15} className="text-emerald-600" />
        ) : setup.phase === "failed" ? (
          <AlertTriangle size={15} className="text-red-600" />
        ) : (
          <LoaderCircle size={15} className="animate-spin text-amber-600" />
        )}
        <div className="min-w-0 flex-1">
          <span className="font-medium">
            Windows-MCP {phaseLabel(setup.phase)}
          </span>
          {setup.version && (
            <span className="ml-2 text-[var(--color-text-secondary)]">
              版本 {setup.version}
            </span>
          )}
          <div className="text-[var(--color-text-secondary)]">
            {setupErrorMessage(setup.error?.message, setup.phase) ||
              (setup.phase === "ready"
                ? `已验证 ${setup.tool_count || 0} 个电脑工具`
                : "首次准备可能需要一到两分钟，可以继续浏览此页面")}
            {setup.phase === "failed" && setup.python_candidates && setup.python_candidates.length > 0 && (
              <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                已尝试：{setup.python_candidates.join("、")}
              </div>
            )}
          </div>
          {(sessionDetails.startedAt || sessionDetails.transportRestarts !== null) && (
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--color-text-secondary)]">
              {sessionDetails.startedAt && <span>会话启动：{formatComputerControlTime(sessionDetails.startedAt)}</span>}
              {sessionDetails.transportRestarts !== null && <span>会话恢复/重启：{sessionDetails.transportRestarts} 次</span>}
            </div>
          )}
          {currentObservation && (
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--color-text-secondary)]">
              {currentObservation.updatedAt && <span>最近观察：{formatComputerControlTime(currentObservation.updatedAt)}</span>}
              {currentObservation.provider && <span>提供者：{computerControlProviderLabel(currentObservation.provider)}</span>}
              {currentObservation.targetWindow && <span className="max-w-72 truncate">目标窗口：{currentObservation.targetWindow}</span>}
              {currentObservation.elementCount !== null && <span>元素：{currentObservation.elementCount} 个</span>}
            </div>
          )}
        </div>
        {setup.phase === "failed" ? (
          <button
            className="inline-flex items-center gap-1 rounded-md border px-2 py-1"
            onClick={() => void beginSetup("repair")}
            disabled={Boolean(busy) || lease.active}
          >
            <Wrench size={13} />
            修复
          </button>
        ) : (
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              title="仅重启 Windows-MCP 连接，不会重新安装或升级"
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 disabled:opacity-40"
              onClick={() => void beginSetup("restart")}
              disabled={setup.phase !== "ready" || Boolean(busy) || lease.active}
            >
              <RefreshCw size={13} />
              重启会话
            </button>
            <button
              title="检查并安装最新版 Windows-MCP"
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 disabled:opacity-40"
              onClick={() => void beginSetup("upgrade")}
              disabled={setup.phase !== "ready" || Boolean(busy) || lease.active}
            >
              <Upload size={13} />
              更新组件
            </button>
          </div>
        )}
      </div>

      <div className="mx-4 mt-3 flex items-center gap-3 rounded-md border border-[var(--color-border)] px-3 py-2 text-xs md:mx-6">
        <ShieldCheck size={15} className="shrink-0 text-emerald-600" />
        <div className="min-w-0 flex-1">
          <div className="font-medium">自动发布并信任新版本</div>
          <div className="text-[var(--color-text-secondary)]">
            保存、回滚或接受 AI 优化后立即可运行；不会自动开启无人值守触发器。
          </div>
        </div>
        {settings.reauthorization_required ? (
          <button
            className="shrink-0 rounded-md border border-amber-500/40 px-2.5 py-1.5 text-amber-700 disabled:opacity-40"
            disabled={Boolean(busy) || !setupReady}
            onClick={() => void updateAutoTrust(true, true)}
          >
            重新授权工具版本
          </button>
        ) : (
          <input
            aria-label="自动发布并信任新版本"
            type="checkbox"
            checked={settings.auto_publish_and_trust}
            disabled={Boolean(busy)}
            onChange={(event) => void updateAutoTrust(event.target.checked)}
          />
        )}
      </div>

      {requests
        .filter((item) => item.status === "pending_approval")
        .map((request) => (
          <div
            key={request.request_id}
            className="mx-4 mt-3 flex items-center gap-3 rounded-md border border-red-500/35 bg-red-500/6 px-3 py-2 text-xs md:mx-6"
          >
            <ShieldCheck size={16} className="shrink-0 text-red-600" />
            <div className="min-w-0 flex-1">
              <div className="font-semibold">电脑控制请求等待确认</div>
              <div className="mt-0.5 truncate text-[var(--color-text-secondary)]">
                {request.text || "智能体请求执行高风险操作"}
              </div>
              <div className="mt-1 text-red-700 dark:text-red-300">
                涉及：
                {request.risk?.high_risk_nodes
                  ?.map((item) => item.title || toolName(item.tool || ""))
                  .filter(Boolean)
                  .join("、") || "系统级操作"}
              </div>
            </div>
            <button
              className="shrink-0 rounded border border-emerald-500/40 px-2 py-1 text-emerald-700 disabled:opacity-40"
              disabled={Boolean(busy)}
              onClick={() => void decideRequest(request.request_id, true)}
            >
              仅批准本次
            </button>
            <button
              className="shrink-0 rounded border border-red-500/30 px-2 py-1 text-red-600 disabled:opacity-40"
              disabled={Boolean(busy)}
              onClick={() => void decideRequest(request.request_id, false)}
            >
              拒绝
            </button>
          </div>
        ))}

      <div className="mx-4 mt-3 grid grid-cols-4 gap-1 rounded-md bg-black/5 p-1 md:hidden">
        {(["tools", "steps", "properties", "runs"] as Section[]).map((item) => (
          <button
            key={item}
            className={`rounded px-2 py-1.5 text-xs ${section === item ? "bg-white text-slate-900 shadow-sm" : "text-[var(--color-text-secondary)]"}`}
            onClick={() => setSection(item)}
          >
            {
              (
                {
                  tools: "工具",
                  steps: "步骤",
                  properties: "属性",
                  runs: "运行",
                } as Record<Section, string>
              )[item]
            }
          </button>
        ))}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[220px_minmax(0,1fr)_310px]">
        <aside
          className={`${section === "tools" ? "block" : "hidden md:block"} min-h-0 border-r border-[var(--color-border)] p-3`}
        >
          <div className="mb-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h2 className="text-xs font-semibold text-[var(--color-text-secondary)]">
                  工作流
                </h2>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] hover:border-[var(--color-accent-primary)] disabled:opacity-40"
                  onClick={() => setTriggerDialogOpen(true)}
                  disabled={!record || Boolean(busy)}
                  title={!record ? "请先选择或创建工作流" : "配置元素、定时、周期和一次性触发"}
                >
                  <AlarmClock size={12} />
                  自动触发
                </button>
              </div>
              <div className="flex items-center gap-1">
                <button
                  title="新建工作流"
                  className="rounded-md p-1.5 hover:bg-black/5 disabled:opacity-40"
                  onClick={() => void createWorkflow()}
                  disabled={Boolean(busy)}
                >
                  <Copy size={15} />
                </button>
                <button
                  title="删除当前工作流"
                  aria-label="删除当前工作流"
                  className="rounded-md p-1.5 text-red-600 hover:bg-red-500/10 disabled:opacity-40"
                  onClick={() => void deleteWorkflow()}
                  disabled={!selectedManifest || Boolean(busy)}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
            <div className="relative">
              <select
                className="h-9 w-full appearance-none rounded-md border border-[var(--color-border)] bg-transparent px-2.5 pr-8 text-sm"
                value={selectedId}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                {workflows.map((item) => (
                  <option key={item.workflow_id} value={item.workflow_id}>
                    {item.name}
                  </option>
                ))}
              </select>
              <ChevronDown
                size={14}
                className="pointer-events-none absolute right-2.5 top-3"
              />
            </div>
          </div>
          {versions.length > 0 && (
            <div className="mb-4 border-t border-[var(--color-border)] pt-3">
              <h2 className="mb-2 text-xs font-semibold text-[var(--color-text-secondary)]">
                版本历史
              </h2>
              <div className="space-y-1">
                {versions.slice(0, 4).map((item) => (
                  <div
                    key={item.version}
                    className="flex items-center justify-between gap-2 rounded px-2 py-1.5 text-xs hover:bg-black/5"
                  >
                    <span>
                      版本 {item.version}
                      {item.version === selectedManifest?.published_version
                        ? " · 已发布"
                        : item.version === selectedManifest?.current_version
                          ? " · 当前"
                          : ""}
                    </span>
                    {item.version !== selectedManifest?.current_version && (
                      <button
                        className="text-[var(--color-accent-primary)] disabled:opacity-40"
                        disabled={Boolean(busy)}
                        onClick={() => void rollback(item.version)}
                      >
                        回滚
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          {proposals.some((item) => item.status === "pending") && (
            <div className="mb-4 border-t border-[var(--color-border)] pt-3">
              <h2 className="mb-2 text-xs font-semibold text-[var(--color-text-secondary)]">
                AI 优化建议
              </h2>
              {proposals
                .filter((item) => item.status === "pending")
                .slice(0, 2)
                .map((item) => (
                  <div
                    key={item.proposal_id}
                    className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs"
                  >
                    <p className="leading-4">
                      {item.summary || "根据运行恢复结果优化工作流"}
                    </p>
                    <div className="mt-2 flex gap-2">
                      <button
                        className="text-emerald-700 disabled:opacity-40"
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void decideProposal(item.proposal_id, "accept")
                        }
                      >
                        接受为新版本
                      </button>
                      <button
                        className="text-[var(--color-text-secondary)] disabled:opacity-40"
                        disabled={Boolean(busy)}
                        onClick={() =>
                          void decideProposal(item.proposal_id, "reject")
                        }
                      >
                        拒绝
                      </button>
                    </div>
                  </div>
                ))}
            </div>
          )}
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold text-[var(--color-text-secondary)]">
              Windows-MCP 工具
            </h2>
            <span className="text-[10px] text-[var(--color-text-secondary)]">
              {tools.length}
            </span>
          </div>
          <div className="relative mb-2">
            <Search
              size={13}
              className="absolute left-2.5 top-2.5 text-[var(--color-text-secondary)]"
            />
            <input
              className="h-8 w-full rounded-md border border-[var(--color-border)] bg-transparent pl-8 pr-2 text-xs outline-none"
              value={toolSearch}
              onChange={(event) => setToolSearch(event.target.value)}
              placeholder="搜索工具"
            />
          </div>
          <div className="max-h-[calc(100vh-310px)] space-y-1 overflow-auto">
            {filteredTools.map((tool) => (
              <button
                draggable
                key={tool.name}
                onDragStart={(event) => {
                  event.dataTransfer.setData(
                    "application/onecolleague-windows-mcp-tool",
                    JSON.stringify(tool),
                  );
                  event.dataTransfer.effectAllowed = "copy";
                }}
                onClick={() => addNode("action", tool)}
                className="block w-full rounded-md border border-transparent px-2 py-2 text-left hover:border-[var(--color-border)] hover:bg-black/5"
              >
                <div className="truncate text-xs font-medium">
                  {toolName(tool.name)}{" "}
                  <span className="font-normal text-[var(--color-text-secondary)]">
                    ({tool.name})
                  </span>
                </div>
                <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-[var(--color-text-secondary)]">
                  {toolDescription(tool.name, tool.description)}
                </div>
              </button>
            ))}
            {setupReady && filteredTools.length === 0 && (
              <div className="p-3 text-center text-xs text-[var(--color-text-secondary)]">
                没有匹配的工具
              </div>
            )}
          </div>
        </aside>

        <section
          className={`${section === "steps" ? "flex" : "hidden md:flex"} min-h-[520px] min-w-0 flex-col border-r border-[var(--color-border)]`}
        >
          <div className="flex h-12 items-center gap-1 overflow-x-auto border-b border-[var(--color-border)] px-3">
            <span className="mr-2 text-xs font-medium text-[var(--color-text-secondary)]">
              添加步骤
            </span>
            <button
              className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs hover:bg-black/5"
              onClick={() => addNode("action")}
            >
              <MousePointer2 size={14} />
              操作
            </button>
            <button
              className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs hover:bg-black/5"
              onClick={() => addNode("condition")}
            >
              <GitBranch size={14} />
              判断
            </button>
            <button
              className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs hover:bg-black/5"
              onClick={() => addNode("wait")}
            >
              <Hourglass size={14} />
              等待
            </button>
            <button
              className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs hover:bg-black/5"
              onClick={() => addNode("approval")}
            >
              <ShieldCheck size={14} />
              确认
            </button>
          </div>
          <div className="hidden min-h-[400px] flex-1 md:block">
            <WorkflowCanvas
              nodes={nodes}
              edges={edges}
              onNodesChange={(changes: NodeChange<CanvasNode>[]) =>
                setNodes((items) => applyNodeChanges(changes, items))
              }
              onEdgesChange={(changes: EdgeChange<CanvasEdge>[]) =>
                setEdges((items) => applyEdgeChanges(changes, items))
              }
              onConnect={onConnect}
              onSelect={setSelectedNodeId}
              onToolDrop={(tool, position) => addNode("action", tool, position)}
            />
          </div>
          <div className="flex-1 space-y-2 overflow-auto p-3 md:hidden">
            {nodes.map((node, index) => (
              <div
                key={node.id}
                className={`flex items-center gap-2 rounded-md border p-2 ${selectedNodeId === node.id ? "border-[var(--color-accent-primary)] bg-black/5" : "border-[var(--color-border)]"}`}
              >
                <button
                  className="min-w-0 flex-1 text-left"
                  onClick={() => {
                    setSelectedNodeId(node.id);
                    setSection("properties");
                  }}
                >
                  <span className="block truncate text-sm font-medium">
                    {index + 1}. {node.data.model.title}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-[var(--color-text-secondary)]">
                    {nodeLabel(node.data.model.type)}
                    {node.data.model.tool
                      ? ` · ${toolName(node.data.model.tool)}`
                      : ""}
                  </span>
                </button>
                {!(["start", "end"] as WorkflowNodeKind[]).includes(
                  node.data.model.type,
                ) && (
                  <div className="flex shrink-0 gap-1">
                    <button
                      title="上移步骤"
                      className="rounded-md border border-[var(--color-border)] p-1.5 disabled:opacity-30"
                      disabled={!canLinearReorder || index <= 1}
                      onClick={() => moveNode(node.id, -1)}
                    >
                      <ChevronUp size={14} />
                    </button>
                    <button
                      title="下移步骤"
                      className="rounded-md border border-[var(--color-border)] p-1.5 disabled:opacity-30"
                      disabled={!canLinearReorder || index >= nodes.length - 2}
                      onClick={() => moveNode(node.id, 1)}
                    >
                      <ChevronDown size={14} />
                    </button>
                  </div>
                )}
              </div>
            ))}
            {!canLinearReorder && nodes.length > 2 && (
              <p className="px-1 text-xs text-[var(--color-text-secondary)]">
                包含分支的流程请在桌面端调整连线。
              </p>
            )}
          </div>
          <div className="hidden h-44 border-t border-[var(--color-border)] lg:block">
            {runsPanel}
          </div>
        </section>

        <aside
          className={`${section === "properties" ? "block" : "hidden md:block"} min-h-0 overflow-auto p-4`}
        >
          {selectedNode ? (
            <div>
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-semibold">步骤属性</h2>
                  <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                    {nodeLabel(selectedNode.data.model.type)}
                  </p>
                </div>
                {!(["start", "end"] as WorkflowNodeKind[]).includes(
                  selectedNode.data.model.type,
                ) && (
                  <button
                    title="删除步骤"
                    className="rounded-md p-2 text-red-600 hover:bg-red-500/10"
                    onClick={removeSelectedNode}
                  >
                    <Trash2 size={15} />
                  </button>
                )}
              </div>
              <label className="block text-xs font-medium">
                步骤名称
                <input
                  className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2.5 text-sm outline-none"
                  value={selectedNode.data.model.title}
                  onChange={(event) =>
                    updateNode(selectedNode.id, { title: event.target.value })
                  }
                />
              </label>
              {selectedNode.data.model.type === "action" && (
                <div className="mt-4">
                  <label className="block text-xs font-medium">
                    执行工具
                    <select
                      className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2 text-sm"
                      value={selectedNode.data.model.tool || ""}
                      onChange={(event) =>
                        updateNode(selectedNode.id, {
                          tool: event.target.value,
                          arguments: {},
                          title: toolName(event.target.value),
                        })
                      }
                    >
                      {tools.map((tool) => (
                        <option key={tool.name} value={tool.name}>
                          {toolName(tool.name)} ({tool.name})
                        </option>
                      ))}
                    </select>
                  </label>
                  <p className="mt-1 text-xs leading-4 text-[var(--color-text-secondary)]">
                    {selectedTool
                      ? toolDescription(
                          selectedTool.name,
                          selectedTool.description,
                        )
                      : "请选择要执行的电脑工具。"}
                  </p>
                  <div className="my-4 border-t border-[var(--color-border)]" />
                  <SchemaForm
                    toolName={selectedTool?.name}
                    schema={selectedTool?.inputSchema}
                    value={selectedNode.data.model.arguments || {}}
                    onChange={(argumentsValue) =>
                      updateNode(selectedNode.id, { arguments: argumentsValue })
                    }
                  />
                  {(selectedNode.data.model.tool || "").toLowerCase() === "click" ||
                  (selectedNode.data.model.tool || "").toLowerCase() === "type" ? (
                    <div className="mt-4 rounded-md border border-[var(--color-border)] p-3">
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <div className="text-xs font-semibold">操作目标</div>
                          <div className="mt-1 text-xs text-[var(--color-text-secondary)]">
                            {selectedNode.data.model.target
                              ? `${selectedNode.data.model.target.name || selectedNode.data.model.target.text || "已选择元素"} · 元素优先`
                              : "尚未选择元素，运行时不会优先使用坐标"}
                          </div>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <button
                            type="button"
                            title="启动桌面拾取器"
                            className="inline-flex items-center gap-1 rounded-md border border-[var(--color-accent-primary)]/50 px-2 py-1 text-xs text-[var(--color-accent-primary)]"
                            onClick={() => void startPicker()}
                            disabled={pickerBusy || Boolean(pickerSession)}
                          >
                            <MousePointer2 size={13} />
                            {pickerBusy ? "启动中…" : "实时捕获元素"}
                          </button>
                          <button
                            type="button"
                            title="读取一次 Windows-MCP 快照"
                            className="rounded-md border border-[var(--color-border)] px-2 py-1 text-xs"
                            onClick={() => void captureElements()}
                            disabled={pickerBusy}
                          >
                            读取快照
                          </button>
                        </div>
                      </div>
                      {pickerSession && (
                        <div className="mt-3 rounded-md border border-sky-500/30 bg-sky-500/5 p-2.5 text-xs">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-medium text-sky-900 dark:text-sky-100">
                              桌面拾取中 · {pickerSession.hotkey === "Ctrl+Shift+LeftClick"
                                ? "Ctrl+Shift+鼠标左键"
                                : pickerSession.hotkey || "Ctrl+Shift+鼠标左键"}
                            </span>
                            <button type="button" className="text-[var(--color-text-secondary)] underline" onClick={() => void stopPicker()}>结束拾取</button>
                          </div>
                          <div className="mt-1 text-[var(--color-text-secondary)]">
                            将鼠标移到目标元素上，按快捷键锁定；也可以使用下方按钮。
                          </div>
                          {pickerSession.element && (
                            <div className="mt-2 rounded border border-[var(--color-border)] bg-[var(--color-bg-primary)] p-2">
                              <div className="font-medium">{String(pickerSession.element.name || pickerSession.element.text || "未命名元素")}</div>
                              <div className="mt-0.5 text-[var(--color-text-secondary)]">{String(pickerSession.element.control_type || "控件")} · {String(pickerSession.element.window_name || "当前窗口")}</div>
                              <div className="mt-0.5 text-[var(--color-text-secondary)]">稳定性：{String(pickerSession.element.stability || pickerSession.stability || "检测中")}</div>
                            </div>
                          )}
                          <div className="mt-2 flex gap-2">
                            <button type="button" className="rounded border border-[var(--color-border)] px-2 py-1" onClick={() => void lockPicker()} disabled={pickerBusy}>锁定当前元素</button>
                            <button type="button" className="rounded border border-emerald-500/40 px-2 py-1 text-emerald-700" onClick={() => void confirmPicker()} disabled={pickerBusy || !pickerSession.element}>确认定位</button>
                          </div>
                          {pickerSession.warning && <div className="mt-2 text-amber-700">{pickerSession.warning}</div>}
                        </div>
                      )}
                      {pickerElements.length > 0 && (
                        <select
                          className="mt-3 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2 text-xs"
                          value=""
                          onChange={(event) => {
                            const item = pickerElements[Number(event.target.value)];
                            if (item) void chooseElement(item);
                          }}
                        >
                          <option value="">从最新快照选择元素…</option>
                          {pickerElements.map((item, index) => (
                            <option key={`${String(item.element_id || index)}`} value={index}>
                              {String(item.name || item.text || "未命名元素")} · {String(item.control_type || "控件")} · {String(item.window_name || "当前窗口")}
                            </option>
                          ))}
                        </select>
                      )}
                      {selectedNode.data.model.target && (
                        <label className="mt-2 flex items-center justify-between rounded border border-[var(--color-border)] px-2.5 py-2 text-xs">
                          <span>
                            <span className="block font-medium">只允许元素定位</span>
                            <span className="text-[var(--color-text-secondary)]">关闭后才允许本次运行使用一次性位置兜底</span>
                          </span>
                          <input
                            type="checkbox"
                            checked={selectedNode.data.model.target.fallback_policy !== "controlled"}
                            onChange={(event) => updateNode(selectedNode.id, {
                              target: { ...selectedNode.data.model.target, fallback_policy: event.target.checked ? "never" : "controlled" },
                            })}
                          />
                        </label>
                      )}
                      {pickerStatus && <div className="mt-2 text-xs text-[var(--color-text-secondary)]">{pickerStatus}</div>}
                    </div>
                  ) : null}
                  <div className="mt-4 grid grid-cols-2 gap-3">
                    <label className="text-xs font-medium">
                      工具等待时长（秒）
                      <input
                        type="number"
                        min={0}
                        placeholder="不限时"
                        className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2"
                        value={selectedNode.data.model.timeout_seconds ?? ""}
                        onChange={(event) =>
                          updateNode(selectedNode.id, {
                            timeout_seconds: event.target.value === "" ? null : Number(event.target.value),
                          })
                        }
                      />
                    </label>
                    <label className="text-xs font-medium">
                      失败重试
                      <select
                        className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2"
                        value={selectedNode.data.model.retries || 0}
                        onChange={(event) =>
                          updateNode(selectedNode.id, {
                            retries: Number(event.target.value),
                          })
                        }
                      >
                        {[0, 1, 2, 3].map((value) => (
                          <option key={value} value={value}>
                            {value} 次
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <label className="mt-4 flex items-center justify-between rounded-md border border-[var(--color-border)] px-3 py-2 text-xs">
                    <span>
                      <span className="block font-medium">AI 自适应恢复</span>
                      <span className="text-[var(--color-text-secondary)]">
                        界面变化时尝试重新定位
                      </span>
                    </span>
                    <input
                      type="checkbox"
                      checked={selectedNode.data.model.adaptive !== false}
                      onChange={(event) =>
                        updateNode(selectedNode.id, {
                          adaptive: event.target.checked,
                        })
                      }
                    />
                  </label>
                </div>
              )}
              {selectedNode.data.model.type === "wait" && (
                <label className="mt-4 block text-xs font-medium">
                  等待时长（秒，留空表示一直等待）
                  <input
                    type="number"
                    min={0}
                    step={0.5}
                    className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2.5 text-sm"
                    value={selectedNode.data.model.duration_seconds ?? ""}
                    onChange={(event) =>
                      updateNode(selectedNode.id, {
                        duration_seconds: event.target.value === "" ? null : Number(event.target.value),
                      })
                    }
                  />
                </label>
              )}
              {selectedNode.data.model.type === "condition" && (
                <label className="mt-4 block text-xs font-medium">
                  判断条件
                  <input
                    className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2.5 text-sm"
                    value={selectedNode.data.model.condition || ""}
                    onChange={(event) =>
                      updateNode(selectedNode.id, {
                        condition: event.target.value,
                      })
                    }
                  />
                </label>
              )}
            </div>
          ) : (
            <div>
              <h2 className="text-sm font-semibold">流程设置</h2>
              <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
                选择画布中的步骤后可编辑属性。
              </p>
              <label className="mt-5 block text-xs font-medium">
                流程名称
                <input
                  className="mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2.5 text-sm"
                  value={definition.name}
                  onChange={(event) =>
                    setDefinition((value) => ({
                      ...value,
                      name: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="mt-4 block text-xs font-medium">
                说明
                <textarea
                  className="mt-1 min-h-20 w-full resize-y rounded-md border border-[var(--color-border)] bg-transparent p-2.5 text-sm"
                  value={definition.description}
                  onChange={(event) =>
                    setDefinition((value) => ({
                      ...value,
                      description: event.target.value,
                    }))
                  }
                />
              </label>
              <label className="mt-4 flex items-center justify-between rounded-md border border-[var(--color-border)] px-3 py-2 text-xs">
                <span>保存关键截图</span>
                <input
                  type="checkbox"
                  checked={definition.save_screenshots}
                  onChange={(event) =>
                    setDefinition((value) => ({
                      ...value,
                      save_screenshots: event.target.checked,
                    }))
                  }
                />
              </label>
              <label className="mt-4 flex items-center justify-between rounded-md border border-[var(--color-border)] px-3 py-2 text-xs">
                <span>
                  <span className="block font-medium">自动验证</span>
                  <span className="text-[var(--color-text-secondary)]">全部步骤和最终证据完成后自动结束运行</span>
                </span>
                <input
                  type="checkbox"
                  checked={definition.auto_verify !== false}
                  onChange={(event) =>
                    setDefinition((value) => ({ ...value, auto_verify: event.target.checked }))
                  }
                />
              </label>
            </div>
          )}
        </aside>

        {section === "runs" && (
          <section className="block min-h-[420px] md:hidden">
            {runsPanel}
          </section>
        )}
      </div>

      {record && (
        <TriggerEditor
          open={triggerDialogOpen}
          groupId={groupId}
          workflowId={record.manifest.workflow_id}
          revision={record.manifest.revision}
          initialTriggers={definition.triggers}
          onClose={() => setTriggerDialogOpen(false)}
          onSaved={(triggers, nextRevision) => {
            setDefinition((current) => ({ ...current, triggers }));
            setRecord((current) => current ? {
              ...current,
              manifest: {
                ...current.manifest,
                revision: nextRevision || current.manifest.revision,
              },
              definition: { ...current.definition, triggers },
            } : current);
            void refresh();
          }}
        />
      )}

      {trustDialogOpen && selectedManifest && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="信任电脑控制工作流"
        >
          <div className="w-full max-w-lg rounded-md bg-[var(--color-bg-primary)] p-5 shadow-2xl">
            <div className="flex items-start gap-3">
              <ShieldCheck
                className="mt-0.5 shrink-0 text-emerald-600"
                size={20}
              />
              <div>
                <h2 className="text-base font-semibold">
                  信任“{selectedManifest.name}”当前版本
                </h2>
                <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
                  信任后，该版本和已启用的自动触发器可以使用 Windows-MCP
                  的全部电脑权限。
                </p>
              </div>
            </div>
            <div className="mt-4 border-y border-[var(--color-border)] py-3 text-sm">
              <div className="font-medium">包含的高风险权限</div>
              <ul className="mt-2 space-y-1 text-[var(--color-text-secondary)]">
                <li>运行 PowerShell 和系统命令</li>
                <li>写入、移动和删除文件</li>
                <li>启动或结束进程</li>
                <li>修改 Windows 注册表</li>
                <li>控制鼠标、键盘、窗口和剪贴板</li>
              </ul>
              <p className="mt-3 text-red-700 dark:text-red-300">
                部分操作不可撤销。工作流或 Windows-MCP
                工具指纹变化后，信任会自动失效。
              </p>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="h-9 rounded-md border border-[var(--color-border)] px-3 text-sm"
                onClick={() => setTrustDialogOpen(false)}
              >
                取消
              </button>
              <button
                className="h-9 rounded-md bg-emerald-600 px-3 text-sm text-white"
                onClick={() => {
                  setTrustDialogOpen(false);
                  void trust();
                }}
              >
                确认信任当前版本
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
