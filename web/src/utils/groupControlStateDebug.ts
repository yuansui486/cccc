import { getGroupControlState, type GroupControlStateInput } from "./groupControlState";
import {
  GROUP_CONTROL_STATE_SCENARIOS,
  evaluateGroupControlStateScenario,
  runGroupControlStateScenarios,
} from "./groupControlStateScenarios";
import { computeSelectedGroupRuntime } from "../hooks/useSelectedGroupRuntime";
import { fetchGroupControlState } from "../services/api";
import { useGroupStore } from "../stores/useGroupStore";
import { useUIStore } from "../stores/useUIStore";

type GroupControlDebugSnapshot = {
  input: GroupControlStateInput;
  state: ReturnType<typeof getGroupControlState>;
};

function getCurrentGroupControlDebugSnapshot(): GroupControlDebugSnapshot {
  const groupState = useGroupStore.getState();
  const uiState = useUIStore.getState();
  const selectedGroupId = String(groupState.selectedGroupId || "").trim();
  const internalActors = selectedGroupId ? groupState.internalRuntimeActorsByGroup[selectedGroupId] || [] : [];
  const selectedRuntime = computeSelectedGroupRuntime({
    groups: groupState.groups,
    selectedGroupId,
    groupDoc: groupState.groupDoc,
    actors: [...groupState.actors, ...internalActors],
  });
  const selectedGroupMeta =
    groupState.getOrderedGroups().find((group) => String(group.group_id || "").trim() === selectedGroupId)
    || selectedRuntime.selectedGroupMeta
    || null;
  const input: GroupControlStateInput = {
    selectedGroupId,
    selectedGroupRunning: selectedRuntime.selectedGroupRunning,
    selectedGroupRuntimeStatus: selectedRuntime.selectedGroupRuntimeStatus,
    groupDoc: groupState.groupDoc,
    groupMeta: selectedGroupMeta,
    actors: groupState.actors,
    busy: uiState.busy,
  };

  return {
    input,
    state: getGroupControlState(input),
  };
}

async function fetchCurrentGroupControlState() {
  const selectedGroupId = String(useGroupStore.getState().selectedGroupId || "").trim();
  if (!selectedGroupId) {
    const result = { ok: false, error: { code: "missing_selected_group", message: "No selected group." } };
    console.warn("[group-control-state] backend current", result);
    return result;
  }
  const result = await fetchGroupControlState(selectedGroupId, { noCache: true });
  console.info("[group-control-state] backend current", result);
  return result;
}

declare global {
  interface Window {
    __OC_GROUP_CONTROL_DEBUG__?: {
      current: () => GroupControlDebugSnapshot;
      fetchCurrent: () => ReturnType<typeof fetchCurrentGroupControlState>;
      ids: () => string[];
      printCurrent: () => GroupControlDebugSnapshot;
      scenario: (id: string) => ReturnType<typeof evaluateGroupControlStateScenario> | null;
      scenarios: () => ReturnType<typeof runGroupControlStateScenarios>;
      state: (input: GroupControlStateInput) => ReturnType<typeof getGroupControlState>;
    };
  }
}

let installed = false;

export function installGroupControlStateDebugTools() {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.__OC_GROUP_CONTROL_DEBUG__ = {
    current: getCurrentGroupControlDebugSnapshot,
    fetchCurrent: fetchCurrentGroupControlState,
    ids: () => GROUP_CONTROL_STATE_SCENARIOS.map((scenario) => scenario.id),
    printCurrent: () => {
      const snapshot = getCurrentGroupControlDebugSnapshot();
      console.info("[group-control-state] current", snapshot);
      return snapshot;
    },
    scenario: (id: string) => {
      const scenario = GROUP_CONTROL_STATE_SCENARIOS.find((item) => item.id === id);
      return scenario ? evaluateGroupControlStateScenario(scenario) : null;
    },
    scenarios: runGroupControlStateScenarios,
    state: getGroupControlState,
  };
}
