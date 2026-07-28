import type { ComputerSetup } from "../../services/api/computerControl";

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
    startedAt: setup.session_started_at ?? setup.session?.started_at ?? setup.started_at ?? null,
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
