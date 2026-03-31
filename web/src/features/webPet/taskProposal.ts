import type { ReminderAction } from "./types";

type TaskProposalAction = Extract<ReminderAction, { type: "task_proposal" }>;

function quoted(value: string): string {
  return `"${value.replace(/"/g, '\\"')}"`;
}

export function buildTaskProposalMessage(action: TaskProposalAction): string {
  const explicit = String(action.text || "").trim();
  if (explicit) return explicit;

  const taskId = String(action.taskId || "").trim();
  const title = String(action.title || "").trim();
  const status = String(action.status || "").trim();
  const assignee = String(action.assignee || "").trim();

  const refs: string[] = [];
  if (taskId) refs.push(`task_id=${taskId}`);
  if (title) refs.push(`title=${quoted(title)}`);
  if (status) refs.push(`status=${status}`);
  if (assignee) refs.push(`assignee=${assignee}`);

  const op = String(action.operation || "").trim() || "update";
  const opText =
    op === "create"
      ? "create"
      : op === "move"
        ? "move"
        : op === "handoff"
          ? "handoff"
          : op === "archive"
            ? "archive"
            : "update";

  return `Pet task proposal: please use cccc_task to ${opText} this task${refs.length > 0 ? ` (${refs.join(", ")})` : ""}.`;
}
