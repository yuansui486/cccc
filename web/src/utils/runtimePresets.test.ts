import { describe, expect, it } from "vitest";

import { buildRuntimeChoiceGroups, buildRuntimeModelOptions, buildRuntimeSelectorOptions } from "./runtimeChoiceGroups";
import {
  claudeReasoningEffortFromCommand,
  clearKnownPresetSecrets,
  commandHasModelFlag,
  codexReasoningEffortFromCommand,
  commandForRuntimePreset,
  defaultCommandForRuntime,
  defaultRuntimePresetFor,
  knownPresetSecretKeysForRuntime,
  mergePresetSecrets,
  mergeAllPresetUnsetKeys,
  mergePresetUnsetKeys,
  mergeRuntimeAuthSecret,
  modelFromRuntimeConfiguration,
  needsDedicatedOneColleagueKey,
  openclawRuntimePresetId,
  opencodeDeepSeekModelFromCommand,
  opencodeRuntimePreset,
  opencodeRuntimePresetId,
  runtimePresetById,
  runtimePresetIdFor,
  runtimePresetForModel,
  runtimeSupportsModelSelection,
  validateRuntimeModelId,
  withClaudeReasoningEffort,
  withCodexReasoningEffort,
  withRuntimeModel,
} from "./runtimePresets";

describe("runtime presets", () => {
  it("detects DeepSeek models that support OpenCode reasoning variants", () => {
    expect(opencodeDeepSeekModelFromCommand("opencode -m onecolleague/deepseek-v4-pro")).toBe("deepseek-v4-pro");
    expect(opencodeDeepSeekModelFromCommand(["opencode", "--model=deepseek-v4-flash"])).toBe("deepseek-v4-flash");
    expect(opencodeDeepSeekModelFromCommand("opencode -m onecolleague/gpt-5.4")).toBe("");
  });

  it("provides shared runtime default commands for profile editors", () => {
    expect(defaultCommandForRuntime("opencode")).toBe("opencode --auto");
    expect(defaultCommandForRuntime("codex")).toBe(
      "codex -c shell_environment_policy.inherit=all --dangerously-bypass-approvals-and-sandbox --search"
    );
    expect(defaultCommandForRuntime("gemini")).toBe("gemini --yolo");
    expect(defaultCommandForRuntime("openclaw")).toBe("openclaw tui");
    expect(defaultCommandForRuntime("custom-runtime")).toBe("custom-runtime");
    expect(defaultCommandForRuntime("  ")).toBe("");
  });

  it("separates runtime installation state from the selected runtime model", () => {
    const runtimes = buildRuntimeSelectorOptions([
      { name: "codex", display_name: "Codex CLI", available: true, recommended_command: "codex" },
      { name: "claude", display_name: "Claude Code", available: false, recommended_command: "claude" },
    ]);
    expect(runtimes.find((item) => item.value === "codex")?.disabled).toBe(false);
    expect(runtimes.find((item) => item.value === "claude")?.disabled).toBe(true);

    const codexModels = buildRuntimeModelOptions("codex");
    expect(codexModels.map((item) => item.value)).toEqual([
      "gpt-5.4",
      "gpt-5.5",
      "gpt-5.6-sol",
      "gpt-5.6-terra",
      "deepseek-v4-flash",
      "deepseek-v4-pro",
    ]);
    expect(buildRuntimeModelOptions("claude").every((item) => item.preset.runtime === "claude")).toBe(true);
    expect(buildRuntimeModelOptions("opencode", ["catalog-model"]).map((item) => item.value)).toEqual(["catalog-model"]);
    expect(buildRuntimeModelOptions("hermes", ["catalog-model"]).map((item) => item.value)).toEqual(["catalog-model"]);
    expect(buildRuntimeModelOptions("openclaw", ["claude-cli/claude-sonnet-4-6"]).map((item) => item.value)).toEqual([
      "claude-cli/claude-sonnet-4-6",
    ]);
    expect(buildRuntimeModelOptions("openclaw", ["catalog-model"]).map((item) => item.value)).toEqual(
      buildRuntimeModelOptions("opencode", ["catalog-model"]).map((item) => item.value),
    );
  });

  it("reads, replaces, and clears models using each known runtime protocol", () => {
    expect(modelFromRuntimeConfiguration("codex", "codex --search -m gpt-5.5")).toBe("gpt-5.5");
    expect(modelFromRuntimeConfiguration("codex", "codex --search -m=gpt-5.6-sol")).toBe("gpt-5.6-sol");
    expect(modelFromRuntimeConfiguration("opencode", "opencode -m onecolleague/deepseek-v4-pro")).toBe("deepseek-v4-pro");
    expect(modelFromRuntimeConfiguration("kimi", "kimi --yolo", "kimi-k2.6")).toBe("kimi-k2.6");
    expect(modelFromRuntimeConfiguration("hermes", "hermes --tui", "deepseek-v4-pro")).toBe("deepseek-v4-pro");
    expect(modelFromRuntimeConfiguration("openclaw", "openclaw", "claude-cli/claude-sonnet-4-6")).toBe(
      "claude-cli/claude-sonnet-4-6"
    );
    expect(modelFromRuntimeConfiguration("openclaw", "openclaw", "onecolleague/gpt-5.5")).toBe("gpt-5.5");
    expect(withRuntimeModel("codex", "codex -m old --search", "gpt-5.5")).toBe("codex --search -m gpt-5.5");
    expect(withRuntimeModel("codex", "codex -m=old --search", "gpt-5.6-sol")).toBe(
      "codex --search -m gpt-5.6-sol"
    );
    expect(withRuntimeModel("claude", "claude --model=old --effort high", "new-model")).toBe(
      "claude --effort high --model new-model"
    );
    expect(withRuntimeModel("gemini", "gemini --model old --yolo", "")).toBe("gemini --yolo");
    expect(withRuntimeModel("opencode", "opencode --auto", "new-model")).toBe(
      "opencode --auto -m onecolleague/new-model"
    );
    expect(withRuntimeModel("kimi", "kimi --yolo", "kimi-k2.6")).toBe("kimi --yolo");
    expect(withRuntimeModel("hermes", "hermes --tui --yolo", "deepseek-v4-pro")).toBe("hermes --tui --yolo");
    expect(withRuntimeModel("openclaw", "openclaw --model old/model", "provider/new-model")).toBe("openclaw");
  });

  it("allows manual models only for known protocols and rejects unsafe IDs", () => {
    expect(runtimeSupportsModelSelection("codex")).toBe(true);
    expect(runtimeSupportsModelSelection("kimi")).toBe(true);
    expect(runtimeSupportsModelSelection("hermes")).toBe(true);
    expect(runtimeSupportsModelSelection("openclaw")).toBe(true);
    expect(runtimeSupportsModelSelection("droid")).toBe(false);
    expect(validateRuntimeModelId("onecolleague/deepseek-v4-pro")).toBe("");
    expect(validateRuntimeModelId(" model with spaces ")).not.toBe("");
    expect(validateRuntimeModelId("model;Remove-Item")).not.toBe("");
    expect(runtimePresetForModel("opencode", "new-model", ["new-model"])?.model).toBe("new-model");
  });

  it("configures Hermes models through the OneColleague provider", () => {
    const preset = runtimePresetForModel("hermes", "deepseek-v4-pro", ["deepseek-v4-pro"]);
    expect(preset).toBeTruthy();
    expect(preset?.envPrivate).toEqual({
      ONECOLLEAGUE_OPENCODE_BASE_URL: "https://peer.shierkeji.com/v1",
    });
    const secrets = mergePresetSecrets("", preset!, "done-hub-key");
    expect(secrets).toContain('ONECOLLEAGUE_OPENCODE_BASE_URL="https://peer.shierkeji.com/v1"');
    expect(secrets).toContain('ONECOLLEAGUE_API_KEY="done-hub-key"');
  });

  it("clears stale preset secrets when switching to a runtime without a model preset", () => {
    const current = 'ANTHROPIC_MODEL="old"\nONECOLLEAGUE_API_KEY="old-key"\nCUSTOM_FLAG="keep"';
    expect(clearKnownPresetSecrets(current)).toBe('CUSTOM_FLAG="keep"');
    const unset = mergeAllPresetUnsetKeys("CUSTOM_OLD");
    expect(unset).toContain("ANTHROPIC_MODEL");
    expect(unset).toContain("ONECOLLEAGUE_API_KEY");
    expect(unset).toContain("CUSTOM_OLD");
  });

  it("warns when a Peer runtime only has an ordinary OpenAI key", () => {
    expect(needsDedicatedOneColleagueKey("opencode", 'OPENAI_API_KEY="openai"')).toBe(true);
    expect(needsDedicatedOneColleagueKey("codex", '$env:OPENAI_API_KEY = "openai"')).toBe(true);
    expect(needsDedicatedOneColleagueKey("openclaw", 'OPENAI_API_KEY="openai"')).toBe(true);
    expect(needsDedicatedOneColleagueKey("opencode", 'OPENAI_API_KEY="openai"\nONECOLLEAGUE_API_KEY="peer"')).toBe(false);
    expect(needsDedicatedOneColleagueKey("openclaw", 'ONECOLLEAGUE_API_KEY="peer"')).toBe(false);
    expect(needsDedicatedOneColleagueKey("claude", 'OPENAI_API_KEY="openai"')).toBe(false);
    expect(mergeRuntimeAuthSecret('CUSTOM_FLAG="keep"', "openclaw", "done-hub-key")).toBe(
      'CUSTOM_FLAG="keep"\nONECOLLEAGUE_API_KEY="done-hub-key"',
    );
    expect(knownPresetSecretKeysForRuntime("openclaw")).toContain("ONECOLLEAGUE_API_KEY");
  });

  it("shows OpenCode as a selectable runtime when discovered", () => {
    const groups = buildRuntimeChoiceGroups([
      { name: "opencode", display_name: "OpenCode", available: true, recommended_command: "opencode" },
    ]);

    expect(groups.find((group) => group.labelKey === "runtimeGroupOpenCode")?.options).toEqual([
      { kind: "runtime", id: "opencode", label: "OpenCode", runtime: "opencode", disabled: false },
    ]);
  });

  it("adds dynamic OpenCode models and uses the OneColleague provider prefix", () => {
    const groups = buildRuntimeChoiceGroups(
      [{ name: "opencode", display_name: "OpenCode", available: true, recommended_command: "opencode" }],
      ["gpt-5.4", "deepseek-v4-pro", "qwen3.6-plus", "new-model"],
    );
    const options = groups.find((group) => group.labelKey === "runtimeGroupOpenCode")?.options || [];

    expect(options.map((option) => option.id)).toContain(opencodeRuntimePresetId("new-model"));
    expect(options.map((option) => option.id)).not.toContain(opencodeRuntimePresetId("gpt-5.5"));
    const preset = opencodeRuntimePreset("new-model");
    expect(preset).toBeTruthy();
    expect(commandForRuntimePreset(preset!, {
      name: "opencode",
      display_name: "OpenCode",
      available: true,
      recommended_command: "opencode --auto",
    })).toBe("opencode --auto -m onecolleague/new-model");
    expect(runtimePresetIdFor("opencode", "opencode --auto -m onecolleague/new-model")).toBe(opencodeRuntimePresetId("new-model"));
    expect(mergePresetSecrets("", preset!, "done-hub-key")).toBe('ONECOLLEAGUE_API_KEY="done-hub-key"');
  });

  it("adds discovered OpenClaw models without changing the launch command", () => {
    const model = "claude-cli/claude-sonnet-4-6";
    const groups = buildRuntimeChoiceGroups(
      [{
          name: "openclaw",
          display_name: "OpenClaw",
          available: true,
          recommended_command: "openclaw",
          models: [model],
      }],
    );
    const options = groups.find((group) => group.labelKey === "runtimeGroupOpenClaw")?.options || [];

    expect(options.map((option) => option.id)).toEqual([openclawRuntimePresetId(model)]);
    expect(options[0]?.label).toBe(model);
    const preset = runtimePresetForModel("openclaw", model, [model]);
    expect(preset?.model).toBe(model);
    expect(commandForRuntimePreset(preset!, {
      name: "openclaw",
      display_name: "OpenClaw",
      available: true,
      recommended_command: "openclaw",
    })).toBe("openclaw");
  });

  it("groups model choices by CLI family", () => {
    const groups = buildRuntimeChoiceGroups([
      { name: "claude", display_name: "Claude Code", available: true, recommended_command: "claude" },
      { name: "codex", display_name: "Codex CLI", available: true, recommended_command: "codex" },
      { name: "gemini", display_name: "Gemini CLI", available: true, recommended_command: "gemini" },
      { name: "kimi", display_name: "Kimi CLI", available: true, recommended_command: "kimi" },
    ]);

    expect(groups.map((group) => group.labelKey)).toEqual([
      "runtimeGroupCodex",
      "runtimeGroupClaude",
      "runtimeGroupGemini",
      "runtimeGroupKimi",
      "runtimeGroupOpenCode",
      "runtimeGroupOpenClaw",
    ]);
    expect(groups.find((group) => group.labelKey === "runtimeGroupClaude")?.options.map((option) => option.id)).toEqual([
      "model:deepseek-v4-pro-claude",
      "model:qwen3.6-max-claude",
      "model:qwen3.6-plus-claude",
      "model:qwen3.6-flash-claude",
      "model:glm-4.7-claude",
      "model:doubao-code-claude",
    ]);
    expect(groups.find((group) => group.labelKey === "runtimeGroupCodex")?.options.map((option) => option.id)).toEqual([
      "model:gpt-5.4-codex",
      "model:gpt-5.5-codex",
      "model:gpt-5.6-sol-codex",
      "model:gpt-5.6-terra-codex",
      "model:deepseek-v4-flash-codex",
      "model:deepseek-v4-pro-codex",
    ]);
    expect(groups.find((group) => group.labelKey === "runtimeGroupGemini")?.options.map((option) => option.id)).toEqual(["gemini"]);
    expect(groups.find((group) => group.labelKey === "runtimeGroupKimi")?.options.map((option) => option.id)).toEqual([
      "model:kimi-k2.6-kimi",
    ]);
  });

  it("shows model names without price decorations", () => {
    const groups = buildRuntimeChoiceGroups(
      [
        { name: "claude", display_name: "Claude Code", available: true, recommended_command: "claude" },
        { name: "codex", display_name: "Codex CLI", available: true, recommended_command: "codex" },
        { name: "gemini", display_name: "Gemini CLI", available: true, recommended_command: "gemini" },
        { name: "kimi", display_name: "Kimi CLI", available: true, recommended_command: "kimi" },
      ],
    );

    expect(groups.find((group) => group.labelKey === "runtimeGroupCodex")?.options.map((option) => option.label)).toEqual([
      "gpt5.4",
      "gpt5.5",
      "gpt-5.6-sol",
      "gpt-5.6-terra",
      "deepseek-v4-flash",
      "deepseek-v4-pro",
    ]);
    expect(groups.find((group) => group.labelKey === "runtimeGroupClaude")?.options[0]?.label).toBe("deepseek-v4");
    expect(groups.find((group) => group.labelKey === "runtimeGroupKimi")?.options[0]?.label).toBe("kimi");
  });

  it.each([
    ["model:gpt-5.6-sol-codex", "gpt-5.6-sol"],
    ["model:gpt-5.6-terra-codex", "gpt-5.6-terra"],
    ["model:deepseek-v4-flash-codex", "deepseek-v4-flash"],
    ["model:deepseek-v4-pro-codex", "deepseek-v4-pro"],
  ])("round-trips the added Codex preset %s", (presetId, model) => {
    const preset = runtimePresetById(presetId);
    expect(preset?.model).toBe(model);

    const command = commandForRuntimePreset(preset!, {
      name: "codex",
      display_name: "Codex CLI",
      available: true,
      recommended_command: "codex --search",
    });
    expect(command).toBe(`codex --search -m ${model}`);
    expect(runtimePresetIdFor("codex", command)).toBe(presetId);
  });

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
    expect(
      commandForRuntimePreset(preset!, {
        name: "codex",
        display_name: "Codex CLI",
        available: true,
        recommended_command: "codex --search -m=old",
      })
    ).toBe("codex --search -m=gpt-5.5");
  });

  it("uses the first Codex preset as the dynamic default model command", () => {
    const preset = defaultRuntimePresetFor("codex");

    expect(preset?.id).toBe("model:gpt-5.4-codex");
    expect(
      commandForRuntimePreset(preset!, {
        name: "codex",
        display_name: "Codex CLI",
        available: true,
        recommended_command: "codex -c shell_environment_policy.inherit=all --search",
      })
    ).toBe("codex -c shell_environment_policy.inherit=all --search -m gpt-5.4");
  });

  it("builds full preset commands when runtime discovery has not loaded yet", () => {
    const codex = runtimePresetById("model:gpt-5.4-codex");
    const claude = runtimePresetById("model:deepseek-v4-pro-claude");

    expect(commandForRuntimePreset(codex!)).toBe(
      "codex -c shell_environment_policy.inherit=all --dangerously-bypass-approvals-and-sandbox --search -m gpt-5.4"
    );
    expect(commandForRuntimePreset(claude!)).toBe("claude --dangerously-skip-permissions --model DeepSeek-V4-Pro");
  });

  it("detects when a CLI command already specifies a model", () => {
    expect(commandHasModelFlag("codex --search")).toBe(false);
    expect(commandHasModelFlag("codex --search -m gpt-5.4")).toBe(true);
    expect(commandHasModelFlag("claude --model=DeepSeek-V4-Pro")).toBe(true);
  });

  it("adds, reads, replaces, and clears Codex reasoning effort", () => {
    const base = "codex -c shell_environment_policy.inherit=all --search -m gpt-5.5";
    const xhighCommand = withCodexReasoningEffort(base, "xhigh");

    expect(xhighCommand).toBe("codex -c shell_environment_policy.inherit=all --search -m gpt-5.5 -c model_reasoning_effort=xhigh");
    expect(codexReasoningEffortFromCommand(xhighCommand)).toBe("xhigh");
    expect(withCodexReasoningEffort(xhighCommand, "low")).toBe(
      "codex -c shell_environment_policy.inherit=all --search -m gpt-5.5 -c model_reasoning_effort=low"
    );
    expect(withCodexReasoningEffort(xhighCommand, "")).toBe(base);
  });

  it("adds, reads, replaces, and clears Claude Code reasoning effort", () => {
    const base = "claude --model DeepSeek-V4-Pro";
    const high = withClaudeReasoningEffort(base, "high");

    expect(high).toBe("claude --model DeepSeek-V4-Pro --effort high");
    expect(runtimePresetIdFor("claude", high)).toBe("model:deepseek-v4-pro-claude");
    expect(claudeReasoningEffortFromCommand(high)).toBe("high");
    expect(claudeReasoningEffortFromCommand("claude --effort=max")).toBe("max");
    expect(claudeReasoningEffortFromCommand("claude --effort x-high")).toBe("xhigh");
    expect(withClaudeReasoningEffort(high, "xhigh")).toBe("claude --model DeepSeek-V4-Pro --effort xhigh");
    expect(withClaudeReasoningEffort(high, "max")).toBe("claude --model DeepSeek-V4-Pro --effort max");
    expect(withClaudeReasoningEffort(high, "")).toBe(base);
  });

  it("adds only the API key env for Codex model presets", () => {
    const preset = runtimePresetById("model:gpt-5.4-codex");
    expect(preset).toBeTruthy();
    const secrets = mergePresetSecrets("", preset!, "done-hub-key");

    expect(secrets).toBe('ONECOLLEAGUE_API_KEY="done-hub-key"');
    expect(secrets).not.toContain("OPENAI_BASE_URL");
    expect(secrets).not.toContain("OPENAI_MODEL");
  });

  it("adds the Codex API key from runtime auth even without a model preset", () => {
    const secrets = mergeRuntimeAuthSecret("", "codex", "done-hub-key");

    expect(secrets).toBe('ONECOLLEAGUE_API_KEY="done-hub-key"');
  });

  it("keeps an unrelated OpenAI key when adding the OneColleague runtime key", () => {
    const secrets = mergeRuntimeAuthSecret('OPENAI_API_KEY="old-key"', "codex", "done-hub-key");

    expect(secrets).toBe('OPENAI_API_KEY="old-key"\nONECOLLEAGUE_API_KEY="done-hub-key"');
  });

  it("preserves existing runtime auth when no Codex token is available", () => {
    const secrets = mergeRuntimeAuthSecret('ONECOLLEAGUE_API_KEY="manual-key"', "codex", "");

    expect(secrets).toBe('ONECOLLEAGUE_API_KEY="manual-key"');
  });

  it("preserves an existing Codex API key when no DoneHub token is available", () => {
    const preset = runtimePresetById("model:gpt-5.5-codex");
    expect(preset).toBeTruthy();
    const secrets = mergePresetSecrets('ONECOLLEAGUE_API_KEY="manual-key"', preset!, "");

    expect(secrets).toBe('ONECOLLEAGUE_API_KEY="manual-key"');
    expect(secrets).not.toContain("OPENAI_BASE_URL");
    expect(secrets).not.toContain("OPENAI_MODEL");
  });

  it("keeps an unrelated OpenAI key when applying a OneColleague model preset", () => {
    const preset = runtimePresetById("model:gpt-5.5-codex");
    expect(preset).toBeTruthy();
    const secrets = mergePresetSecrets('OPENAI_API_KEY="old-key"', preset!, "done-hub-key");

    expect(secrets).toBe('OPENAI_API_KEY="old-key"\nONECOLLEAGUE_API_KEY="done-hub-key"');
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
    ).toBe("claude --dangerously-skip-permissions --model DeepSeek-V4-Pro");
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
    expect(secrets).not.toContain("CLAUDE_CODE_EFFORT_LEVEL");
    expect(secrets).not.toContain("ignored-token");
  });

  it("adds Peer Claude presets for Qwen plus, Qwen flash, and GLM", () => {
    const cases = [
      { id: "model:qwen3.6-plus-claude", model: "qwen3.6-plus" },
      { id: "model:qwen3.6-flash-claude", model: "qwen3.6-flash" },
      { id: "model:glm-4.7-claude", model: "GLM-4.7" },
    ] as const;

    for (const item of cases) {
      const preset = runtimePresetById(item.id);
      expect(preset).toBeTruthy();
      expect(runtimePresetIdFor("claude", `claude --model ${item.model}`)).toBe(item.id);
      expect(
        commandForRuntimePreset(preset!, {
          name: "claude",
          display_name: "Claude Code",
          available: true,
          recommended_command: "claude --dangerously-skip-permissions",
        })
      ).toBe(`claude --dangerously-skip-permissions --model ${item.model}`);

      const secrets = mergePresetSecrets("", preset!, "done-hub-key");
      expect(secrets).toContain('ANTHROPIC_BASE_URL="https://peer.shierkeji.com/claude"');
      expect(secrets).toContain('ANTHROPIC_AUTH_TOKEN="done-hub-key"');
      expect(secrets).toContain(`ANTHROPIC_MODEL="${item.model}"`);
      expect(secrets).toContain(`ANTHROPIC_DEFAULT_OPUS_MODEL="${item.model}"`);
      expect(secrets).toContain(`ANTHROPIC_DEFAULT_SONNET_MODEL="${item.model}"`);
      expect(secrets).toContain(`ANTHROPIC_DEFAULT_HAIKU_MODEL="${item.model}"`);
      expect(secrets).toContain(`CLAUDE_CODE_SUBAGENT_MODEL="${item.model}"`);
      expect(secrets).toContain('ENABLE_TOOL_SEARCH="true"');
    }
  });

  it("uses the fixed DeepSeek Claude provider environment", () => {
    const preset = runtimePresetById("model:deepseek-v4-pro-claude");
    expect(preset).toBeTruthy();
    const secrets = mergePresetSecrets("", preset!, "done-hub-key");

    expect(secrets).toContain('ANTHROPIC_BASE_URL="https://peer.shierkeji.com/claude"');
    expect(secrets).toContain('ANTHROPIC_AUTH_TOKEN="done-hub-key"');
    expect(secrets).toContain('ANTHROPIC_MODEL="DeepSeek-V4-Pro"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_OPUS_MODEL="DeepSeek-V4-Pro"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_SONNET_MODEL="DeepSeek-V4-Pro"');
    expect(secrets).toContain('ANTHROPIC_DEFAULT_HAIKU_MODEL="DeepSeek-V4-Pro"');
    expect(secrets).toContain('CLAUDE_CODE_SUBAGENT_MODEL="DeepSeek-V4-Pro"');
    expect(secrets).not.toContain("CLAUDE_CODE_EFFORT_LEVEL");
    expect(secrets).not.toContain("api.deepseek.com");
  });

  it("clears stale DeepSeek keys when switching to a Codex preset", () => {
    const deepseek = runtimePresetById("model:deepseek-v4-pro-claude");
    const codex = runtimePresetById("model:gpt-5.4-codex");
    expect(deepseek).toBeTruthy();
    expect(codex).toBeTruthy();
    const current = `${mergePresetSecrets("", deepseek!, "done-hub-key")}\nCUSTOM_FLAG="keep"`;
    const next = mergePresetSecrets(current, codex!, "done-hub-key");

    expect(next).toBe('CUSTOM_FLAG="keep"\nONECOLLEAGUE_API_KEY="done-hub-key"');
    expect(next).not.toContain("ANTHROPIC_");
    expect(next).not.toContain("CLAUDE_CODE_");
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
