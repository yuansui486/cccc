import { shouldProjectReminderForGroupState } from "./useWebPetNotifications";
import { shouldSurfaceReminder } from "./useWebPetData";
import type { PetReminder } from "./types";

export function isManualReviewReminderReady(reminder: PetReminder, groupState: string): boolean {
  if (!shouldSurfaceReminder(reminder)) return false;
  const normalizedState = String(groupState || "").trim().toLowerCase();
  if (normalizedState === "idle" && reminder.action.type !== "restart_actor") {
    return false;
  }
  return shouldProjectReminderForGroupState(reminder, groupState);
}
