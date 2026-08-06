import { describe, expect, it } from "vitest";

import type { GroupServerControlState } from "../../src/types";
import { shouldStartGroupAfterActivation } from "../../src/hooks/groupActionDecision";

function controlState(runtimeRunning: boolean): GroupServerControlState {
  return {
    status_key: runtimeRunning ? "run" : "stop",
    lifecycle_state: "active",
    runtime_running: runtimeRunning,
    primary_action: runtimeRunning ? "pause" : "start",
    can_start: true,
    can_pause: runtimeRunning,
    can_stop: true,
    actor_count: 1,
    running_actor_count: runtimeRunning ? 1 : 0,
    has_running_foreman: runtimeRunning,
  };
}

describe("group activation decision", () => {
  it("starts only when the fresh control state reports no running runtime", () => {
    expect(shouldStartGroupAfterActivation(controlState(false))).toBe(true);
    expect(shouldStartGroupAfterActivation(controlState(true))).toBe(false);
  });

  it("does not guess from stale local state when the fresh snapshot is unavailable", () => {
    expect(shouldStartGroupAfterActivation(null)).toBe(false);
    expect(shouldStartGroupAfterActivation(undefined)).toBe(false);
  });
});
