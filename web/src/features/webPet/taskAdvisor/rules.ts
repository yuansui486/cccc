import type { Task } from "../../../types";
import type { TaskAdvisorEvidence, TaskAdvisorRule, TaskProposalCandidate } from "./types";

const STALLED_ACTIVE_TASK_MIN_MOUNTED_MS = 20 * 60 * 1000;
const STALLED_ACTIVE_TASK_MIN_STALE_MS = 15 * 60 * 1000;
const STALLED_ACTIVE_TASK_MIN_FOCUS_CYCLES = 3;
const OWNERSHIP_DRIFT_MIN_STALE_MS = 10 * 60 * 1000;

function taskTitle(task: Task): string {
  return String(task.title || "").trim();
}

function buildMoveActiveCandidate(evidence: TaskAdvisorEvidence): TaskProposalCandidate {
  const title = taskTitle(evidence.task);
  return {
    kind: "move_active",
    fingerprint: `local-task-proposal:${evidence.groupId}:${evidence.taskId}:move-active`,
    priority: 82,
    actorId: evidence.actorId,
    taskId: evidence.taskId,
    title,
    action: {
      type: "task_proposal",
      groupId: evidence.groupId,
      operation: "move",
      taskId: evidence.taskId,
      title: title || undefined,
      status: "active",
      reason: {
        kind: "move_active",
        actorId: evidence.actorId,
      },
    },
  };
}

function buildWaitingUserCandidate(evidence: TaskAdvisorEvidence): TaskProposalCandidate {
  const title = taskTitle(evidence.task);
  const focusText = evidence.actorFocus || evidence.actorNextAction;
  return {
    kind: "sync_waiting_user",
    fingerprint: `local-task-proposal:${evidence.groupId}:${evidence.taskId}:waiting-user`,
    priority: 88,
    actorId: evidence.actorId,
    taskId: evidence.taskId,
    title,
    action: {
      type: "task_proposal",
      groupId: evidence.groupId,
      operation: "update",
      taskId: evidence.taskId,
      title: title || undefined,
      assignee: String(evidence.task.assignee || "").trim() || evidence.actorId,
      reason: {
        kind: "sync_waiting_user",
        actorId: evidence.actorId,
        focus: focusText,
      },
    },
  };
}

function buildBlockedCandidate(evidence: TaskAdvisorEvidence): TaskProposalCandidate {
  const title = taskTitle(evidence.task);
  return {
    kind: "sync_blocked",
    fingerprint: `local-task-proposal:${evidence.groupId}:${evidence.taskId}:blocked-sync`,
    priority: 86,
    actorId: evidence.actorId,
    taskId: evidence.taskId,
    title,
    action: {
      type: "task_proposal",
      groupId: evidence.groupId,
      operation: "update",
      taskId: evidence.taskId,
      title: title || undefined,
      assignee: String(evidence.task.assignee || "").trim() || evidence.actorId,
      reason: {
        kind: "sync_blocked",
        actorId: evidence.actorId,
        blockers: evidence.actorBlockers.slice(0, 3),
      },
    },
  };
}

function buildStalledActiveTaskCandidate(evidence: TaskAdvisorEvidence): TaskProposalCandidate {
  const title = taskTitle(evidence.task);
  const focusText = evidence.actorFocus || evidence.actorNextAction || "current task context";
  const hasBlockers = evidence.actorBlockers.length > 0 || evidence.taskBlockedBy.length > 0;
  const operation = hasBlockers ? "update" : "handoff";
  return {
    kind: "stalled_active_task",
    fingerprint: `local-task-proposal:${evidence.groupId}:${evidence.taskId}:stalled-active`,
    priority: 74,
    actorId: evidence.actorId,
    taskId: evidence.taskId,
    title,
    action: {
      type: "task_proposal",
      groupId: evidence.groupId,
      operation,
      taskId: evidence.taskId,
      title: title || undefined,
      assignee: String(evidence.task.assignee || "").trim() || evidence.actorId,
      reason: {
        kind: "stalled_active_task",
        actorId: evidence.actorId,
        focus: focusText,
        mountedMinutes: evidence.mountedDurationMs / 60000,
        blockers: evidence.actorBlockers.slice(0, 3),
        suggestedOperation: operation,
      },
    },
  };
}

function buildOwnershipDriftCandidate(evidence: TaskAdvisorEvidence): TaskProposalCandidate {
  const title = taskTitle(evidence.task);
  return {
    kind: "ownership_drift",
    fingerprint: `local-task-proposal:${evidence.groupId}:${evidence.taskId}:ownership-drift`,
    priority: 78,
    actorId: evidence.actorId,
    taskId: evidence.taskId,
    title,
    action: {
      type: "task_proposal",
      groupId: evidence.groupId,
      operation: "update",
      taskId: evidence.taskId,
      title: title || undefined,
      assignee: evidence.actorId,
      reason: {
        kind: "ownership_drift",
        actorId: evidence.actorId,
        currentActiveTaskId: evidence.actorActiveTaskId || undefined,
      },
    },
  };
}

function buildAssignActiveOwnerCandidate(evidence: TaskAdvisorEvidence): TaskProposalCandidate {
  const title = taskTitle(evidence.task);
  return {
    kind: "assign_active_owner",
    fingerprint: `local-task-proposal:${evidence.groupId}:${evidence.taskId}:assign-owner`,
    priority: 84,
    actorId: evidence.actorId,
    taskId: evidence.taskId,
    title,
    action: {
      type: "task_proposal",
      groupId: evidence.groupId,
      operation: "update",
      taskId: evidence.taskId,
      title: title || undefined,
      assignee: evidence.actorId,
      reason: {
        kind: "assign_active_owner",
        actorId: evidence.actorId,
      },
    },
  };
}

function buildEscalatedWaitingUserCandidate(evidence: TaskAdvisorEvidence): TaskProposalCandidate {
  const title = taskTitle(evidence.task);
  const focusText = evidence.actorFocus || evidence.actorNextAction || "waiting_user";
  return {
    kind: "escalated_waiting_user",
    fingerprint: `local-task-proposal:${evidence.groupId}:${evidence.taskId}:waiting-user-stale`,
    priority: 92,
    actorId: evidence.actorId,
    taskId: evidence.taskId,
    title,
    action: {
      type: "task_proposal",
      groupId: evidence.groupId,
      operation: "move",
      taskId: evidence.taskId,
      title: title || undefined,
      status: "active",
      assignee: String(evidence.task.assignee || "").trim() || evidence.actorId,
      reason: {
        kind: "escalated_waiting_user",
        actorId: evidence.actorId,
        focus: focusText,
        mountedMinutes: evidence.mountedDurationMs / 60000,
      },
    },
  };
}

export const taskAdvisorRules: TaskAdvisorRule[] = [
  (evidence) => (evidence.hasActiveTaskMismatch ? buildMoveActiveCandidate(evidence) : null),
  (evidence) => (evidence.hasActiveTaskWithoutOwner ? buildAssignActiveOwnerCandidate(evidence) : null),
  (evidence) => (
    evidence.waitingUserSyncOverdue
      ? buildEscalatedWaitingUserCandidate(evidence)
      : null
  ),
  (evidence) => (
    (evidence.focusSuggestsWaitingUser || evidence.nextActionSuggestsWaitingUser) && evidence.taskWaitingOn !== "user"
      ? buildWaitingUserCandidate(evidence)
      : null
  ),
  (evidence) => (evidence.hasUnsyncedBlockers ? buildBlockedCandidate(evidence) : null),
  (evidence) => (
    evidence.taskStatus === "active"
      && !evidence.hasRecentTaskChange
      && evidence.actorMountedThisTask
      && evidence.mountedDurationMs >= STALLED_ACTIVE_TASK_MIN_MOUNTED_MS
      && evidence.taskStaleDurationMs >= STALLED_ACTIVE_TASK_MIN_STALE_MS
      && evidence.focusUnchangedCycles >= STALLED_ACTIVE_TASK_MIN_FOCUS_CYCLES
      ? buildStalledActiveTaskCandidate(evidence)
      : null
  ),
  (evidence) => (
    evidence.hasOwnershipDrift
      && evidence.taskStaleDurationMs >= OWNERSHIP_DRIFT_MIN_STALE_MS
      ? buildOwnershipDriftCandidate(evidence)
      : null
  ),
];
