import { describe, expect, it } from "vitest";

import {
  actorHasRuntimeResumeFailure,
  shouldFetchStoppedTerminalTail,
  shouldShowRuntimeTerminalInitializing,
} from "../../src/components/AgentTab";

describe("AgentTab stopped terminal tail model", () => {
  it.each(["openclaw", "hermes"])("keeps a %s terminal behind initialization until output arrives", (runtime) => {
    expect(shouldShowRuntimeTerminalInitializing({
      runtime,
      isRunning: true,
      isHeadless: false,
      terminalHasOutput: false,
    })).toBe(true);
    expect(shouldShowRuntimeTerminalInitializing({
      runtime,
      isRunning: true,
      isHeadless: false,
      terminalHasOutput: true,
    })).toBe(false);
  });

  it("does not show terminal initialization for stopped, headless, or unrelated runtimes", () => {
    expect(shouldShowRuntimeTerminalInitializing({
      runtime: "codex",
      isRunning: true,
      isHeadless: false,
      terminalHasOutput: false,
    })).toBe(false);
    expect(shouldShowRuntimeTerminalInitializing({
      runtime: "hermes",
      isRunning: false,
      isHeadless: false,
      terminalHasOutput: false,
    })).toBe(false);
    expect(shouldShowRuntimeTerminalInitializing({
      runtime: "hermes",
      isRunning: true,
      isHeadless: true,
      terminalHasOutput: false,
    })).toBe(false);
  });

  it("detects persisted runtime resume failures from actor state", () => {
    expect(actorHasRuntimeResumeFailure({ runtime_session_status: "resume_failed" })).toBe(true);
    expect(actorHasRuntimeResumeFailure({ runtime_session_status: "usable" })).toBe(false);
  });

  it("does not fetch stopped terminal output while the actor has an in-flight lifecycle action", () => {
    expect(
      shouldFetchStoppedTerminalTail({
        activated: true,
        isRunning: false,
        isHeadless: false,
        groupId: "g1",
        actorId: "Development",
        isActorBusy: true,
      }),
    ).toBe(false);
  });

  it("fetches stopped terminal output only for an activated non-headless stopped actor", () => {
    expect(
      shouldFetchStoppedTerminalTail({
        activated: true,
        isRunning: false,
        isHeadless: false,
        groupId: "g1",
        actorId: "Development",
        isActorBusy: false,
      }),
    ).toBe(true);
    expect(
      shouldFetchStoppedTerminalTail({
        activated: true,
        isRunning: true,
        isHeadless: false,
        groupId: "g1",
        actorId: "Development",
        isActorBusy: false,
      }),
    ).toBe(false);
  });
});
