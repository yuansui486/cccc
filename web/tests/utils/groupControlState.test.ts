import { describe, expect, it } from "vitest";

import { getGroupControlState } from "../../src/utils/groupControlState";

function issueCodes(state: ReturnType<typeof getGroupControlState>): string[] {
  return state.issues.map((issue) => issue.code);
}

describe("groupControlState", () => {
  it("uses backend control state for the delivery toggle", () => {
    const state = getGroupControlState({
      selectedGroupId: "g1",
      groupDoc: {
        group_id: "g1",
        control_state: {
          status_key: "run",
          lifecycle_state: "active",
          runtime_running: true,
          primary_action: "pause",
          can_start: true,
          can_pause: true,
          can_stop: true,
          actor_count: 1,
          running_actor_count: 1,
          has_running_foreman: true,
        },
      },
      actors: [{ id: "foreman", role: "foreman", running: true }],
      busy: "",
    });

    expect(state.statusKey).toBe("run");
    expect(state.deliveryToggleKind).toBe("pause");
    expect(state.deliveryToggleIsPause).toBe(true);
    expect(state.deliveryToggleDisabled).toBe(false);
  });

  it("uses display-only fallback when backend control state is absent", () => {
    const state = getGroupControlState({
      selectedGroupId: "g1",
      groupDoc: {
        group_id: "g1",
        runtime_status: {
          lifecycle_state: "paused",
          runtime_running: true,
          running_actor_count: 1,
          has_running_foreman: false,
        },
      },
      actors: [{ id: "peer-1", running: true }],
    });

    expect(state.statusKey).toBe("paused");
    expect(state.deliveryToggleKind).toBe("resume");
    expect(state.launchDisabled).toBe(true);
    expect(issueCodes(state)).toContain("missing_backend_control_state");
  });

  it("passes backend control state issues through", () => {
    const state = getGroupControlState({
      selectedGroupId: "g1",
      groupDoc: {
        group_id: "g1",
        control_state: {
          status_key: "run",
          lifecycle_state: "active",
          runtime_running: true,
          primary_action: "pause",
          can_start: true,
          can_pause: true,
          can_stop: true,
          actor_count: 1,
          running_actor_count: 1,
          has_running_foreman: true,
          issues: [{ code: "backend_example", severity: "warning", message: "backend issue" }],
        },
      },
    });

    expect(issueCodes(state)).toContain("backend_example");
  });

  it("prefers backend control state and keeps local busy as a UI overlay", () => {
    const state = getGroupControlState({
      selectedGroupId: "g1",
      groupDoc: {
        group_id: "g1",
        state: "active",
        running: true,
        runtime_status: {
          lifecycle_state: "active",
          runtime_running: true,
          running_actor_count: 1,
          has_running_foreman: true,
        },
        control_state: {
          status_key: "run",
          lifecycle_state: "active",
          runtime_running: true,
          primary_action: "pause",
          can_start: true,
          can_pause: true,
          can_stop: true,
          actor_count: 1,
          running_actor_count: 1,
          has_running_foreman: true,
          issues: [],
        },
      },
      actors: [{ id: "foreman", role: "foreman", running: true }],
      busy: "group-pause",
    });

    expect(state.backendControlState?.status_key).toBe("run");
    expect(state.statusKey).toBe("run");
    expect(state.deliveryToggleKind).toBe("pause");
    expect(state.pauseHardUnavailable).toBe(false);
    expect(state.pauseDisabled).toBe(true);
    expect(state.pauseControl.pending).toBe(true);
  });
});
