import { ChangeEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive, Ban, ChevronRight, Download, Eye, FileCheck2, FileSearch, Files,
  FolderOpen, History, KeyRound, Loader2, Pencil, Play, Plus, RefreshCw, RotateCcw,
  Save, Search, Settings2, ShieldCheck, SlidersHorizontal, Trash2, Upload, X,
} from "lucide-react";
import {
  Analysis, Batch, BatchItem, Entity, ModelStatus, Policy, Rule, RuleCategory, Task,
  WorkspaceMetadata, desensitizationApi,
} from "../../services/api/desensitization";

type View = "files" | "text" | "review" | "batches" | "history" | "rules" | "policies" | "models";
type RuleDraft = Pick<Rule, "name" | "kind" | "pattern" | "entity_type" | "replacement" | "category" | "scope">;
type ApiFailure = Error & { code?: string; details?: unknown };

const supported = new Set(["txt", "md", "docx", "xlsx", "xlsm", "pdf", "png", "jpg", "jpeg", "bmp", "tif", "tiff"]);
const terminalBatchStatuses = new Set(["completed", "partial", "failed", "cancelled"]);
const defaultRule: RuleDraft = {
  name: "", kind: "word", pattern: "", entity_type: "CUSTOM", replacement: "已脱敏",
  category: "自定义规则", scope: "all",
};
const navItems: Array<{ key: View; label: string; icon: typeof Files }> = [
  { key: "files", label: "文件中心", icon: Files },
  { key: "text", label: "文本脱敏", icon: FileSearch },
  { key: "review", label: "单文件复核", icon: FileSearch },
  { key: "batches", label: "批量任务", icon: Archive },
  { key: "history", label: "历史记录", icon: History },
  { key: "rules", label: "识别规则", icon: Search },
  { key: "policies", label: "脱敏策略", icon: SlidersHorizontal },
  { key: "models", label: "本地模型", icon: Settings2 },
];

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function Button({ children, className, icon: Icon, tone = "plain", ...props }: {
  children?: ReactNode; className?: string; icon?: typeof Files; tone?: "plain" | "primary" | "danger";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} className={cx(
    "inline-flex min-h-9 items-center justify-center gap-2 rounded-md border px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
    tone === "primary" && "border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)] text-white",
    tone === "danger" && "border-red-500/40 text-red-500 hover:bg-red-500/10",
    tone === "plain" && "border-[var(--color-border-primary)] hover:bg-[var(--color-bg-secondary)]",
    className,
  )}>{Icon && <Icon size={15} />}{children}</button>;
}

function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return <label className={cx("flex cursor-pointer items-center gap-2 text-xs", disabled && "opacity-50")}>
    <input className="sr-only" type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
    <span className={cx("relative h-5 w-9 rounded-full border transition-colors", checked ? "border-emerald-500 bg-emerald-500" : "border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]")}>
      <span className={cx("absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white transition-transform", checked ? "translate-x-[17px]" : "translate-x-0.5")} />
    </span>{label}
  </label>;
}

function Status({ value }: { value?: string }) {
  const good = value === "ready" || value === "completed";
  const bad = value === "failed" || value === "error" || value === "partial";
  const labels: Record<string, string> = {
    queued: "排队中", analyzing: "分析中", running: "执行中", awaiting_review: "等待复核",
    completed: "已完成", partial: "部分完成", failed: "失败", cancelled: "已取消",
    ready: "已就绪", starting: "初始化中", initializing: "初始化中", retrying: "重试中", error: "异常",
  };
  return <span className={cx("inline-flex items-center gap-1.5 text-xs", good && "text-emerald-600", bad && "text-red-500")}>
    <span className={cx("h-1.5 w-1.5 rounded-full", good ? "bg-emerald-500" : bad ? "bg-red-500" : "bg-amber-500")} />
    {labels[value || ""] || value || "未知"}
  </span>;
}

function serviceLabel(model: ModelStatus | null): string {
  if (model?.status === "error" || model?.status === "failed") return "初始化失败";
  switch (model?.service_phase) {
    case "installing":
    case "starting": return "正在初始化";
    case "ready": return model.status === "ready" ? "已就绪" : "正在初始化";
    case "failed": return "初始化失败";
    default: return "正在检查运行环境";
  }
}

function serviceReady(model: ModelStatus | null): boolean {
  return model?.service_phase === "ready" && model.status === "ready";
}

function modelInitializing(model: ModelStatus | null): boolean {
  return Boolean(model && model.status !== "ready" && model.status !== "error" && model.status !== "failed" && model.service_phase !== "failed");
}

function serviceErrorLabel(model: ModelStatus | null): string {
  const code = model?.service_error_code || model?.error || "";
  const labels: Record<string, string> = {
    desensitization_component_missing: "脱敏组件文件缺失",
    desensitization_uv_missing: "未找到 uv",
    desensitization_python_missing: "需要 Python 3.12 x64",
    desensitization_install_failed: "组件依赖安装失败",
    desensitization_service_unavailable: "脱敏服务未能启动",
  };
  const detail = String(model?.service_error_message || "").trim();
  const label = labels[code];
  if (label && detail && detail !== code) return `${label}：${detail}`;
  return label || detail || (code ? "初始化失败" : "");
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="grid gap-1.5 text-xs text-[var(--color-text-secondary)]"><span>{label}</span>{children}</label>;
}

const inputClass = "min-h-9 w-full rounded-md border border-[var(--color-border-primary)] bg-transparent px-2.5 text-sm outline-none focus:border-[var(--color-accent-primary)]";

function formatTime(value?: number) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(new Date(value < 10_000_000_000 ? value * 1000 : value));
}

function updateAnalysisEntity(current: Analysis, index: number, checked: boolean): Analysis {
  return { ...current, entities: current.entities?.map((item, itemIndex) => itemIndex === index ? { ...item, selected: checked } : item) };
}

function updateAnalysisBox(current: Analysis, index: number, checked: boolean): Analysis {
  return { ...current, boxes: current.boxes?.map((item, itemIndex) => itemIndex === index ? { ...item, selected: checked } : item) };
}

export function DesensitizationWorkspace({ groupId }: { groupId: string }) {
  const [view, setView] = useState<View>("files");
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [metadata, setMetadata] = useState<WorkspaceMetadata | null>(null);
  const [pendingBatch, setPendingBatch] = useState<{ files: File[]; skipped: number; groupId: string; scopeRoot: string; scopeKey: string } | null>(null);
  const [outputRelativePath, setOutputRelativePath] = useState("");
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<Batch | null>(null);
  const [mode, setMode] = useState<"auto" | "review">("auto");
  const [reversible, setReversible] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [rules, setRules] = useState<Rule[]>([]);
  const [builtinRules, setBuiltinRules] = useState<Rule[]>([]);
  const [nerEnabled, setNerEnabled] = useState(true);
  const [categories, setCategories] = useState<RuleCategory[]>([]);
  const [categoryName, setCategoryName] = useState("");
  const [ruleDraft, setRuleDraft] = useState<RuleDraft>(defaultRule);
  const [editingRule, setEditingRule] = useState<string | null>(null);
  const [policies, setPolicies] = useState<Record<string, Policy>>({});
  const [restoreTask, setRestoreTask] = useState<Task | null>(null);
  const [restorePassword, setRestorePassword] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [maskedPreview, setMaskedPreview] = useState<{ view?: Analysis["source_view"]; previewId: string; url?: string } | null>(null);
  useEffect(() => { setMaskedPreview(null); }, [analysis]);
  useEffect(() => () => { if (maskedPreview?.url) URL.revokeObjectURL(maskedPreview.url); }, [maskedPreview]);
  const previewObjectRef = useRef<string>("");
  const [textSource, setTextSource] = useState("");
  const [textEntities, setTextEntities] = useState<Entity[]>([]);
  const [textResult, setTextResult] = useState("");
  const [textTaskId, setTextTaskId] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const modelJobTimerRef = useRef<number | null>(null);
  const modelJobInFlightRef = useRef(false);
  const modelPollTimerRef = useRef<number | null>(null);
  const modelPollInFlightRef = useRef(false);
  const groupEpochRef = useRef(0);
  const modelRef = useRef<ModelStatus | null>(null);
  const workspaceLoadRef = useRef<Promise<void> | null>(null);
  const installLogRef = useRef<HTMLPreElement>(null);
  const [statusPollNonce, setStatusPollNonce] = useState(0);
  const ready = serviceReady(model);
  // Business endpoints are intentionally held until both the Sidecar and its
  // models are ready.  A healthy HTTP process alone is not enough to analyze
  // or mutate workspace data.
  const sidecarReady = serviceReady(model);

  const reportError = useCallback((reason: unknown, fallback: string) => {
    setMessage(""); setError(reason instanceof Error ? reason.message : fallback);
  }, []);

  const setModelSnapshot = useCallback((next: ModelStatus | null) => {
    modelRef.current = next;
    setModel(next);
  }, []);

  const loadWorkspaceData = useCallback(async (epoch: number) => {
    if (!groupId || epoch !== groupEpochRef.current || !serviceReady(modelRef.current)) return;
    // A status poll and a user-triggered refresh can arrive together. Share
    // one read so a slow Sidecar never receives overlapping workspace loads.
    if (workspaceLoadRef.current) return workspaceLoadRef.current;
    const request = (async () => {
      const results = await Promise.allSettled([
        desensitizationApi.metadata(groupId),
        desensitizationApi.tasks(groupId),
        desensitizationApi.batches(groupId),
      ]);
      if (epoch !== groupEpochRef.current) return;
      const [metadataResult, tasksResult, batchesResult] = results;
      if (metadataResult.status === "fulfilled") setMetadata(metadataResult.value);
      if (tasksResult.status === "fulfilled") setTasks(tasksResult.value.items || []);
      if (batchesResult.status === "fulfilled") setBatches(batchesResult.value.items || []);
      const failure = results.find((item) => item.status === "rejected") as PromiseRejectedResult | undefined;
      if (failure) reportError(failure.reason, "工作台数据读取失败");
    })();
    workspaceLoadRef.current = request;
    // A request from the previous group may finish after a new group's load
    // has started. Only that request is allowed to clear the shared slot.
    return request.finally(() => {
      if (workspaceLoadRef.current === request) workspaceLoadRef.current = null;
    });
  }, [groupId, reportError]);

  const loadCore = useCallback(async (epoch = groupEpochRef.current, refreshWorkspace = true): Promise<ModelStatus | null> => {
    if (!groupId || epoch !== groupEpochRef.current) return null;
    let nextModel: ModelStatus;
    try {
      nextModel = await desensitizationApi.modelStatus();
    } catch (reason) {
      if (epoch !== groupEpochRef.current) return null;
      const failure = reason as ApiFailure;
      const current = modelRef.current;
      const details = failure.details && typeof failure.details === "object" && !Array.isArray(failure.details)
        ? failure.details as Partial<ModelStatus>
        : {};
      // Preserve an in-progress install/start phase on transient transport
      // failures. A single dropped poll must not turn the full-screen loader
      // into a permanent failure state.
      const phase = details.service_phase || current?.service_phase || (failure.code === "NETWORK_ERROR" ? "starting" : "failed");
      const fallback: ModelStatus = {
        ...(current || { status: phase === "failed" ? "error" : "starting" }),
        ...details,
        status: details.status || (current?.status === "ready" ? "ready" : phase === "failed" ? "error" : (current?.status || "starting")),
        service_phase: phase,
        service_error_code: details.service_error_code || failure.code || current?.service_error_code || "desensitization_service_unavailable",
        service_error_message: details.service_error_message || failure.message || current?.service_error_message || "脱敏服务状态读取失败",
        service_retryable: details.service_retryable ?? current?.service_retryable ?? true,
      };
      setModelSnapshot(fallback);
      reportError(reason, "脱敏服务状态读取失败");
      return fallback;
    }
    if (epoch !== groupEpochRef.current) return null;
    setModelSnapshot(nextModel);
    if (refreshWorkspace && serviceReady(nextModel)) await loadWorkspaceData(epoch);
    return nextModel;
  }, [groupId, loadWorkspaceData, reportError, setModelSnapshot]);

  const loadRules = useCallback(async () => {
    const epoch = groupEpochRef.current;
    if (!groupId || !serviceReady(modelRef.current)) return;
    try {
      const [result, categoryResult] = await Promise.all([desensitizationApi.rules(groupId), desensitizationApi.ruleCategories(groupId)]);
      if (epoch !== groupEpochRef.current) return;
      setRules(result.rules || []); setBuiltinRules(result.builtin_rules || []); setNerEnabled(result.ner_enabled !== false);
      setCategories(categoryResult.categories || []);
    } catch (reason) { reportError(reason, "规则读取失败"); }
  }, [groupId, reportError]);

  const loadPolicies = useCallback(async () => {
    const epoch = groupEpochRef.current;
    if (!groupId || !serviceReady(modelRef.current)) return;
    try {
      const result = await desensitizationApi.policies(groupId);
      if (epoch === groupEpochRef.current) setPolicies(result.policies || {});
    }
    catch (reason) { reportError(reason, "策略读取失败"); }
  }, [groupId, reportError]);

  useEffect(() => {
    const epoch = ++groupEpochRef.current;
    if (modelJobTimerRef.current !== null) {
      window.clearTimeout(modelJobTimerRef.current);
      modelJobTimerRef.current = null;
    }
    if (modelPollTimerRef.current !== null) {
      window.clearTimeout(modelPollTimerRef.current);
      modelPollTimerRef.current = null;
    }
    modelJobInFlightRef.current = false;
    modelPollInFlightRef.current = false;
    workspaceLoadRef.current = null;
    setModelSnapshot(null); setMetadata(null); setAnalysis(null); setTasks([]); setBatches([]);
    setSelectedBatch(null); setRules([]); setBuiltinRules([]); setCategories([]); setPolicies({});
    setMessage(""); setError(""); setTextEntities([]); setTextResult(""); setTextTaskId("");

    let cancelled = false;
    const poll = async () => {
      if (cancelled || epoch !== groupEpochRef.current || modelPollInFlightRef.current) return;
      modelPollInFlightRef.current = true;
      let next: ModelStatus | null = null;
      try {
        next = await loadCore(epoch, true);
      } finally {
        modelPollInFlightRef.current = false;
      }
      if (cancelled || epoch !== groupEpochRef.current) return;
      const keepPolling = !next || next.service_phase === "installing" || next.service_phase === "starting" ||
        (next.service_phase === "ready" && next.status !== "ready" && next.status !== "error" && next.status !== "failed");
      if (keepPolling) modelPollTimerRef.current = window.setTimeout(() => { void poll(); }, 1000);
      else modelPollTimerRef.current = null;
    };
    void poll();
    return () => {
      cancelled = true;
      if (modelPollTimerRef.current !== null) {
        window.clearTimeout(modelPollTimerRef.current);
        modelPollTimerRef.current = null;
      }
    };
  }, [groupId, loadCore, setModelSnapshot, statusPollNonce]);
  useEffect(() => { if (view === "rules" && ready) void loadRules(); }, [loadRules, ready, view]);
  useEffect(() => { if (view === "policies" && ready) void loadPolicies(); }, [loadPolicies, ready, view]);
  useEffect(() => { folderRef.current?.setAttribute("webkitdirectory", ""); }, []);

  useEffect(() => {
    const log = installLogRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [model?.install_log_tail, model?.last_log_line]);

  useEffect(() => {
    const id = selectedBatch?.id || selectedBatch?.batch_id;
    if (!id || !sidecarReady || terminalBatchStatuses.has(selectedBatch?.status || "") || selectedBatch?.status === "awaiting_review") return;
    const epoch = groupEpochRef.current;
    let cancelled = false;
    let timer: number | null = null;
    let inFlight = false;
    const poll = async () => {
      if (cancelled || inFlight || epoch !== groupEpochRef.current) return;
      inFlight = true;
      try {
        const next = await desensitizationApi.batch(groupId, id);
        if (cancelled || epoch !== groupEpochRef.current) return;
        setSelectedBatch(next);
        if (terminalBatchStatuses.has(next.status) || next.status === "awaiting_review") void loadCore(epoch, true);
        else timer = window.setTimeout(() => { void poll(); }, 1500);
      } catch (reason) {
        if (!cancelled) reportError(reason, "批次状态读取失败");
        timer = cancelled ? null : window.setTimeout(() => { void poll(); }, 2000);
      } finally { inFlight = false; }
    };
    timer = window.setTimeout(() => { void poll(); }, 1500);
    return () => { cancelled = true; if (timer !== null) window.clearTimeout(timer); };
  }, [groupId, loadCore, reportError, sidecarReady, selectedBatch?.batch_id, selectedBatch?.id, selectedBatch?.status]);

  useEffect(() => {
    let active = true; let objectUrl = ""; if (previewObjectRef.current) { URL.revokeObjectURL(previewObjectRef.current); previewObjectRef.current = ""; } setPreviewUrl("");
    const url = analysis?.source_view?.url || analysis?.source_preview;
    if (!url || analysis?.source_view?.kind === "text") return;
    void desensitizationApi.blob(url, analysis?.filename || "preview").then(({ blob }) => {
      if (!active) return; objectUrl = URL.createObjectURL(blob); previewObjectRef.current = objectUrl; setPreviewUrl(objectUrl);
    }).catch((reason) => { if (active) reportError(reason, "预览读取失败"); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [analysis?.analysis_id, analysis?.filename, analysis?.source_preview, analysis?.source_view?.type, analysis?.source_view?.kind, analysis?.source_view?.url, reportError]);

  useEffect(() => () => {
    if (modelJobTimerRef.current !== null) window.clearTimeout(modelJobTimerRef.current);
    if (modelPollTimerRef.current !== null) window.clearTimeout(modelPollTimerRef.current);
  }, []);

  const options = useMemo(() => ({ reversible, ...(reversible ? { password } : {}) }), [password, reversible]);
  const requirePassword = () => {
    if (reversible && password.length < 12) { setError("可逆脱敏口令至少需要 12 个字符"); return false; }
    return true;
  };

  const requireReady = () => {
    if (ready) return true;
    setError(serviceLabel(model));
    return false;
  };

  const requireService = () => {
    if (sidecarReady) return true;
    setError(serviceLabel(model));
    return false;
  };

  const retryInitialization = useCallback(async () => {
    setError("");
    try {
      if (modelRef.current?.service_phase !== "ready") {
        await desensitizationApi.retryService();
        setStatusPollNonce((value) => value + 1);
        return;
      }
      const result = await desensitizationApi.initializeModels();
      const jobId = String(result.job_id || "").trim();
      if (!jobId) throw new Error("模型初始化未返回任务编号");
      const epoch = groupEpochRef.current;
      setModelSnapshot({
        ...(modelRef.current || {}),
        status: "retrying",
        service_phase: "ready",
        progress: 0,
        message: "正在重新初始化本地模型",
        service_error_code: undefined,
        service_error_message: undefined,
      } as ModelStatus);
      const pollJob = async () => {
        if (epoch !== groupEpochRef.current || modelJobInFlightRef.current) return;
        modelJobInFlightRef.current = true;
        try {
          const job = await desensitizationApi.modelJob(jobId);
          if (epoch !== groupEpochRef.current) return;
          if (job.status === "completed") {
            await loadCore(epoch, true);
            return;
          }
          if (["error", "failed", "not_found"].includes(job.status)) {
            const missing = job.status === "not_found";
            setModelSnapshot({
              ...(modelRef.current || {}),
              ...job,
              status: "error",
              service_phase: "ready",
              service_error_code: missing ? "desensitization_model_job_missing" : job.error || "desensitization_model_init_failed",
              service_error_message: job.message || job.ner_error || job.ocr_error || (missing ? "模型初始化任务已失效，请重试" : "模型初始化失败"),
              service_retryable: true,
            } as ModelStatus);
            return;
          }
          setModelSnapshot({ ...(modelRef.current || {}), ...job, service_phase: "ready" } as ModelStatus);
          modelJobTimerRef.current = window.setTimeout(() => { void pollJob(); }, 1000);
        } catch (reason) {
          if (epoch === groupEpochRef.current) reportError(reason, "模型状态读取失败");
        } finally {
          modelJobInFlightRef.current = false;
        }
      };
      void pollJob();
    } catch (reason) {
      reportError(reason, "重试失败");
    }
  }, [loadCore, reportError, setModelSnapshot]);

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; event.target.value = ""; if (!file) return;
    if (!requireReady()) return;
    setBusy(true); setError("");
    try { setAnalysis(await desensitizationApi.analyzeFile(groupId, file)); setView("review"); }
    catch (reason) { reportError(reason, "分析失败"); } finally { setBusy(false); }
  };

  const onFolder = async (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files || []); event.target.value = "";
    const visible = chosen.filter((file) => !(file.webkitRelativePath || file.name).split("/").some((part) => part.startsWith(".")));
    const files = visible.filter((file) => supported.has(file.name.split(".").pop()?.toLowerCase() || ""));
    if (!files.length) { setError("文件夹中没有支持的文件"); return; }
    if (!requireReady()) return;
    if (!requirePassword()) return;
    if (!metadata?.scope_root) { setError("工作目录尚未加载"); return; }
    setOutputRelativePath(`脱敏后/批次-${new Date().toISOString().replace(/\D/g, "")}-${crypto.randomUUID().slice(0, 8)}`);
    setPendingBatch({ files, skipped: chosen.length - files.length, groupId, scopeRoot: metadata.scope_root, scopeKey: metadata.scope_key || "" });
  };

  const confirmBatch = async () => {
    if (!pendingBatch || busy) return;
    if (pendingBatch.groupId !== groupId) { setPendingBatch(null); setError("工作组已切换，请重新选择文件夹"); return; }
    setBusy(true); setError("");
    try {
      const created = await desensitizationApi.createBatch(groupId, pendingBatch.files, mode, { ...options, output_relative_path: outputRelativePath, scope_root: pendingBatch.scopeRoot, scope_key: pendingBatch.scopeKey });
      setSelectedBatch(created); setView("batches");
      const skipped = pendingBatch.skipped;
      setPendingBatch(null);
      setMessage(skipped ? `批次已提交，跳过 ${skipped} 个隐藏或不支持的文件` : "批次已提交");
      await loadCore();
    } catch (reason) { reportError(reason, "批次提交失败"); } finally { setBusy(false); }
  };

  const executeFile = async () => {
    if (!analysis || !requireReady() || !requirePassword()) return;
    setBusy(true); setError("");
    try {
      await desensitizationApi.maskFile(groupId, {
        analysis_id: analysis.analysis_id, filename: analysis.filename, entities: analysis.entities,
        boxes: analysis.boxes, embedded_images: analysis.embedded_images, reversible, password: reversible ? password : undefined,
      });
      setMessage("脱敏文件已保存到当前工作 scope 的“脱敏后”文件夹"); await loadCore(); setView("history");
    } catch (reason) { reportError(reason, "脱敏失败"); } finally { setBusy(false); }
  };

  const openBatch = async (batch: Batch) => {
    const id = batch.id || batch.batch_id; if (!id || !requireService()) return; setBusy(true);
    try { setSelectedBatch(await desensitizationApi.batch(groupId, id)); }
    catch (reason) { reportError(reason, "批次读取失败"); } finally { setBusy(false); }
  };

  const setBatchItemEntity = (sequence: number, entityIndex: number, checked: boolean) => {
    setSelectedBatch((current) => current ? {
      ...current, items: current.items?.map((item) => item.sequence === sequence && item.analysis ? {
        ...item, analysis: updateAnalysisEntity(item.analysis, entityIndex, checked),
      } : item),
    } : current);
  };

  const setBatchItemBox = (sequence: number, boxIndex: number, checked: boolean) => {
    setSelectedBatch((current) => current ? { ...current, items: current.items?.map((item) => item.sequence === sequence && item.analysis ? { ...item, analysis: updateAnalysisBox(item.analysis, boxIndex, checked) } : item) } : current);
  };

  const executeBatch = async () => {
    const id = selectedBatch?.id || selectedBatch?.batch_id; if (!id || !requireReady() || !requirePassword()) return;
    setBusy(true);
    try {
      const items = (selectedBatch?.items || []).map((item) => ({ task_id: item.task_id, entities: item.analysis?.entities, boxes: item.analysis?.boxes, embedded_images: item.analysis?.embedded_images }));
      setSelectedBatch(await desensitizationApi.executeBatch(groupId, id, { items, ...options })); setMessage("批次已进入执行队列");
    } catch (reason) { reportError(reason, "批次执行失败"); } finally { setBusy(false); }
  };

  const saveRule = async () => {
    if (!requireService()) return;
    if (!ruleDraft.name.trim() || !ruleDraft.pattern?.trim()) { setError("规则名称和匹配内容不能为空"); return; }
    setBusy(true);
    try {
      if (editingRule) await desensitizationApi.updateRule(groupId, editingRule, ruleDraft);
      else await desensitizationApi.createRule(groupId, ruleDraft);
      setRuleDraft(defaultRule); setEditingRule(null); setMessage("规则已保存"); await loadRules();
    } catch (reason) { reportError(reason, "规则保存失败"); } finally { setBusy(false); }
  };

  const savePolicies = async () => {
    if (!requireService()) return;
    setBusy(true);
    try { await desensitizationApi.putPolicies(groupId, policies); setMessage("策略已保存"); }
    catch (reason) { reportError(reason, "策略保存失败"); } finally { setBusy(false); }
  };

  const doDownload = async (url: string, fallback: string) => {
    if (!requireService()) return;
    try { await desensitizationApi.download(groupId, url, fallback); }
    catch (reason) { reportError(reason, "下载失败"); }
  };

  const analyzeText = async () => {
    if (!requireReady()) return;
    if (!textSource.trim()) { setError("请先输入文本"); return; }
    setBusy(true); setError("");
    try { const result = await desensitizationApi.textAnalyze(groupId, textSource); setTextEntities((result.entities || []).map((item) => ({ ...item, selected: item.selected !== false }))); setMessage("文本识别完成"); }
    catch (reason) { reportError(reason, "文本识别失败"); } finally { setBusy(false); }
  };

  const maskText = async () => {
    if (!requireReady()) return;
    if (!textSource.trim()) { setError("请先输入文本"); return; }
    setBusy(true); setError("");
    try { const result = await desensitizationApi.textMask(groupId, textSource, textEntities, policies); setTextResult(String(result.masked_text || "")); setTextTaskId(String(result.task_id || "")); setMessage("文本脱敏完成"); await loadCore(); }
    catch (reason) { reportError(reason, "文本脱敏失败"); } finally { setBusy(false); }
  };

  if (!groupId) return <div className="flex h-full items-center justify-center text-sm">请先选择工作组</div>;
  const batchId = selectedBatch?.id || selectedBatch?.batch_id;
  const outputPath = metadata?.output_dir || metadata?.output_path || "正在读取";
  const initializationFailed = Boolean(model && (model.service_phase === "failed" || model.status === "error" || model.status === "failed"));
  const initializing = !initializationFailed && !ready;

  return <div className="flex min-h-0 flex-1 flex-col bg-[var(--color-bg-primary)] text-[var(--color-text-primary)]">
    {pendingBatch && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"><section role="dialog" aria-modal="true" aria-labelledby="batch-output-title" className="w-full max-w-lg space-y-4 rounded-md bg-[var(--color-bg-primary)] p-5"><h2 id="batch-output-title" className="text-sm font-semibold">确认保存目录</h2><p className="break-all text-sm">本次脱敏的数据将保存在 {pendingBatch.scopeRoot}/{outputRelativePath} 文件夹</p><label className="block text-xs">相对子目录<input autoFocus className={cx(inputClass, "mt-2 w-full")} value={outputRelativePath} disabled={busy} onChange={(event) => setOutputRelativePath(event.target.value)} /></label>{error && <p role="alert" className="text-sm text-red-600">{error}</p>}<div className="flex justify-end gap-2"><Button disabled={busy} onClick={() => setPendingBatch(null)}>取消</Button><Button tone="primary" disabled={busy || !outputRelativePath.trim()} onClick={() => void confirmBatch()}>确认开始</Button></div></section></div>}
    <aside className="shrink-0 border-b border-[var(--color-border-primary)]">
      <div className="flex items-center gap-2 px-4 py-3 text-sm font-semibold"><ShieldCheck size={18} />数据脱敏</div>
      <nav className="flex gap-1 overflow-x-auto px-2 pb-2">
        {navItems.map(({ key, label, icon: Icon }) => <button key={key} onClick={() => setView(key)} className={cx(
          "flex min-h-9 shrink-0 items-center gap-2 rounded-md px-3 text-xs transition-colors",
          view === key ? "bg-[var(--color-bg-secondary)] font-medium" : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)]",
        )}><Icon size={15} />{label}</button>)}
      </nav>
    </aside>

    <main className="min-w-0 flex-1 overflow-auto">
      <header className="sticky top-0 z-10 flex min-h-14 items-center justify-between border-b border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] px-4 md:px-6">
        <div><h1 className="text-sm font-semibold">{navItems.find((item) => item.key === view)?.label}</h1><p className="max-w-[50vw] truncate text-[11px] text-[var(--color-text-muted)]" title={outputPath}>输出：{outputPath}</p></div>
        <button title="刷新" aria-label="刷新" className="flex items-center gap-2 rounded-md p-2 text-xs hover:bg-[var(--color-bg-secondary)]" onClick={() => void loadCore()}><Status value={model?.status} /><RefreshCw size={14} /></button>
      </header>

      <div className="mx-auto w-full max-w-[1440px] p-4 md:p-6">
        {model && (!ready || model.service_phase === "failed") && <div className={cx("mb-4 rounded-md border p-3 text-xs", initializationFailed ? "border-red-500/30 bg-red-500/5 text-red-500" : "border-amber-500/30 bg-amber-500/5")}><div className="flex items-center justify-between gap-3"><span className="font-medium">{modelInitializing(model) ? "正在初始化" : serviceLabel(model)}</span>{initializationFailed && model.service_retryable && <Button icon={RefreshCw} onClick={() => void retryInitialization()}>重试</Button>}</div>{model.install_step && <p className="mt-1">{model.install_step}</p>}{model.last_log_line && <p className="mt-1 truncate text-[var(--color-text-muted)]" title={model.last_log_line}>{model.last_log_line}</p>}{serviceErrorLabel(model) && <p className="mt-1">{serviceErrorLabel(model)}</p>}{model.ner_error && <p className="mt-1">NER：{model.ner_error}</p>}{model.ocr_error && <p className="mt-1">OCR：{model.ocr_error}</p>}</div>}
        {(message || error) && <div className={cx("mb-4 flex items-start justify-between rounded-md border px-3 py-2 text-xs", error ? "border-red-500/30 bg-red-500/5 text-red-500" : "border-emerald-500/30 bg-emerald-500/5 text-emerald-600")}><span>{error || message}</span><button aria-label="关闭提示" onClick={() => { setError(""); setMessage(""); }}><X size={14} /></button></div>}

        {view === "files" && <FileCenter model={model} ready={ready} busy={busy} tasks={tasks} batches={batches} mode={mode} reversible={reversible} password={password} fileRef={fileRef} folderRef={folderRef} onMode={setMode} onReversible={setReversible} onPassword={setPassword} onFile={onFile} onFolder={onFolder} />}

        {view === "text" && <TextView ready={ready} source={textSource} entities={textEntities} result={textResult} busy={busy} onSource={setTextSource} onEntities={setTextEntities} onAnalyze={() => void analyzeText()} onMask={() => void maskText()} onRestore={async () => { if (!textTaskId || !requireService()) return; try { const restored = await desensitizationApi.textRestore(groupId, textTaskId, textResult); setTextResult(restored.text); setMessage("文本已恢复"); } catch (reason) { reportError(reason, "文本恢复失败"); } }} />}

        {view === "review" && <ReviewView maskedPreview={maskedPreview} ready={ready} analysis={analysis} previewUrl={maskedPreview?.url || previewUrl} busy={busy} onChange={setAnalysis} onExecute={executeFile} onPreview={async () => {
          if (!analysis || !requireReady()) return; setBusy(true);
          try {
            const result = await desensitizationApi.previewFile(groupId, analysis.analysis_id, { filename: analysis.filename, entities: analysis.entities, boxes: analysis.boxes, embedded_images: analysis.embedded_images });
            const view = result.masked_view as Analysis["source_view"];
            const remoteUrl = view?.url || String(result.preview_url || "");
            let url: string | undefined;
            if (remoteUrl && !["document", "spreadsheet", "text"].includes(view?.type || view?.kind || "")) {
              const loaded = await desensitizationApi.blob(remoteUrl, `preview-${analysis.filename}`);
              url = URL.createObjectURL(loaded.blob);
            }
            setMaskedPreview({ view, previewId: String(result.preview_id || ""), url });
            setMessage("预览已生成");
          }
          catch (reason) { reportError(reason, "预览生成失败"); } finally { setBusy(false); }
        }} />}

        {view === "batches" && <section className="grid min-h-[520px] gap-4 lg:grid-cols-[330px_1fr]">
          <div className="border-r-0 border-[var(--color-border-primary)] lg:border-r lg:pr-4"><div className="mb-3 flex items-center justify-between"><p className="text-xs font-medium">全部批次</p><span className="text-[11px] text-[var(--color-text-muted)]">{batches.length}</span></div><div className="space-y-1">{batches.map((item) => { const id = item.id || item.batch_id || ""; return <button key={id} onClick={() => void openBatch(item)} className={cx("flex w-full items-center gap-3 rounded-md px-3 py-3 text-left hover:bg-[var(--color-bg-secondary)]", batchId === id && "bg-[var(--color-bg-secondary)]")}><Archive size={15} /><span className="min-w-0 flex-1"><span className="block truncate text-xs">{item.root_name || `批次 ${id.slice(0, 8)}`}</span><span className="text-[11px] text-[var(--color-text-muted)]">{formatTime(item.created)} · {item.total} 个文件</span></span><ChevronRight size={14} /></button>; })}{!batches.length && <p className="py-10 text-center text-xs text-[var(--color-text-muted)]">尚无批次</p>}</div></div>
          <BatchDetail onAnalysis={(sequence, value) => setSelectedBatch((current) => current ? { ...current, items: current.items?.map((item) => item.sequence === sequence ? { ...item, analysis: value } : item) } : current)} ready={ready} batch={selectedBatch} busy={busy} reversible={reversible} password={password} onReversible={setReversible} onPassword={setPassword} onEntity={setBatchItemEntity} onBox={setBatchItemBox} onExecute={() => void executeBatch()} onCancel={async () => {
            if (!batchId || !requireService()) return; try { setSelectedBatch(await desensitizationApi.cancelBatch(groupId, batchId)); } catch (reason) { reportError(reason, "取消失败"); }
          }} onDownload={(kind) => batchId && void doDownload(desensitizationApi.batchUrl(groupId, batchId, kind), kind === "download" ? `desensitization-${batchId.slice(0, 8)}.zip` : "report.json")} />
        </section>}

        {view === "history" && <HistoryView tasks={tasks} onRefresh={async () => { await loadCore(); }} onOpen={async (task) => {
          if (!requireService()) return; try { setAnalysis(await desensitizationApi.taskAnalysis(groupId, task.id)); setView("review"); } catch (reason) { reportError(reason, "记录打开失败"); }
        }} onDownload={(task, kind) => void doDownload(desensitizationApi.taskUrl(groupId, task.id, kind), kind === "report" ? `${task.original_name}.report.json` : task.original_name)} onRestore={(task) => { if (requireService()) { setRestoreTask(task); setRestorePassword(""); } }} onDelete={async (task) => { if (!requireService()) return; try { await desensitizationApi.deleteTask(groupId, task.id); await loadCore(); } catch (reason) { reportError(reason, "删除失败"); } }} />}

        {view === "rules" && <RulesView groupId={groupId} ready={sidecarReady} rules={rules} builtinRules={builtinRules} nerEnabled={nerEnabled} categories={categories} categoryName={categoryName} draft={ruleDraft} editingRule={editingRule} busy={busy} onCategoryName={setCategoryName} onDraft={setRuleDraft} onSave={() => void saveRule()} onCancelEdit={() => { setEditingRule(null); setRuleDraft(defaultRule); }} onEdit={(rule) => { setEditingRule(rule.id); setRuleDraft({ name: rule.name, kind: rule.kind, pattern: rule.pattern || "", entity_type: rule.entity_type || "CUSTOM", replacement: rule.replacement || "已脱敏", category: rule.category || "自定义规则", scope: rule.scope || "all" }); }} onModel={async (enabled) => { if (!requireService()) return; try { await desensitizationApi.toggleModelRule(groupId, enabled); setNerEnabled(enabled); } catch (reason) { reportError(reason, "模型规则更新失败"); } }} onBuiltin={async (rule, enabled) => { if (!requireService()) return; try { await desensitizationApi.toggleBuiltinRule(groupId, rule.id, enabled); await loadRules(); } catch (reason) { reportError(reason, "内置规则更新失败"); } }} onToggleRule={async (rule, enabled) => { if (!requireService()) return; try { await desensitizationApi.updateRule(groupId, rule.id, { enabled }); await loadRules(); } catch (reason) { reportError(reason, "规则更新失败"); } }} onDeleteRule={async (rule) => { if (!requireService()) return; try { await desensitizationApi.deleteRule(groupId, rule.id); await loadRules(); } catch (reason) { reportError(reason, "规则删除失败"); } }} onCreateCategory={async () => { if (!requireService() || !categoryName.trim()) return; try { await desensitizationApi.createRuleCategory(groupId, categoryName.trim()); setCategoryName(""); await loadRules(); } catch (reason) { reportError(reason, "分类创建失败"); } }} onDeleteCategory={async (category) => { if (!requireService()) return; try { await desensitizationApi.deleteRuleCategory(groupId, category.id); await loadRules(); } catch (reason) { reportError(reason, "分类删除失败"); } }} />}

        {view === "policies" && <PoliciesView ready={sidecarReady} policies={policies} busy={busy} onChange={setPolicies} onSave={() => void savePolicies()} onReset={async () => { if (!requireService()) return; try { await desensitizationApi.resetPolicies(groupId); await loadPolicies(); } catch (reason) { reportError(reason, "策略重置失败"); } }} />}

        {view === "models" && <ModelsView model={model} onRefresh={() => { void loadCore(); }} onInitialize={() => void retryInitialization()} />}
      </div>
    </main>

    {restoreTask && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="恢复脱敏文件"><div className="w-full max-w-sm rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] p-5 shadow-xl"><div className="mb-4 flex items-center justify-between"><p className="text-sm font-semibold">恢复文件</p><button aria-label="关闭" onClick={() => setRestoreTask(null)}><X size={16} /></button></div><p className="mb-4 truncate text-xs text-[var(--color-text-secondary)]">{restoreTask.original_name}</p><Field label="恢复口令"><input autoFocus className={inputClass} type="password" value={restorePassword} onChange={(event) => setRestorePassword(event.target.value)} /></Field><div className="mt-5 flex justify-end gap-2"><Button onClick={() => setRestoreTask(null)}>取消</Button><Button tone="primary" icon={KeyRound} disabled={restorePassword.length < 12} onClick={async () => { try { await desensitizationApi.restore(groupId, restoreTask.id, restorePassword); await doDownload(desensitizationApi.taskUrl(groupId, restoreTask.id, "restored"), `restored-${restoreTask.original_name}`); setRestoreTask(null); await loadCore(); } catch (reason) { reportError(reason, "恢复失败"); } }}>恢复并下载</Button></div></div></div>}
    {(initializing || initializationFailed) && <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[var(--color-bg-primary)]/95 p-4 backdrop-blur-sm sm:p-6" role="status" aria-live="polite" aria-busy={initializing}><div className="flex max-h-full w-full max-w-2xl flex-col"><div className="mb-4 flex items-center gap-3">{initializationFailed ? <Ban className="shrink-0 text-red-500" size={22} /> : <Loader2 className="shrink-0 animate-spin text-[var(--color-accent-primary)]" size={22} />}<div className="min-w-0"><h2 className="text-base font-semibold">{initializationFailed ? "初始化失败" : "正在初始化"}</h2><p className="mt-1 text-xs text-[var(--color-text-secondary)]">首次启动需要准备独立运行环境和本地模型，请稍候。</p></div></div><p className="mb-2 min-h-5 text-xs text-[var(--color-text-secondary)]">{model?.message || model?.install_step || (initializationFailed ? "请查看日志并重试" : "正在检查运行环境")}</p>{typeof (model?.progress ?? model?.install_progress) === "number" && <div className="mb-4 h-2 shrink-0 overflow-hidden rounded bg-[var(--color-bg-secondary)]"><div className="h-full bg-[var(--color-accent-primary)] transition-[width]" style={{ width: `${Math.max(2, Math.min(model?.status === "ready" ? 100 : 99, Number(model?.progress ?? model?.install_progress)))}%` }} /></div>}<pre ref={installLogRef} tabIndex={0} aria-label="初始化日志" className="min-h-40 flex-1 overflow-auto whitespace-pre-wrap break-words rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4 text-[11px] leading-relaxed text-[var(--color-text-secondary)]">{model?.install_log_tail?.length ? model.install_log_tail.join("\n") : model?.last_log_line || "正在等待初始化日志..."}</pre>{initializationFailed && <div className="mt-4 flex items-start justify-between gap-3"><p className="min-w-0 flex-1 break-words text-xs text-red-500">{serviceErrorLabel(model) || "初始化失败，请查看日志后重试"}</p>{model?.service_retryable && <Button icon={RefreshCw} onClick={() => void retryInitialization()}>重试</Button>}</div>}</div></div>}
  </div>;
}

function FileCenter({ model, ready, busy, tasks, batches, mode, reversible, password, fileRef, folderRef, onMode, onReversible, onPassword, onFile, onFolder }: {
  model: ModelStatus | null; ready: boolean; busy: boolean; tasks: Task[]; batches: Batch[]; mode: "auto" | "review"; reversible: boolean; password: string;
  fileRef: React.RefObject<HTMLInputElement>; folderRef: React.RefObject<HTMLInputElement>;
  onMode: (value: "auto" | "review") => void; onReversible: (value: boolean) => void; onPassword: (value: string) => void;
  onFile: (event: ChangeEvent<HTMLInputElement>) => void; onFolder: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return <section className="space-y-6"><div className="grid gap-4 border-b border-[var(--color-border-primary)] pb-6 lg:grid-cols-[1fr_320px]">
    <div className="flex min-h-72 flex-col items-center justify-center rounded-md border border-dashed border-[var(--color-border-primary)] px-5 text-center"><FolderOpen className="mb-4 text-[var(--color-text-muted)]" size={30} /><p className="text-sm font-medium">选择待脱敏文件或整个文件夹</p><p className="mt-1 text-xs text-[var(--color-text-muted)]">TXT、Markdown、Word、Excel、PDF 和常见图片</p><div className="mt-5 flex flex-wrap justify-center gap-2"><Button tone="primary" icon={Upload} disabled={busy || !ready} onClick={() => fileRef.current?.click()}>选择文件</Button><Button icon={FolderOpen} disabled={busy || !ready} onClick={() => folderRef.current?.click()}>选择文件夹</Button></div><input ref={fileRef} hidden type="file" accept=".txt,.md,.docx,.xlsx,.xlsm,.pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff" onChange={onFile} /><input ref={folderRef} hidden type="file" multiple onChange={onFolder} /></div>
    <div className="space-y-5 lg:border-l lg:border-[var(--color-border-primary)] lg:pl-5"><div><p className="mb-2 text-xs font-medium">文件夹处理方式</p><div className="grid grid-cols-2 rounded-md border border-[var(--color-border-primary)] p-0.5">{(["auto", "review"] as const).map((value) => <button key={value} onClick={() => onMode(value)} className={cx("min-h-8 rounded text-xs", mode === value && "bg-[var(--color-bg-secondary)] font-medium")}>{value === "auto" ? "自动执行" : "分析后复核"}</button>)}</div></div><Toggle checked={reversible} onChange={onReversible} label="启用可逆脱敏" />{reversible && <Field label="恢复口令（至少 12 个字符）"><input className={inputClass} type="password" autoComplete="new-password" value={password} onChange={(event) => onPassword(event.target.value)} /></Field>}<div className="border-t border-[var(--color-border-primary)] pt-4 text-xs text-[var(--color-text-secondary)]"><p>输出不会覆盖同名文件。</p><p className="mt-1">文件夹层级会在批次结果中保留。</p></div></div>
  </div><div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-[var(--color-border-primary)] bg-[var(--color-border-primary)] md:grid-cols-4">{[["支持格式", "12 种"], ["历史任务", String(tasks.length)], ["批次任务", String(batches.length)], ["模型状态", model?.status === "ready" ? "已就绪" : "待处理"]].map(([label, value]) => <div key={label} className="bg-[var(--color-bg-primary)] p-4"><p className="text-[11px] text-[var(--color-text-muted)]">{label}</p><p className="mt-1 text-sm font-medium">{value}</p></div>)}</div></section>;
}

function TextView({ ready, source, entities, result, busy, onSource, onEntities, onAnalyze, onMask, onRestore }: { ready: boolean; source: string; entities: Entity[]; result: string; busy: boolean; onSource: (value: string) => void; onEntities: (value: Entity[]) => void; onAnalyze: () => void; onMask: () => void; onRestore: () => void }) {
  return <section className="grid gap-5 xl:grid-cols-2"><div><Field label="原始文本"><textarea className={cx(inputClass, "min-h-64 resize-y py-2")} value={source} onChange={(event) => onSource(event.target.value)} placeholder="粘贴需要识别和脱敏的文本" /></Field><div className="mt-3 flex flex-wrap gap-2"><Button icon={FileSearch} disabled={!ready || busy || !source.trim()} onClick={onAnalyze}>识别敏感信息</Button><Button tone="primary" icon={Play} disabled={!ready || busy || !source.trim()} onClick={onMask}>执行脱敏</Button></div></div><div><Field label="脱敏结果"><textarea className={cx(inputClass, "min-h-64 resize-y py-2")} value={result} readOnly placeholder="脱敏结果将在这里显示" /></Field><div className="mt-3 flex items-center justify-between"><span className="text-xs text-[var(--color-text-muted)]">识别到 {entities.filter((item) => item.selected !== false).length} 项</span><Button icon={RotateCcw} disabled={!ready || busy || !result} onClick={onRestore}>恢复文本</Button></div><div className="mt-3 max-h-48 overflow-auto border-y border-[var(--color-border-primary)]">{entities.map((entity, index) => <EntityRow key={`${entity.start}-${entity.end}-${index}`} entity={entity} checked={entity.selected !== false} onChange={(checked) => onEntities(entities.map((item, itemIndex) => itemIndex === index ? { ...item, selected: checked } : item))} />)}{!entities.length && <p className="py-8 text-center text-xs text-[var(--color-text-muted)]">尚未识别文本实体</p>}</div></div></section>;
}

function previewType(analysis: Analysis): string {
  return String(analysis.source_view?.type || analysis.source_view?.kind || analysis.kind || "").toLowerCase();
}

function StructuredPreview({ view }: { view?: Analysis["source_view"] }) {
  const type = String(view?.type || view?.kind || "").toLowerCase();
  if (type === "spreadsheet") {
    const sheets = Array.isArray(view?.sheets) ? view.sheets : [];
    return <div className="space-y-4 text-xs">{sheets.map((rawSheet, sheetIndex) => {
      const sheet = rawSheet as Record<string, unknown>;
      const cells = Array.isArray(sheet.cells) ? sheet.cells : [];
      return <div key={`${String(sheet.name || "sheet")}-${sheetIndex}`} className="overflow-x-auto border border-[var(--color-border-primary)]"><div className="border-b border-[var(--color-border-primary)] px-3 py-2 text-xs font-medium">{String(sheet.name || `工作表 ${sheetIndex + 1}`)}{sheet.state && sheet.state !== "visible" ? "（隐藏）" : ""}</div><table className="w-full min-w-[420px] text-left"><thead><tr className="border-b border-[var(--color-border-primary)] text-[var(--color-text-muted)]"><th className="px-3 py-2 font-medium">单元格</th><th className="px-3 py-2 font-medium">内容</th><th className="px-3 py-2 font-medium">类型</th></tr></thead><tbody>{cells.map((rawCell, cellIndex) => { const cell = rawCell as Record<string, unknown>; return <tr key={`${String(cell.address || cellIndex)}-${cellIndex}`} className="border-b border-[var(--color-border-primary)] last:border-0"><td className="px-3 py-2">{String(cell.address || "")}</td><td className="max-w-[560px] whitespace-pre-wrap break-words px-3 py-2">{String(cell.value ?? "")}</td><td className="px-3 py-2 text-[var(--color-text-muted)]">{cell.kind === "formula" ? "公式" : "值"}</td></tr>; })}</tbody></table>{!cells.length && <p className="px-3 py-5 text-center text-[var(--color-text-muted)]">该工作表没有可显示内容</p>}</div>;
    })}{!sheets.length && <p className="py-10 text-center text-[var(--color-text-muted)]">该 Excel 文件没有可显示的工作表</p>}{view?.truncated && <p className="text-[11px] text-amber-600">内容较多，预览仅显示前一部分。</p>}</div>;
  }
  if (type === "document") {
    const blocks = Array.isArray(view?.blocks) ? view.blocks : [];
    return <div className="space-y-3 text-sm leading-6">{blocks.map((rawBlock, index) => { const block = rawBlock as Record<string, unknown>; const rows = Array.isArray(block.rows) ? block.rows : []; if (block.type === "table") return <div key={index} className="overflow-x-auto border border-[var(--color-border-primary)]"><table className="w-full text-left text-xs"><tbody>{rows.map((rawRow, rowIndex) => <tr key={rowIndex} className="border-b border-[var(--color-border-primary)] last:border-0">{(Array.isArray(rawRow) ? rawRow : []).map((value, cellIndex) => <td key={cellIndex} className="px-3 py-2 align-top">{String(value ?? "")}</td>)}</tr>)}</tbody></table></div>; const text = String(block.text || ""); if (!text) return null; return <p key={index} className={String(block.style || "").toLowerCase().startsWith("heading") ? "font-semibold" : "whitespace-pre-wrap"}>{text}</p>; })}{!blocks.length && <p className="py-10 text-center text-[var(--color-text-muted)]">该 Word 文件没有可显示的文字层</p>}{view?.truncated && <p className="text-[11px] text-amber-600">内容较多，预览仅显示前一部分。</p>}</div>;
  }
  return <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-6">{view?.text || "正在准备文档预览"}</pre>;
}

function ReviewView({ maskedPreview, ready, analysis, previewUrl, busy, onChange, onExecute, onPreview }: { maskedPreview?: { view?: Analysis["source_view"]; previewId: string; url?: string } | null; ready: boolean; analysis: Analysis | null; previewUrl: string; busy: boolean; onChange: (value: Analysis) => void; onExecute: () => void; onPreview: () => void }) {
  if (!analysis) return <div className="flex min-h-80 flex-col items-center justify-center text-center"><FileSearch className="mb-3 text-[var(--color-text-muted)]" size={28} /><p className="text-sm">请先在文件中心选择单个文件</p></div>;
  const selected = (analysis.entities || []).filter((item) => item.selected !== false).length + (analysis.boxes || []).filter((item) => item.selected !== false).length;
  const type = previewType(analysis);
  const extension = String(analysis.filename.split(".").pop() || "").toLowerCase();
  const image = new Set(["png", "jpg", "jpeg", "bmp", "tif", "tiff", "image"]).has(type) || new Set(["png", "jpg", "jpeg", "bmp", "tif", "tiff"]).has(extension);
  const pdf = type === "pdf" || extension === "pdf";
  const structured = type === "document" || type === "spreadsheet" || new Set(["docx", "xlsx", "xlsm"]).has(extension);
  const sourceView = maskedPreview?.view || analysis.source_view;
  const preview = previewUrl
    ? pdf
      ? <iframe title="PDF 预览" src={previewUrl} className="h-full min-h-[500px] w-full border-0 bg-white" />
      : image
        ? maskedPreview ? <img src={previewUrl} alt="脱敏结果" className="max-w-full" /> : <ImageReview analysis={analysis} src={previewUrl} onChange={onChange} />
        : structured
          ? <StructuredPreview view={sourceView} />
          : <img alt={analysis.filename} src={previewUrl} className="mx-auto max-h-full max-w-full object-contain" />
    : structured
      ? <StructuredPreview view={sourceView} />
      : <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-6">{sourceView?.text || analysis.text || "正在准备文档预览"}</pre>;
  return <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
    <div className="min-h-[520px] overflow-hidden rounded-md border border-[var(--color-border-primary)]">
      <div className="flex items-center justify-between border-b border-[var(--color-border-primary)] px-3 py-2">
        <p className="truncate text-xs font-medium">{analysis.filename}</p>
        <span className="text-[11px] uppercase text-[var(--color-text-muted)]">{analysis.kind}</span>
      </div>
      <div className="h-[min(68vh,700px)] overflow-auto bg-[var(--color-bg-secondary)] p-4"><p className="mb-2 text-xs">{maskedPreview ? "脱敏结果预览" : "原始预览"}</p>{preview}<EmbeddedImagesReview analysis={analysis} onChange={onChange} previewId={maskedPreview?.previewId} /></div>
    </div>
    <div className="min-w-0">
      <div className="mb-3 flex items-center justify-between">
        <div><p className="text-sm font-medium">识别结果</p><p className="text-[11px] text-[var(--color-text-muted)]">已选择 {selected} 项</p></div>
        <div className="flex gap-2"><Button icon={Eye} disabled={busy || !ready} onClick={onPreview}>生成预览</Button><Button tone="primary" icon={busy ? Loader2 : Play} disabled={busy || !ready} onClick={onExecute}>执行脱敏</Button></div>
      </div>
      <div className="max-h-[65vh] overflow-auto border-y border-[var(--color-border-primary)]">
        {(analysis.entities || []).map((entity, index) => <EntityRow key={`${entity.start}-${entity.end}-${index}`} entity={entity} checked={entity.selected !== false} onChange={(checked) => onChange(updateAnalysisEntity(analysis, index, checked))} />)}
        {(analysis.boxes || []).map((box, index) => <label key={box.id || index} className="flex items-start gap-3 border-b border-[var(--color-border-primary)] px-2 py-3 text-xs last:border-0"><input type="checkbox" className="mt-0.5" checked={box.selected !== false} onChange={(event) => onChange(updateAnalysisBox(analysis, index, event.target.checked))} /><span className="min-w-0"><span className="block font-medium">OCR 区域 · 第 {box.page || 1} 页</span><span className="block truncate text-[var(--color-text-muted)]">{box.text || "手动区域"}</span></span></label>)}
        {!analysis.entities?.length && !analysis.boxes?.length && <p className="py-14 text-center text-xs text-[var(--color-text-muted)]">未识别到敏感内容</p>}
      </div>
      {analysis.warnings?.map((warning, index) => <p key={index} className="mt-2 text-[11px] text-amber-600">{warning.message || warning.code}</p>)}
    </div>
  </section>;
}

function EmbeddedImagesReview({ analysis, onChange, previewId }: { analysis: Analysis; onChange: (value: Analysis) => void; previewId?: string }) {
  const [index, setIndex] = useState(0);
  const [loaded, setLoaded] = useState({ key: "", src: "", error: "" });
  const images = analysis.embedded_images || [];
  const item = images[Math.min(index, Math.max(0, images.length - 1))];
  const key = `${item?.source_preview || ""}:${previewId || ""}`;
  const src = loaded.key === key ? loaded.src : "";
  const error = loaded.key === key ? loaded.error : "";
  useEffect(() => {
    let active = true; let url = "";
    if (item?.source_preview) void desensitizationApi.blob(`${item.source_preview}${previewId ? `?preview_id=${encodeURIComponent(previewId)}` : ""}`).then(({ blob }) => {
      if (active) { url = URL.createObjectURL(blob); setLoaded({ key, src: url, error: "" }); }
    }).catch(() => { if (active) setLoaded({ key, src: "", error: "图片预览读取失败，原资源保留" }); });
    return () => { active = false; if (url) URL.revokeObjectURL(url); };
  }, [item?.source_preview, previewId, key]);
  if (!item) return null;
  const imageAnalysis: Analysis = { ...item, analysis_id: analysis.analysis_id, filename: `图片 ${index + 1}`, kind: "image" };
  const change = (value: Analysis) => onChange({ ...analysis, embedded_images: images.map((image) => image.image_id === item.image_id ? { ...image, entities: value.entities || [], boxes: value.boxes || [] } : image) });
  return <section className="mt-4 space-y-3 border-t border-[var(--color-border-primary)] pt-3"><select aria-label="Word 图片" className={inputClass} value={Math.min(index, images.length - 1)} onChange={(event) => setIndex(Number(event.target.value))}>{images.map((image, i) => <option key={image.image_id} value={i}>图片 {i + 1} · {image.status}</option>)}</select><p className="break-all text-xs">{item.locations?.join(", ")}</p>{error && <p role="alert" className="text-xs text-amber-600">{error}</p>}{src && (previewId ? <img src={src} alt="脱敏后的 Word 图片" className="max-w-full" /> : <ImageReview analysis={imageAnalysis} src={src} onChange={change} />)}{!previewId && <>{item.entities.map((entity, i) => <EntityRow key={entity.id || i} entity={entity} checked={entity.selected !== false} onChange={(checked) => change(updateAnalysisEntity(imageAnalysis, i, checked))} />)}{item.boxes.map((box, i) => <div key={box.id || i} className="flex flex-wrap items-center gap-2 text-xs"><input aria-label={`选择图片区域 ${i + 1}`} type="checkbox" checked={box.selected !== false} onChange={(event) => change(updateAnalysisBox(imageAnalysis, i, event.target.checked))} />{(["x", "y", "width", "height"] as const).map((key) => <label key={key}>{key}<input aria-label={`区域 ${i + 1} ${key}`} className="ml-1 w-16 border bg-transparent" type="number" min={key === "width" || key === "height" ? 0.001 : 0} max="1" step="0.01" value={box[key] ?? (box as unknown as { bbox?: Record<string, number> }).bbox?.[key] ?? 0} onChange={(event) => change({ ...imageAnalysis, boxes: item.boxes.map((value, n) => n === i ? { ...value, [key]: Number(event.target.value), bbox: { ...((value as unknown as { bbox?: Record<string, number> }).bbox || value), [key]: Number(event.target.value) } } : value) })} /></label>)}</div>)}</>}</section>;
}

function ImageReview({ analysis, src, onChange }: { analysis: Analysis; src: string; onChange: (value: Analysis) => void }) {
  const stageRef = useRef<HTMLDivElement>(null);
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [dragCurrent, setDragCurrent] = useState<{ x: number; y: number } | null>(null);
  const normalize = (event: React.PointerEvent) => {
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return null;
    return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
  };
  const finish = (event: React.PointerEvent) => {
    if (!dragStart) return;
    const point = normalize(event) || dragCurrent || dragStart;
    const x = Math.min(dragStart.x, point.x); const y = Math.min(dragStart.y, point.y);
    const width = Math.abs(point.x - dragStart.x); const height = Math.abs(point.y - dragStart.y);
    setDragStart(null); setDragCurrent(null);
    if (width < 0.01 || height < 0.01) return;
    const boxes = [...(analysis.boxes || []), { id: `manual-${Date.now()}`, page: 1, x, y, width, height, text: "", selected: true, source: "manual" }];
    onChange({ ...analysis, boxes });
  };
  return <div ref={stageRef} className="relative mx-auto w-fit max-w-full select-none" onPointerDown={(event) => { const point = normalize(event); if (point) { stageRef.current?.setPointerCapture(event.pointerId); setDragStart(point); setDragCurrent(point); } }} onPointerMove={(event) => { if (dragStart) setDragCurrent(normalize(event)); }} onPointerUp={finish} onPointerCancel={() => { setDragStart(null); setDragCurrent(null); }}><img alt={analysis.filename} src={src} className="block max-h-[62vh] max-w-full object-contain" />{(analysis.boxes || []).map((box, index) => { const candidate = box as unknown as Record<string, unknown>; const raw = (candidate.bbox && typeof candidate.bbox === "object" ? candidate.bbox : candidate) as Record<string, number>; return <button type="button" key={box.id || index} aria-label={`切换区域 ${index + 1}`} onPointerDown={(event) => event.stopPropagation()} onClick={() => onChange({ ...analysis, boxes: analysis.boxes?.map((item, itemIndex) => itemIndex === index ? { ...item, selected: item.selected === false } : item) })} className={cx("absolute border-2", box.selected === false ? "border-slate-400/60 bg-slate-400/10" : "border-amber-500 bg-amber-400/20")} style={{ left: `${Number(raw.x || 0) * 100}%`, top: `${Number(raw.y || 0) * 100}%`, width: `${Number(raw.width || 0) * 100}%`, height: `${Number(raw.height || 0) * 100}%` }} />; })}{dragStart && dragCurrent && <span className="pointer-events-none absolute border-2 border-dashed border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/10" style={{ left: `${Math.min(dragStart.x, dragCurrent.x) * 100}%`, top: `${Math.min(dragStart.y, dragCurrent.y) * 100}%`, width: `${Math.abs(dragCurrent.x - dragStart.x) * 100}%`, height: `${Math.abs(dragCurrent.y - dragStart.y) * 100}%` }} />}</div>;
}

type SensitiveItem = Rule & { mode: "literal" | "category"; value: string };

function SensitiveItemsView({ groupId }: { groupId: string }) {
  const empty = { mode: "literal" as "literal" | "category", value: "", name: "", entity_type: "CUSTOM", replacement: "已脱敏", enabled: true };
  const [items, setItems] = useState<SensitiveItem[]>([]);
  const [draft, setDraft] = useState(empty);
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => { const result = await desensitizationApi.customSensitive(groupId); setItems(result.items as SensitiveItem[]); }, [groupId]);
  useEffect(() => { void refresh().catch((reason: unknown) => setError(String(reason))); }, [refresh]);
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await action(); await refresh(); return true; }
    catch (reason) { setError(String(reason)); return false; }
    finally { setBusy(false); }
  };
  const payload = (item: SensitiveItem) => ({ mode: item.mode, value: item.value, name: item.name, entity_type: item.entity_type || "CUSTOM", replacement: item.replacement || "", enabled: item.enabled });
  return <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]"><div>{error && <p role="alert" className="break-words text-sm text-red-600">{error}</p>}{items.map((item) => <div key={item.id} className="flex items-center gap-2 border-b border-[var(--color-border-primary)] py-3"><span className="min-w-0 flex-1 break-all text-xs">{item.name}<small className="block">{item.value}</small></span><input aria-label={`启用 ${item.name}`} type="checkbox" checked={item.enabled} disabled={busy} onChange={(event) => void mutate(() => desensitizationApi.updateCustomSensitive(groupId, item.id, { ...payload(item), enabled: event.target.checked }))} /><Button icon={Pencil} aria-label={`编辑 ${item.name}`} title="编辑敏感项" disabled={busy} onClick={() => { setEditing(item.id); setDraft(payload(item)); }} /><Button icon={Trash2} aria-label={`删除 ${item.name}`} title="删除敏感项" disabled={busy} onClick={() => void mutate(() => desensitizationApi.deleteCustomSensitive(groupId, item.id))} /></div>)}</div><form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void mutate(() => editing ? desensitizationApi.updateCustomSensitive(groupId, editing, draft) : desensitizationApi.createCustomSensitive(groupId, draft)).then((ok) => { if (ok) { setEditing(""); setDraft(empty); } }); }}><h3 className="text-sm">{editing ? "编辑敏感项" : "添加敏感项"}</h3><Field label="类型"><select className={inputClass} value={draft.mode} onChange={(event) => setDraft({ ...draft, mode: event.target.value as "literal" | "category" })}><option value="literal">固定文字</option><option value="category">敏感类别</option></select></Field>{([["name", "名称"], ["value", "敏感内容或类别"], ["entity_type", "实体类型"], ["replacement", "替换文本"]] as const).map(([key, label]) => <Field key={key} label={label}><input className={inputClass} required={key === "value" || key === "entity_type"} value={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></Field>)}<div className="flex gap-2"><Button type="submit" icon={Save} disabled={busy}>保存敏感项</Button>{editing && <Button type="button" onClick={() => { setEditing(""); setDraft(empty); }}>取消编辑</Button>}</div></form></section>;
}

function EntityRow({ entity, checked, onChange }: { entity: Entity; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex items-start gap-3 border-b border-[var(--color-border-primary)] px-2 py-3 text-xs last:border-0"><input type="checkbox" className="mt-0.5" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span className="min-w-0 flex-1"><span className="flex items-center justify-between gap-2"><b>{entity.type_name || entity.type}</b>{typeof entity.score === "number" && <small className="text-[var(--color-text-muted)]">{Math.round(entity.score * 100)}%</small>}</span><span className="mt-1 block break-all text-[var(--color-text-secondary)]">{entity.text || `${entity.start}-${entity.end}`}</span></span></label>;
}

function BatchDetail({ ready, batch, busy, reversible, password, onReversible, onPassword, onEntity, onBox, onExecute, onCancel, onDownload, onAnalysis }: { onAnalysis: (sequence: number, value: Analysis) => void; ready: boolean; batch: Batch | null; busy: boolean; reversible: boolean; password: string; onReversible: (value: boolean) => void; onPassword: (value: string) => void; onEntity: (sequence: number, index: number, checked: boolean) => void; onBox: (sequence: number, index: number, checked: boolean) => void; onExecute: () => void; onCancel: () => void; onDownload: (kind: "download" | "report") => void }) {
  if (!batch) return <div className="flex min-h-72 items-center justify-center text-xs text-[var(--color-text-muted)]">选择一个批次查看详情</div>;
  const awaiting = batch.status === "awaiting_review"; const terminal = terminalBatchStatuses.has(batch.status);
  return <div className="min-w-0"><div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--color-border-primary)] pb-4"><div><div className="flex items-center gap-3"><p className="text-sm font-semibold">{batch.root_name || `批次 ${(batch.id || batch.batch_id || "").slice(0, 8)}`}</p><Status value={batch.status} /></div><p className="mt-1 text-[11px] text-[var(--color-text-muted)]">完成 {batch.completed || 0} · 失败 {batch.failed || 0} · 共 {batch.total || 0}</p></div><div className="flex gap-2">{!terminal && <Button tone="danger" icon={Ban} onClick={onCancel}>取消</Button>}{terminal && <><Button icon={Download} onClick={() => onDownload("download")}>结果 ZIP</Button><Button icon={FileCheck2} onClick={() => onDownload("report")}>JSON 报告</Button></>}</div></div>{awaiting && <div className="my-4 flex flex-wrap items-end gap-4 rounded-md border border-[var(--color-border-primary)] p-3"><Toggle checked={reversible} onChange={onReversible} label="可逆脱敏" />{reversible && <Field label="恢复口令"><input className={cx(inputClass, "w-56")} type="password" value={password} onChange={(event) => onPassword(event.target.value)} /></Field>}<Button tone="primary" icon={busy ? Loader2 : Play} disabled={busy || !ready} onClick={onExecute}>确认并执行批次</Button></div>}<div className="mt-4 space-y-2">{(batch.items || []).map((item: BatchItem) => <div key={item.sequence} className="rounded-md border border-[var(--color-border-primary)]"><div className="flex items-center gap-3 px-3 py-2"><FileCheck2 size={14} /><span className="min-w-0 flex-1 truncate text-xs">{item.relative_path}</span><Status value={item.status} /></div>{awaiting && item.analysis && <div className="max-h-48 overflow-auto border-t border-[var(--color-border-primary)] px-3">{(item.analysis.entities || []).map((entity, index) => <EntityRow key={`${entity.start}-${entity.end}-${index}`} entity={entity} checked={entity.selected !== false} onChange={(checked) => onEntity(item.sequence, index, checked)} />)}{(item.analysis.boxes || []).map((box, index) => <label key={box.id || index} className="flex items-center gap-3 border-t border-[var(--color-border-primary)] py-2 text-xs"><input type="checkbox" checked={box.selected !== false} onChange={(event) => onBox(item.sequence, index, event.target.checked)} /><span className="truncate">OCR/区域 · 第 {box.page || 1} 页 · {box.text || "手工区域"}</span></label>)}{!item.analysis.entities?.length && !item.analysis.boxes?.length && <p className="py-4 text-xs text-[var(--color-text-muted)]">未识别到敏感区域</p>}</div>}{awaiting && item.analysis && <EmbeddedImagesReview analysis={item.analysis} onChange={(value) => onAnalysis(item.sequence, value)} />}{item.error_message && <p className="border-t border-red-500/20 px-3 py-2 text-[11px] text-red-500">{item.error_message}</p>}</div>)}{!batch.items?.length && <p className="py-12 text-center text-xs text-[var(--color-text-muted)]">批次条目正在加载</p>}</div></div>;
}

function HistoryView({ tasks, onRefresh, onOpen, onDownload, onRestore, onDelete }: { tasks: Task[]; onRefresh: () => Promise<void>; onOpen: (task: Task) => void; onDownload: (task: Task, kind: "artifact" | "report") => void; onRestore: (task: Task) => void; onDelete: (task: Task) => void }) {
  return <section><div className="mb-3 flex items-center justify-between"><p className="text-xs text-[var(--color-text-secondary)]">共 {tasks.length} 条记录</p><Button icon={RefreshCw} onClick={() => void onRefresh()}>刷新</Button></div><div className="overflow-x-auto border-y border-[var(--color-border-primary)]"><table className="w-full min-w-[760px] text-left text-xs"><thead className="text-[var(--color-text-muted)]"><tr className="border-b border-[var(--color-border-primary)]"><th className="px-3 py-2 font-medium">文件</th><th className="px-3 py-2 font-medium">状态</th><th className="px-3 py-2 font-medium">类型</th><th className="px-3 py-2 font-medium">时间</th><th className="px-3 py-2 text-right font-medium">操作</th></tr></thead><tbody>{tasks.map((task) => <tr key={task.id} className="border-b border-[var(--color-border-primary)] last:border-0"><td className="max-w-[320px] px-3 py-3"><p className="truncate font-medium">{task.original_name}</p><p className="truncate text-[11px] text-[var(--color-text-muted)]">{task.relative_path || task.id}</p></td><td className="px-3 py-3"><Status value={task.status} /></td><td className="px-3 py-3 uppercase">{task.kind}</td><td className="px-3 py-3">{formatTime(task.updated)}</td><td className="px-3 py-3"><div className="flex justify-end gap-1"><Button title="打开复核" aria-label="打开复核" className="w-9 px-0" icon={Eye} onClick={() => onOpen(task)} />{task.artifact_path && <Button title="下载结果" aria-label="下载结果" className="w-9 px-0" icon={Download} onClick={() => onDownload(task, "artifact")} />}{task.report_path && <Button title="下载报告" aria-label="下载报告" className="w-9 px-0" icon={FileCheck2} onClick={() => onDownload(task, "report")} />}{task.reversible && <Button title="恢复文件" aria-label="恢复文件" className="w-9 px-0" icon={KeyRound} onClick={() => onRestore(task)} />}<Button title="删除记录" aria-label="删除记录" tone="danger" className="w-9 px-0" icon={Trash2} onClick={() => onDelete(task)} /></div></td></tr>)}</tbody></table>{!tasks.length && <p className="py-16 text-center text-xs text-[var(--color-text-muted)]">暂无历史记录</p>}</div></section>;
}

function RulesView(props: { groupId: string; ready: boolean; rules: Rule[]; builtinRules: Rule[]; nerEnabled: boolean; categories: RuleCategory[]; categoryName: string; draft: RuleDraft; editingRule: string | null; busy: boolean; onCategoryName: (value: string) => void; onDraft: (value: RuleDraft) => void; onSave: () => void; onCancelEdit: () => void; onEdit: (rule: Rule) => void; onModel: (enabled: boolean) => void; onBuiltin: (rule: Rule, enabled: boolean) => void; onToggleRule: (rule: Rule, enabled: boolean) => void; onDeleteRule: (rule: Rule) => void; onCreateCategory: () => void; onDeleteCategory: (category: RuleCategory) => void }) {
  const p = { ...props, rules: props.rules.filter((rule) => !["literal", "category"].includes(rule.kind)) };
  const [tab, setTab] = useState("builtin");
  return <><nav aria-label="识别规则分类" className="mb-5 flex gap-2">{[["builtin", "内置规则"], ["sensitive", "自定义敏感项"], ["custom", "自定义规则"]].map(([key, label]) => <button key={key} onClick={() => setTab(key)} aria-current={tab === key ? "page" : undefined} className={cx("px-3 py-2 text-xs border-b-2", tab === key ? "border-[var(--color-accent-primary)]" : "border-transparent")}>{label}</button>)}</nav>{tab === "builtin" && <section><div className="space-y-5"><div className="flex items-center justify-between border-b border-[var(--color-border-primary)] pb-3"><div><p className="text-sm font-medium">模型与内置规则</p><p className="text-[11px] text-[var(--color-text-muted)]">配置只作用于当前工作组</p></div><Toggle checked={p.nerEnabled} label="NER 模型" onChange={p.onModel} /></div><div className="grid gap-2 sm:grid-cols-2">{p.builtinRules.map((rule) => <div key={rule.id} className="flex items-center justify-between rounded-md border border-[var(--color-border-primary)] px-3 py-2"><span className="min-w-0"><span className="block truncate text-xs font-medium">{rule.name || rule.entity_type}</span><span className="text-[11px] text-[var(--color-text-muted)]">{rule.entity_type || rule.id}</span></span><Toggle checked={rule.enabled !== false} label="" onChange={(enabled) => p.onBuiltin(rule, enabled)} /></div>)}</div></div></section>}{tab === "sensitive" && <SensitiveItemsView key={p.groupId} groupId={p.groupId} />}{tab === "custom" && <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]"><div className="space-y-5"><div><p className="mb-2 text-sm font-medium">自定义规则</p><div className="divide-y divide-[var(--color-border-primary)] border-y border-[var(--color-border-primary)]">{p.rules.filter((rule) => !["literal", "category"].includes(rule.kind)).map((rule) => <div key={rule.id} className="flex items-center gap-3 py-3"><span className="min-w-0 flex-1"><span className="block truncate text-xs font-medium">{rule.name}</span><span className="block truncate text-[11px] text-[var(--color-text-muted)]">{rule.kind} · {rule.pattern} · {rule.category}</span></span><Toggle checked={rule.enabled !== false} label="" onChange={(enabled) => p.onToggleRule(rule, enabled)} /><Button aria-label="编辑规则" title="编辑规则" className="w-9 px-0" icon={Pencil} onClick={() => p.onEdit(rule)} /><Button aria-label="删除规则" title="删除规则" tone="danger" className="w-9 px-0" icon={Trash2} onClick={() => p.onDeleteRule(rule)} /></div>)}{!p.rules.length && <p className="py-8 text-center text-xs text-[var(--color-text-muted)]">暂无自定义规则</p>}</div></div><div><p className="mb-2 text-sm font-medium">规则分类</p><div className="flex gap-2"><input className={inputClass} placeholder="新分类名称" value={p.categoryName} onChange={(event) => p.onCategoryName(event.target.value)} /><Button icon={Plus} onClick={p.onCreateCategory}>添加</Button></div><div className="mt-2 flex flex-wrap gap-2">{p.categories.map((category) => <span key={category.id} className="inline-flex items-center gap-2 rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-xs">{category.name}<small>{category.count ?? 0}</small>{!category.builtin && <button aria-label={`删除分类 ${category.name}`} onClick={() => p.onDeleteCategory(category)}><X size={12} /></button>}</span>)}</div></div></div>
    <div className="border-t border-[var(--color-border-primary)] pt-5 xl:border-l xl:border-t-0 xl:pl-6 xl:pt-0"><div className="mb-4 flex items-center justify-between"><p className="text-sm font-medium">{p.editingRule ? "编辑规则" : "新建规则"}</p>{p.editingRule && <button className="text-xs" onClick={p.onCancelEdit}>取消编辑</button>}</div><div className="space-y-3"><Field label="规则名称"><input className={inputClass} value={p.draft.name} onChange={(event) => p.onDraft({ ...p.draft, name: event.target.value })} /></Field><div className="grid grid-cols-2 gap-3"><Field label="匹配方式"><select className={inputClass} value={p.draft.kind} onChange={(event) => p.onDraft({ ...p.draft, kind: event.target.value })}><option value="word">关键词</option><option value="regex">正则表达式</option></select></Field><Field label="应用范围"><select className={inputClass} value={p.draft.scope} onChange={(event) => p.onDraft({ ...p.draft, scope: event.target.value })}><option value="all">全部</option><option value="text">文本</option><option value="image">图片</option><option value="document">文档</option></select></Field></div><Field label="匹配内容"><textarea className={cx(inputClass, "min-h-24 py-2")} value={p.draft.pattern} onChange={(event) => p.onDraft({ ...p.draft, pattern: event.target.value })} /></Field><div className="grid grid-cols-2 gap-3"><Field label="实体类型"><input className={inputClass} value={p.draft.entity_type} onChange={(event) => p.onDraft({ ...p.draft, entity_type: event.target.value })} /></Field><Field label="分类"><select className={inputClass} value={p.draft.category} onChange={(event) => p.onDraft({ ...p.draft, category: event.target.value })}><option>自定义规则</option>{p.categories.filter((item) => !item.builtin && item.name !== "自定义规则").map((item) => <option key={item.id}>{item.name}</option>)}</select></Field></div><Field label="替换文本"><input className={inputClass} value={p.draft.replacement} onChange={(event) => p.onDraft({ ...p.draft, replacement: event.target.value })} /></Field><Button tone="primary" icon={Save} disabled={p.busy} onClick={p.onSave}>保存规则</Button></div></div>
  </section>}</>;
}

function PoliciesView({ ready, policies, busy, onChange, onSave, onReset }: { ready: boolean; policies: Record<string, Policy>; busy: boolean; onChange: (value: Record<string, Policy>) => void; onSave: () => void; onReset: () => void }) {
  return <section><div className="mb-4 flex flex-wrap items-center justify-between gap-2"><div><p className="text-sm font-medium">实体处理策略</p><p className="text-[11px] text-[var(--color-text-muted)]">文本、图片和文档按实体类型独立配置</p></div><div className="flex gap-2"><Button icon={RotateCcw} onClick={onReset}>恢复默认</Button><Button tone="primary" icon={Save} disabled={busy} onClick={onSave}>保存策略</Button></div></div><div className="overflow-x-auto border-y border-[var(--color-border-primary)]"><table className="w-full min-w-[850px] text-left text-xs"><thead><tr className="border-b border-[var(--color-border-primary)] text-[var(--color-text-muted)]"><th className="px-3 py-2 font-medium">实体类型</th><th className="px-3 py-2 font-medium">文本动作</th><th className="px-3 py-2 font-medium">替换内容</th><th className="px-3 py-2 font-medium">图片动作</th><th className="px-3 py-2 font-medium">强度 / 像素</th><th className="px-3 py-2 font-medium">颜色</th></tr></thead><tbody>{Object.entries(policies).map(([key, policy]) => <tr key={key} className="border-b border-[var(--color-border-primary)] last:border-0"><td className="px-3 py-3 font-medium">{key}</td><td className="px-3 py-3"><select className={inputClass} value={policy.text_action || "replace"} onChange={(event) => onChange({ ...policies, [key]: { ...policy, text_action: event.target.value } })}><option value="replace">替换</option><option value="mask">掩码</option><option value="remove">移除</option><option value="keep">保留</option></select></td><td className="px-3 py-3"><input className={inputClass} value={policy.replacement || ""} onChange={(event) => onChange({ ...policies, [key]: { ...policy, replacement: event.target.value } })} /></td><td className="px-3 py-3"><select className={inputClass} value={policy.image_action || "blur"} onChange={(event) => onChange({ ...policies, [key]: { ...policy, image_action: event.target.value } })}><option value="blur">模糊</option><option value="pixelate">像素化</option><option value="fill">色块</option><option value="keep">保留</option></select></td><td className="px-3 py-3"><input className={inputClass} type="number" min="1" max="80" value={policy.image_action === "pixelate" ? policy.pixel_size || 12 : policy.blur_radius || 14} onChange={(event) => { const value = Number(event.target.value); onChange({ ...policies, [key]: policy.image_action === "pixelate" ? { ...policy, pixel_size: value } : { ...policy, blur_radius: value } }); }} /></td><td className="px-3 py-3"><input className="h-9 w-12 rounded border border-[var(--color-border-primary)] bg-transparent p-1" type="color" value={policy.color || "#000000"} aria-label={`${key} 颜色`} onChange={(event) => onChange({ ...policies, [key]: { ...policy, color: event.target.value } })} /></td></tr>)}</tbody></table>{!Object.keys(policies).length && <p className="py-14 text-center text-xs text-[var(--color-text-muted)]">正在读取策略</p>}</div></section>;
}

function ModelsView({ model, onRefresh, onInitialize }: { model: ModelStatus | null; onRefresh: () => void; onInitialize: () => void }) {
  const rawProgress = model?.progress ?? model?.install_progress;
  const progress = model?.status === "ready" ? 100 : typeof rawProgress === "number" && rawProgress >= 100 ? 99 : rawProgress;
  return <section className="max-w-3xl space-y-6"><div className="flex items-start justify-between border-b border-[var(--color-border-primary)] pb-5"><div><div className="mb-2 flex items-center gap-3"><h2 className="text-base font-semibold">本地中文识别模型</h2><span className="text-xs">{serviceLabel(model)}</span></div><p className="text-xs text-[var(--color-text-secondary)]">NER 与 OCR 均在本机 CPU 运行，模型文件不随安装包分发。</p></div><Button icon={RefreshCw} onClick={onRefresh}>检查</Button></div><div className="grid gap-px overflow-hidden rounded-md border border-[var(--color-border-primary)] bg-[var(--color-border-primary)] sm:grid-cols-2"><div className="bg-[var(--color-bg-primary)] p-4"><p className="text-[11px] text-[var(--color-text-muted)]">命名实体识别</p><p className="mt-1 text-sm">{model?.ner_available ? "可用" : "未就绪"}</p></div><div className="bg-[var(--color-bg-primary)] p-4"><p className="text-[11px] text-[var(--color-text-muted)]">OCR 文字识别</p><p className="mt-1 text-sm">{model?.ocr_available ? "可用" : "未就绪"}</p></div></div>{typeof progress === "number" && model?.status !== "ready" && <div><div className="mb-1 flex justify-between text-xs"><span>{model?.message || model?.install_step || "正在初始化"}</span><span>{progress}%</span></div><div className="h-2 overflow-hidden rounded bg-[var(--color-bg-secondary)]"><div className="h-full bg-[var(--color-accent-primary)] transition-[width]" style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div></div>}{(model?.install_log_tail?.length || 0) > 0 && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-[var(--color-border-primary)] p-3 text-[11px] text-[var(--color-text-muted)]">{model?.install_log_tail?.join("\n")}</pre>}{(model?.missing_dependencies?.length || 0) > 0 && <div className="rounded-md border border-red-500/30 bg-red-500/5 p-4 text-xs text-red-500"><p className="font-medium">安装包缺少运行依赖</p><p className="mt-1 break-words">{model?.missing_dependencies?.join("、")}</p></div>}<dl className="grid grid-cols-[110px_1fr] gap-x-4 gap-y-3 text-xs"><dt className="text-[var(--color-text-muted)]">模型目录</dt><dd className="break-all">{model?.model_dir || "-"}</dd><dt className="text-[var(--color-text-muted)]">运行阶段</dt><dd>{serviceLabel(model)}</dd><dt className="text-[var(--color-text-muted)]">错误信息</dt><dd className="break-words text-red-500">{serviceErrorLabel(model) || model?.error || "-"}</dd></dl>{model?.status !== "ready" && <Button tone="primary" icon={model?.status === "starting" ? Loader2 : RefreshCw} onClick={onInitialize}>初始化 / 重试</Button>}</section>;
}
