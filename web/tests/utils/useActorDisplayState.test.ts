import { describe, expect, it } from "vitest";

import { computeActorDisplayState } from "../../src/hooks/useActorDisplayState";

describe("computeActorDisplayState", () => {
  it("keeps backend running actors as running", () => {
    const state = computeActorDisplayState({
      actor: {
        id: "actor-1",
        running: true,
        enabled: true,
        effective_working_state: "waiting",
      },
    } as any);

    expect(state.isRunning).toBe(true);
    expect(state.assumeRunning).toBe(false);
    expect(state.workingState).toBe("waiting");
  });

  it("recovers from false-negative running state when backend working_state still indicates a live actor", () => {
    const state = computeActorDisplayState({
      actor: {
        id: "actor-2",
        running: false,
        enabled: true,
        effective_working_state: "waiting",
      },
    } as any);

    expect(state.isRunning).toBe(true);
    expect(state.assumeRunning).toBe(true);
    expect(state.workingState).toBe("waiting");
  });

  it("shows a hydrating actor as waiting instead of stopped while group runtime is coming up", () => {
    const state = computeActorDisplayState({
      actor: {
        id: "actor-3",
        running: false,
        enabled: true,
        effective_working_state: "stopped",
      },
      selectedGroupRunning: true,
      selectedGroupActorsHydrating: true,
    } as any);

    expect(state.isRunning).toBe(true);
    expect(state.assumeRunning).toBe(true);
    expect(state.workingState).toBe("waiting");
  });

  it("keeps genuinely stopped actors stopped", () => {
    const state = computeActorDisplayState({
      actor: {
        id: "actor-4",
        running: false,
        enabled: false,
        effective_working_state: "stopped",
      },
    } as any);

    expect(state.isRunning).toBe(false);
    expect(state.assumeRunning).toBe(false);
    expect(state.workingState).toBe("stopped");
  });
});
