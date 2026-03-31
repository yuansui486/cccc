import { describe, expect, it } from "vitest";
import {
  appendTerminalSignalBuffer,
  getActorDisplayWorkingState,
  getTerminalSignalFromChunk,
  isCodexWorkingBannerVisible,
  isTerminalPromptVisible,
} from "../../src/utils/terminalWorkingState";

describe("terminalWorkingState", () => {
  it("detects codex-style prompt lines after ansi stripping", () => {
    const buffer = appendTerminalSignalBuffer("", "\u001b[32m> Improve documentation in @filename\u001b[0m\n");
    expect(isTerminalPromptVisible(buffer)).toBe(true);
  });

  it("marks prompt-visible chunks as idle prompt signals", () => {
    const result = getTerminalSignalFromChunk("", "> Improve documentation in @filename\n", "bash");
    expect(result.signalKind).toBe("idle_prompt");
  });

  it("detects codex working banner lines", () => {
    expect(isCodexWorkingBannerVisible("◦ Working (6s • esc to interrupt)\n")).toBe(true);
  });

  it("treats codex working banner as stronger than the input box", () => {
    const result = getTerminalSignalFromChunk(
      "",
      "◦ Working (6s • esc to interrupt)\n› Run /review on my current changes\n",
      "codex",
    );
    expect(result.signalKind).toBe("working_output");
  });

  it("treats codex input box as idle only when the working banner is absent", () => {
    const result = getTerminalSignalFromChunk("", "› Run /review on my current changes\n", "codex");
    expect(result.signalKind).toBe("idle_prompt");
  });

  it("keeps codex in working when the latest signal is the working banner", () => {
    const result = getTerminalSignalFromChunk(
      "",
      "› Run /review on my current changes\n◦ Working (6s • esc to interrupt)\n",
      "codex",
    );
    expect(result.signalKind).toBe("working_output");
  });

  it("does not keep codex in working from older banner lines", () => {
    const result = getTerminalSignalFromChunk(
      "",
      "header\nmodel: gpt-5.4\n directory: ~/Desktop\n◦ Working (6s • esc to interrupt)\n",
      "codex",
    );
    expect(result.signalKind).toBe("working_output");
  });

  it("keeps codex in working when banner is still visible near the tail with extra status lines below", () => {
    const result = getTerminalSignalFromChunk(
      "",
      [
        "• Working (8s • esc to interrupt)",
        "• Messages to be submitted after next tool call",
        "↳ [cccc] user -> @foreman:",
        "Use /skills to list available skills",
        "gpt-5.4 default · 100% left · ~/Desktop/waterbang/ai/cccc",
      ].join("\n"),
      "codex",
    );
    expect(result.signalKind).toBe("working_output");
  });

  it("marks non-prompt visible output as working output", () => {
    const result = getTerminalSignalFromChunk("", "Running task T083...\n");
    expect(result.signalKind).toBe("working_output");
  });

  it("lets terminal prompt override stale backend working state for pty actors", () => {
    expect(
      getActorDisplayWorkingState(
        {
          id: "peer-1",
          running: true,
          runner: "pty",
          effective_working_state: "working",
        },
        { kind: "idle_prompt", updatedAt: Date.now() },
      ),
    ).toBe("idle");
  });

  it("temporarily upgrades backend idle to working when fresh terminal output is flowing", () => {
    expect(
      getActorDisplayWorkingState(
        {
          id: "peer-1",
          running: true,
          runner: "pty",
          effective_working_state: "idle",
        },
        { kind: "working_output", updatedAt: 10_000 },
        12_000,
      ),
    ).toBe("working");
  });

  it("does not keep working-output overrides forever", () => {
    expect(
      getActorDisplayWorkingState(
        {
          id: "peer-1",
          running: true,
          runner: "pty",
          effective_working_state: "idle",
        },
        { kind: "working_output", updatedAt: 10_000 },
        20_500,
      ),
    ).toBe("idle");
  });
});
