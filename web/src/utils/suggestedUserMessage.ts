import type { LedgerEvent } from "../types";

export type SuggestedUserMessage = { eventId: string; text: string; by: string; ts: string };

export const SUGGESTED_USER_MESSAGE_MAX_CHARS = 4000;
const CONSUMED_SUGGESTED_MESSAGE_KEY = "onecolleague.suggestedUserMessage.consumed.v1";

export function normalizeSuggestedUserMessage(value: unknown): string {
  return String(value || "").trim().slice(0, SUGGESTED_USER_MESSAGE_MAX_CHARS);
}

export function consumeSuggestedUserMessage(eventId: string): void {
  const id = String(eventId || "").trim();
  if (!id || typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(CONSUMED_SUGGESTED_MESSAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    const ids = Array.isArray(parsed) ? parsed.map((item) => String(item || "").trim()).filter(Boolean) : [];
    if (!ids.includes(id)) ids.push(id);
    window.localStorage.setItem(CONSUMED_SUGGESTED_MESSAGE_KEY, JSON.stringify(ids.slice(-200)));
  } catch {
    // Local persistence is advisory; accepting the suggestion must still work.
  }
}

function targetsUser(data: Record<string, unknown>): boolean {
  if (!Array.isArray(data.to)) return false;
  return data.to.some((item) => {
    const token = String(item || "").trim();
    return token === "user" || token === "@user";
  });
}

export function latestSuggestedUserMessage(messages: LedgerEvent[]): SuggestedUserMessage | null {
  for (let index = (messages || []).length - 1; index >= 0; index -= 1) {
    const event = messages[index];
    if (String(event?.kind || "").trim() !== "chat.message") continue;
    const by = String(event?.by || "").trim();
    if (by === "user") return null;
    const eventId = String(event?.id || "").trim();
    const data = event?.data && typeof event.data === "object"
      ? (event.data as Record<string, unknown>)
      : {};
    const text = normalizeSuggestedUserMessage(data.suggested_user_message);
    if (!eventId || !text || !targetsUser(data)) continue;
    return { eventId, text, by, ts: String(event?.ts || "").trim() };
  }
  return null;
}
