// Chat Outbox Store — manages optimistic pending messages before server confirmation.
// Pending-only by design: failed sends roll back to the composer instead of living
// as a second local-only message state in the chat timeline.

import { create } from "zustand";
import type { LedgerEvent } from "../types";

function collectEventObjectUrls(event: LedgerEvent | undefined): string[] {
  const data = event?.data;
  const attachments =
    data && typeof data === "object" && Array.isArray((data as { attachments?: unknown[] }).attachments)
      ? ((data as { attachments?: unknown[] }).attachments as unknown[])
      : [];
  const urls: string[] = [];
  for (const item of attachments) {
    if (!item || typeof item !== "object") continue;
    const previewUrl = typeof (item as { local_preview_url?: unknown }).local_preview_url === "string"
      ? String((item as { local_preview_url?: string }).local_preview_url || "").trim()
      : "";
    if (previewUrl.startsWith("blob:")) {
      urls.push(previewUrl);
    }
  }
  return urls;
}

function revokeObjectUrls(urls: string[]): void {
  for (const previewUrl of urls) {
    try {
      URL.revokeObjectURL(previewUrl);
    } catch {
      void 0;
    }
  }
}

function revokeEventObjectUrls(event: LedgerEvent | undefined): void {
  revokeObjectUrls(collectEventObjectUrls(event));
}

const transferredPreviewUrlKeys = new Set<string>();

function previewTransferKey(groupId: string, localId: string): string {
  return `${groupId}::${localId}`;
}

export interface OutboxEntry {
  localId: string;
  groupId: string;
  event: LedgerEvent; // The optimistic event shape for display while the request is in flight.
  createdAt: number;
}

interface ChatOutboxState {
  /** Outbox entries keyed by groupId. Each value is a stable array reference. */
  entriesByGroup: Record<string, OutboxEntry[]>;

  /** Add a pending message to the outbox. */
  enqueue: (groupId: string, localId: string, event: LedgerEvent) => void;

  /** Remove an entry by localId (on HTTP success or SSE reconciliation). */
  remove: (groupId: string, localId: string) => void;

  /** Clear all pending entries for a group and release local preview URLs. */
  clearGroup: (groupId: string) => void;

  /** Clear every pending entry and release all local preview URLs. */
  clearAll: () => void;
}

export const useChatOutboxStore = create<ChatOutboxState>((set) => ({
  entriesByGroup: {},

  enqueue: (groupId, localId, event) =>
    set((state) => {
      const prev = state.entriesByGroup[groupId] || [];
      const entry: OutboxEntry = {
        localId,
        groupId,
        event,
        createdAt: Date.now(),
      };
      return {
        entriesByGroup: {
          ...state.entriesByGroup,
          [groupId]: [...prev, entry],
        },
      };
    }),

  remove: (groupId, localId) =>
    set((state) => {
      const prev = state.entriesByGroup[groupId];
      if (!prev || prev.length === 0) return state;
      const removed = prev.find((e) => e.localId === localId);
      const next = prev.filter((e) => e.localId !== localId);
      if (next.length === prev.length) return state; // no change
      const transferKey = previewTransferKey(groupId, localId);
      if (transferredPreviewUrlKeys.has(transferKey)) {
        transferredPreviewUrlKeys.delete(transferKey);
      } else {
        revokeEventObjectUrls(removed?.event);
      }
      return {
        entriesByGroup: {
          ...state.entriesByGroup,
          [groupId]: next,
        },
      };
    }),

  clearGroup: (groupId) =>
    set((state) => {
      const prev = state.entriesByGroup[groupId];
      if (!prev || prev.length === 0) return state;
      for (const entry of prev) revokeEventObjectUrls(entry.event);
      const nextEntries = { ...state.entriesByGroup };
      delete nextEntries[groupId];
      return { entriesByGroup: nextEntries };
    }),

  clearAll: () =>
    set((state) => {
      const groups = Object.values(state.entriesByGroup);
      if (groups.length === 0) return state;
      for (const entries of groups) {
        for (const entry of entries) revokeEventObjectUrls(entry.event);
      }
      return { entriesByGroup: {} };
    }),
}));

/** Stable selector: returns the outbox entries array for a group (or empty array). */
const EMPTY: OutboxEntry[] = [];
export function selectOutboxEntries(state: ChatOutboxState, groupId: string): OutboxEntry[] {
  return state.entriesByGroup[groupId] || EMPTY;
}

export function getOutboxEntry(groupId: string, localId: string): OutboxEntry | null {
  const entries = useChatOutboxStore.getState().entriesByGroup[groupId] || EMPTY;
  return entries.find((entry) => entry.localId === localId) || null;
}

export function transferOutboxPreviewUrls(groupId: string, localId: string): string[] {
  const entry = getOutboxEntry(groupId, localId);
  if (!entry) return [];
  const urls = collectEventObjectUrls(entry.event);
  if (urls.length > 0) {
    transferredPreviewUrlKeys.add(previewTransferKey(groupId, localId));
  }
  return urls;
}

export function releaseTransferredPreviewUrls(urls: string[], delayMs = 60000): void {
  if (urls.length <= 0) return;
  window.setTimeout(() => {
    revokeObjectUrls(urls);
  }, delayMs);
}

/**
 * Preserve local attachment previews while replacing an optimistic message
 * with the server's canonical event. The caller owns appending/removing the
 * resulting event and releasing transferred URLs after render.
 */
export function mergeCanonicalAttachmentsWithOptimisticPreview(
  event: LedgerEvent,
  groupId: string,
): { event: LedgerEvent; transferredPreviewUrls: string[] } {
  if (String(event.kind || "").trim() !== "chat.message" || String(event.by || "").trim() !== "user") {
    return { event, transferredPreviewUrls: [] };
  }
  const data = event.data && typeof event.data === "object" ? event.data as Record<string, unknown> : null;
  const clientId = data && typeof data.client_id === "string" ? data.client_id.trim() : "";
  if (!clientId) return { event, transferredPreviewUrls: [] };

  const optimisticData = getOutboxEntry(groupId, clientId)?.event?.data;
  const optimisticAttachments = optimisticData && typeof optimisticData === "object" && Array.isArray((optimisticData as { attachments?: unknown[] }).attachments)
    ? (optimisticData as { attachments: unknown[] }).attachments
    : [];
  const canonicalData = data || {};
  const canonicalAttachments = Array.isArray(canonicalData.attachments) ? canonicalData.attachments : [];
  if (optimisticAttachments.length <= 0 || canonicalAttachments.length <= 0) {
    return { event, transferredPreviewUrls: [] };
  }

  const mergedAttachments = canonicalAttachments.map((attachment, index) => {
    if (!attachment || typeof attachment !== "object") return attachment;
    const optimistic = optimisticAttachments[index];
    if (!optimistic || typeof optimistic !== "object") return attachment;
    const previewUrl = typeof (optimistic as { local_preview_url?: unknown }).local_preview_url === "string"
      ? String((optimistic as { local_preview_url?: string }).local_preview_url || "").trim()
      : "";
    return previewUrl.startsWith("blob:") ? { ...attachment, local_preview_url: previewUrl } : attachment;
  });

  return {
    event: { ...event, data: { ...canonicalData, attachments: mergedAttachments } },
    transferredPreviewUrls: transferOutboxPreviewUrls(groupId, clientId),
  };
}
