import { RUNTIME_INFO, SUPPORTED_RUNTIMES, type RuntimeInfo, type SupportedRuntime } from "../types";
import { runtimePresetsForModels, runtimePresetsForRuntime, type RuntimePreset } from "./runtimePresets";

export type RuntimeChoiceOption =
  | { kind: "preset"; id: string; label: string; runtime: SupportedRuntime; disabled: boolean }
  | { kind: "runtime"; id: SupportedRuntime; label: string; runtime: SupportedRuntime; disabled: boolean };

export type RuntimeChoiceGroup = {
  labelKey: string;
  labelFallback: string;
  options: RuntimeChoiceOption[];
};

const GROUP_ORDER = ["codex", "claude", "gemini", "kimi", "opencode", "openclaw"] as const;
type GroupKey = typeof GROUP_ORDER[number];
const VISIBLE_RUNTIME_CHOICES = new Set<SupportedRuntime>(["gemini", "opencode", "openclaw"]);
export const ACTOR_RUNTIME_CHOICES: SupportedRuntime[] = ["codex", "claude", "gemini", "hermes", "kimi", "opencode", "openclaw"];

export type RuntimeSelectorOption = {
  value: SupportedRuntime;
  label: string;
  description: string;
  disabled: boolean;
};

export type RuntimeModelOption = {
  value: string;
  label: string;
  preset: RuntimePreset;
};

export function buildRuntimeSelectorOptions(
  runtimes: RuntimeInfo[],
  allowedRuntimes: readonly SupportedRuntime[] = ACTOR_RUNTIME_CHOICES,
  runtimeDescriptions: Partial<Record<SupportedRuntime, string>> = {},
): RuntimeSelectorOption[] {
  const byName = new Map(runtimes.map((item) => [item.name, item]));
  return allowedRuntimes.map((runtime) => {
    const info = byName.get(runtime);
    return {
      value: runtime,
      label: info?.display_name || RUNTIME_INFO[runtime]?.label || runtime,
      description: runtimeDescriptions[runtime] ?? RUNTIME_INFO[runtime]?.desc ?? "",
      disabled: runtime === "custom" || runtime === "web_model" ? false : !info?.available,
    };
  });
}

export function buildRuntimeModelOptions(
  runtime: SupportedRuntime | string,
  modelCatalog?: string[],
): RuntimeModelOption[] {
  return runtimePresetsForRuntime(runtime, modelCatalog || [])
    .filter((preset) => preset.runtime === runtime && Boolean(preset.model))
    .map((preset) => ({
      value: String(preset.model || ""),
      label: preset.label,
      preset,
    }));
}

const GROUP_LABELS: Record<GroupKey, { labelKey: string; labelFallback: string }> = {
  claude: { labelKey: "runtimeGroupClaude", labelFallback: "Claude Code" },
  codex: { labelKey: "runtimeGroupCodex", labelFallback: "Codex" },
  gemini: { labelKey: "runtimeGroupGemini", labelFallback: "Gemini" },
  kimi: { labelKey: "runtimeGroupKimi", labelFallback: "Kimi" },
  opencode: { labelKey: "runtimeGroupOpenCode", labelFallback: "OpenCode" },
  openclaw: { labelKey: "runtimeGroupOpenClaw", labelFallback: "OpenClaw" },
};

export function buildRuntimeChoiceGroups(
  runtimes: RuntimeInfo[],
  modelCatalog?: string[],
): RuntimeChoiceGroup[] {
  const groups = new Map<GroupKey, RuntimeChoiceOption[]>();
  for (const key of GROUP_ORDER) groups.set(key, []);

  const includeOpenCodeModels = Array.isArray(modelCatalog) && modelCatalog.length > 0;
  const openclawModels = runtimes.find((item) => item.name === "openclaw")?.models || [];
  const presets = [
    ...runtimePresetsForModels(modelCatalog || []),
    ...runtimePresetsForRuntime("openclaw", openclawModels).filter((preset) => preset.runtime === "openclaw"),
  ].filter(
    (preset) => preset.runtime !== "opencode" || includeOpenCodeModels,
  );
  for (const preset of presets) {
    const runtimeAvailable = Boolean(runtimes.find((item) => item.name === preset.runtime)?.available);
    groups.get(groupKeyForRuntime(preset.runtime))?.push({
      kind: "preset",
      id: preset.id,
      label: preset.label,
      runtime: preset.runtime,
      disabled: !runtimeAvailable,
    });
  }

  for (const runtime of SUPPORTED_RUNTIMES) {
    if (runtimeHasPreset(runtime, presets)) continue;
    if (!VISIBLE_RUNTIME_CHOICES.has(runtime)) continue;
    const runtimeAvailable = Boolean(runtimes.find((item) => item.name === runtime)?.available);
    const selectable = runtimeAvailable;
    groups.get(groupKeyForRuntime(runtime))?.push({
      kind: "runtime",
      id: runtime,
      label: RUNTIME_INFO[runtime]?.label || runtime,
      runtime,
      disabled: !selectable,
    });
  }

  return GROUP_ORDER.map((key) => ({ ...GROUP_LABELS[key], options: groups.get(key) || [] })).filter(
    (group) => group.options.length > 0
  );
}

function groupKeyForRuntime(runtime: SupportedRuntime): GroupKey {
  if (runtime === "codex") return "codex";
  if (runtime === "gemini") return "gemini";
  if (runtime === "kimi") return "kimi";
  if (runtime === "opencode") return "opencode";
  if (runtime === "openclaw") return "openclaw";
  return "claude";
}

function runtimeHasPreset(runtime: SupportedRuntime, presets: RuntimePreset[]): boolean {
  return presets.some((preset: RuntimePreset) => preset.runtime === runtime);
}
