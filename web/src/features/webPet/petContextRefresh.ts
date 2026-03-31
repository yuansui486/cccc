import type { LedgerEvent } from "../../types";

const PET_CONTEXT_REFRESH_EVENT_KINDS = new Set([
  "pet.decisions.replace",
  "pet.decisions.clear",
  "pet.decision.outcome",
]);

export function shouldRefreshPetContextFromEvent(event: LedgerEvent | null | undefined): boolean {
  if (!event) return false;
  return PET_CONTEXT_REFRESH_EVENT_KINDS.has(String(event.kind || "").trim());
}

export function getLatestPetContextRefreshMarker(events: LedgerEvent[]): string {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!shouldRefreshPetContextFromEvent(event)) continue;
    const eventId = String(event?.id || "").trim();
    const kind = String(event?.kind || "").trim();
    if (eventId && kind) {
      return `${kind}:${eventId}`;
    }
  }
  return "";
}
