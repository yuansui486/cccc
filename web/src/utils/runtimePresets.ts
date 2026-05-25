import type { RuntimeInfo, SupportedRuntime } from "../types";

export type RuntimePresetId =
  | "default:claude"
  | "default:codex"
  | "model:deepseek-v4-pro-claude"
  | "model:qwen3.6-max-claude"
  | "model:doubao-code-claude"
  | "model:gpt-5.4-codex"
  | "model:gpt-5.5-codex"
  | "model:kimi-k2.6-kimi";

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
    label: "claude",
    runtime: "claude",
    description: "不指定模型，使用 Claude CLI 本机默认配置。",
  },
  {
    id: "model:deepseek-v4-pro-claude",
    label: "deepseek-v4",
    runtime: "claude",
    model: "deepseek-v4-pro[1m]",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic",
      ANTHROPIC_AUTH_TOKEN: "sk-7f21d214c2dc4947b06a7289c357f558",
      ANTHROPIC_MODEL: "deepseek-v4-pro[1m]",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "deepseek-v4-pro[1m]",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "deepseek-v4-pro[1m]",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "deepseek-v4-flash",
      CLAUDE_CODE_SUBAGENT_MODEL: "deepseek-v4-flash",
      CLAUDE_CODE_EFFORT_LEVEL: "max",
    },
  },
  {
    id: "model:qwen3.6-max-claude",
    label: "Qwen3.6（阿里千问）",
    runtime: "claude",
    model: "qwen3.6-max-preview",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://dashscope.aliyuncs.com/apps/anthropic",
      ANTHROPIC_AUTH_TOKEN: "sk-ec022412cf4447d092935f05d604a7f4",
      ANTHROPIC_MODEL: "qwen3.6-max-preview",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "qwen3.6-max-preview",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "qwen3.6-plus",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "qwen3.6-flash",
      CLAUDE_CODE_SUBAGENT_MODEL: "qwen3.6-plus",
      CLAUDE_CODE_EFFORT_LEVEL: "max",
    },
  },
  {
    id: "model:doubao-code-claude",
    label: "豆包（字节跳动）",
    runtime: "claude",
    model: "doubao-seed-2-0-pro-260215",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "doubao-seed-2-0-pro-260215",
    },
  },
  {
    id: "default:codex",
    label: "codex",
    runtime: "codex",
    description: "不指定模型，使用 Codex CLI 本机默认配置。",
  },
  {
    id: "model:gpt-5.4-codex",
    label: "gpt5.4",
    runtime: "codex",
    model: "gpt-5.4",
  },
  {
    id: "model:gpt-5.5-codex",
    label: "gpt5.5",
    runtime: "codex",
    model: "gpt-5.5",
  },
  {
    id: "model:kimi-k2.6-kimi",
    label: "kimi（月之暗面）",
    runtime: "kimi",
    model: "kimi-k2.6",
    envPrivate: {
      KIMI_BASE_URL: "https://peer.shierkeji.com/v1",
      KIMI_MODEL_NAME: "kimi-k2.6",
    },
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
  if (preset.runtime === "claude" && preset.model) {
    return withCommandModel(command, preset.model, "--model").join(" ");
  }
  if (preset.runtime === "codex" && preset.model) {
    return withCommandModel(command, preset.model, "-m").join(" ");
  }
  return command.join(" ");
}

export function secretsTextForRuntimePreset(preset: RuntimePreset, authToken?: string): string {
  const lines: string[] = [];
  const env = preset.envPrivate || {};
  for (const [key, value] of Object.entries(env)) {
    lines.push(`${key}=${quoteEnvValue(value)}`);
  }
  const authKey = authSecretKeyForRuntimePreset(preset);
  if (authKey && env[authKey] === undefined) {
    const token = String(authToken || "").trim();
    if (token) {
      lines.splice(1, 0, `${authKey}=${quoteEnvValue(token)}`);
    }
  }
  return lines.join("\n");
}

export function mergePresetSecrets(existing: string, preset: RuntimePreset, authToken?: string): string {
  const presetText = secretsTextForRuntimePreset(preset, authToken);
  const current = String(existing || "").trim();
  const presetKeys = knownPresetSecretKeysForRuntime(preset.runtime);
  const authKey = authSecretKeyForRuntimePreset(preset);
  const shouldReplaceAuthToken = Boolean(authKey && String(authToken || "").trim());
  if (!current) return presetText;
  const kept = current
    .split("\n")
    .filter((line) => {
      const key = line.match(/^\s*(?:export\s+|set\s+|\$env:)?([A-Za-z_][A-Za-z0-9_]*)\s*=/i)?.[1];
      if (authKey && key === authKey && !shouldReplaceAuthToken) return true;
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
  const authKey = authSecretKeyForRuntimePreset(preset);
  if (authKey) activeKeys.add(authKey);
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
  if (runtime === "kimi") out.add("KIMI_API_KEY");
  return out;
}

function authSecretKeyForRuntimePreset(preset: RuntimePreset): string {
  if (!preset.model) return "";
  if (preset.runtime === "claude") return "ANTHROPIC_AUTH_TOKEN";
  if (preset.runtime === "kimi") return "KIMI_API_KEY";
  return "";
}

function withCommandModel(command: string[], model: string, preferredFlag: "-m" | "--model"): string[] {
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
  return [...cleaned, preferredFlag, modelName];
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
