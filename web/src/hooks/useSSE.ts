// SSE connection management for the ledger stream.
import { useEffect, useRef } from "react";
import { useGroupStore, useUIStore, useModalStore } from "../stores";
import {
  getOutboxEntry,
  releaseTransferredPreviewUrls,
  transferOutboxPreviewUrls,
  useChatOutboxStore,
} from "../stores/chatOutboxStore";
import { beginContextRequest, isLatestContextRequest } from "../stores/useGroupStore";
import * as api from "../services/api";
import type { FetchContextOptions } from "../services/api";
import type { Actor, ChatMessageData, GroupContext } from "../types";
import { runReconnectCatchup, scheduleContextSummaryCatchup } from "./sseCatchup";
import {
  isContextSyncEvent,
  isChatReadEvent,
  isChatAckEvent,
  isChatMessageEvent,
  isActorActivityEvent,
  extractChatReadData,
  extractChatAckData,
  initializeReadStatus,
  initializeAckStatus,
  initializeObligationStatus,
  shouldIncrementUnread,
  getActorRefreshMode,
  isPresentationPublishEvent,
  isPresentationClearEvent,
  // Re-export for consumers
  getRecipientActorIdsForEvent,
  getAckRecipientIdsForEvent,
} from "../utils/ledgerEventHandlers";
import { getPresentationMessageRefs, getPresentationRefStatus } from "../utils/presentationRefs";
import { mergeLedgerEvents } from "../utils/mergeLedgerEvents";

// Re-export for backward compatibility
export { getRecipientActorIdsForEvent, getAckRecipientIdsForEvent };

const MAX_RECONCILED_EVENTS = 800;

function mergeCanonicalAttachmentsWithOptimisticPreview(
  ev: Record<string, unknown>,
  groupId: string,
): { event: Record<string, unknown>; transferredPreviewUrls: string[] } {
  if (String(ev.kind || "").trim() !== "chat.message" || String(ev.by || "").trim() !== "user") {
    return { event: ev, transferredPreviewUrls: [] };
  }
  const data = ev.data && typeof ev.data === "object" ? (ev.data as Record<string, unknown>) : null;
  const clientId = data && typeof data.client_id === "string" ? data.client_id.trim() : "";
  if (!clientId) {
    return { event: ev, transferredPreviewUrls: [] };
  }

  const outboxEntry = getOutboxEntry(groupId, clientId);
  const optimisticData = outboxEntry?.event?.data && typeof outboxEntry.event.data === "object"
    ? (outboxEntry.event.data as { attachments?: unknown[] })
    : null;
  const optimisticAttachments = Array.isArray(optimisticData?.attachments) ? optimisticData.attachments : [];
  const canonicalAttachments = Array.isArray(data?.attachments) ? data.attachments : [];
  if (optimisticAttachments.length <= 0 || canonicalAttachments.length <= 0) {
    return { event: ev, transferredPreviewUrls: [] };
  }

  const mergedAttachments = canonicalAttachments.map((attachment, index) => {
    if (!attachment || typeof attachment !== "object") return attachment;
    const optimistic = optimisticAttachments[index];
    if (!optimistic || typeof optimistic !== "object") return attachment;
    const previewUrl = typeof (optimistic as { local_preview_url?: unknown }).local_preview_url === "string"
      ? String((optimistic as { local_preview_url?: string }).local_preview_url || "").trim()
      : "";
    if (!previewUrl.startsWith("blob:")) return attachment;
    return {
      ...attachment,
      local_preview_url: previewUrl,
    };
  });

  const transferredPreviewUrls = transferOutboxPreviewUrls(groupId, clientId);
  return {
    event: {
      ...ev,
      data: {
        ...data,
        attachments: mergedAttachments,
      },
    },
    transferredPreviewUrls,
  };
}

interface UseSSEOptions {
  activeTabRef: React.MutableRefObject<string>;
  chatAtBottomRef: React.MutableRefObject<boolean>;
  actorsRef: React.MutableRefObject<Actor[]>;
}

export function useSSE({ activeTabRef, chatAtBottomRef, actorsRef }: UseSSEOptions) {
  // Use individual selectors to avoid subscribing to the entire store.
  // Without selectors, every state change (e.g. appendEvent) would trigger
  // a re-render cascade through App.tsx → all children.
  const selectedGroupId = useGroupStore((s) => s.selectedGroupId);
  const appendEvent = useGroupStore((s) => s.appendEvent);
  const updateReadStatus = useGroupStore((s) => s.updateReadStatus);
  const updateAckStatus = useGroupStore((s) => s.updateAckStatus);
  const updateReplyStatus = useGroupStore((s) => s.updateReplyStatus);
  const incrementActorUnread = useGroupStore((s) => s.incrementActorUnread);
  const updateActorActivity = useGroupStore((s) => s.updateActorActivity);
  const setGroupContext = useGroupStore((s) => s.setGroupContext);
  const refreshActors = useGroupStore((s) => s.refreshActors);
  const refreshPresentation = useGroupStore((s) => s.refreshPresentation);

  const incrementChatUnread = useUIStore((s) => s.incrementChatUnread);
  const setSSEStatus = useUIStore((s) => s.setSSEStatus);
  const markPresentationSlotAttention = useModalStore((s) => s.markPresentationSlotAttention);
  const clearPresentationSlotAttention = useModalStore((s) => s.clearPresentationSlotAttention);

  const eventSourceRef = useRef<EventSource | null>(null);
  const contextRefreshTimerRef = useRef<number | null>(null);
  const selectedGroupIdRef = useRef<string>("");
  const reconnectDelayRef = useRef<number>(1000);
  const reconnectTimerRef = useRef<number | null>(null);
  const hasConnectedOnceRef = useRef<boolean>(false);

  useEffect(() => {
    selectedGroupIdRef.current = selectedGroupId;
  }, [selectedGroupId]);

  function getNotifyTargetActorId(ev: unknown): string {
    if (ev === null || typeof ev !== "object") return "";
    const kind = String((ev as { kind?: unknown }).kind || "").trim();
    if (kind !== "system.notify") return "";
    const data = (ev as { data?: unknown }).data;
    if (!data || typeof data !== "object") return "";
    const targetActorId = String((data as { target_actor_id?: unknown }).target_actor_id || "").trim();
    return targetActorId && targetActorId !== "user" ? targetActorId : "";
  }

  async function fetchContext(groupId: string, opts?: FetchContextOptions) {
    if (opts?.fresh && contextRefreshTimerRef.current) {
      window.clearTimeout(contextRefreshTimerRef.current);
      contextRefreshTimerRef.current = null;
    }
    const contextEpoch = beginContextRequest(groupId);
    const resp = await api.fetchContext(groupId, {
      fresh: opts?.fresh,
      detail: opts?.detail ?? "summary",
    });
    if (
      resp.ok &&
      resp.result &&
      typeof resp.result === "object" &&
      selectedGroupIdRef.current === groupId &&
      isLatestContextRequest(groupId, contextEpoch)
    ) {
      setGroupContext(resp.result as GroupContext);
    }
  }

  async function reconcileLedgerTail(groupId: string) {
    const resp = await api.fetchLedgerTail(groupId);
    if (!resp.ok || selectedGroupIdRef.current !== groupId) return;

    const store = useGroupStore.getState();
    const bucket = store.chatByGroup[groupId];
    const currentEvents = Array.isArray(bucket?.events) ? bucket.events : [];
    const fetchedEvents = Array.isArray(resp.result.events) ? resp.result.events : [];
    const nextEvents = mergeLedgerEvents(currentEvents, fetchedEvents, MAX_RECONCILED_EVENTS);

    store.setEvents(
      nextEvents,
      groupId
    );
    store.setHasMoreHistory(!!resp.result.has_more, groupId);
  }

  async function resyncAfterReconnect(groupId: string) {
    await runReconnectCatchup(groupId, {
      invalidateContextRead: api.invalidateContextRead,
      reconcileLedgerTail,
      refreshActors,
      fetchContextSummary: fetchContext,
    });
  }

  function connectStream(groupId: string) {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    setSSEStatus("connecting");
    const es = new EventSource(api.withAuthToken(`/api/v1/groups/${encodeURIComponent(groupId)}/ledger/stream`));

    const isReconnect = hasConnectedOnceRef.current;

    es.onopen = () => {
      reconnectDelayRef.current = 1000;
      setSSEStatus("connected");
      hasConnectedOnceRef.current = true;

      // New SSE connections start at EOF, so every reconnect needs a
      // lightweight catch-up to cover the disconnect window.
      if (isReconnect) {
        void resyncAfterReconnect(groupId);
      }
    };

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      setSSEStatus("disconnected");

      const delay = reconnectDelayRef.current;
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null;
        if (selectedGroupIdRef.current === groupId) {
          connectStream(groupId);
        }
      }, delay);

      reconnectDelayRef.current = Math.min(delay * 2, 30000);
    };

    es.addEventListener("ledger", (e) => {
      const msg = e as MessageEvent;
      try {
        const ev = JSON.parse(String(msg.data || "{}"));

        // Context sync - debounced refresh
        if (isContextSyncEvent(ev)) {
          contextRefreshTimerRef.current = scheduleContextSummaryCatchup(groupId, {
            invalidateContextRead: api.invalidateContextRead,
            existingTimer: contextRefreshTimerRef.current,
            clearTimer: window.clearTimeout,
            setTimer: (cb, delayMs) => window.setTimeout(cb, delayMs),
            fetchContextSummary: (gid, opts) => {
              void fetchContext(gid, opts);
            },
          });
          return;
        }

        // Actor activity - lightweight idle_seconds update (no full refresh)
        if (isActorActivityEvent(ev)) {
          const actors = ev.data?.actors;
          if (Array.isArray(actors) && actors.length > 0) {
            updateActorActivity(actors);
          }
          return;
        }

        if (isPresentationPublishEvent(ev)) {
          void refreshPresentation(groupId);

          const slotId = String(ev.data?.slot_id || "").trim();
          if (slotId) {
            markPresentationSlotAttention(groupId, slotId);
          }
          return;
        }

        if (isPresentationClearEvent(ev)) {
          void refreshPresentation(groupId);
          const clearedSlots = Array.isArray(ev.data?.cleared_slots)
            ? ev.data.cleared_slots
            : [];
          for (const slot of clearedSlots) {
            const slotId = String(slot || "").trim();
            if (slotId) {
              clearPresentationSlotAttention(groupId, slotId);
            }
          }
          return;
        }

        // Chat read status update
        if (isChatReadEvent(ev)) {
          const data = extractChatReadData(ev);
          if (data) {
            updateReadStatus(data.eventId, data.actorId, groupId);
          }
          if (getActorRefreshMode(ev) === "unread") {
            void refreshActors(groupId, { includeUnread: true });
          }
          return;
        }

        // Chat ack status update
        if (isChatAckEvent(ev)) {
          const data = extractChatAckData(ev);
          if (data) {
            updateAckStatus(data.eventId, data.actorId, groupId);
          }
          return;
        }

        const reconciled = mergeCanonicalAttachmentsWithOptimisticPreview(ev as Record<string, unknown>, groupId);
        const nextEvent = reconciled.event;

        // Initialize read/ack status for new messages
        initializeReadStatus(nextEvent, actorsRef.current);
        initializeAckStatus(nextEvent, actorsRef.current);
        initializeObligationStatus(nextEvent, actorsRef.current);

        appendEvent(nextEvent, groupId);

        // Reconcile outbox: when a user's chat.message arrives via SSE,
        // remove only the exact optimistic entry that produced this canonical event.
        if (isChatMessageEvent(nextEvent) && String(nextEvent.by || "") === "user") {
          const msgData = nextEvent.data && typeof nextEvent.data === "object" ? (nextEvent.data as { client_id?: unknown }) : null;
          const clientId = msgData && typeof msgData.client_id === "string" ? msgData.client_id.trim() : "";
          if (clientId) {
            useChatOutboxStore.getState().remove(groupId, clientId);
            releaseTransferredPreviewUrls(reconciled.transferredPreviewUrls);
          }
        }

        // Reply to an earlier message updates its obligation status in-place.
        if (isChatMessageEvent(nextEvent)) {
          const msgData = nextEvent.data && typeof nextEvent.data === "object" ? (nextEvent.data as { reply_to?: unknown }) : null;
          const replyTo = msgData && typeof msgData.reply_to === "string" ? String(msgData.reply_to || "").trim() : "";
          const replyBy = String(nextEvent.by || "").trim();
          if (replyTo && replyBy) {
            updateReplyStatus(replyTo, replyBy, groupId);
          }
        }

        if (isChatMessageEvent(nextEvent) && String(nextEvent.by || "").trim() !== "user") {
          const msgData = nextEvent.data && typeof nextEvent.data === "object" ? (nextEvent.data as ChatMessageData) : null;
          const presentationRefs = getPresentationMessageRefs(msgData?.refs);
          const needsAttention =
            String(msgData?.priority || "normal").trim() === "attention" ||
            !!msgData?.reply_required;
          for (const ref of presentationRefs) {
            if (needsAttention || getPresentationRefStatus(ref, msgData, ev) === "needs_user") {
              const slotId = String(ref.slot_id || "").trim();
              if (slotId) {
                markPresentationSlotAttention(groupId, slotId);
              }
            }
          }
        }

        if (isChatMessageEvent(nextEvent)) {
          const recipients = getRecipientActorIdsForEvent(nextEvent, actorsRef.current);
          if (recipients.length > 0) {
            incrementActorUnread(recipients);
          }
        }

        // Update unread count
        if (shouldIncrementUnread(nextEvent, activeTabRef.current === "chat", chatAtBottomRef.current)) {
          incrementChatUnread(groupId);
        }

        const notifyTargetActorId = getNotifyTargetActorId(nextEvent);
        const actorRefreshMode = getActorRefreshMode(nextEvent);
        if (notifyTargetActorId) {
          // Hot-path optimization: most system.notify events already declare a
          // single target actor, so we can update the unread badge locally
          // instead of forcing a daemon round-trip for actor_list(unread=true).
          incrementActorUnread([notifyTargetActorId]);
        } else if (actorRefreshMode === "unread") {
          void refreshActors(groupId, { includeUnread: true });
        } else if (actorRefreshMode === "readonly") {
          void refreshActors(groupId, { includeUnread: false });
        }
      } catch {
        /* ignore parse errors */
      }
    });
    eventSourceRef.current = es;
  }

  function cleanup() {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (contextRefreshTimerRef.current) {
      window.clearTimeout(contextRefreshTimerRef.current);
      contextRefreshTimerRef.current = null;
    }
    reconnectDelayRef.current = 1000;
    hasConnectedOnceRef.current = false;
    setSSEStatus("disconnected");
  }

  return {
    connectStream,
    fetchContext,
    cleanup,
    contextRefreshTimerRef,
  };
}
