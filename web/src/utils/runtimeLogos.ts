import type { SupportedRuntime } from "../types";

type RuntimeLogoRuntime = Exclude<SupportedRuntime, "custom">;
type ActorLogoRuntimeHints = string | string[] | null | undefined;
type ActorLogoEnv = Record<string, string> | null | undefined;

export type ActorModelBrand =
  | "openai"
  | "anthropic"
  | "google"
  | "deepseek"
  | "doubao"
  | "kimi"
  | "qwen"
  | "minimax";

function assetSrc(relativePath: string): string {
  return `${import.meta.env.BASE_URL}${relativePath}`;
}

export const OPENAI_MODEL_ICON_PATH = "logos/models/openai.svg";
export const CLAUDE_MODEL_ICON_PATH = "logos/models/claude-color.svg";
export const QWEN_MODEL_ICON_PATH = "logos/models/qwen-color.svg";
export const GEMINI_MODEL_ICON_PATH = "logos/models/gemini-color.svg";
export const MINIMAX_MODEL_ICON_PATH = "logos/models/minimax-color.svg";
export const DEEPSEEK_MODEL_ICON_PATH = "logos/models/deepseek-color.svg";
export const MOONSHOT_MODEL_ICON_PATH = "logos/models/moonshot.svg";
export const DOUBAO_MODEL_ICON_PATH = "logos/models/doubao_avatar.png";

export const OPENAI_MODEL_ICON_SRC = assetSrc(OPENAI_MODEL_ICON_PATH);
export const CLAUDE_MODEL_ICON_SRC = assetSrc(CLAUDE_MODEL_ICON_PATH);
export const QWEN_MODEL_ICON_SRC = assetSrc(QWEN_MODEL_ICON_PATH);
export const GEMINI_MODEL_ICON_SRC = assetSrc(GEMINI_MODEL_ICON_PATH);
export const MINIMAX_MODEL_ICON_SRC = assetSrc(MINIMAX_MODEL_ICON_PATH);
export const DEEPSEEK_MODEL_ICON_SRC = assetSrc(DEEPSEEK_MODEL_ICON_PATH);
export const MOONSHOT_MODEL_ICON_SRC = assetSrc(MOONSHOT_MODEL_ICON_PATH);
export const DOUBAO_MODEL_ICON_SRC = assetSrc(DOUBAO_MODEL_ICON_PATH);

export const RUNTIME_LOGO_FILE_BY_RUNTIME: Partial<Record<RuntimeLogoRuntime, string>> = {
  amp: "logos/amp.png",
  auggie: "logos/auggie.png",
  claude: "logos/claude.png",
  codex: "logos/codex.png",
  droid: "logos/droid.png",
  gemini: "logos/gemini.png",
  hermes: "logos/hermes.svg",
  kimi: "logos/kimi.png",
  neovate: "logos/neovate.png",
  web_model: "logo.png",
};

function normalizeRuntime(runtime: string | null | undefined): RuntimeLogoRuntime {
  return String(runtime || "").trim().toLowerCase() as RuntimeLogoRuntime;
}

function normalizeText(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

function commandTokens(command: ActorLogoRuntimeHints): string[] {
  if (Array.isArray(command)) {
    return command.map((item) => String(item || "").trim()).filter(Boolean);
  }
  const raw = String(command || "").trim();
  return raw ? raw.split(/\s+/).filter(Boolean) : [];
}

function collectCommandModelHints(command: ActorLogoRuntimeHints): string[] {
  const tokens = commandTokens(command);
  const out: string[] = [];
  for (let idx = 0; idx < tokens.length; idx += 1) {
    const item = tokens[idx];
    if ((item === "-m" || item === "--model") && idx + 1 < tokens.length) {
      out.push(tokens[idx + 1]);
      continue;
    }
    if (item.startsWith("--model=")) {
      out.push(item.split("=", 2)[1] || "");
    }
  }
  return out.map((item) => String(item || "").trim()).filter(Boolean);
}

function collectEnvModelHints(env: ActorLogoEnv): string[] {
  if (!env) return [];
  const keys = [
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "OPENAI_MODEL",
    "KIMI_MODEL_NAME",
    "GOOGLE_MODEL",
    "GEMINI_MODEL",
    "MODEL",
  ];
  return keys.map((key) => String(env[key] || "").trim()).filter(Boolean);
}

function brandFromHint(text: string): ActorModelBrand | null {
  const normalized = normalizeText(text);
  if (!normalized) return null;
  if (/\b(deepseek)\b/.test(normalized)) return "deepseek";
  if (/\b(doubao)\b/.test(normalized)) return "doubao";
  if (/\b(kimi|moonshot)\b/.test(normalized)) return "kimi";
  if (/\b(minimax|abab)\b/.test(normalized)) return "minimax";
  if (/\b(qwen)\b/.test(normalized)) return "qwen";
  if (/\b(gemini|google)\b/.test(normalized)) return "google";
  if (/\b(claude|anthropic)\b/.test(normalized)) return "anthropic";
  if (/\b(chatgpt|openai|codex|gpt(?:-[a-z0-9.]+)?|o[134](?:-[a-z0-9.]+)?)\b/.test(normalized)) {
    return "openai";
  }
  return null;
}

export function inferActorModelBrand(args: {
  title?: string | null;
  command?: ActorLogoRuntimeHints;
  env?: ActorLogoEnv;
  modelHint?: string | null;
}): ActorModelBrand | null {
  const candidates = [
    String(args.modelHint || "").trim(),
    ...collectCommandModelHints(args.command),
    ...collectEnvModelHints(args.env),
    String(args.title || "").trim(),
  ].filter(Boolean);
  for (const candidate of candidates) {
    const brand = brandFromHint(candidate);
    if (brand) return brand;
  }
  return null;
}

const MODEL_ICON_SRC_BY_BRAND: Partial<Record<ActorModelBrand, string>> = {
  openai: OPENAI_MODEL_ICON_SRC,
  anthropic: CLAUDE_MODEL_ICON_SRC,
  google: GEMINI_MODEL_ICON_SRC,
  deepseek: DEEPSEEK_MODEL_ICON_SRC,
  doubao: DOUBAO_MODEL_ICON_SRC,
  kimi: MOONSHOT_MODEL_ICON_SRC,
  qwen: QWEN_MODEL_ICON_SRC,
  minimax: MINIMAX_MODEL_ICON_SRC,
};

export function getActorLogoSrc(args: {
  runtime?: string | null;
  title?: string | null;
  command?: ActorLogoRuntimeHints;
  env?: ActorLogoEnv;
  modelHint?: string | null;
}): string | null {
  const explicitBrand = inferActorModelBrand(args);
  if (explicitBrand) {
    return MODEL_ICON_SRC_BY_BRAND[explicitBrand] || null;
  }
  const runtime = normalizeRuntime(args.runtime);
  if (runtime === "codex" || runtime === "web_model") {
    return OPENAI_MODEL_ICON_SRC;
  }
  return null;
}

export function getRuntimeLogoSrc(runtime: string | null | undefined): string | null {
  const relativePath = RUNTIME_LOGO_FILE_BY_RUNTIME[normalizeRuntime(runtime)];
  return relativePath ? assetSrc(relativePath) : null;
}
