export type TriggerKind = "interval" | "schedule" | "at" | "cron" | "element";
export type ScheduleMode = "daily" | "weekly";
export type AtMode = "at" | "countdown";

export type TriggerLocator = {
  selector_version?: number;
  strategy?: string;
  window_name?: string;
  control_type?: string;
  name?: string;
  text?: string;
  automation_id?: string;
  class_name?: string;
  process_name?: string;
  framework_id?: string;
  parent_name?: string;
  match?: string;
  monitor?: number;
  stability?: string;
  fallback_policy?: "never" | "controlled";
};

export type TriggerRuntimeStatus = {
  state?: string;
  nextCheckAt?: number | string | null;
  nextFireAt?: number | string | null;
  lastCheckedAt?: number | string | null;
  lastFiredAt?: number | string | null;
  pending?: boolean;
  pendingReason?: string;
  lastRunId?: string;
  error?: string;
};

export function triggerKindLabel(kind: TriggerKind): string {
  return ({ interval: "按间隔", schedule: "定时计划", at: "一次性计划", cron: "Cron（高级）", element: "元素出现" } as Record<TriggerKind, string>)[kind];
}

export type TriggerDraft = {
  id: string;
  title: string;
  kind: TriggerKind;
  enabled: boolean;
  actorId: string;
  cooldownSeconds: number;
  intervalSeconds: number;
  scheduleMode: ScheduleMode;
  time: string;
  weekdays: number[];
  timezone: string;
  atMode: AtMode;
  runAt: string;
  countdownSeconds: number;
  cronExpression: string;
  elementCondition: "next_appear";
  pollIntervalSeconds: number;
  requiredHits: number;
  locator: TriggerLocator | null;
  runtime: TriggerRuntimeStatus;
};

export type PersistedTrigger = Record<string, unknown>;

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown, fallback = ""): string {
  const result = String(value ?? "").trim();
  return result || fallback;
}

function numberValue(value: unknown, fallback: number): number {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

function runtimeTime(...values: unknown[]): number | string | null | undefined {
  let sawNull = false;
  for (const value of values) {
    if (value === null) {
      sawNull = true;
      continue;
    }
    if (typeof value === "number" || typeof value === "string") return value;
  }
  return sawNull ? null : undefined;
}

export function localDateTimeValue(value: number | string | Date, now = new Date()): string {
  const date = value instanceof Date ? value : new Date(typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value);
  const valid = Number.isFinite(date.getTime()) ? date : now;
  const offset = valid.getTimezoneOffset() * 60_000;
  return new Date(valid.getTime() - offset).toISOString().slice(0, 16);
}

export function createTrigger(kind: TriggerKind, index = 1, now = new Date()): TriggerDraft {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  return {
    id: `trigger_${Date.now().toString(36)}_${index}`,
    title: ({ interval: "固定间隔", schedule: "每日计划", at: "单次计划", cron: "Cron 计划", element: "元素出现" } as Record<TriggerKind, string>)[kind],
    kind,
    enabled: false,
    actorId: "foreman",
    cooldownSeconds: 30,
    intervalSeconds: 300,
    scheduleMode: "daily",
    time: "09:00",
    weekdays: [0],
    timezone,
    atMode: "at",
    runAt: localDateTimeValue(new Date(now.getTime() + 5 * 60_000), now),
    countdownSeconds: 300,
    cronExpression: "0 9 * * *",
    elementCondition: "next_appear",
    pollIntervalSeconds: 2,
    requiredHits: 2,
    locator: null,
    runtime: {},
  };
}

export function normalizeTriggerRuntime(rawValue: unknown, configValue: unknown = {}): TriggerRuntimeStatus {
  const raw = record(rawValue);
  const config = record(configValue);
  const status = record(raw.status);
  const scheduler = record(raw.scheduler ?? status.scheduler);
  const reason = text(raw.reason ?? status.reason);
  const state = text(raw.state ?? status.state ?? scheduler.state, status.runtime_enabled === true ? "active" : reason);
  const explicitPendingReason = text(raw.pending_reason ?? status.pending_reason ?? scheduler.pending_reason);
  const pendingReason = explicitPendingReason || (reason === "active" ? "" : reason);
  return {
    state,
    nextCheckAt: runtimeTime(raw.next_check_at, status.next_check_at, scheduler.next_check_at, raw.next_poll_at, status.next_poll_at, scheduler.next_poll_at),
    nextFireAt: runtimeTime(raw.next_fire_at, status.next_fire_at, scheduler.next_fire_at, raw.next_due_at, status.next_due_at, scheduler.next_due_at, raw.next_run_at, status.next_run_at, config.next_run_at),
    lastCheckedAt: runtimeTime(raw.last_checked_at, status.last_checked_at, scheduler.last_checked_at, raw.last_check_at),
    lastFiredAt: runtimeTime(raw.last_fired_at, status.last_fired_at, scheduler.last_fired_at, raw.last_run_at, status.last_run_at),
    pending: Boolean(raw.pending ?? status.pending ?? scheduler.pending ?? (state === "pending" || explicitPendingReason || status.pending_changes === true)),
    pendingReason,
    lastRunId: text(raw.last_run_id ?? status.last_run_id ?? scheduler.last_run_id),
    error: text(raw.last_error ?? status.last_error ?? scheduler.last_error ?? raw.error ?? status.error ?? scheduler.error),
  };
}

export function normalizeTrigger(rawValue: unknown, index: number, now = new Date()): TriggerDraft {
  const raw = record(rawValue);
  const config = record(raw.config);
  const type = text(raw.type, "interval");
  let kind: TriggerKind = (["interval", "schedule", "at", "cron", "element"].includes(type) ? type : "interval") as TriggerKind;
  const scheduleMode = text(config.schedule_mode || config.mode) as ScheduleMode;
  if (type === "cron" && ["daily", "weekly"].includes(scheduleMode)) kind = "schedule";
  if (type === "cron" && (config.once || config.run_at || config.countdown_seconds)) kind = "at";
  const base = createTrigger(kind, index + 1, now);
  const weekdays = Array.isArray(config.weekdays)
    ? config.weekdays.map(Number).filter((value) => Number.isInteger(value) && value >= 0 && value <= 6)
    : base.weekdays;
  return {
    ...base,
    id: text(raw.id, base.id),
    title: text(raw.name || raw.title, base.title),
    enabled: raw.enabled === true,
    actorId: text(raw.actor_id, "foreman"),
    cooldownSeconds: Math.max(0, numberValue(raw.cooldown_seconds, 30)),
    intervalSeconds: Math.max(1, numberValue(config.seconds, 300)),
    scheduleMode: scheduleMode === "weekly" ? "weekly" : "daily",
    time: text(config.time, base.time),
    weekdays: weekdays.length ? weekdays : [0],
    timezone: text(config.timezone, base.timezone),
    atMode: config.countdown_seconds ? "countdown" : "at",
    runAt: localDateTimeValue(config.at as string || config.datetime as string || config.timestamp as string || config.run_at as string || base.runAt, now),
    countdownSeconds: Math.max(1, numberValue(config.countdown_seconds, 300)),
    cronExpression: text(config.expression || config.cron, base.cronExpression),
    elementCondition: "next_appear",
    pollIntervalSeconds: Math.max(0.5, numberValue(config.poll_seconds || config.poll_interval_seconds, 2)),
    requiredHits: Math.max(1, Math.round(numberValue(config.required_hits, 2))),
    locator: Object.keys(record(config.locator)).length ? record(config.locator) as TriggerLocator : null,
    runtime: normalizeTriggerRuntime(raw, config),
  };
}

function timeParts(value: string): [number, number] {
  const [hour, minute] = String(value || "09:00").split(":").map(Number);
  return [Number.isFinite(hour) ? hour : 9, Number.isFinite(minute) ? minute : 0];
}

export function serializeTrigger(trigger: TriggerDraft, now = new Date()): PersistedTrigger {
  const common = {
    id: trigger.id,
    name: trigger.title,
    enabled: trigger.enabled,
    actor_id: trigger.actorId || "foreman",
    cooldown_seconds: Math.max(0, Math.round(trigger.cooldownSeconds)),
  };
  if (trigger.kind === "interval") {
    return { ...common, type: "interval", config: { seconds: Math.max(1, Math.round(trigger.intervalSeconds)) } };
  }
  if (trigger.kind === "schedule") {
    const [hour, minute] = timeParts(trigger.time);
    const weekly = trigger.scheduleMode === "weekly";
    return {
      ...common,
      type: "schedule",
      config: {
        timezone: trigger.timezone,
        schedule_mode: trigger.scheduleMode,
        time: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`,
        weekdays: weekly ? trigger.weekdays : [],
      },
    };
  }
  if (trigger.kind === "at") {
    const runAt = trigger.atMode === "countdown"
      ? new Date(now.getTime() + Math.max(1, trigger.countdownSeconds) * 1000)
      : new Date(trigger.runAt);
    const safeRunAt = Number.isFinite(runAt.getTime()) ? runAt : new Date(now.getTime() + 300_000);
    return {
      ...common,
      type: "at",
      config: {
        at: safeRunAt.toISOString(),
        timezone: trigger.timezone,
        countdown_seconds: trigger.atMode === "countdown" ? Math.max(1, Math.round(trigger.countdownSeconds)) : undefined,
      },
    };
  }
  if (trigger.kind === "element") {
    return {
      ...common,
      type: "element",
      config: {
        locator: trigger.locator || {},
        condition: trigger.elementCondition,
        poll_seconds: Math.max(0.5, trigger.pollIntervalSeconds),
        required_hits: Math.max(1, Math.round(trigger.requiredHits)),
      },
    };
  }
  return {
    ...common,
    type: "cron",
    config: { expression: trigger.cronExpression.trim(), timezone: trigger.timezone },
  };
}

export function triggerSummary(trigger: TriggerDraft): string {
  if (trigger.kind === "interval") return `每 ${Math.round(trigger.intervalSeconds)} 秒运行`;
  if (trigger.kind === "schedule") return trigger.scheduleMode === "daily" ? `每天 ${trigger.time}` : `每周 ${trigger.weekdays.length} 天的 ${trigger.time}`;
  if (trigger.kind === "at") return trigger.atMode === "countdown" ? `${Math.round(trigger.countdownSeconds)} 秒后运行一次` : `${trigger.runAt.replace("T", " ")} 运行一次`;
  if (trigger.kind === "element") return trigger.locator ? `监测“${trigger.locator.name || trigger.locator.text || trigger.locator.control_type || "目标元素"}”` : "尚未选择元素";
  return trigger.cronExpression || "尚未填写 Cron";
}
