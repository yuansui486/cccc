import type { PetCompanionProfile, PetReminder } from "./types";
import { buildTaskProposalMessage } from "./taskProposal";

export function getPetReminderDraftText(reminder: PetReminder | null | undefined): string {
  if (!reminder || reminder.action.type !== "draft_message") return "";
  return String(reminder.action.text || "").trim();
}

export function getPetReminderPrimaryText(reminder: PetReminder | null | undefined): string {
  if (!reminder) return "";
  const summary = String(reminder.summary || "").trim();
  if (summary) return summary;
  if (reminder.action.type === "draft_message") {
    return getPetReminderDraftText(reminder);
  }
  if (reminder.action.type === "task_proposal") {
    return String(reminder.action.text || reminder.action.title || "").trim();
  }
  return "";
}

export function getPetReminderActionPreviewText(reminder: PetReminder | null | undefined): string {
  if (!reminder) return "";
  if (reminder.action.type === "draft_message") {
    return getPetReminderDraftText(reminder);
  }
  if (reminder.action.type === "task_proposal") {
    return buildTaskProposalMessage(reminder.action);
  }
  return "";
}

export function getPetReminderPreviewLabel(
  reminder: PetReminder | null | undefined,
  companion: PetCompanionProfile | null | undefined,
): string {
  if (!reminder) return "";
  const petName = String(companion?.name || "").trim();
  if (reminder.action.type === "draft_message") {
    return petName ? `${petName} 准备替你发出的消息` : "准备发送的消息";
  }
  if (reminder.action.type === "task_proposal") {
    return petName ? `${petName} 准备好的任务建议` : "准备好的任务建议";
  }
  return "";
}

export function getPetReminderRouteInfo(
  reminder: PetReminder | null | undefined,
): { toText: string; replyInThread: boolean } {
  if (!reminder) {
    return { toText: "", replyInThread: false };
  }
  if (reminder.action.type === "draft_message") {
    return {
      toText: Array.isArray(reminder.action.to) ? reminder.action.to.join(", ") : "",
      replyInThread: !!String(reminder.action.replyTo || "").trim(),
    };
  }
  if (reminder.action.type === "task_proposal") {
    return {
      toText: "@foreman",
      replyInThread: false,
    };
  }
  return { toText: "", replyInThread: false };
}
