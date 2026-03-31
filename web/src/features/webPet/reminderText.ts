import type { PetReminder } from "./types";

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
  if (reminder.action.type === "automation_proposal") {
    return String(reminder.action.summary || reminder.action.title || "").trim();
  }
  return "";
}
