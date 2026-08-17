import { describe, expect, it } from "vitest";

import { terminalHasOutputForSession } from "../../src/components/agentTerminal/useAgentTerminalConnection";

describe("OpenClaw terminal output session state", () => {
  const runningPty = {
    isRunning: true,
    isHeadless: false,
    terminalSessionKey: "group-1\u0000actor-1\u00000",
  };

  it("keeps output visible while the same terminal session reconnects", () => {
    expect(terminalHasOutputForSession({
      ...runningPty,
      outputSessionKey: runningPty.terminalSessionKey,
    })).toBe(true);
  });

  it("waits for fresh output after the terminal session changes", () => {
    expect(terminalHasOutputForSession({
      ...runningPty,
      terminalSessionKey: "group-1\u0000actor-1\u00001",
      outputSessionKey: runningPty.terminalSessionKey,
    })).toBe(false);
  });

  it("does not retain output visibility while the terminal is stopped or headless", () => {
    expect(terminalHasOutputForSession({
      ...runningPty,
      isRunning: false,
      outputSessionKey: runningPty.terminalSessionKey,
    })).toBe(false);
    expect(terminalHasOutputForSession({
      ...runningPty,
      isHeadless: true,
      outputSessionKey: runningPty.terminalSessionKey,
    })).toBe(false);
  });
});
