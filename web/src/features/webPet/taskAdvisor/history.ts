export type TaskAdvisorHistoryEntry = {
  firstSeenAtMs: number;
  lastSeenAtMs: number;
  lastFocus: string;
  lastTaskUpdatedAt: string;
  lastTaskStatus: string;
  focusUnchangedCycles: number;
};

export type TaskAdvisorHistoryState = Map<string, TaskAdvisorHistoryEntry>;

const historyByKey = new Map<string, TaskAdvisorHistoryEntry>();

function buildHistoryKey(groupId: string, actorId: string, taskId: string): string {
  return `${groupId}::${actorId}::${taskId}`;
}

export function cloneTaskAdvisorHistory(
  history: TaskAdvisorHistoryState = historyByKey,
): TaskAdvisorHistoryState {
  return new Map(history);
}

export function getTaskAdvisorHistoryEntry(
  groupId: string,
  actorId: string,
  taskId: string,
  history: TaskAdvisorHistoryState = historyByKey,
): TaskAdvisorHistoryEntry | null {
  return history.get(buildHistoryKey(groupId, actorId, taskId)) ?? null;
}

export function upsertTaskAdvisorHistoryEntry(
  groupId: string,
  actorId: string,
  taskId: string,
  nextEntry: TaskAdvisorHistoryEntry,
  history: TaskAdvisorHistoryState = historyByKey,
): void {
  history.set(buildHistoryKey(groupId, actorId, taskId), nextEntry);
}

export function replaceTaskAdvisorHistory(history: TaskAdvisorHistoryState): void {
  historyByKey.clear();
  for (const [key, value] of history.entries()) {
    historyByKey.set(key, value);
  }
}

export function resetTaskAdvisorHistory(): void {
  historyByKey.clear();
}
