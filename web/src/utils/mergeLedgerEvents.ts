import type { LedgerEvent } from "../types";

export function mergeLedgerEvents(existing: LedgerEvent[], incoming: LedgerEvent[], maxEvents: number): LedgerEvent[] {
  const nextIncoming = Array.isArray(incoming) ? incoming.filter(Boolean) : [];
  if (nextIncoming.length === 0) {
    const nextExisting = Array.isArray(existing) ? existing.filter(Boolean) : [];
    return nextExisting.length > maxEvents ? nextExisting.slice(nextExisting.length - maxEvents) : nextExisting;
  }

  const incomingIds = new Set(
    nextIncoming
      .map((event) => String(event?.id || "").trim())
      .filter((eventId) => eventId.length > 0)
  );

  const localOnlyExisting = (Array.isArray(existing) ? existing : []).filter((event) => {
    if (!event) return false;
    const eventId = String(event.id || "").trim();
    return !eventId || !incomingIds.has(eventId);
  });

  const merged = [...nextIncoming, ...localOnlyExisting]
    .map((event, index) => ({
      event,
      index,
      ts: Date.parse(String(event.ts || "")),
    }))
    .sort((left, right) => {
      const leftValid = Number.isFinite(left.ts);
      const rightValid = Number.isFinite(right.ts);
      if (leftValid && rightValid && left.ts !== right.ts) return left.ts - right.ts;
      if (leftValid !== rightValid) return leftValid ? -1 : 1;
      return left.index - right.index;
    })
    .map((entry) => entry.event);

  return merged.length > maxEvents ? merged.slice(merged.length - maxEvents) : merged;
}
