import { RUNTIME_INFO, SUPPORTED_RUNTIMES, type RuntimeInfo, type SupportedRuntime } from "../types";
import { RUNTIME_PRESETS, type RuntimePreset } from "./runtimePresets";

export type RuntimeChoiceOption =
  | { kind: "preset"; id: string; label: string; runtime: SupportedRuntime; disabled: boolean }
  | { kind: "runtime"; id: SupportedRuntime; label: string; runtime: SupportedRuntime; disabled: boolean };

export type RuntimeChoiceGroup = {
  labelKey: string;
  labelFallback: string;
  options: RuntimeChoiceOption[];
};

const GROUP_ORDER = ["codex", "claude", "gemini", "kimi"] as const;
type GroupKey = typeof GROUP_ORDER[number];

const GROUP_LABELS: Record<GroupKey, { labelKey: string; labelFallback: string }> = {
  claude: { labelKey: "runtimeGroupClaude", labelFallback: "Claude Code" },
  codex: { labelKey: "runtimeGroupCodex", labelFallback: "Codex" },
  gemini: { labelKey: "runtimeGroupGemini", labelFallback: "Gemini" },
  kimi: { labelKey: "runtimeGroupKimi", labelFallback: "Kimi" },
};

export function buildRuntimeChoiceGroups(runtimes: RuntimeInfo[]): RuntimeChoiceGroup[] {
  const groups = new Map<GroupKey, RuntimeChoiceOption[]>();
  for (const key of GROUP_ORDER) groups.set(key, []);

  for (const preset of RUNTIME_PRESETS) {
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
    if (runtimeHasPreset(runtime)) continue;
    const runtimeAvailable = Boolean(runtimes.find((item) => item.name === runtime)?.available);
    const selectable = runtimeAvailable || runtime === "custom";
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
  return "claude";
}

function runtimeHasPreset(runtime: SupportedRuntime): boolean {
  return RUNTIME_PRESETS.some((preset: RuntimePreset) => preset.runtime === runtime);
}
