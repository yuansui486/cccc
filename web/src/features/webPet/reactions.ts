import type { GroupContext, LedgerEvent, Task } from "../../types";

export type PetReactionKind = "mention" | "success" | "error";

export interface PetReaction {
  kind: PetReactionKind;
  durationMs: number;
  reason: string;
  source?: {
    eventId?: string;
    taskId?: string;
  };
}

export interface TaskReactionSnapshot {
  id: string;
  status: string;
  title: string;
}

export type TaskReactionSnapshotMap = Record<string, TaskReactionSnapshot>;

const DEFAULT_REACTION_DURATION_MS = 2200;
const ERROR_STATUSES = new Set(["failed", "error", "blocked"]);

function normalizeStatus(status: string): string {
  return status.trim().toLowerCase();
}

function truncate(text: string, maxChars = 96): string {
  const cleaned = text.trim().replace(/\s+/g, " ");
  if (cleaned.length <= maxChars) return cleaned;
  return cleaned.slice(0, maxChars - 1) + "…";
}

function normalizeTasks(context: GroupContext | null): Task[] {
  const tasks = context?.coordination?.tasks;
  return Array.isArray(tasks) ? tasks : [];
}

export function createTaskReactionSnapshot(
  context: GroupContext | null,
): TaskReactionSnapshotMap {
  const snapshot: TaskReactionSnapshotMap = {};

  for (const task of normalizeTasks(context)) {
    const taskId = String(task.id || "").trim();
    if (!taskId) continue;
    snapshot[taskId] = {
      id: taskId,
      status: normalizeStatus(String(task.status || "")),
      title: truncate(String(task.title || "")),
    };
  }

  return snapshot;
}

export function createMentionReaction(
  event: LedgerEvent | null | undefined,
): PetReaction | null {
  if (!event || event.kind !== "chat.message") return null;

  const eventId = String(event.id || "").trim();
  if (!eventId || String(event.by || "").trim() === "user") return null;

  const data = event.data as Record<string, unknown> | undefined;
  const to = Array.isArray(data?.to)
    ? data.to.map((entry) => String(entry || "").trim()).filter(Boolean)
    : [];
  if (!to.includes("user") && !to.includes("@user")) {
    return null;
  }

  return {
    kind: "mention",
    durationMs: DEFAULT_REACTION_DURATION_MS,
    reason: truncate(String(data?.text || "")) || "Mention received",
    source: { eventId },
  };
}

export function diffTaskReaction(
  previousSnapshot: TaskReactionSnapshotMap,
  nextSnapshot: TaskReactionSnapshotMap,
): PetReaction | null {
  for (const [taskId, nextTask] of Object.entries(nextSnapshot)) {
    const previousTask = previousSnapshot[taskId];
    if (!previousTask) continue;

    if (previousTask.status === nextTask.status) continue;

    if (nextTask.status === "done") {
      return {
        kind: "success",
        durationMs: DEFAULT_REACTION_DURATION_MS,
        reason: nextTask.title || "Task completed",
        source: { taskId },
      };
    }

    if (ERROR_STATUSES.has(nextTask.status)) {
      return {
        kind: "error",
        durationMs: DEFAULT_REACTION_DURATION_MS,
        reason: nextTask.title || "Task entered an error state",
        source: { taskId },
      };
    }
  }

  return null;
}

