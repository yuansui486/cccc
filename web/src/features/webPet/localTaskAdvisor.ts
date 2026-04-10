import type { PetReminder } from "./types";
import type { GroupContext } from "../../types";
import { buildTaskAdvisorEvidence } from "./taskAdvisor/buildEvidence";
import {
  cloneTaskAdvisorHistory,
  replaceTaskAdvisorHistory,
  type TaskAdvisorHistoryState,
} from "./taskAdvisor/history";
import { indexTaskAdvisorContext } from "./taskAdvisor/indexContext";
import { renderTaskProposalReminder } from "./taskAdvisor/renderProposal";
import { taskAdvisorRules } from "./taskAdvisor/rules";
import type { TaskProposalCandidate } from "./taskAdvisor/types";
import type { TaskProposalStylePolicy } from "./types";

export type LocalTaskProposalEvaluation = {
  reminders: PetReminder[];
  nextHistory: TaskAdvisorHistoryState;
  signature: string;
};

function buildTaskAdvisorContextSignature(context: ReturnType<typeof indexTaskAdvisorContext>): string {
  if (!context) return "";
  const agents = context.agentStates.map((agent) => ({
    id: String(agent?.id || "").trim(),
    activeTaskId: String(agent?.hot?.active_task_id || "").trim(),
    focus: String(agent?.hot?.focus || "").trim(),
    nextAction: String(agent?.hot?.next_action || "").trim(),
    blockers: Array.isArray(agent?.hot?.blockers)
      ? agent.hot.blockers.map((item) => String(item || "").trim()).filter(Boolean)
      : [],
  }));
  const tasks = context.tasks.map((task) => ({
    id: String(task.id || "").trim(),
    title: String(task.title || "").trim(),
    status: String(task.status || "").trim(),
    waitingOn: String(task.waiting_on || "").trim(),
    assignee: String(task.assignee || "").trim(),
    blockedBy: Array.isArray(task.blocked_by)
      ? task.blocked_by.map((item) => String(item || "").trim()).filter(Boolean)
      : [],
    updatedAt: String(task.updated_at || "").trim(),
  }));
  return JSON.stringify({ groupId: context.groupId, agents, tasks });
}

export function evaluateLocalTaskProposalReminders(
  groupId: string,
  groupContext: GroupContext | null,
  style?: TaskProposalStylePolicy,
  history?: TaskAdvisorHistoryState,
): LocalTaskProposalEvaluation {
  const context = indexTaskAdvisorContext({ groupId, groupContext });
  const signature = buildTaskAdvisorContextSignature(context);
  const baseHistory = history ? cloneTaskAdvisorHistory(history) : cloneTaskAdvisorHistory();
  if (!context) {
    return {
      reminders: [],
      nextHistory: baseHistory,
      signature,
    };
  }

  const { evidenceList, nextHistory } = buildTaskAdvisorEvidence(context, baseHistory);
  const bestByTask = new Map<string, TaskProposalCandidate>();
  for (const evidence of evidenceList) {
    for (const rule of taskAdvisorRules) {
      const candidate = rule(evidence);
      if (!candidate) continue;
      const previous = bestByTask.get(candidate.taskId);
      if (!previous || candidate.priority > previous.priority) {
        bestByTask.set(candidate.taskId, candidate);
      }
    }
  }

  return {
    reminders: Array.from(bestByTask.values())
      .sort((left, right) => right.priority - left.priority)
      .map((candidate) => renderTaskProposalReminder(candidate, style)),
    nextHistory,
    signature,
  };
}

export function buildLocalTaskProposalReminders(
  groupId: string,
  groupContext: GroupContext | null,
  style?: TaskProposalStylePolicy,
): PetReminder[] {
  const evaluation = evaluateLocalTaskProposalReminders(groupId, groupContext, style);
  replaceTaskAdvisorHistory(evaluation.nextHistory);
  return evaluation.reminders;
}
