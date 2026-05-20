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

  it("builds Claude model commands from the runtime default", () => {
    const preset = runtimePresetById("model:deepseek-v4-pro-claude");
    expect(preset).toBeTruthy();
    expect(
      commandForRuntimePreset(preset!, {
        name: "claude",
        display_name: "Claude Code",
        available: true,
        recommended_command: "claude --dangerously-skip-permissions",
      })
    ).toBe("claude --dangerously-skip-permissions --model deepseek-v4-pro[1m]");
  });

  it("adds the Qwen Claude preset with fixed DashScope environment", () => {
    const preset = runtimePresetById("model:qwen3.6-max-claude");
    expect(preset).toBeTruthy();
    expect(
      commandForRuntimePreset(preset!, {
        name: "claude",
        display_name: "Claude Code",
        available: true,
        recommended_command: "claude --dangerously-skip-permissions",
      })
    ).toBe("claude --dangerously-skip-permissions --model qwen3.6-max-preview");

    const secrets = mergePresetSecrets("", preset!, "ignored-token");
    expect(secrets).toContain('ANTHROPIC_BASE_URL="https://dashscope.aliyuncs.com/apps/anthropic"');
    expect(secrets).toContain('ANTHROPIC_AUTH_TOKEN="sk-ec022412cf4447d092935f05d604a7f4"');
    expect(secrets).toContain('ANTHROPIC_MODEL="qwen3.6-max-preview"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_OPUS_MODEL="qwen3.6-max-preview"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_SONNET_MODEL="qwen3.6-plus"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_HAIKU_MODEL="qwen3.6-flash"');
    expect(secrets).toContain('CLAUDE_CODE_SUBAGENT_MODEL="qwen3.6-plus"');
    expect(secrets).toContain('CLAUDE_CODE_EFFORT_LEVEL="max"');
    expect(secrets).not.toContain("ignored-token");
  });

  it("uses the fixed DeepSeek Claude provider environment", () => {
    const preset = runtimePresetById("model:deepseek-v4-pro-claude");
    expect(preset).toBeTruthy();
    const secrets = mergePresetSecrets("", preset!, "ignored-token");

    expect(secrets).toContain('ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic"');
    expect(secrets).toContain('ANTHROPIC_AUTH_TOKEN="sk-7f21d214c2dc4947b06a7289c357f558"');
    expect(secrets).toContain('ANTHROPIC_MODEL="deepseek-v4-pro[1m]"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek-v4-pro[1m]"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_SONNET_MODEL="deepseek-v4-pro[1m]"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_HAIKU_MODEL="deepseek-v4-flash"');
    expect(secrets).toContain('CLAUDE_CODE_SUBAGENT_MODEL="deepseek-v4-flash"');
    expect(secrets).toContain('CLAUDE_CODE_EFFORT_LEVEL="max"');
    expect(secrets).not.toContain("ignored-token");
  });

  it("replaces stale Claude model command flags", () => {
    const doubao = runtimePresetById("model:doubao-code-claude");
    expect(doubao).toBeTruthy();
    expect(
      commandForRuntimePreset(doubao!, {
        name: "claude",
        display_name: "Claude Code",
        available: true,
        recommended_command: "claude --dangerously-skip-permissions --model old-model",
      })
    ).toBe("claude --dangerously-skip-permissions --model doubao-seed-2-0-pro-260215");
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

  it("adds Kimi model env and preserves existing key without a DoneHub token", () => {
    const kimi = runtimePresetById("model:kimi-k2.6-kimi");
    expect(kimi).toBeTruthy();
    const next = mergePresetSecrets('KIMI_API_KEY="real-token"', kimi!, "");
    expect(next).toContain('KIMI_API_KEY="real-token"');
    expect(next).toContain('KIMI_BASE_URL="https://peer.shierkeji.com/v1"');
    expect(next).toContain('KIMI_MODEL_NAME="kimi-k2.6"');
    expect(next).not.toContain("一号同事的key");
  });

  it("uses the DoneHub token for Kimi API key when available", () => {
    const kimi = runtimePresetById("model:kimi-k2.6-kimi");
    expect(kimi).toBeTruthy();
    const next = mergePresetSecrets('KIMI_API_KEY="old-token"', kimi!, "done-hub-key");
    expect(next).toContain('KIMI_API_KEY="done-hub-key"');
    expect(next).not.toContain("old-token");
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
