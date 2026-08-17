import { describe, expect, it } from "vitest";

import {
  actorHasRuntimeResumeFailure,
  shouldFetchStoppedTerminalTail,
  shouldShowOpenClawTerminalInitializing,
} from "../../src/components/AgentTab";

describe("AgentTab stopped terminal tail model", () => {
  it("keeps an OpenClaw terminal behind the initialization state until output arrives", () => {
    expect(shouldShowOpenClawTerminalInitializing({
      isOpenClaw: true,
      isRunning: true,
      terminalHasOutput: false,
    })).toBe(true);
    expect(shouldShowOpenClawTerminalInitializing({
      isOpenClaw: true,
      isRunning: true,
      terminalHasOutput: true,
    })).toBe(false);
    expect(shouldShowOpenClawTerminalInitializing({
      isOpenClaw: false,
      isRunning: true,
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
