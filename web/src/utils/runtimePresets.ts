import type { RuntimeInfo, SupportedRuntime } from "../types";

export type RuntimePresetId =
  | "default:claude"
  | "default:codex"
  | "model:deepseek-v4-pro-claude"
  | "model:doubao-code-claude"
  | "model:gpt-5.4-codex"
  | "model:gpt-5.5-codex";

export type RuntimePreset = {
  id: RuntimePresetId;
  label: string;
  runtime: SupportedRuntime;
  model?: string;
  command?: string[];
  envPrivate?: Record<string, string>;
  description?: string;
};

export const RUNTIME_PRESETS: RuntimePreset[] = [
  {
    id: "default:claude",
    label: "Claude（CLI 默认配置）",
    runtime: "claude",
    description: "不指定模型，使用 Claude CLI 本机默认配置。",
  },
  {
    id: "model:deepseek-v4-pro-claude",
    label: "DeepSeek-V4-Pro（Claude）",
    runtime: "claude",
    model: "deepseek-v4-pro[1m]",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "deepseek-v4-pro[1m]",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "deepseek-v4-pro[1m]",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "deepseek-v4-pro[1m]",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "deepseek-v4-flash",
      CLAUDE_CODE_SUBAGENT_MODEL: "deepseek-v4-flash",
      CLAUDE_CODE_EFFORT_LEVEL: "max",
    },
  },
  {
    id: "model:doubao-code-claude",
    label: "Doubao-Code（Claude）",
    runtime: "claude",
    model: "doubao-seed-2-0-pro-260215",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "doubao-seed-2-0-pro-260215",
    },
  },
  {
    id: "default:codex",
    label: "Codex（CLI 默认配置）",
    runtime: "codex",
    description: "不指定模型，使用 Codex CLI 本机默认配置。",
  },
  {
    id: "model:gpt-5.4-codex",
    label: "GPT-5.4（Codex）",
    runtime: "codex",
    model: "gpt-5.4",
  },
  {
    id: "model:gpt-5.5-codex",
    label: "GPT-5.5（Codex）",
    runtime: "codex",
    model: "gpt-5.5",
  },
];

export function runtimePresetById(id: string): RuntimePreset | null {
  const needle = String(id || "").trim();
  if (!needle) return null;
  return RUNTIME_PRESETS.find((preset) => preset.id === needle) || null;
}

export function runtimePresetIdFor(runtime: string, command: string | string[] | undefined): RuntimePresetId | "" {
  const normalizedRuntime = String(runtime || "").trim();
  const tokens = Array.isArray(command)
    ? command.map((item) => String(item || "").trim()).filter(Boolean)
    : splitCommand(String(command || "").trim());
  const model = modelFromCommand(tokens);
  if (normalizedRuntime === "claude") {
    return model ? "" : "default:claude";
  }
  if (normalizedRuntime === "codex") {
    if (!model) return "default:codex";
    if (model === "gpt-5.4") return "model:gpt-5.4-codex";
    if (model === "gpt-5.5") return "model:gpt-5.5-codex";
  }
  return "";
}

export function commandForRuntimePreset(preset: RuntimePreset, runtimeInfo?: RuntimeInfo): string {
  const base = splitCommand(String(runtimeInfo?.recommended_command || "").trim());
  const command = preset.command && preset.command.length ? preset.command : base;
  if (preset.runtime === "codex" && preset.model) {
    return withCommandModel(command, preset.model).join(" ");
  }
  return command.join(" ");
}

export function secretsTextForRuntimePreset(preset: RuntimePreset, authToken?: string): string {
  const lines: string[] = [];
  const env = preset.envPrivate || {};
  for (const [key, value] of Object.entries(env)) {
    lines.push(`${key}=${quoteEnvValue(value)}`);
  }
  if (preset.runtime === "claude" && preset.model) {
    const token = String(authToken || "").trim();
    lines.splice(1, 0, `ANTHROPIC_AUTH_TOKEN=${quoteEnvValue(token || "一号同事登陆后得到的key")}`);
  }
  return lines.join("\n");
}

export function mergePresetSecrets(existing: string, preset: RuntimePreset, authToken?: string): string {
  const presetText = secretsTextForRuntimePreset(preset, authToken);
  const current = String(existing || "").trim();
  const presetKeys = knownPresetSecretKeysForRuntime(preset.runtime);
  if (!current) return presetText;
  const kept = current
    .split("\n")
    .filter((line) => {
      const key = line.match(/^\s*(?:export\s+|set\s+|\$env:)?([A-Za-z_][A-Za-z0-9_]*)\s*=/i)?.[1];
      return !key || !presetKeys.has(key);
    })
    .join("\n")
    .trim();
  if (!presetText.trim()) return kept;
  return kept ? `${kept}\n${presetText}` : presetText;
}

export function mergePresetUnsetKeys(existing: string, preset: RuntimePreset): string {
  const current = String(existing || "").trim();
  const activeKeys = new Set(Object.keys(preset.envPrivate || {}));
  if (preset.runtime === "claude" && preset.model) activeKeys.add("ANTHROPIC_AUTH_TOKEN");
  const keysToUnset = Array.from(knownPresetSecretKeysForRuntime(preset.runtime)).filter((key) => !activeKeys.has(key));
  if (!keysToUnset.length) return current;
  const seen = new Set<string>();
  const lines = [...current.split("\n"), ...keysToUnset]
    .map((line) => line.trim())
    .filter((line) => {
      if (!line) return false;
      const key = line.match(/^\s*(?:unset\s+|remove-item\s+env:|\$env:)?([A-Za-z_][A-Za-z0-9_]*)/i)?.[1] || line;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  return lines.join("\n");
}

export function knownPresetSecretKeysForRuntime(runtime: string): Set<string> {
  const out = new Set<string>();
  for (const preset of RUNTIME_PRESETS) {
    if (preset.runtime !== runtime) continue;
    for (const key of Object.keys(preset.envPrivate || {})) out.add(key);
  }
  if (runtime === "claude") out.add("ANTHROPIC_AUTH_TOKEN");
  return out;
}

function withCommandModel(command: string[], model: string): string[] {
  const cleaned = command.filter(Boolean);
  const modelName = String(model || "").trim();
  if (!modelName) return cleaned;
  for (let idx = 0; idx < cleaned.length; idx += 1) {
    const item = cleaned[idx];
    if (item === "-m" || item === "--model") {
      return [...cleaned.slice(0, idx + 1), modelName, ...cleaned.slice(idx + 2)];
    }
    if (item.startsWith("--model=")) {
      return [...cleaned.slice(0, idx), `--model=${modelName}`, ...cleaned.slice(idx + 1)];
    }
  }
  return [...cleaned, "-m", modelName];
}

function modelFromCommand(command: string[]): string {
  for (let idx = 0; idx < command.length; idx += 1) {
    const item = command[idx];
    if ((item === "-m" || item === "--model") && idx + 1 < command.length) return command[idx + 1];
    if (item.startsWith("--model=")) return item.split("=", 2)[1] || "";
  }
  return "";
}

function splitCommand(command: string): string[] {
  return String(command || "").trim().split(/\s+/).filter(Boolean);
}

function quoteEnvValue(value: string): string {
  const raw = String(value ?? "");
  return `"${raw.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}
