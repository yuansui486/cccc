import type { TaskAdvisorContext, TaskAdvisorEvidence } from "./types";
import {
  cloneTaskAdvisorHistory,
  getTaskAdvisorHistoryEntry,
  upsertTaskAdvisorHistoryEntry,
  type TaskAdvisorHistoryState,
} from "./history";

function parseIsoMs(value: string): number | null {
  const raw = String(value || "").trim();
  if (!raw) return null;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : null;
}

function normalizeTaskStatus(status: string): string {
  return String(status || "").trim().toLowerCase();
}

function normalizeTaskWaitingOn(waitingOn: string): string {
  return String(waitingOn || "").trim().toLowerCase();
}

function looksLikeWaitingUser(text: string): boolean {
  const normalized = String(text || "").trim().toLowerCase();
  if (!normalized) return false;
  return /waiting[_\s-]?user|need user|await user|user input|user reply|clarify with user|approval/.test(normalized);
}

export function buildTaskAdvisorEvidence(
  context: TaskAdvisorContext,
  history: TaskAdvisorHistoryState,
): { evidenceList: TaskAdvisorEvidence[]; nextHistory: TaskAdvisorHistoryState } {
  const evidenceList: TaskAdvisorEvidence[] = [];
  const nowMs = Date.now();
  const agentById = new Map(context.agentStates.map((agent) => [String(agent?.id || "").trim(), agent]));
  const nextHistory = cloneTaskAdvisorHistory(history);

  for (const agent of context.agentStates) {
    const actorId = String(agent?.id || "").trim();
    const taskId = String(agent?.hot?.active_task_id || "").trim();
    if (!actorId || !taskId) continue;
    const task = context.taskById.get(taskId);
    if (!task) continue;

    const taskStatus = normalizeTaskStatus(String(task.status || ""));
    const taskWaitingOn = normalizeTaskWaitingOn(String(task.waiting_on || ""));
    const taskBlockedBy = Array.isArray(task.blocked_by)
      ? task.blocked_by.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const actorFocus = String(agent?.hot?.focus || "").trim();
    const actorNextAction = String(agent?.hot?.next_action || "").trim();
    const actorBlockers = Array.isArray(agent?.hot?.blockers)
      ? agent.hot.blockers.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const taskUpdatedAt = String(task.updated_at || "").trim();
    const previous = getTaskAdvisorHistoryEntry(context.groupId, actorId, taskId, history);
    const focusUnchangedCycles =
      previous && previous.lastFocus === actorFocus
        ? previous.focusUnchangedCycles + 1
        : 1;
    const mountedDurationMs = previous
      ? Math.max(0, nowMs - previous.firstSeenAtMs)
      : 0;
    const taskUpdatedAtMs = parseIsoMs(taskUpdatedAt);
    const taskStaleDurationMs = taskUpdatedAtMs === null
      ? mountedDurationMs
      : Math.max(0, nowMs - taskUpdatedAtMs);
    const hasRecentTaskChange = Boolean(
      previous
      && (
        previous.lastTaskUpdatedAt !== taskUpdatedAt
        || previous.lastTaskStatus !== taskStatus
      ),
    );

    evidenceList.push({
      groupId: context.groupId,
      actorId,
      taskId,
      task,
      actorActiveTaskId: taskId,
      taskStatus,
      taskWaitingOn,
      taskBlockedBy,
      actorFocus,
      actorNextAction,
      actorBlockers,
      actorMountedThisTask: true,
      hasActiveTaskMismatch: !["active", "done", "completed", "archived"].includes(taskStatus),
      focusSuggestsWaitingUser: looksLikeWaitingUser(actorFocus),
      nextActionSuggestsWaitingUser: looksLikeWaitingUser(actorNextAction),
      hasUnsyncedBlockers: actorBlockers.length > 0 && taskWaitingOn === "" && taskBlockedBy.length === 0,
      hasOwnershipDrift: false,
      hasActiveTaskWithoutOwner: taskStatus === "active" && !String(task.assignee || "").trim(),
      waitingUserSyncOverdue:
        (looksLikeWaitingUser(actorFocus) || looksLikeWaitingUser(actorNextAction))
        && taskWaitingOn !== "user"
        && focusUnchangedCycles >= 2
        && mountedDurationMs >= 10 * 60 * 1000,
      mountedDurationMs,
      focusUnchangedCycles,
      hasRecentTaskChange,
      taskStaleDurationMs,
    });

    upsertTaskAdvisorHistoryEntry(context.groupId, actorId, taskId, {
      firstSeenAtMs: previous?.firstSeenAtMs ?? nowMs,
      lastSeenAtMs: nowMs,
      lastFocus: actorFocus,
      lastTaskUpdatedAt: taskUpdatedAt,
      lastTaskStatus: taskStatus,
      focusUnchangedCycles,
    }, nextHistory);
  }

  for (const task of context.tasks) {
    const taskId = String(task.id || "").trim();
    const actorId = String(task.assignee || "").trim();
    if (!taskId || !actorId) continue;
    const agent = agentById.get(actorId);
    if (!agent) continue;
    const actorActiveTaskId = String(agent?.hot?.active_task_id || "").trim();
    if (!actorActiveTaskId || actorActiveTaskId === taskId) continue;

    const taskStatus = normalizeTaskStatus(String(task.status || ""));
    if (taskStatus !== "active") continue;

    const taskWaitingOn = normalizeTaskWaitingOn(String(task.waiting_on || ""));
    const taskBlockedBy = Array.isArray(task.blocked_by)
      ? task.blocked_by.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const actorFocus = String(agent?.hot?.focus || "").trim();
    const actorNextAction = String(agent?.hot?.next_action || "").trim();
    const actorBlockers = Array.isArray(agent?.hot?.blockers)
      ? agent.hot.blockers.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const taskUpdatedAtMs = parseIsoMs(String(task.updated_at || "").trim());
    const taskStaleDurationMs = taskUpdatedAtMs === null ? 0 : Math.max(0, nowMs - taskUpdatedAtMs);

    evidenceList.push({
      groupId: context.groupId,
      actorId,
      taskId,
      task,
      actorActiveTaskId,
      taskStatus,
      taskWaitingOn,
      taskBlockedBy,
      actorFocus,
      actorNextAction,
      actorBlockers,
      actorMountedThisTask: false,
      hasActiveTaskMismatch: false,
      focusSuggestsWaitingUser: looksLikeWaitingUser(actorFocus),
      nextActionSuggestsWaitingUser: looksLikeWaitingUser(actorNextAction),
      hasUnsyncedBlockers: false,
      hasOwnershipDrift: true,
      hasActiveTaskWithoutOwner: false,
      waitingUserSyncOverdue: false,
      mountedDurationMs: 0,
      focusUnchangedCycles: 0,
      hasRecentTaskChange: false,
      taskStaleDurationMs,
    });
  }

  return { evidenceList, nextHistory };
}
