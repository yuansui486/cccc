import { describe, expect, it } from "vitest";

import { getGroupControlState } from "../utils/groupControlState";
import { patchGroupRuntimeStatus } from "./groupStoreCore";

describe("group store control state plumbing", () => {
  it("keeps backend control_state when patching runtime status", () => {
    const controlState = {
      status_key: "stop",
      lifecycle_state: "stopped",
      runtime_running: false,
      primary_action: "start",
      can_start: true,
      can_pause: false,
      can_stop: true,
      actor_count: 2,
      running_actor_count: 0,
      has_running_foreman: false,
    } as const;

    const next = patchGroupRuntimeStatus(
      [{
        group_id: "g1",
        state: "active",
        running: true,
        runtime_status: {
          lifecycle_state: "active",
          runtime_running: true,
          running_actor_count: 2,
          has_running_foreman: true,
        },
        control_state: controlState,
      }],
      "g1",
      {
        lifecycle_state: "stopped",
        runtime_running: false,
        running_actor_count: 0,
        has_running_foreman: false,
      },
    );

    expect(next[0]?.control_state).toBe(controlState);
  });

  it("keeps the launch button enabled in stopped state when backend says start is allowed", () => {
    const controlState = {
      status_key: "stop",
      lifecycle_state: "stopped",
      runtime_running: false,
      primary_action: "start",
      can_start: true,
      can_pause: false,
      can_stop: true,
      actor_count: 2,
      running_actor_count: 0,
      has_running_foreman: false,
    } as const;

    const state = getGroupControlState({
      selectedGroupId: "g1",
      groupDoc: {
        group_id: "g1",
        state: "stopped",
        running: false,
        runtime_status: {
          lifecycle_state: "stopped",
          runtime_running: false,
          running_actor_count: 0,
          has_running_foreman: false,
        },
        control_state: controlState,
      },
      busy: "",
    });

    expect(state.statusKey).toBe("stop");
    expect(state.deliveryToggleKind).toBe("start");
    expect(state.launchDisabled).toBe(false);
  });
});
