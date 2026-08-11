import type { RuntimeInfo, SupportedRuntime } from "../types";

export type RuntimePresetId = string;

export type RuntimePreset = {
  id: RuntimePresetId;
  label: string;
  runtime: SupportedRuntime;
  model?: string;
  command?: string[];
  envPrivate?: Record<string, string>;
  description?: string;
};

export type CodexReasoningEffort = "" | "minimal" | "low" | "medium" | "high" | "xhigh";
export type ClaudeReasoningEffort = "" | "low" | "medium" | "high" | "xhigh" | "max";

const CODEX_REASONING_EFFORT_KEY = "model_reasoning_effort";
const CLAUDE_LEGACY_EFFORT_ENV_KEY = "CLAUDE_CODE_EFFORT_LEVEL";
export const OPENCODE_PROVIDER_ID = "onecolleague";

export const OPENCODE_FALLBACK_MODELS = [
  "gpt-5.4",
  "gpt-5.5",
  "deepseek-v4-pro",
  "deepseek-v4-flash",
  "qwen3.6-plus",
  "qwen3.6-flash",
  "GLM-4.7",
  "doubao-seed-2-0-pro-260215",
  "kimi-k2.6",
] as const;

function envTextHasAssignment(text: string, key: string): boolean {
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^\\s*(?:export\\s+|set\\s+|\\$env:)?${escapedKey}\\s*=`, "im").test(String(text || ""));
}

export function needsDedicatedOneColleagueKey(runtime: string, secretsText: string): boolean {
  const normalizedRuntime = String(runtime || "").trim().toLowerCase();
  if (normalizedRuntime !== "codex" && normalizedRuntime !== "opencode") return false;
  return envTextHasAssignment(secretsText, "OPENAI_API_KEY")
    && !envTextHasAssignment(secretsText, "ONECOLLEAGUE_API_KEY");
}

const FALLBACK_RUNTIME_COMMANDS: Partial<Record<SupportedRuntime, string[]>> = {
  amp: ["amp"],
  auggie: ["auggie"],
  claude: ["claude", "--dangerously-skip-permissions"],
  codex: ["codex", "-c", "shell_environment_policy.inherit=all", "--dangerously-bypass-approvals-and-sandbox", "--search"],
  droid: ["droid", "--auto", "high"],
  gemini: ["gemini", "--yolo"],
  hermes: ["hermes"],
  kimi: ["kimi", "--yolo"],
  neovate: ["neovate"],
  opencode: ["opencode", "--auto"],
};

export function defaultCommandForRuntime(runtime: string): string {
  const normalizedRuntime = String(runtime || "").trim();
  if (!normalizedRuntime) return "";
  const fallback = FALLBACK_RUNTIME_COMMANDS[normalizedRuntime as SupportedRuntime];
  return fallback?.length ? fallback.join(" ") : normalizedRuntime;
}

export const RUNTIME_PRESETS: RuntimePreset[] = [
  {
    id: "model:deepseek-v4-pro-claude",
    label: "deepseek-v4",
    runtime: "claude",
    model: "DeepSeek-V4-Pro",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "DeepSeek-V4-Pro",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "DeepSeek-V4-Pro",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "DeepSeek-V4-Pro",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "DeepSeek-V4-Pro",
      CLAUDE_CODE_SUBAGENT_MODEL: "DeepSeek-V4-Pro",
    },
  },
  {
    id: "model:qwen3.6-max-claude",
    label: "Qwen3.6",
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
    },
  },
  {
    id: "model:qwen3.6-plus-claude",
    label: "qwen3.6-plus",
    runtime: "claude",
    model: "qwen3.6-plus",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "qwen3.6-plus",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "qwen3.6-plus",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "qwen3.6-plus",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "qwen3.6-plus",
      CLAUDE_CODE_SUBAGENT_MODEL: "qwen3.6-plus",
      ENABLE_TOOL_SEARCH: "true",
    },
  },
  {
    id: "model:qwen3.6-flash-claude",
    label: "qwen3.6-flash",
    runtime: "claude",
    model: "qwen3.6-flash",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "qwen3.6-flash",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "qwen3.6-flash",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "qwen3.6-flash",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "qwen3.6-flash",
      CLAUDE_CODE_SUBAGENT_MODEL: "qwen3.6-flash",
      ENABLE_TOOL_SEARCH: "true",
    },
  },
  {
    id: "model:glm-4.7-claude",
    label: "GLM-4.7",
    runtime: "claude",
    model: "GLM-4.7",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "GLM-4.7",
      ANTHROPIC_DEFAULT_OPUS_MODEL: "GLM-4.7",
      ANTHROPIC_DEFAULT_SONNET_MODEL: "GLM-4.7",
      ANTHROPIC_DEFAULT_HAIKU_MODEL: "GLM-4.7",
      CLAUDE_CODE_SUBAGENT_MODEL: "GLM-4.7",
      ENABLE_TOOL_SEARCH: "true",
    },
  },
  {
    id: "model:doubao-code-claude",
    label: "豆包",
    runtime: "claude",
    model: "doubao-seed-2-0-pro-260215",
    envPrivate: {
      ANTHROPIC_BASE_URL: "https://peer.shierkeji.com/claude",
      ANTHROPIC_MODEL: "doubao-seed-2-0-pro-260215",
    },
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
    label: "kimi",
    runtime: "kimi",
    model: "kimi-k2.6",
    envPrivate: {
      KIMI_BASE_URL: "https://peer.shierkeji.com/v1",
      KIMI_MODEL_NAME: "kimi-k2.6",
    },
  },
  ...[
    ["gpt-5.4", "gpt5.4"],
    ["gpt-5.5", "gpt5.5"],
    ["deepseek-v4-pro", "deepseek-v4-pro"],
    ["deepseek-v4-flash", "deepseek-v4-flash"],
    ["qwen3.6-plus", "qwen3.6-plus"],
    ["qwen3.6-flash", "qwen3.6-flash"],
  ].map(([model, label]) => ({
    id: `model:opencode:${encodeURIComponent(model)}`,
    label,
    runtime: "opencode" as SupportedRuntime,
    model,
  })),
];

export function opencodeRuntimePresetId(model: string): RuntimePresetId {
  return `model:opencode:${encodeURIComponent(String(model || "").trim())}`;
}

export function opencodeRuntimePreset(model: string, label?: string): RuntimePreset | null {
  const normalized = String(model || "").trim();
  if (!normalized) return null;
  return {
    id: opencodeRuntimePresetId(normalized),
    label: String(label || normalized).trim() || normalized,
    runtime: "opencode",
    model: normalized,
  };
}

export function runtimePresetsForModels(models: string[]): RuntimePreset[] {
  const normalizedModels = models
    .map((model) => String(model || "").trim())
    .filter(Boolean);
  if (!normalizedModels.length) return [...RUNTIME_PRESETS];

  const seen = new Set<string>();
  const dynamic = normalizedModels
    .filter((model) => {
      const key = model.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map((model) => opencodeRuntimePreset(model))
    .filter((preset): preset is RuntimePreset => Boolean(preset));
  return [...RUNTIME_PRESETS.filter((preset) => preset.runtime !== "opencode"), ...dynamic];
}

export function runtimePresetById(id: string): RuntimePreset | null {
  const needle = String(id || "").trim();
  if (!needle) return null;
  const existing = RUNTIME_PRESETS.find((preset) => preset.id === needle);
  if (existing) return existing;
  const prefix = "model:opencode:";
  if (!needle.startsWith(prefix)) return null;
  try {
    return opencodeRuntimePreset(decodeURIComponent(needle.slice(prefix.length)));
  } catch {
    return null;
  }
}

export function defaultRuntimePresetFor(runtime: string): RuntimePreset | null {
  const normalizedRuntime = String(runtime || "").trim();
  if (!normalizedRuntime) return null;
  return RUNTIME_PRESETS.find((preset) => preset.runtime === normalizedRuntime) || null;
}

export function runtimePresetIdFor(runtime: string, command: string | string[] | undefined): RuntimePresetId | "" {
  const normalizedRuntime = String(runtime || "").trim();
  const tokens = Array.isArray(command)
    ? command.map((item) => String(item || "").trim()).filter(Boolean)
    : splitCommand(String(command || "").trim());
  const model = modelFromCommand(tokens);
  if (normalizedRuntime === "claude") {
    if (model === "DeepSeek-V4-Pro") return "model:deepseek-v4-pro-claude";
    if (model === "qwen3.6-max-preview") return "model:qwen3.6-max-claude";
    if (model === "qwen3.6-plus") return "model:qwen3.6-plus-claude";
    if (model === "qwen3.6-flash") return "model:qwen3.6-flash-claude";
    if (model === "GLM-4.7") return "model:glm-4.7-claude";
    if (model === "doubao-seed-2-0-pro-260215") return "model:doubao-code-claude";
    return "";
  }
  if (normalizedRuntime === "codex") {
    if (model === "gpt-5.4") return "model:gpt-5.4-codex";
    if (model === "gpt-5.5") return "model:gpt-5.5-codex";
  }
  if (normalizedRuntime === "opencode") {
    const modelId = model.startsWith(`${OPENCODE_PROVIDER_ID}/`)
      ? model.slice(OPENCODE_PROVIDER_ID.length + 1)
      : model;
    if (modelId) return opencodeRuntimePresetId(modelId);
  }
  return "";
}

export function commandHasModelFlag(command: string | string[] | undefined): boolean {
  const tokens = Array.isArray(command)
    ? command.map((item) => String(item || "").trim()).filter(Boolean)
    : splitCommand(String(command || "").trim());
  return Boolean(modelFromCommand(tokens));
}

export function commandForRuntimePreset(preset: RuntimePreset, runtimeInfo?: RuntimeInfo): string {
  const base = splitCommand(String(runtimeInfo?.recommended_command || "").trim());
  const fallback = FALLBACK_RUNTIME_COMMANDS[preset.runtime] || [];
  const command = preset.command && preset.command.length ? preset.command : base.length ? base : fallback;
  if (preset.runtime === "claude" && preset.model) {
    return withCommandModel(command, preset.model, "--model").join(" ");
  }
  if (preset.runtime === "codex" && preset.model) {
    return withCommandModel(command, preset.model, "-m").join(" ");
  }
  if (preset.runtime === "opencode" && preset.model) {
    return withCommandModel(command, `${OPENCODE_PROVIDER_ID}/${preset.model}`, "-m").join(" ");
  }
  return command.join(" ");
}

export function codexReasoningEffortFromCommand(command: string | string[] | undefined): CodexReasoningEffort {
  const tokens = Array.isArray(command)
    ? command.map((item) => String(item || "").trim()).filter(Boolean)
    : splitCommand(String(command || "").trim());
  for (let idx = 0; idx < tokens.length - 1; idx += 1) {
    if (tokens[idx] !== "-c") continue;
    const next = tokens[idx + 1];
    if (!next.startsWith(`${CODEX_REASONING_EFFORT_KEY}=`)) continue;
    return normalizeCodexReasoningEffort(next.slice(CODEX_REASONING_EFFORT_KEY.length + 1));
  }
  return "";
}

export function withCodexReasoningEffort(command: string | string[] | undefined, effort: CodexReasoningEffort): string {
  const tokens = Array.isArray(command)
    ? command.map((item) => String(item || "").trim()).filter(Boolean)
    : splitCommand(String(command || "").trim());
  const normalized = normalizeCodexReasoningEffort(effort);
  const cleaned: string[] = [];
  for (let idx = 0; idx < tokens.length; idx += 1) {
    const item = tokens[idx];
    const next = tokens[idx + 1] || "";
    if (item === "-c" && next.startsWith(`${CODEX_REASONING_EFFORT_KEY}=`)) {
      idx += 1;
      continue;
    }
    cleaned.push(item);
  }
  if (!normalized) return cleaned.join(" ");
  return [...cleaned, "-c", `${CODEX_REASONING_EFFORT_KEY}=${normalized}`].join(" ");
}

export function claudeReasoningEffortFromCommand(command: string | string[] | undefined): ClaudeReasoningEffort {
  const tokens = Array.isArray(command)
    ? command.map((item) => String(item || "").trim()).filter(Boolean)
    : splitCommand(String(command || "").trim());
  for (let idx = 0; idx < tokens.length; idx += 1) {
    const item = tokens[idx];
    if (item === "--effort" && idx + 1 < tokens.length) return normalizeClaudeReasoningEffort(tokens[idx + 1]);
    if (item.startsWith("--effort=")) return normalizeClaudeReasoningEffort(item.slice("--effort=".length));
  }
  return "";
}

export function withClaudeReasoningEffort(command: string | string[] | undefined, effort: ClaudeReasoningEffort): string {
  const tokens = Array.isArray(command)
    ? command.map((item) => String(item || "").trim()).filter(Boolean)
    : splitCommand(String(command || "").trim());
  const normalized = normalizeClaudeReasoningEffort(effort);
  const cleaned: string[] = [];
  for (let idx = 0; idx < tokens.length; idx += 1) {
    const item = tokens[idx];
    if (item === "--effort") {
      idx += 1;
      continue;
    }
    if (item.startsWith("--effort=")) continue;
    cleaned.push(item);
  }
  if (!normalized) return cleaned.join(" ");
  return [...cleaned, "--effort", normalized].join(" ");
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
  const presetKeys = knownAllPresetSecretKeys();
  const authKey = authSecretKeyForRuntimePreset(preset);
  const shouldReplaceAuthToken = Boolean(authKey && String(authToken || "").trim());
  if (!current) return presetText;
  const kept = current
    .split("\n")
    .filter((line) => {
      const key = line.match(/^\s*(?:export\s+|set\s+|\$env:)?([A-Za-z_][A-Za-z0-9_]*)\s*=/i)?.[1];
      if (authKey === "ONECOLLEAGUE_API_KEY" && key === "OPENAI_API_KEY") return true;
      if (authKey && key === authKey && !shouldReplaceAuthToken) return true;
      return !key || !presetKeys.has(key);
    })
    .join("\n")
    .trim();
  if (!presetText.trim()) return kept;
  return kept ? `${kept}\n${presetText}` : presetText;
}

export function mergeRuntimeAuthSecret(existing: string, runtime: string, authToken?: string): string {
  const authKey = authSecretKeyForRuntime(runtime);
  const token = String(authToken || "").trim();
  const current = String(existing || "").trim();
  if (!authKey) return current;
  if (!token) return current;
  const authText = `${authKey}=${quoteEnvValue(token)}`;
  const obsoleteKeys = new Set<string>([authKey]);
  const kept = current
    .split("\n")
    .filter((line) => {
      const key = line.match(/^\s*(?:export\s+|set\s+|\$env:)?([A-Za-z_][A-Za-z0-9_]*)\s*=/i)?.[1];
      return !key || !obsoleteKeys.has(key);
    })
    .join("\n")
    .trim();
  return kept ? `${kept}\n${authText}` : authText;
}

export function mergePresetUnsetKeys(existing: string, preset: RuntimePreset): string {
  const current = String(existing || "").trim();
  const activeKeys = new Set(Object.keys(preset.envPrivate || {}));
  const authKey = authSecretKeyForRuntimePreset(preset);
  if (authKey) activeKeys.add(authKey);
  const keysToUnset = Array.from(knownAllPresetSecretKeys()).filter((key) => !activeKeys.has(key));
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
  if (runtime === "codex" || runtime === "opencode") {
    out.add("ONECOLLEAGUE_API_KEY");
    out.add("OPENAI_API_KEY");
  }
  if (runtime === "kimi") out.add("KIMI_API_KEY");
  return out;
}

function knownAllPresetSecretKeys(): Set<string> {
  const out = new Set<string>();
  for (const preset of RUNTIME_PRESETS) {
    for (const key of Object.keys(preset.envPrivate || {})) out.add(key);
    const authKey = authSecretKeyForRuntimePreset(preset);
    if (authKey) out.add(authKey);
  }
  out.add(CLAUDE_LEGACY_EFFORT_ENV_KEY);
  out.add("OPENAI_API_KEY");
  return out;
}

function authSecretKeyForRuntimePreset(preset: RuntimePreset): string {
  const runtimeAuthKey = authSecretKeyForRuntime(preset.runtime);
  if (runtimeAuthKey) return runtimeAuthKey;
  if (!preset.model) return "";
  if (preset.runtime === "claude") return "ANTHROPIC_AUTH_TOKEN";
  if (preset.runtime === "kimi") return "KIMI_API_KEY";
  return "";
}

function authSecretKeyForRuntime(runtime: string): string {
  if (["codex", "opencode"].includes(String(runtime || "").trim())) return "ONECOLLEAGUE_API_KEY";
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

function normalizeCodexReasoningEffort(value: string): CodexReasoningEffort {
  const normalized = String(value || "").trim().toLowerCase();
  if (
    normalized === "minimal" ||
    normalized === "low" ||
    normalized === "medium" ||
    normalized === "high" ||
    normalized === "xhigh"
  ) {
    return normalized;
  }
  return "";
}

function normalizeClaudeReasoningEffort(value: string): ClaudeReasoningEffort {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "x-high" || normalized === "extra-high") return "xhigh";
  if (
    normalized === "low" ||
    normalized === "medium" ||
    normalized === "high" ||
    normalized === "xhigh" ||
    normalized === "max"
  ) {
    return normalized;
  }
  return "";
}

function parseEnvAssignmentLine(line: string): { key: string; value: string } | null {
  const match = String(line || "").match(/^\s*(?:export\s+|set\s+|\$env:)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/i);
  if (!match) return null;
  const key = String(match[1] || "").trim();
  let value = String(match[2] || "").trim();
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    value = value.slice(1, -1);
  }
  return { key, value };
}

function splitCommand(command: string): string[] {
  return String(command || "").trim().split(/\s+/).filter(Boolean);
}

function quoteEnvValue(value: string): string {
  const raw = String(value ?? "");
  return `"${raw.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}
