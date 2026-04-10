import type { GroupContext, Task, AgentState } from "../../../types";
import type { PetReminder } from "../types";

export type TaskAdvisorInput = {
  groupId: string;
  groupContext: GroupContext | null;
};

export type TaskAdvisorContext = {
  groupId: string;
  groupContext: GroupContext;
  agentStates: AgentState[];
  tasks: Task[];
  taskById: Map<string, Task>;
};

export type TaskAdvisorEvidence = {
  groupId: string;
  actorId: string;
  taskId: string;
  task: Task;
  actorActiveTaskId: string;
  taskStatus: string;
  taskWaitingOn: string;
  taskBlockedBy: string[];
  actorFocus: string;
  actorNextAction: string;
  actorBlockers: string[];
  actorMountedThisTask: boolean;
  hasActiveTaskMismatch: boolean;
  focusSuggestsWaitingUser: boolean;
  nextActionSuggestsWaitingUser: boolean;
  hasUnsyncedBlockers: boolean;
  hasOwnershipDrift: boolean;
  hasActiveTaskWithoutOwner: boolean;
  waitingUserSyncOverdue: boolean;
  mountedDurationMs: number;
  focusUnchangedCycles: number;
  hasRecentTaskChange: boolean;
  taskStaleDurationMs: number;
};

export type TaskProposalKind =
  | "move_active"
  | "sync_waiting_user"
  | "sync_blocked"
  | "stalled_active_task"
  | "ownership_drift"
  | "assign_active_owner"
  | "escalated_waiting_user";

export type TaskProposalCandidate = {
  kind: TaskProposalKind;
  fingerprint: string;
  priority: number;
  actorId: string;
  taskId: string;
  title: string;
  summary?: string;
  action: PetReminder["action"];
};

export type TaskAdvisorRule = (evidence: TaskAdvisorEvidence) => TaskProposalCandidate | null;
