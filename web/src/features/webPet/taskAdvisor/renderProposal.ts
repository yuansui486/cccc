import type { PetReminder } from "../types";
import { buildTaskProposalSummary } from "../taskProposal";
import type { TaskProposalCandidate } from "./types";
import type { TaskProposalStylePolicy } from "../types";

export function renderTaskProposalReminder(
  candidate: TaskProposalCandidate,
  style?: TaskProposalStylePolicy,
): PetReminder {
  const action = candidate.action.type === "task_proposal"
    ? { ...candidate.action, style: candidate.action.style || style }
    : candidate.action;
  const summary = action.type === "task_proposal"
    ? buildTaskProposalSummary(action)
    : candidate.summary || "";
  return {
    id: candidate.fingerprint,
    kind: "suggestion",
    priority: candidate.priority,
    summary,
    agent: "system",
    source: {
      taskId: candidate.taskId,
      actorId: candidate.actorId,
    },
    fingerprint: candidate.fingerprint,
    action,
  };
}
