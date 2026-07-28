import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlarmClock,
  Check,
  LoaderCircle,
  MousePointer2,
  Plus,
  Play,
  Power,
  Trash2,
  X,
} from "lucide-react";
import { apiJson } from "../../services/api/base";
import { computerControlApi, type ElementPickerElement, type ElementPickerSession } from "../../services/api/computerControl";
import {
  createTrigger,
  normalizeTrigger,
  serializeTrigger,
  triggerSummary,
  type PersistedTrigger,
  type TriggerDraft,
  type TriggerKind,
  type TriggerLocator,
} from "./triggerTypes";

type Props = {
  open: boolean;
  groupId: string;
  workflowId: string;
  revision: number;
  initialTriggers: Record<string, unknown>[];
  onClose: () => void;
  onSaved: (triggers: PersistedTrigger[], revision?: number) => void;
};

type TriggerListResponse = {
  revision?: number;
  triggers?: PersistedTrigger[];
  trigger_status?: Record<string, Record<string, unknown>>;
};
type TriggerWriteResponse = {
  revision?: number;
  version?: number;
  triggers?: PersistedTrigger[];
  trigger_status?: Record<string, Record<string, unknown>>;
};

const fieldClass = "mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2.5 text-sm outline-none focus:border-[var(--color-accent-primary)]";
const groupRoot = (groupId: string, workflowId: string) => `/api/v1/groups/${encodeURIComponent(groupId)}/computer-control/workflows/${encodeURIComponent(workflowId)}/triggers`;

function triggerKindLabel(kind: TriggerKind): string {
  return ({ interval: "固定间隔", schedule: "每日 / 每周", at: "指定时间 / 倒计时", cron: "Cron 表达式", element: "界面元素" } as Record<TriggerKind, string>)[kind];
}

function formatStatusTime(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未记录";
  const numeric = Number(value);
  const date = new Date(Number.isFinite(numeric) ? (numeric < 10_000_000_000 ? numeric * 1000 : numeric) : String(value));
  if (!Number.isFinite(date.getTime())) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}

function locatorFromElement(element: ElementPickerElement): TriggerLocator {
  const source = element.locator && typeof element.locator === "object" ? element.locator : element;
  return Object.fromEntries(Object.entries({
    selector_version: Number(source.selector_version || 1),
    strategy: String(source.strategy || "uia"),
    window_name: String(source.window_name || ""),
    control_type: String(source.control_type || ""),
    name: String(source.name || ""),
    text: String(source.text || ""),
    automation_id: String(source.automation_id || ""),
    class_name: String(source.class_name || ""),
    process_name: String(source.process_name || ""),
    framework_id: String(source.framework_id || ""),
    parent_name: String(source.parent_name || ""),
    match: String(source.match || "exact"),
    monitor: typeof source.monitor === "number" ? source.monitor : undefined,
    stability: String(source.stability || "unknown"),
    fallback_policy: "never",
  }).filter(([, value]) => value !== "" && value !== undefined)) as TriggerLocator;
}

function draftsFromResponse(response: TriggerListResponse, fallback: PersistedTrigger[] = []): TriggerDraft[] {
  const values = response.triggers || fallback;
  return values.map((item, index) => {
    const id = String(item.id || "");
    const runtime = id ? response.trigger_status?.[id] : undefined;
    return normalizeTrigger(runtime ? { ...item, status: runtime } : item, index);
  });
}

export function TriggerEditor({ open, groupId, workflowId, revision, initialTriggers, onClose, onSaved }: Props) {
  const [triggers, setTriggers] = useState<TriggerDraft[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [currentRevision, setCurrentRevision] = useState(revision);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [picker, setPicker] = useState<ElementPickerSession | null>(null);
  const pickerRef = useRef<ElementPickerSession | null>(null);
  const initialTriggersRef = useRef(initialTriggers);
  const revisionRef = useRef(revision);

  const selected = useMemo(() => triggers.find((item) => item.id === selectedId) || null, [selectedId, triggers]);

  useEffect(() => {
    pickerRef.current = picker;
  }, [picker]);

  useEffect(() => {
    initialTriggersRef.current = initialTriggers;
    revisionRef.current = revision;
  }, [initialTriggers, revision]);

  const load = useCallback(async () => {
    if (!open || !groupId || !workflowId) return;
    setBusy("load");
    const response = await apiJson<TriggerListResponse>(groupRoot(groupId, workflowId));
    if (response.ok) {
      const values = draftsFromResponse(response.result);
      setTriggers(values);
      setCurrentRevision(Number(response.result.revision || revisionRef.current));
      setDirty(false);
      setSelectedId((current) => values.some((item) => item.id === current) ? current : values[0]?.id || "");
      setMessage("");
    } else {
      const values = initialTriggersRef.current.map((item, index) => normalizeTrigger(item, index));
      setTriggers(values);
      setSelectedId(values[0]?.id || "");
      setMessage(response.error.message || "触发器读取失败");
    }
    setBusy("");
  }, [groupId, open, workflowId]);

  useEffect(() => {
    if (!open) return undefined;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, open]);

  useEffect(() => {
    if (!open || !picker || ["ended", "confirmed", "failed"].includes(picker.status)) return undefined;
    const timer = window.setInterval(() => {
      void computerControlApi.pickerSession(groupId, picker.session_id).then((response) => {
        if (response.ok) setPicker(response.result);
      });
    }, 700);
    return () => window.clearInterval(timer);
  }, [groupId, open, picker]);

  useEffect(() => {
    if (open) return undefined;
    const active = pickerRef.current;
    if (active && !["ended", "confirmed"].includes(active.status)) void computerControlApi.pickerStop(groupId, active.session_id);
    const timer = window.setTimeout(() => setPicker(null), 0);
    return () => window.clearTimeout(timer);
  }, [groupId, open]);

  function updateSelected(patch: Partial<TriggerDraft>) {
    if (!selected) return;
    setTriggers((items) => items.map((item) => item.id === selected.id ? { ...item, ...patch } : item));
    setDirty(true);
  }

  function add(kind: TriggerKind) {
    const next = createTrigger(kind, triggers.length + 1);
    setTriggers((items) => [...items, next]);
    setSelectedId(next.id);
    setDirty(true);
  }

  function removeSelected() {
    if (!selected || !window.confirm(`删除触发器“${selected.title}”？`)) return;
    const index = triggers.findIndex((item) => item.id === selected.id);
    const next = triggers.filter((item) => item.id !== selected.id);
    setTriggers(next);
    setSelectedId(next[Math.min(index, next.length - 1)]?.id || "");
    setDirty(true);
  }

  async function save() {
    setBusy("save");
    setMessage("");
    const payload = triggers.map((item) => serializeTrigger(item));
    const response = await apiJson<TriggerWriteResponse>(groupRoot(groupId, workflowId), {
      method: "PUT",
      body: JSON.stringify({ expected_revision: currentRevision, triggers: payload }),
    });
    if (response.ok) {
      const stored = response.result.triggers || payload;
      const nextRevision = Number(response.result.revision || currentRevision + 1);
      setCurrentRevision(nextRevision);
      setTriggers(draftsFromResponse(response.result, stored));
      setDirty(false);
      setMessage("触发器已保存为新的工作流版本。");
      onSaved(stored, nextRevision);
    } else setMessage(response.error.message || "触发器保存失败");
    setBusy("");
  }

  async function setActivation(trigger: TriggerDraft, enabled: boolean) {
    if (dirty) {
      setMessage("请先保存当前修改，再启用或停用自动触发。");
      return;
    }
    setBusy(`activation-${trigger.id}`);
    const response = await apiJson<TriggerWriteResponse>(`${groupRoot(groupId, workflowId)}/${encodeURIComponent(trigger.id)}/activation`, {
      method: "PATCH",
      body: JSON.stringify({ enabled, expected_revision: currentRevision }),
    });
    if (response.ok) {
      const fallback = triggers.map((item) => serializeTrigger(item.id === trigger.id ? { ...item, enabled } : item));
      const stored = response.result.triggers || fallback;
      setTriggers(draftsFromResponse(response.result, stored));
      const nextRevision = Number(response.result.revision || currentRevision + 1);
      setCurrentRevision(nextRevision);
      setDirty(false);
      setMessage(enabled ? "自动触发已启用。" : "自动触发已停用。");
      onSaved(stored, nextRevision);
    } else setMessage(response.error.message || "启用状态更新失败");
    setBusy("");
  }

  async function testTrigger(trigger: TriggerDraft) {
    if (dirty) {
      setMessage("请先保存当前修改，再测试已保存的触发配置。");
      return;
    }
    setBusy(`test-${trigger.id}`);
    const response = await apiJson<Record<string, unknown>>(`${groupRoot(groupId, workflowId)}/${encodeURIComponent(trigger.id)}/test`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    const result = response.ok
      ? String(response.result.message || response.result.status || (response.result.valid ? "配置有效，只读测试已完成" : "只读测试已完成"))
      : response.error.message || "测试失败";
    setTestResult((items) => ({ ...items, [trigger.id]: result }));
    setBusy("");
  }

  async function startPicker() {
    setBusy("picker-start");
    const response = await computerControlApi.pickerStart(groupId);
    if (response.ok) {
      setPicker(response.result);
      setMessage("拾取器已启动：将鼠标移到目标元素，按 Ctrl+Shift+L 锁定。");
    } else setMessage(response.error.message || "元素拾取器启动失败");
    setBusy("");
  }

  async function lockPicker() {
    if (!picker) return;
    setBusy("picker-lock");
    const response = await computerControlApi.pickerLock(groupId, picker.session_id);
    if (response.ok) {
      setPicker(response.result);
      if (response.result.element) updateSelected({ locator: locatorFromElement(response.result.element) });
    } else setMessage(response.error.message || "未能锁定当前元素");
    setBusy("");
  }

  async function confirmPicker() {
    if (!picker || !picker.element) return;
    setBusy("picker-confirm");
    const response = await computerControlApi.pickerConfirm(groupId, picker.session_id, picker.element.locator);
    if (response.ok) {
      const element = response.result.element || picker.element;
      updateSelected({ locator: locatorFromElement(element) });
      setPicker(response.result);
      setMessage("元素定位已确认，触发检查会重新观察并匹配它。");
    } else setMessage(response.error.message || "元素定位确认失败");
    setBusy("");
  }

  async function stopPicker() {
    if (!picker) return;
    await computerControlApi.pickerStop(groupId, picker.session_id);
    setPicker(null);
  }

  if (!open) return null;
  const weekdays = ["一", "二", "三", "四", "五", "六", "日"];
  return (
    <div className="fixed inset-0 z-[70] bg-black/50 sm:p-5" role="dialog" aria-modal="true" aria-label="工作流触发器">
      <div className="flex h-full w-full flex-col overflow-hidden bg-[var(--color-bg-primary)] shadow-2xl sm:mx-auto sm:max-w-6xl sm:rounded-md sm:border sm:border-[var(--color-border)]">
        <header className="flex min-h-14 items-center gap-3 border-b border-[var(--color-border)] px-4">
          <AlarmClock size={19} />
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold">工作流触发器</h2>
            <p className="truncate text-xs text-[var(--color-text-secondary)]">按时间或桌面元素自动运行当前工作流</p>
          </div>
          <button type="button" className="rounded p-2 hover:bg-black/5" aria-label="关闭触发器编辑器" onClick={onClose}><X size={18} /></button>
        </header>

        {message && <div className="flex items-center justify-between gap-3 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-800 dark:text-amber-200"><span>{message}</span><button type="button" className="shrink-0 rounded p-1 hover:bg-black/5" aria-label="关闭提示" onClick={() => setMessage("")}><X size={14} /></button></div>}

        <div className="grid min-h-0 flex-1 grid-cols-1 sm:grid-cols-[280px_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-auto border-b border-[var(--color-border)] p-3 sm:border-b-0 sm:border-r">
            <div className="mb-3 grid grid-cols-2 gap-1.5">
              {(["interval", "schedule", "at", "cron", "element"] as TriggerKind[]).map((kind) => (
                <button key={kind} type="button" className="flex min-h-9 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2 text-left text-xs hover:border-[var(--color-accent-primary)]" onClick={() => add(kind)}>
                  <Plus size={12} /> {triggerKindLabel(kind)}
                </button>
              ))}
            </div>
            {busy === "load" ? <div className="flex items-center gap-2 py-6 text-xs text-[var(--color-text-secondary)]"><LoaderCircle size={14} className="animate-spin" />正在读取触发器</div> : triggers.length === 0 ? (
              <div className="border border-dashed border-[var(--color-border)] p-4 text-center text-xs text-[var(--color-text-secondary)]">还没有触发器，请选择上方类型新建。</div>
            ) : (
              <div className="space-y-1.5">
                {triggers.map((trigger) => (
                  <button key={trigger.id} type="button" className={`w-full border px-3 py-2 text-left text-xs ${selectedId === trigger.id ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/5" : "border-[var(--color-border)]"}`} onClick={() => setSelectedId(trigger.id)}>
                    <span className="flex items-center justify-between gap-2"><span className="truncate font-medium">{trigger.title}</span><span className={trigger.enabled ? "text-emerald-600" : "text-[var(--color-text-secondary)]"}>{trigger.enabled ? "已启用" : "已停用"}</span></span>
                    <span className="mt-1 block truncate text-[var(--color-text-secondary)]">{triggerSummary(trigger)}</span>
                    {(trigger.runtime.pending || trigger.runtime.error) && <span className={`mt-1 block ${trigger.runtime.error ? "text-red-600" : "text-amber-600"}`}>{trigger.runtime.error || "等待执行"}</span>}
                  </button>
                ))}
              </div>
            )}
          </aside>

          <section className="min-h-0 overflow-auto p-4 sm:p-5">
            {!selected ? <div className="flex h-full items-center justify-center text-sm text-[var(--color-text-secondary)]">选择或新建一个触发器</div> : (
              <div className="mx-auto max-w-3xl space-y-5">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <label className="block text-xs font-medium">触发器名称<input className={fieldClass} value={selected.title} onChange={(event) => updateSelected({ title: event.target.value })} /></label>
                  </div>
                  <button type="button" title="删除触发器" className="mt-5 rounded p-2 text-red-600 hover:bg-red-500/10" onClick={removeSelected}><Trash2 size={16} /></button>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block text-xs font-medium">执行智能体<input className={fieldClass} value={selected.actorId} onChange={(event) => updateSelected({ actorId: event.target.value })} placeholder="默认 foreman" /></label>
                  <label className="block text-xs font-medium">触发后冷却（秒）<input className={fieldClass} type="number" min={0} max={86400} value={selected.cooldownSeconds} onChange={(event) => updateSelected({ cooldownSeconds: Number(event.target.value) })} /></label>
                </div>

                {selected.kind === "interval" && <label className="block text-xs font-medium">固定间隔（秒）<input className={fieldClass} type="number" min={1} value={selected.intervalSeconds} onChange={(event) => updateSelected({ intervalSeconds: Number(event.target.value) })} /><span className="mt-1 block font-normal text-[var(--color-text-secondary)]">默认 5 分钟。电脑忙、锁屏或工作组暂停时会跳过，不补跑。</span></label>}

                {selected.kind === "schedule" && (
                  <div className="space-y-3">
                    <div className="inline-flex rounded-md border border-[var(--color-border)] p-0.5 text-xs">
                      {(["daily", "weekly"] as const).map((mode) => <button key={mode} type="button" className={`rounded px-3 py-1.5 ${selected.scheduleMode === mode ? "bg-[var(--color-accent-primary)] text-white" : ""}`} onClick={() => updateSelected({ scheduleMode: mode })}>{mode === "daily" ? "每天" : "每周"}</button>)}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium">运行时间<input type="time" className={fieldClass} value={selected.time} onChange={(event) => updateSelected({ time: event.target.value })} /></label><label className="text-xs font-medium">时区<input className={fieldClass} value={selected.timezone} onChange={(event) => updateSelected({ timezone: event.target.value })} /></label></div>
                    {selected.scheduleMode === "weekly" && <div><div className="text-xs font-medium">运行星期</div><div className="mt-2 flex flex-wrap gap-1.5">{weekdays.map((label, day) => <button type="button" key={day} className={`h-8 w-8 rounded border text-xs ${selected.weekdays.includes(day) ? "border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)] text-white" : "border-[var(--color-border)]"}`} onClick={() => updateSelected({ weekdays: selected.weekdays.includes(day) ? selected.weekdays.filter((item) => item !== day) : [...selected.weekdays, day] })}>{label}</button>)}</div></div>}
                  </div>
                )}

                {selected.kind === "at" && (
                  <div className="space-y-3">
                    <div className="inline-flex rounded-md border border-[var(--color-border)] p-0.5 text-xs">{(["at", "countdown"] as const).map((mode) => <button key={mode} type="button" className={`rounded px-3 py-1.5 ${selected.atMode === mode ? "bg-[var(--color-accent-primary)] text-white" : ""}`} onClick={() => updateSelected({ atMode: mode })}>{mode === "at" ? "指定时间" : "倒计时"}</button>)}</div>
                    {selected.atMode === "at" ? <div className="grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium">运行时间<input type="datetime-local" className={fieldClass} value={selected.runAt} onChange={(event) => updateSelected({ runAt: event.target.value })} /></label><label className="text-xs font-medium">时区<input className={fieldClass} value={selected.timezone} onChange={(event) => updateSelected({ timezone: event.target.value })} /></label></div> : <label className="block text-xs font-medium">倒计时（秒）<input className={fieldClass} type="number" min={1} value={selected.countdownSeconds} onChange={(event) => updateSelected({ countdownSeconds: Number(event.target.value) })} /></label>}
                  </div>
                )}

                {selected.kind === "cron" && <div className="grid gap-3 sm:grid-cols-2"><label className="text-xs font-medium">Cron 表达式<input className={fieldClass} value={selected.cronExpression} onChange={(event) => updateSelected({ cronExpression: event.target.value })} placeholder="0 9 * * 1-5" /><span className="mt-1 block font-normal text-[var(--color-text-secondary)]">依次为分钟、小时、日期、月份、星期。</span></label><label className="text-xs font-medium">时区<input className={fieldClass} value={selected.timezone} onChange={(event) => updateSelected({ timezone: event.target.value })} /></label></div>}

                {selected.kind === "element" && (
                  <div className="space-y-4">
                    <div className="border border-[var(--color-border)] p-3 text-xs">
                      <div className="flex items-start justify-between gap-3"><div><div className="font-medium">目标界面元素</div><div className="mt-1 text-[var(--color-text-secondary)]">{selected.locator ? `${selected.locator.name || selected.locator.text || "未命名元素"} · ${selected.locator.control_type || "控件"} · ${selected.locator.window_name || "当前窗口"}` : "尚未捕获元素"}</div></div>{!picker && <button type="button" className="inline-flex shrink-0 items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1.5" disabled={Boolean(busy)} onClick={() => void startPicker()}><MousePointer2 size={13} />实时捕获</button>}</div>
                      {picker && <div className="mt-3 border-t border-[var(--color-border)] pt-3"><div className="font-medium">桌面拾取器 · {picker.hotkey || "Ctrl+Shift+L"}</div><div className="mt-1 text-[var(--color-text-secondary)]">移动鼠标到目标元素后按快捷键，或点击“锁定当前元素”。</div>{picker.element && <div className="mt-2 bg-black/5 px-2 py-1.5 dark:bg-white/5">{picker.element.name || picker.element.text || "未命名元素"} · {picker.element.control_type || "控件"}</div>}<div className="mt-2 flex gap-2"><button type="button" className="rounded border px-2 py-1" disabled={Boolean(busy)} onClick={() => void lockPicker()}>锁定当前元素</button><button type="button" className="rounded border border-emerald-500/40 px-2 py-1 text-emerald-700 disabled:opacity-40" disabled={Boolean(busy) || !picker.element} onClick={() => void confirmPicker()}>确认定位</button><button type="button" className="rounded px-2 py-1 text-red-600" onClick={() => void stopPicker()}>取消</button></div></div>}
                    </div>
                    <div className="grid gap-3 sm:grid-cols-2"><div className="text-xs font-medium">触发条件<div className="mt-1 flex h-9 items-center rounded-md border border-[var(--color-border)] bg-black/[0.025] px-2.5 font-normal dark:bg-white/[0.025]">元素从未出现变为出现时触发</div></div><label className="text-xs font-medium">检查间隔（秒）<input className={fieldClass} type="number" min={0.5} step={0.5} value={selected.pollIntervalSeconds} onChange={(event) => updateSelected({ pollIntervalSeconds: Math.max(0.5, Number(event.target.value)) })} /></label></div>
                    <label className="block text-xs font-medium">连续命中次数<input className={fieldClass} type="number" min={1} max={10} value={selected.requiredHits} onChange={(event) => updateSelected({ requiredHits: Number(event.target.value) })} /><span className="mt-1 block font-normal text-[var(--color-text-secondary)]">默认连续命中 2 次才确认，触发后需恢复为不命中才会重新布防。</span></label>
                  </div>
                )}

                <div className="border-t border-[var(--color-border)] pt-4">
                  <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4"><div><span className="text-[var(--color-text-secondary)]">下一次检查</span><div className="mt-0.5">{formatStatusTime(selected.runtime.nextCheckAt)}</div></div><div><span className="text-[var(--color-text-secondary)]">下一次触发</span><div className="mt-0.5">{formatStatusTime(selected.runtime.nextFireAt)}</div></div><div><span className="text-[var(--color-text-secondary)]">最近检查</span><div className="mt-0.5">{formatStatusTime(selected.runtime.lastCheckedAt)}</div></div><div><span className="text-[var(--color-text-secondary)]">最近触发</span><div className="mt-0.5">{formatStatusTime(selected.runtime.lastFiredAt)}</div></div></div>
                  {(selected.runtime.state || selected.runtime.lastRunId) && <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--color-text-secondary)]">{selected.runtime.state && <span>状态：{selected.runtime.state}</span>}{selected.runtime.lastRunId && <span>最近运行：{selected.runtime.lastRunId}</span>}</div>}
                  {(selected.runtime.pending || selected.runtime.error || testResult[selected.id]) && <div className={`mt-3 px-2 py-1.5 text-xs ${selected.runtime.error ? "bg-red-500/8 text-red-700" : "bg-amber-500/8 text-amber-800"}`}>{selected.runtime.error || testResult[selected.id] || selected.runtime.pendingReason || "触发运行等待处理中"}</div>}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--color-border)] pt-4">
                  <button type="button" className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-xs ${selected.enabled ? "border-emerald-500/40 text-emerald-700" : "border-[var(--color-border)]"}`} disabled={Boolean(busy) || dirty} title={dirty ? "请先保存当前修改" : undefined} onClick={() => void setActivation(selected, !selected.enabled)}><Power size={14} />{selected.enabled ? "已启用，点击停用" : "启用自动触发"}</button>
                  <div className="flex gap-2"><button type="button" className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] px-3 py-2 text-xs disabled:opacity-40" disabled={Boolean(busy) || dirty} title={dirty ? "请先保存当前修改" : undefined} onClick={() => void testTrigger(selected)}>{busy === `test-${selected.id}` ? <LoaderCircle size={13} className="animate-spin" /> : <Play size={13} />}测试触发器</button><button type="button" className="inline-flex items-center gap-1 rounded-md bg-[var(--color-accent-primary)] px-4 py-2 text-xs text-white disabled:opacity-40" disabled={Boolean(busy) || !dirty} onClick={() => void save()}>{busy === "save" ? <LoaderCircle size={13} className="animate-spin" /> : <Check size={13} />}{dirty ? "保存全部" : "已保存"}</button></div>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
