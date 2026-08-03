import { RUNTIME_INFO, SUPPORTED_RUNTIMES, type RuntimeInfo, type SupportedRuntime } from "../types";
import { runtimePresetsForModels, type RuntimePreset } from "./runtimePresets";
import { runtimePriceLabel, type RuntimePriceMap } from "./runtimePrices";

export type RuntimeChoiceOption =
  | { kind: "preset"; id: string; label: string; runtime: SupportedRuntime; disabled: boolean }
  | { kind: "runtime"; id: SupportedRuntime; label: string; runtime: SupportedRuntime; disabled: boolean };

export type RuntimeChoiceGroup = {
  labelKey: string;
  labelFallback: string;
  options: RuntimeChoiceOption[];
};

const GROUP_ORDER = ["codex", "claude", "gemini", "kimi", "opencode"] as const;
type GroupKey = typeof GROUP_ORDER[number];
const VISIBLE_RUNTIME_CHOICES = new Set<SupportedRuntime>(["gemini", "opencode"]);

const GROUP_LABELS: Record<GroupKey, { labelKey: string; labelFallback: string }> = {
  claude: { labelKey: "runtimeGroupClaude", labelFallback: "Claude Code" },
  codex: { labelKey: "runtimeGroupCodex", labelFallback: "Codex" },
  gemini: { labelKey: "runtimeGroupGemini", labelFallback: "Gemini" },
  kimi: { labelKey: "runtimeGroupKimi", labelFallback: "Kimi" },
  opencode: { labelKey: "runtimeGroupOpenCode", labelFallback: "OpenCode" },
};

export function buildRuntimeChoiceGroups(
  runtimes: RuntimeInfo[],
  priceMap?: RuntimePriceMap | null,
  modelCatalog?: string[],
): RuntimeChoiceGroup[] {
  const groups = new Map<GroupKey, RuntimeChoiceOption[]>();
  for (const key of GROUP_ORDER) groups.set(key, []);

  const includeOpenCodeModels = Array.isArray(modelCatalog) && modelCatalog.length > 0;
  const presets = runtimePresetsForModels(modelCatalog || []).filter(
    (preset) => preset.runtime !== "opencode" || includeOpenCodeModels,
  );
  for (const preset of presets) {
    const runtimeAvailable = Boolean(runtimes.find((item) => item.name === preset.runtime)?.available);
    groups.get(groupKeyForRuntime(preset.runtime))?.push({
      kind: "preset",
      id: preset.id,
      label: runtimePriceLabel({ id: preset.id, kind: "preset", label: preset.label }, priceMap, preset),
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
      label: runtimePriceLabel({ id: runtime, kind: "runtime", label: RUNTIME_INFO[runtime]?.label || runtime }, priceMap),
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
  return "claude";
}

function runtimeHasPreset(runtime: SupportedRuntime, presets: RuntimePreset[]): boolean {
  return presets.some((preset: RuntimePreset) => preset.runtime === runtime);
}
