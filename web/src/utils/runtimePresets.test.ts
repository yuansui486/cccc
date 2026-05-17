import { describe, expect, it } from "vitest";

import {
  commandForRuntimePreset,
  mergePresetSecrets,
  mergePresetUnsetKeys,
  runtimePresetById,
} from "./runtimePresets";

describe("runtime presets", () => {
  it("builds Codex model commands from the runtime default", () => {
    const preset = runtimePresetById("model:gpt-5.5-codex");
    expect(preset).toBeTruthy();
    expect(
      commandForRuntimePreset(preset!, {
        name: "codex",
        display_name: "Codex CLI",
        available: true,
        recommended_command: "codex -c shell_environment_policy.inherit=all --search",
      })
    ).toBe("codex -c shell_environment_policy.inherit=all --search -m gpt-5.5");
  });

  it("replaces stale Claude preset keys when changing models", () => {
    const doubao = runtimePresetById("model:doubao-code-claude");
    expect(doubao).toBeTruthy();
    const next = mergePresetSecrets(
      'ANTHROPIC_MODEL="deepseek-v4-pro[1m]"\nCLAUDE_CODE_EFFORT_LEVEL="max"\nCUSTOM_FLAG="keep"',
      doubao!,
      "done-hub-key"
    );
    expect(next).toContain('CUSTOM_FLAG="keep"');
    expect(next).toContain('ANTHROPIC_AUTH_TOKEN="done-hub-key"');
    expect(next).toContain('ANTHROPIC_MODEL="doubao-seed-2-0-pro-260215"');
    expect(next).not.toContain("deepseek-v4-pro");
    expect(next).not.toContain("CLAUDE_CODE_EFFORT_LEVEL");
  });

  it("does not overwrite Claude auth token with placeholder text", () => {
    const doubao = runtimePresetById("model:doubao-code-claude");
    expect(doubao).toBeTruthy();
    const next = mergePresetSecrets('ANTHROPIC_AUTH_TOKEN="real-token"', doubao!, "");
    expect(next).toContain('ANTHROPIC_AUTH_TOKEN="real-token"');
    expect(next).not.toContain("一号同事登陆后得到的key");
  });

  it("adds unset keys when switching to a smaller Claude preset", () => {
    const doubao = runtimePresetById("model:doubao-code-claude");
    expect(doubao).toBeTruthy();
    const unset = mergePresetUnsetKeys("", doubao!);
    expect(unset).toContain("ANTHROPIC_DEFAULT_OPUS_MODEL");
    expect(unset).toContain("CLAUDE_CODE_EFFORT_LEVEL");
    expect(unset).not.toContain("ANTHROPIC_MODEL");
    expect(unset).not.toContain("ANTHROPIC_AUTH_TOKEN");
  });
});
