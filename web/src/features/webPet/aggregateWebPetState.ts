// Pure function: aggregate group context data into CatState + PanelData.
// Ported from desktop/src-tauri/src/state_aggregator.rs.

import type {
  GroupContext,
  AgentState,
  ActorRuntimeState,
  LedgerEvent,
} from "../../types";
import type { TerminalSignal } from "../../stores/useTerminalSignalsStore";
import { getTerminalSignalKey } from "../../stores/useTerminalSignalsStore";
import { getActorDisplayWorkingState } from "../../utils/terminalWorkingState";
import type {
  CatState,
  PanelData,
  AgentSummary,
} from "./types";

export interface AggregateInput {
  groupContext: GroupContext | null;
  events: LedgerEvent[];
  sseStatus: "connected" | "connecting" | "disconnected";
  groupState?: string;
  teamName?: string;
  groupId?: string;
  terminalSignals?: Record<string, TerminalSignal>;
}

export interface AggregateOutput {
  catState: CatState;
  panelData: PanelData;
}

const MAX_SUMMARY_CHARS = 96;

export function truncateWebPetSummary(text: string): string {
  const cleaned = text.trim().replace(/\n/g, " ");
  if (cleaned.length <= MAX_SUMMARY_CHARS) return cleaned;
  return cleaned.slice(0, MAX_SUMMARY_CHARS - 1) + "…";
}

export function getChatMessageText(event: LedgerEvent): string {
  const data = event.data as Record<string, unknown> | undefined;
  return String(data?.text ?? "").trim();
}

function getWebPetWorkingState(
  groupId: string,
  _agent: AgentState,
  runtimeState: ActorRuntimeState | undefined,
  terminalSignals: Record<string, TerminalSignal>,
): string {
  if (!runtimeState) return "idle";
  const actorLike: ActorRuntimeState = runtimeState ?? {
    id: _agent.id,
    running: false,
    runner: "pty",
    runner_effective: "pty",
    effective_working_state: "",
  };
  const signal = terminalSignals[getTerminalSignalKey(groupId, actorLike.id)];
  return getActorDisplayWorkingState(actorLike, signal);
}

function hasActivity(workingState: string): boolean {
  return String(workingState || "").trim().toLowerCase() === "working";
}

export function aggregateWebPetState(input: AggregateInput): AggregateOutput {
  const {
    groupContext,
    sseStatus,
    groupState = "",
    teamName = "Team",
    groupId = "",
    terminalSignals = {},
  } = input;
  void input.events;

  const context = groupContext ?? {};
  const agentStates: AgentState[] = context.agent_states ?? [];
  const actorsRuntime = Array.isArray(context.actors_runtime) ? context.actors_runtime : [];
  const runtimeStateById = new Map(actorsRuntime.map((item) => [item.id, item]));
  const agentStateById = new Map(agentStates.map((item) => [item.id, item]));
  const actorIds = Array.from(new Set([...agentStateById.keys(), ...runtimeStateById.keys()]));

  const workingStateById = new Map(
    actorIds.map((id) => [
      id,
      getWebPetWorkingState(groupId, agentStateById.get(id) ?? { id }, runtimeStateById.get(id), terminalSignals),
    ]),
  );

  // When group is inactive, treat all agents as idle
  const groupInactive = groupState === "paused" || groupState === "idle" || groupState === "stopped";

  // Count active agents (none when group is inactive)
  const activeAgents = groupInactive
    ? []
    : actorIds.filter((id) => hasActivity(workingStateById.get(id) || ""));

  // Determine cat state (priority: inactive > busy > working > napping)
  let catState: CatState;
  if (groupInactive) {
    catState = "napping";
  } else if (activeAgents.length >= 2) {
    catState = "busy";
  } else if (activeAgents.length === 1) {
    catState = "working";
  } else {
    catState = "napping";
  }

  // Build agent summaries
  const agents: AgentSummary[] = actorIds.map((id) => {
    const agent = agentStateById.get(id);
    const workingState = workingStateById.get(id) || "";
    return {
      id,
      state: groupInactive
        ? "napping"
        : hasActivity(workingState)
          ? activeAgents.length >= 2
            ? "busy"
            : "working"
          : "napping",
      focus: agent?.hot?.focus ?? "",
      activeTaskId: String(agent?.hot?.active_task_id ?? "").trim() || undefined,
    };
  });

  // Connection status from SSE
  const connected = sseStatus === "connected";
  // Return i18n key (webPet namespace) — translated at the component layer.
  const connectionMessage =
    sseStatus === "connected"
      ? "connectionConnected"
      : sseStatus === "connecting"
        ? "connectionConnecting"
        : "connectionDisconnected";

  // Extract task progress from tasks_summary.
  // Exclude archived from total so the progress bar reflects actionable tasks only.
  const tasksSummary = (context as GroupContext).tasks_summary;
  const taskProgress = tasksSummary
    ? {
        total: tasksSummary.total - (tasksSummary.archived ?? 0),
        done: tasksSummary.done,
        active: tasksSummary.active,
      }
    : undefined;

  return {
    catState,
    panelData: {
      teamName,
      agents,
      connection: {
        connected,
        message: connectionMessage,
      },
      taskProgress,
    },
  };
}
