// Pure function: aggregate group context data into CatState + PanelData.
// Ported from desktop/src-tauri/src/state_aggregator.rs.

import type {
  GroupContext,
  AgentState,
  LedgerEvent,
  ObligationStatus,
} from "../../types";
import type {
  CatState,
  PanelData,
  AgentSummary,
  ActionItem,
} from "./types";

export interface AggregateInput {
  groupContext: GroupContext | null;
  events: LedgerEvent[];
  sseStatus: "connected" | "connecting" | "disconnected";
  groupState?: string;
  teamName?: string;
  groupId?: string;
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

function displayActor(actorId: string): string {
  const trimmed = actorId.trim();
  return trimmed || "system";
}

export function getChatMessageText(event: LedgerEvent): string {
  const data = event.data as Record<string, unknown> | undefined;
  return String(data?.text ?? "").trim();
}

export function isPendingReplyRequiredForUser(event: LedgerEvent): boolean {
  if (event.kind !== "chat.message") return false;

  const obligationStatus = event._obligation_status;
  if (!obligationStatus) return false;

  const userStatus: ObligationStatus | undefined = obligationStatus["user"];
  if (!userStatus) return false;

  return !!(
    userStatus.reply_required &&
    !userStatus.acked &&
    !userStatus.replied
  );
}

function hasActivity(agent: AgentState): boolean {
  return !!(agent.hot?.active_task_id ?? "").trim();
}

function isTaskDoneOrArchived(
  taskId: string,
  context: GroupContext,
): boolean {
  if (!taskId) return false;
  const tasks = context.coordination?.tasks ?? [];
  const task = tasks.find((t) => t.id === taskId);
  if (!task) return false;
  const status = (task.status ?? "").toLowerCase();
  return status === "done" || status === "archived";
}

function collectWaitingUserTasks(
  context: GroupContext,
  groupId: string,
): ActionItem[] {
  const attention = context.attention;
  const waitingUser = attention?.waiting_user;

  // If attention.waiting_user is an array of tasks/entries, use it
  if (Array.isArray(waitingUser) && waitingUser.length > 0) {
    const items: ActionItem[] = [];
    for (const entry of waitingUser) {
      if (typeof entry === "string") {
        items.push({
          id: `${entry}_waiting_on_user`,
          agent: "system",
          summary: entry,
        });
        continue;
      }
      const taskId = String(entry.id || "").trim();
      if (taskId && isTaskDoneOrArchived(taskId, context)) continue;
      items.push({
        id: `${taskId || "unknown"}_waiting_on_user`,
        agent: displayActor(entry.assignee || ""),
        summary: truncateWebPetSummary(entry.title || ""),
        action: taskId && groupId
          ? { type: "open_task", groupId, taskId }
          : undefined,
      });
    }
    return items;
  }

  // Fallback: scan coordination tasks for waiting_on=user
  const tasks = context.coordination?.tasks ?? [];
  return tasks
    .filter((task) => {
      const waitingOn = (task.waiting_on ?? "").toLowerCase();
      const status = (task.status ?? "").toLowerCase();
      return waitingOn === "user" && status !== "done" && status !== "archived";
    })
    .map((task) => {
      const taskId = (task.id || "").trim();
      return {
        id: `${taskId || "unknown"}_waiting_on_user`,
        agent: displayActor(task.assignee || ""),
        summary: truncateWebPetSummary(task.title || ""),
        action: taskId && groupId
          ? { type: "open_task" as const, groupId, taskId }
          : undefined,
      };
    });
}

function collectPendingReplyRequired(
  events: LedgerEvent[],
  groupId: string,
): ActionItem[] {
  const items: ActionItem[] = [];

  for (const event of events) {
    if (!isPendingReplyRequiredForUser(event)) continue;

    const text = getChatMessageText(event);
    if (!text) continue;

    const eventId = event.id || "unknown";
    items.push({
      id: eventId,
      agent: displayActor(event.by || ""),
      summary: truncateWebPetSummary(text),
      action: groupId
        ? { type: "open_chat", groupId, eventId }
        : undefined,
    });
  }

  return items;
}

export function aggregateWebPetState(input: AggregateInput): AggregateOutput {
  const {
    groupContext,
    events,
    sseStatus,
    groupState = "",
    teamName = "Team",
    groupId = "",
  } = input;

  const context = groupContext ?? {};
  const agentStates: AgentState[] = context.agent_states ?? [];

  // Collect action items
  const waitingUserItems = collectWaitingUserTasks(context as GroupContext, groupId);
  const replyRequiredItems = collectPendingReplyRequired(events, groupId);
  const actionItems = [...waitingUserItems, ...replyRequiredItems];

  // When group is inactive, treat all agents as idle
  const groupInactive = groupState === "paused" || groupState === "idle" || groupState === "stopped";

  // Count active agents (none when group is inactive)
  const activeAgents = groupInactive ? [] : agentStates.filter(hasActivity);

  // Determine cat state (priority: inactive > needs_you > busy > working > napping)
  let catState: CatState;
  if (groupInactive) {
    catState = "napping";
  } else if (actionItems.length > 0) {
    catState = "needs_you";
  } else if (activeAgents.length >= 2) {
    catState = "busy";
  } else if (activeAgents.length === 1) {
    catState = "working";
  } else {
    catState = "napping";
  }

  // Build agent summaries
  const agents: AgentSummary[] = agentStates.map((agent) => ({
    id: agent.id,
    state: groupInactive
      ? "napping"
      : hasActivity(agent)
        ? activeAgents.length >= 2
          ? "busy"
          : "working"
        : "napping",
    focus: agent.hot?.focus ?? "",
  }));

  // Add "attention" pseudo-agent if needs_you but no agent has that state
  if (
    catState === "needs_you" &&
    !agents.some((a) => a.state === "needs_you")
  ) {
    const first = actionItems[0];
    if (first) {
      agents.push({
        id: "attention",
        state: "needs_you",
        focus: first.summary,
      });
    }
  }

  // Limit action items to top 3
  const topActionItems = actionItems.slice(0, 3);

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
      actionItems: topActionItems,
      connection: {
        connected,
        message: connectionMessage,
      },
      taskProgress,
    },
  };
}
