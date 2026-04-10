import type { ReminderAction } from "./types";
import { renderTaskProposalDraft, renderTaskProposalSummary } from "./taskProposalRenderer";

type TaskProposalAction = Extract<ReminderAction, { type: "task_proposal" }>;

export function buildTaskProposalSummary(action: TaskProposalAction): string {
  return renderTaskProposalSummary(action);
}

export function buildTaskProposalMessage(action: TaskProposalAction): string {
  return renderTaskProposalDraft(action);
}
