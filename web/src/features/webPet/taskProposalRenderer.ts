import i18n from "../../i18n";
import { getDefaultTaskProposalStylePolicy } from "./taskProposalStylePolicy";
import type { ReminderAction, TaskProposalStylePolicy } from "./types";

type TaskProposalAction = Extract<ReminderAction, { type: "task_proposal" }>;
type TaskProposalRenderMode = "summary" | "draft";

function quoted(value: string): string {
  return `"${value.replace(/"/g, '\\"')}"`;
}

function tr(key: string, vars?: Record<string, unknown>): string {
  return String(i18n.t(key, { ns: "webPet", ...(vars || {}) }));
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeTaskTitleForSummary(taskId: string, title: string): string {
  const normalizedTaskId = String(taskId || "").trim();
  const normalizedTitle = String(title || "").trim();
  if (!normalizedTaskId || !normalizedTitle) return normalizedTitle;
  const duplicatePrefix = new RegExp(`^${escapeRegExp(normalizedTaskId)}(?:\\s*[:：\\-]\\s*|\\s+)`, "i");
  return normalizedTitle.replace(duplicatePrefix, "").trim();
}

function formatTaskProposalRefs(action: TaskProposalAction): string {
  const taskId = String(action.taskId || "").trim();
  const title = String(action.title || "").trim();
  const status = String(action.status || "").trim();
  const assignee = String(action.assignee || "").trim();

  const refs: string[] = [];
  if (taskId) refs.push(`task_id=${taskId}`);
  if (title) refs.push(`title=${quoted(title)}`);
  if (status) refs.push(`status=${status}`);
  if (assignee) refs.push(`assignee=${assignee}`);

  return refs.length > 0 ? ` (${refs.join(", ")})` : "";
}

function formatTaskProposalOperation(operation: TaskProposalAction["operation"]): string {
  if (operation === "create") return "create";
  if (operation === "move") return "move";
  if (operation === "handoff") return "handoff";
  if (operation === "archive") return "archive";
  return "update";
}

function getStylePolicy(action: TaskProposalAction): TaskProposalStylePolicy {
  return action.style || getDefaultTaskProposalStylePolicy();
}

export function renderTaskProposalReason(action: TaskProposalAction, mode: TaskProposalRenderMode): string {
  if (!action.reason) return "";
  const reason = action.reason;
  const style = getStylePolicy(action);
  const taskId = String(action.taskId || "").trim();
  const title = String(action.title || "").trim();
  const displayTitle = normalizeTaskTitleForSummary(taskId, title);
  const suffix = displayTitle ? tr("taskProposal.punctuation.titleSuffix", { title: displayTitle }) : "";

  switch (reason.kind) {
    case "move_active":
      return mode === "summary"
        ? tr("taskProposal.summary.moveActive", { actorId: reason.actorId, taskId, suffix })
        : tr("taskProposal.draft.moveActive", { actorId: reason.actorId });
    case "sync_waiting_user":
      return mode === "summary"
        ? tr(
          style.waitingUserMode === "close"
            ? "taskProposal.summary.escalatedWaitingUser"
            : "taskProposal.summary.syncWaitingUser",
          { taskId, suffix },
        )
        : tr(
          style.waitingUserMode === "close"
            ? "taskProposal.draft.syncWaitingUserClose"
            : "taskProposal.draft.syncWaitingUser",
          { actorId: reason.actorId, focus: quoted(reason.focus) },
        );
    case "sync_blocked":
      return mode === "summary"
        ? tr("taskProposal.summary.syncBlocked", { actorId: reason.actorId, taskId, suffix })
        : tr("taskProposal.draft.syncBlocked", {
          actorId: reason.actorId,
          blockers: quoted(reason.blockers.join("; ")),
        });
    case "stalled_active_task":
      if (mode === "summary") {
        return tr("taskProposal.summary.stalledActiveTask", { taskId, suffix });
      }
      {
        const base = tr("taskProposal.draft.stalledActiveTaskBase", {
          actorId: reason.actorId,
          mountedMinutes: reason.mountedMinutes.toFixed(0),
          focus: quoted(reason.focus),
        });
        if (reason.blockers.length === 0) {
          return `${base} ${tr(
            style.stalledActiveMode === "escalate"
              ? "taskProposal.draft.stalledActiveTaskNoBlockersEscalate"
              : "taskProposal.draft.stalledActiveTaskNoBlockers",
          )}`.trim();
        }
        return `${base} ${tr("taskProposal.draft.stalledActiveTaskWithBlockers", {
          blockers: quoted(reason.blockers.join("; ")),
        })}`.trim();
      }
    case "ownership_drift":
      if (mode === "summary") {
        return tr("taskProposal.summary.ownershipDrift", { taskId, actorId: reason.actorId, suffix });
      }
      {
        const activeTaskText = reason.currentActiveTaskId
          ? tr("taskProposal.draft.ownershipDriftActiveTask", {
            actorId: reason.actorId,
            activeTaskId: reason.currentActiveTaskId,
          })
          : tr("taskProposal.draft.ownershipDriftNoActiveTask", {
            actorId: reason.actorId,
          });
        return tr("taskProposal.draft.ownershipDrift", {
          activeTaskText,
          taskId: taskId || tr("taskFallback"),
          resolution: tr(
            style.ownershipDriftMode === "reassign"
              ? "taskProposal.draft.ownershipDriftResolutionReassign"
              : "taskProposal.draft.ownershipDriftResolutionReconfirm",
            { taskId: taskId || tr("taskFallback") },
          ),
        });
      }
    case "assign_active_owner":
      return mode === "summary"
        ? tr("taskProposal.summary.assignActiveOwner", { taskId, actorId: reason.actorId, suffix })
        : tr("taskProposal.draft.assignActiveOwner", { actorId: reason.actorId });
    case "escalated_waiting_user":
      return mode === "summary"
        ? tr("taskProposal.summary.escalatedWaitingUser", { taskId, suffix })
        : tr("taskProposal.draft.escalatedWaitingUser", {
          actorId: reason.actorId,
          focus: quoted(reason.focus),
          mountedMinutes: reason.mountedMinutes.toFixed(0),
        });
  }
}

export function renderTaskProposalSummary(action: TaskProposalAction): string {
  const rendered = renderTaskProposalReason(action, "summary");
  if (rendered) return rendered;
  const title = String(action.title || "").trim();
  if (title) return title;
  return String(action.taskId || "").trim();
}

export function renderTaskProposalDraft(action: TaskProposalAction): string {
  const reason = renderTaskProposalReason(action, "draft");
  if (!reason) {
    const explicit = String(action.text || "").trim();
    if (explicit) return explicit;
  }
  const opText = formatTaskProposalOperation(action.operation);
  const refs = formatTaskProposalRefs(action);
  return tr("taskProposal.draft.prefix", {
    operation: opText,
    refs,
    reason: reason ? ` ${reason}` : "",
  }).trim();
}
