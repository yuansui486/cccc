import type { ComputerSetup } from "../../services/api/computerControl";

export function setupErrorMessage(message: unknown, phase = ""): string {
  const text = String(message || "");
  if (/9009|python(?:\.exe|\.EXE)?.*未找到|not found.*python/i.test(text)) {
    return "找不到可用的 Python 命令（Windows 错误 9009）。请安装 Python 3.9+，勾选“Add Python to PATH”或安装 Python Launcher，然后点击“修复”。";
  }
  if (/Unable to install uv with pip/i.test(text)) {
    return "无法通过 Python 安装 uv。请确认 Python 和 pip 可用，并允许当前用户安装工具后再点击“修复”。";
  }
  return text || (phase === "failed" ? "Windows-MCP 安装失败，请点击“修复”重试。" : "");
}

const SETUP_STEP_LABELS: Record<string, string> = {
  checking_uv: "正在检查 uv",
  uv_ready: "uv 已就绪",
  installing_uv_with_pip: "正在通过 Python 安装 uv",
  uv_installed: "uv 安装完成",
  checking_python_313: "正在检查 Python 3.13",
  installing_python_313: "正在由 uv 下载 Python 3.13",
  python_313_ready: "Python 3.13 已就绪",
  installing_windows_mcp: "正在安装 Windows-MCP",
  upgrading_windows_mcp: "正在升级 Windows-MCP",
  starting_mcp_session: "正在启动 Windows-MCP 服务",
  checking_tool_catalog: "正在验证电脑控制工具",
  setup_cancelled: "安装已取消",
  setup_interrupted: "上一次安装已中断",
};

export function setupStepLabel(step: unknown, phase = ""): string {
  const key = String(step || "").trim();
  if (SETUP_STEP_LABELS[key]) return SETUP_STEP_LABELS[key];
  return ({
    checking: "正在检查安装环境",
    downloading: "正在准备组件",
    initializing: "正在启动服务",
    verifying: "正在验证工具",
    ready: "安装完成",
    failed: "安装失败",
    cancelled: "安装已取消",
    not_started: "等待安装",
  } as Record<string, string>)[phase] || key || "等待安装";
}

function timestampMillis(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const numeric = typeof value === "number" ? value : Number(value);
  const millis = Number.isFinite(numeric) ? (numeric < 10_000_000_000 ? numeric * 1000 : numeric) : Date.parse(String(value));
  return Number.isFinite(millis) ? millis : null;
}

export function formatSetupDuration(
  startedAt: number | string | null | undefined,
  endedAt?: number | string | null,
  now = Date.now(),
): string {
  const started = timestampMillis(startedAt);
  if (started === null) return "";
  const ended = timestampMillis(endedAt) ?? now;
  const totalSeconds = Math.max(0, Math.floor((ended - started) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分 ${seconds} 秒`;
  return `${seconds} 秒`;
}

export function formatSetupDiagnostics(setup: ComputerSetup, pollingError = ""): string {
  const lines = [
    "OneColleague Windows-MCP 安装诊断",
    `状态：${setup.phase || "unknown"}`,
    `阶段：${setupStepLabel(setup.step || setup.detail, setup.phase)}`,
  ];
  if (setup.attempt_id) lines.push(`尝试 ID：${setup.attempt_id}`);
  if (setup.operation) lines.push(`操作：${setup.operation}`);
  if (setup.started_at) lines.push(`开始时间：${formatComputerControlTime(setup.started_at)}`);
  if (setup.last_activity_at) lines.push(`最近活动：${formatComputerControlTime(setup.last_activity_at)}`);
  if (setup.uv_version) lines.push(`uv：${setup.uv_version}`);
  if (setup.python_path) lines.push(`Python 3.13：${setup.python_path} (${setup.python_source || "unknown"})`);
  if (setup.package_index) lines.push(`Python 包索引：${setup.package_index}${setup.package_index_fallback ? "（已回退）" : ""}`);
  if (setup.current_command) lines.push(`当前命令：${setup.current_command}`);
  if (setup.process_id) lines.push(`进程 PID：${setup.process_id}`);
  if (setup.error?.code) lines.push(`错误代码：${setup.error.code}`);
  if (setup.error?.message) lines.push(`错误：${setup.error.message}`);
  if (pollingError) lines.push(`状态刷新错误：${pollingError}`);
  if (setup.logs?.length) lines.push("", "安装日志：", ...setup.logs);
  return lines.join("\n");
}

export type ComputerControlSessionDetails = {
  startedAt: number | string | null;
  transportRestarts: number | null;
  observation: {
    updatedAt: number | string | null;
    provider: string;
    targetWindow: string;
    elementCount: number | null;
  } | null;
};

export function computerControlSessionDetails(setup: ComputerSetup): ComputerControlSessionDetails {
  const observation = setup.observation || setup.last_observation;
  const rawElementCount = observation?.element_count ?? observation?.count ?? setup.observation_element_count;
  const hasObservation = Boolean(
    observation || setup.observation_updated_at || setup.observation_provider
      || setup.observation_target_window || rawElementCount !== undefined,
  );
  const rawRestarts = setup.transport_restarts ?? setup.session?.transport_restarts;
  return {
    startedAt: setup.session_started_at ?? setup.session?.started_at ?? null,
    transportRestarts: Number.isFinite(Number(rawRestarts)) ? Number(rawRestarts) : null,
    observation: hasObservation ? {
      updatedAt: observation?.updated_at ?? observation?.captured_at ?? setup.observation_updated_at ?? null,
      provider: String(observation?.provider || observation?.source || setup.observation_provider || "").trim(),
      targetWindow: String(observation?.target_window || observation?.window_name || setup.observation_target_window || "").trim(),
      elementCount: Number.isFinite(Number(rawElementCount)) ? Number(rawElementCount) : null,
    } : null,
  };
}

export function formatComputerControlTime(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const numeric = typeof value === "number" ? value : Number(value);
  const millis = Number.isFinite(numeric) ? (numeric < 10_000_000_000 ? numeric * 1000 : numeric) : Date.parse(String(value));
  if (!Number.isFinite(millis)) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(millis));
}

export function computerControlProviderLabel(provider: string): string {
  const normalized = String(provider || "").trim().toLowerCase();
  return ({
    uia: "Windows UI 自动化",
    dom: "网页 DOM",
    semantic: "语义识别",
    "windows-mcp": "Windows-MCP",
    windows_mcp: "Windows-MCP",
    native: "Windows 原生拾取器",
    native_uia: "Windows UI 自动化",
    "windows_mcp+native_uia": "Windows-MCP + Windows UI 自动化",
  } as Record<string, string>)[normalized] || provider;
}

export function shouldRefreshComputerControlAfterSseTransition(
  disconnectedSinceRefresh: boolean,
  current: string,
  active: boolean,
): boolean {
  return active && disconnectedSinceRefresh && current === "connected";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function timestampValue(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value < 10_000_000_000 ? value * 1000 : value;
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function newestComputerControlObservation(
  setupObservation: ComputerControlSessionDetails["observation"],
  runObservation: ComputerControlSessionDetails["observation"],
): ComputerControlSessionDetails["observation"] {
  if (!setupObservation) return runObservation;
  if (!runObservation) return setupObservation;
  const setupAt = timestampValue(setupObservation.updatedAt);
  const runAt = timestampValue(runObservation.updatedAt);
  return runAt > setupAt ? runObservation : setupObservation;
}

export function latestComputerControlObservation(
  runs: Record<string, unknown>[],
): ComputerControlSessionDetails["observation"] {
  let latest: ComputerControlSessionDetails["observation"] = null;
  let latestAt = -1;
  for (const run of Array.isArray(runs) ? runs : []) {
    const events = Array.isArray(run.events) ? run.events : [];
    for (const rawEvent of events) {
      const event = asRecord(rawEvent);
      if (!event) continue;
      const resolution = asRecord(event.target_resolution);
      const candidates = [
        asRecord(event.observation_context),
        asRecord(event.post_action_observation),
        asRecord(event.observation),
        asRecord(resolution?.observation),
      ].filter((item): item is Record<string, unknown> => Boolean(item));
      for (const candidate of candidates) {
        const updatedAt = candidate.updated_at ?? candidate.captured_at ?? event.completed_at ?? event.updated_at ?? event.started_at ?? run.updated_at;
        const at = timestampValue(updatedAt);
        if (at < latestAt) continue;
        const targetWindow = String(candidate.target_window || candidate.window_name || candidate.focused_window || "").trim();
        const counts = asRecord(candidate.window_element_counts);
        const rawElementCount = candidate.target_window_element_count
          ?? candidate.element_count
          ?? candidate.count
          ?? (targetWindow && counts ? counts[targetWindow] : undefined);
        latest = {
          updatedAt: (updatedAt as number | string | null | undefined) ?? null,
          provider: String(candidate.provider || candidate.source || "").trim(),
          targetWindow,
          elementCount: Number.isFinite(Number(rawElementCount)) ? Number(rawElementCount) : null,
        };
        latestAt = at;
      }
    }
  }
  return latest;
}
