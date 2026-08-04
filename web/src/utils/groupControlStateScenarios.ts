import type { GroupDoc, GroupServerControlState } from "../types";
import { getGroupControlState, type GroupControlState, type GroupControlStateInput } from "./groupControlState";

export type GroupControlStateScenario = {
  id: string;
  title: string;
  description: string;
  input: GroupControlStateInput;
  expectedStatusKey: GroupControlState["statusKey"];
  expectedIssueCodes: string[];
};

function controlState(
  status_key: GroupServerControlState["status_key"],
  primary_action: GroupServerControlState["primary_action"],
  overrides: Partial<GroupServerControlState> = {},
): GroupServerControlState {
  const lifecycle_state = status_key === "run" ? "active" : status_key === "stop" ? "stopped" : status_key;
  const runtime_running = status_key !== "stop";
  return {
    status_key,
    lifecycle_state,
    runtime_running,
    primary_action,
    can_start: true,
    can_pause: status_key !== "stop",
    can_stop: true,
    actor_count: 1,
    running_actor_count: runtime_running ? 1 : 0,
    has_running_foreman: runtime_running,
    ...overrides,
  };
}

function groupDoc(control_state?: GroupServerControlState): GroupDoc {
  return {
    group_id: "g-debug",
    state: control_state?.lifecycle_state as GroupDoc["state"],
    running: control_state?.runtime_running ?? false,
    runtime_status: control_state
      ? {
          lifecycle_state: control_state.lifecycle_state,
          runtime_running: control_state.runtime_running,
          running_actor_count: control_state.running_actor_count,
          has_running_foreman: control_state.has_running_foreman,
        }
      : undefined,
    control_state,
  };
}

export const GROUP_CONTROL_STATE_SCENARIOS: GroupControlStateScenario[] = [
  {
    id: "backend-active",
    title: "Backend active",
    description: "Backend canonical state says the group is running.",
    input: {
      selectedGroupId: "g-debug",
      groupDoc: groupDoc(controlState("run", "pause")),
      busy: "",
    },
    expectedStatusKey: "run",
    expectedIssueCodes: [],
  },
  {
    id: "backend-paused",
    title: "Backend paused",
    description: "Backend canonical state says the group can resume.",
    input: {
      selectedGroupId: "g-debug",
      groupDoc: groupDoc(controlState("paused", "resume")),
      busy: "",
    },
    expectedStatusKey: "paused",
    expectedIssueCodes: [],
  },
  {
    id: "backend-stopped",
    title: "Backend stopped",
    description: "Backend canonical state says the group can start.",
    input: {
      selectedGroupId: "g-debug",
      groupDoc: groupDoc(controlState("stop", "start", { can_pause: false })),
      busy: "",
    },
    expectedStatusKey: "stop",
    expectedIssueCodes: [],
  },
  {
    id: "backend-invalid",
    title: "Backend invalid",
    description: "Backend returned an unrecognized control state shape.",
    input: {
      selectedGroupId: "g-debug",
      groupDoc: groupDoc(controlState("weird-state", "weird-action")),
    },
    expectedStatusKey: "run",
    expectedIssueCodes: ["invalid_backend_status_key", "invalid_backend_primary_action"],
  },
  {
    id: "missing-backend-control-state",
    title: "Missing backend control state",
    description: "The UI falls back to runtime_status for display and disables controls.",
    input: {
      selectedGroupId: "g-debug",
      groupDoc: {
        group_id: "g-debug",
        runtime_status: {
          lifecycle_state: "paused",
          runtime_running: true,
          running_actor_count: 1,
          has_running_foreman: true,
        },
      },
    },
    expectedStatusKey: "paused",
    expectedIssueCodes: ["missing_backend_control_state"],
  },
  {
    id: "missing-group",
    title: "Missing selected group",
    description: "Controls are rendered without a selected group.",
    input: {
      selectedGroupId: "",
      actors: [],
    },
    expectedStatusKey: null,
    expectedIssueCodes: ["missing_selected_group"],
  },
];

export function evaluateGroupControlStateScenario(scenario: GroupControlStateScenario) {
  const state = getGroupControlState(scenario.input);
  return {
    id: scenario.id,
    title: scenario.title,
    statusKey: state.statusKey,
    deliveryToggleKind: state.deliveryToggleKind,
    launchDisabled: state.launchDisabled,
    pauseDisabled: state.pauseDisabled,
    stopDisabled: state.stopDisabled,
    issues: state.issues,
  };
}

export function runGroupControlStateScenarios() {
  return GROUP_CONTROL_STATE_SCENARIOS.map(evaluateGroupControlStateScenario);
}
