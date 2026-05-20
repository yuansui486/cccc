import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { SUPPORTED_RUNTIMES } from "../../src/types";
import {
  CLAUDE_MODEL_ICON_SRC,
  CLAUDE_MODEL_ICON_PATH,
  DEEPSEEK_MODEL_ICON_SRC,
  DEEPSEEK_MODEL_ICON_PATH,
  DOUBAO_MODEL_ICON_SRC,
  DOUBAO_MODEL_ICON_PATH,
  GEMINI_MODEL_ICON_SRC,
  GEMINI_MODEL_ICON_PATH,
  getActorLogoSrc,
  getRuntimeLogoSrc,
  inferActorModelBrand,
  MINIMAX_MODEL_ICON_SRC,
  MINIMAX_MODEL_ICON_PATH,
  MOONSHOT_MODEL_ICON_SRC,
  MOONSHOT_MODEL_ICON_PATH,
  OPENAI_MODEL_ICON_SRC,
  OPENAI_MODEL_ICON_PATH,
  QWEN_MODEL_ICON_SRC,
  QWEN_MODEL_ICON_PATH,
  RUNTIME_LOGO_FILE_BY_RUNTIME,
} from "../../src/utils/runtimeLogos";

const TEST_DIR = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(TEST_DIR, "../..");
const PUBLIC_ROOT = resolve(WEB_ROOT, "public");

describe("runtimeLogos", () => {
  it("covers every built-in supported runtime except custom", () => {
    const expected = SUPPORTED_RUNTIMES.filter((runtime) => runtime !== "custom").sort();
    const actual = Object.keys(RUNTIME_LOGO_FILE_BY_RUNTIME).sort();
    expect(actual).toEqual(expected);
  });

  it("maps built-in runtimes to local logo assets that exist", () => {
    for (const runtime of Object.keys(RUNTIME_LOGO_FILE_BY_RUNTIME)) {
      const relativePath = RUNTIME_LOGO_FILE_BY_RUNTIME[runtime as keyof typeof RUNTIME_LOGO_FILE_BY_RUNTIME];
      expect(getRuntimeLogoSrc(runtime)).toBe(`${import.meta.env.BASE_URL}${relativePath}`);
      expect(existsSync(resolve(PUBLIC_ROOT, relativePath))).toBe(true);
    }
  });

  it("maps model brand icons to local assets that exist", () => {
    const paths = [
      OPENAI_MODEL_ICON_PATH,
      CLAUDE_MODEL_ICON_PATH,
      QWEN_MODEL_ICON_PATH,
      GEMINI_MODEL_ICON_PATH,
      MINIMAX_MODEL_ICON_PATH,
      DEEPSEEK_MODEL_ICON_PATH,
      MOONSHOT_MODEL_ICON_PATH,
      DOUBAO_MODEL_ICON_PATH,
    ];
    for (const relativePath of paths) {
      expect(resolve(PUBLIC_ROOT, relativePath)).toBeTruthy();
      expect(existsSync(resolve(PUBLIC_ROOT, relativePath))).toBe(true);
    }
  });

  it("returns null for custom or unknown runtimes", () => {
    expect(getRuntimeLogoSrc("custom")).toBeNull();
    expect(getRuntimeLogoSrc("unknown-runtime")).toBeNull();
    expect(getRuntimeLogoSrc("")).toBeNull();
  });

  it("uses the OpenAI icon for Codex and ChatGPT web actors", () => {
    expect(getActorLogoSrc({ runtime: "codex" })).toBe(OPENAI_MODEL_ICON_SRC);
    expect(getActorLogoSrc({ runtime: "web_model" })).toBe(OPENAI_MODEL_ICON_SRC);
  });

  it("keeps room for model-specific icons to override runtime defaults", () => {
    expect(inferActorModelBrand({ command: ["codex", "-m", "deepseek-v4-pro"] })).toBe("deepseek");
    expect(getActorLogoSrc({ runtime: "codex", command: ["codex", "-m", "deepseek-v4-pro"] })).toBe(
      DEEPSEEK_MODEL_ICON_SRC,
    );
    expect(getActorLogoSrc({ runtime: "claude", command: ["claude", "--model", "gpt-5.5"] })).toBe(
      OPENAI_MODEL_ICON_SRC,
    );
  });

  it("maps the configured model families to their requested icons", () => {
    expect(getActorLogoSrc({ command: ["codex", "-m", "claude-3.7-sonnet"] })).toBe(CLAUDE_MODEL_ICON_SRC);
    expect(getActorLogoSrc({ command: ["codex", "-m", "qwen-max"] })).toBe(QWEN_MODEL_ICON_SRC);
    expect(getActorLogoSrc({ command: ["codex", "-m", "gemini-2.5-pro"] })).toBe(GEMINI_MODEL_ICON_SRC);
    expect(getActorLogoSrc({ command: ["codex", "-m", "minimax-m1"] })).toBe(MINIMAX_MODEL_ICON_SRC);
    expect(getActorLogoSrc({ command: ["codex", "-m", "moonshot-v1-128k"] })).toBe(MOONSHOT_MODEL_ICON_SRC);
    expect(getActorLogoSrc({ command: ["codex", "-m", "doubao-seed-2-0-pro-250528"] })).toBe(
      DOUBAO_MODEL_ICON_SRC,
    );
  });
});
