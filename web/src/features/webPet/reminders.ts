import type { PetReminder } from "./types";

export type { PetReminder } from "./types";

export interface ReminderTaskInput {
  taskId: string;
  title: string;
  assignee?: string;
  waitingOn?: string;
  status?: string;
}

export interface ReminderWaitingUserInput {
  taskId?: string;
  label: string;
  agent?: string;
}

export interface ReminderActorInput {
  actorId: string;
  running: boolean;
  idleSeconds: number;
  activeTaskId?: string;
}

export interface ReminderEventInput {
  eventId: string;
  kind: string;
  by: string;
  text: string;
  to: string[];
  replyRequired: boolean;
  acked: boolean;
  replied: boolean;
}

export interface ProjectPetRemindersInput {
  groupId: string;
  waitingUser: ReminderWaitingUserInput[];
  tasks: ReminderTaskInput[];
  actors: ReminderActorInput[];
  events: ReminderEventInput[];
}

type ReminderDraft = PetReminder & {
  sortIndex: number;
};

const WAITING_USER_PRIORITY = 100;
const MENTION_PRIORITY = 90;
const REPLY_REQUIRED_PRIORITY = 80;
const STALLED_PEER_PRIORITY = 70;
const STALLED_IDLE_SECONDS = 600;

function truncate(text: string, maxChars = 96): string {
  const cleaned = text.trim().replace(/\s+/g, " ");
  if (cleaned.length <= maxChars) return cleaned;
  return cleaned.slice(0, maxChars - 1) + "…";
}

function normalizeStatus(status: string | undefined): string {
  return String(status || "").trim().toLowerCase();
}

function stableTextHash(text: string): string {
  let hash = 2166136261;
  for (const char of text) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function buildFingerprint(
  groupId: string,
  kind: PetReminder["kind"],
  stableSourceId: string
): string {
  return `group:${groupId}:${kind}:${stableSourceId}`;
}

function buildTaskMap(tasks: ReminderTaskInput[]): Map<string, ReminderTaskInput> {
  const taskMap = new Map<string, ReminderTaskInput>();
  for (const task of tasks) {
    const taskId = String(task.taskId || "").trim();
    if (!taskId) continue;
    taskMap.set(taskId, task);
  }
  return taskMap;
}

function createWaitingUserReminder(
  groupId: string,
  taskMap: Map<string, ReminderTaskInput>,
  entry: ReminderWaitingUserInput,
  sortIndex: number
): ReminderDraft | null {
  const taskId = String(entry.taskId || "").trim();
  const mappedTask = taskId ? taskMap.get(taskId) : undefined;
  const summary = truncate(
    mappedTask?.title || entry.label || taskId
  );

  if (!summary) return null;

  const stableSourceId = taskId || `hash:${stableTextHash(summary.toLowerCase())}`;
  return {
    id: `waiting_user:${stableSourceId}`,
    kind: "waiting_user",
    priority: WAITING_USER_PRIORITY,
    summary,
    agent: entry.agent || mappedTask?.assignee || "system",
    ephemeral: false,
    source: taskId ? { taskId } : {},
    fingerprint: buildFingerprint(groupId, "waiting_user", stableSourceId),
    action: taskId
      ? {
          type: "open_task",
          groupId,
          taskId,
        }
      : {
          type: "open_panel",
          groupId,
        },
    sortIndex,
  };
}

function collectWaitingUserReminders(
  input: ProjectPetRemindersInput,
  taskMap: Map<string, ReminderTaskInput>,
  startIndex: number
): ReminderDraft[] {
  const reminders: ReminderDraft[] = [];
  let sortIndex = startIndex;

  if (input.waitingUser.length > 0) {
    for (const entry of input.waitingUser) {
      // Skip entries whose linked task is already done/archived
      const taskId = String(entry.taskId || "").trim();
      if (taskId) {
        const task = taskMap.get(taskId);
        if (task) {
          const status = normalizeStatus(task.status);
          if (status === "done" || status === "archived") continue;
        }
      }
      const reminder = createWaitingUserReminder(
        input.groupId,
        taskMap,
        entry,
        sortIndex
      );
      if (!reminder) continue;
      reminders.push(reminder);
      sortIndex += 1;
    }
    return reminders;
  }

  for (const task of input.tasks) {
    if (normalizeStatus(task.status) === "done") continue;
    if (normalizeStatus(task.status) === "archived") continue;
    if (normalizeStatus(task.waitingOn) !== "user") continue;

    const reminder = createWaitingUserReminder(
      input.groupId,
      taskMap,
      {
        taskId: task.taskId,
        label: task.title,
        agent: task.assignee,
      },
      sortIndex
    );
    if (!reminder) continue;
    reminders.push(reminder);
    sortIndex += 1;
  }

  return reminders;
}

function isReplyRequired(event: ReminderEventInput): boolean {
  return event.replyRequired && !event.acked && !event.replied;
}

function isMention(event: ReminderEventInput): boolean {
  // In multi-agent workflows, agents frequently send status updates addressed
  // to the user. These are already visible in the chat panel and showing them
  // as popup reminders creates noise ("不智能"). Truly actionable messages are
  // covered by the reply_required and waiting_user reminder types instead.
  // The cat mention *reaction* (animation) is handled separately and still works.
  void event;
  return false;
}

function createMentionReminderDraft(
  groupId: string,
  event: ReminderEventInput,
  sortIndex: number
): ReminderDraft | null {
  const eventId = String(event.eventId || "").trim();
  if (!eventId || !isMention(event)) return null;

  return {
    id: `mention:${eventId}`,
    kind: "mention",
    priority: MENTION_PRIORITY,
    summary: truncate(event.text || `${event.by} 提到了你`),
    agent: event.by || "system",
    ephemeral: false,
    source: { eventId },
    fingerprint: buildFingerprint(groupId, "mention", eventId),
    action: {
      type: "open_chat",
      groupId,
      eventId,
    },
    sortIndex,
  };
}

export function createMentionReminder(
  groupId: string,
  event: ReminderEventInput | null | undefined
): PetReminder | null {
  if (!event) return null;
  const reminder = createMentionReminderDraft(groupId, event, 0);
  if (!reminder) return null;
  const { sortIndex: _sortIndex, ...rest } = reminder;
  return {
    ...rest,
    ephemeral: true,
    action: {
      type: "open_chat",
      groupId,
      eventId: rest.source.eventId || rest.id,
    },
  };
}

function collectMentionReminders(
  input: ProjectPetRemindersInput,
  startIndex: number
): ReminderDraft[] {
  const reminders: ReminderDraft[] = [];
  let sortIndex = startIndex;

  for (const event of input.events) {
    const reminder = createMentionReminderDraft(input.groupId, event, sortIndex);
    if (!reminder) continue;
    reminders.push(reminder);
    sortIndex += 1;
  }

  return reminders;
}

function collectReplyRequiredReminders(
  input: ProjectPetRemindersInput,
  startIndex: number
): ReminderDraft[] {
  const reminders: ReminderDraft[] = [];
  let sortIndex = startIndex;

  for (const event of input.events) {
    const eventId = String(event.eventId || "").trim();
    if (!eventId || !isReplyRequired(event)) continue;

    reminders.push({
      id: `reply_required:${eventId}`,
      kind: "reply_required",
      priority: REPLY_REQUIRED_PRIORITY,
      summary: truncate(event.text || `${event.by} 的消息等待回复`),
      agent: event.by || "system",
      ephemeral: false,
      source: { eventId },
      fingerprint: buildFingerprint(input.groupId, "reply_required", eventId),
      action: {
        type: "open_chat",
        groupId: input.groupId,
        eventId,
      },
      sortIndex,
    });
    sortIndex += 1;
  }

  return reminders;
}

function collectStalledPeerReminders(
  input: ProjectPetRemindersInput,
  startIndex: number
): ReminderDraft[] {
  const reminders: ReminderDraft[] = [];
  let sortIndex = startIndex;

  for (const actor of input.actors) {
    const actorId = actor.actorId.trim();
    const activeTaskId = String(actor.activeTaskId || "").trim();
    if (!actorId || !activeTaskId) continue;
    if (!actor.running) continue;
    if (actor.idleSeconds < STALLED_IDLE_SECONDS) continue;

    reminders.push({
      id: `stalled_peer:${actorId}:${activeTaskId}`,
      kind: "stalled_peer",
      priority: STALLED_PEER_PRIORITY,
      summary: `${actorId} 已空闲较久，仍挂着 ${activeTaskId}`,
      agent: actorId,
      ephemeral: false,
      source: { actorId, taskId: activeTaskId },
      fingerprint: buildFingerprint(
        input.groupId,
        "stalled_peer",
        `${actorId}:${activeTaskId}`
      ),
      action: {
        type: "open_panel",
        groupId: input.groupId,
      },
      sortIndex,
    });
    sortIndex += 1;
  }

  return reminders;
}

function dedupeAndSortReminders(reminders: ReminderDraft[]): PetReminder[] {
  const sorted = [...reminders].sort((left, right) => {
    if (right.priority !== left.priority) {
      return right.priority - left.priority;
    }
    return left.sortIndex - right.sortIndex;
  });

  const deduped = new Map<string, PetReminder>();
  for (const reminder of sorted) {
    if (deduped.has(reminder.fingerprint)) continue;
    const { sortIndex: _sortIndex, ...rest } = reminder;
    deduped.set(reminder.fingerprint, rest);
  }

  return [...deduped.values()];
}

export function projectPetReminders(
  input: ProjectPetRemindersInput
): PetReminder[] {
  const taskMap = buildTaskMap(input.tasks);
  const waitingUserReminders = collectWaitingUserReminders(input, taskMap, 0);
  const mentionReminders = collectMentionReminders(
    input,
    waitingUserReminders.length
  );
  const replyRequiredReminders = collectReplyRequiredReminders(
    input,
    waitingUserReminders.length + mentionReminders.length
  );
  const stalledPeerReminders = collectStalledPeerReminders(
    input,
    waitingUserReminders.length +
      mentionReminders.length +
      replyRequiredReminders.length
  );

  return dedupeAndSortReminders([
    ...waitingUserReminders,
    ...mentionReminders,
    ...replyRequiredReminders,
    ...stalledPeerReminders,
  ]);
}
