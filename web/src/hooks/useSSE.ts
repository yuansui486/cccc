// SSE connection management for the ledger stream.
import { useEffect, useRef } from "react";
import { useGroupStore, useUIStore, useModalStore } from "../stores";
import { useChatOutboxStore } from "../stores/chatOutboxStore";
import { beginContextRequest, isLatestContextRequest } from "../stores/useGroupStore";
import * as api from "../services/api";
import type { FetchContextOptions } from "../services/api";
import type { Actor, ChatMessageData, GroupContext } from "../types";
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

// Re-export for backward compatibility
export { getRecipientActorIdsForEvent, getAckRecipientIdsForEvent };

const MAX_RECONCILED_EVENTS = 800;

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
  const scheduleActorUnreadRefresh = useGroupStore((s) => s.scheduleActorUnreadRefresh);

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
    const fetchedById = new Map(
      fetchedEvents
        .filter((event) => !!event?.id)
        .map((event) => [String(event.id), event] as const)
    );
    const currentIds = new Set(currentEvents.map((event) => String(event.id || "")).filter(Boolean));

    const reconciled = currentEvents.map((event) => {
      const eventId = String(event.id || "");
      return eventId && fetchedById.has(eventId) ? fetchedById.get(eventId)! : event;
    });
    const missingEvents = fetchedEvents.filter((event) => {
      const eventId = String(event.id || "");
      return !eventId || !currentIds.has(eventId);
    });
    const nextEvents = [...reconciled, ...missingEvents];

    store.setEvents(
      nextEvents.length > MAX_RECONCILED_EVENTS
        ? nextEvents.slice(nextEvents.length - MAX_RECONCILED_EVENTS)
        : nextEvents,
      groupId
    );
    store.setHasMoreHistory(!!resp.result.has_more, groupId);
  }

  async function resyncAfterReconnect(groupId: string) {
    await Promise.allSettled([
      reconcileLedgerTail(groupId),
      refreshActors(groupId, { includeUnread: false }),
      fetchContext(groupId, { fresh: true, detail: "summary" }),
    ]);
    scheduleActorUnreadRefresh(groupId, 800);
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
          api.invalidateContextRead(groupId);
          if (contextRefreshTimerRef.current) window.clearTimeout(contextRefreshTimerRef.current);
          contextRefreshTimerRef.current = window.setTimeout(() => {
            contextRefreshTimerRef.current = null;
            void fetchContext(groupId, { fresh: true, detail: "summary" });
          }, 150);
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
          scheduleActorUnreadRefresh(groupId, 400);
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

        // Initialize read/ack status for new messages
        initializeReadStatus(ev, actorsRef.current);
        initializeAckStatus(ev, actorsRef.current);
        initializeObligationStatus(ev, actorsRef.current);

        appendEvent(ev, groupId);

        // Reconcile outbox: when a user's chat.message arrives via SSE,
        // remove only the exact optimistic entry that produced this canonical event.
        if (isChatMessageEvent(ev) && String(ev.by || "") === "user") {
          const msgData = ev.data && typeof ev.data === "object" ? (ev.data as { client_id?: unknown }) : null;
          const clientId = msgData && typeof msgData.client_id === "string" ? msgData.client_id.trim() : "";
          if (clientId) {
            useChatOutboxStore.getState().remove(groupId, clientId);
          }
        }

        // Reply to an earlier message updates its obligation status in-place.
        if (isChatMessageEvent(ev)) {
          const msgData = ev.data && typeof ev.data === "object" ? (ev.data as { reply_to?: unknown }) : null;
          const replyTo = msgData && typeof msgData.reply_to === "string" ? String(msgData.reply_to || "").trim() : "";
          const replyBy = String(ev.by || "").trim();
          if (replyTo && replyBy) {
            updateReplyStatus(replyTo, replyBy, groupId);
          }
        }

        if (isChatMessageEvent(ev) && String(ev.by || "").trim() !== "user") {
          const msgData = ev.data && typeof ev.data === "object" ? (ev.data as ChatMessageData) : null;
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

        if (isChatMessageEvent(ev)) {
          const recipients = getRecipientActorIdsForEvent(ev, actorsRef.current);
          if (recipients.length > 0) {
            incrementActorUnread(recipients);
          }
        }

        // Update unread count
        if (shouldIncrementUnread(ev, activeTabRef.current === "chat", chatAtBottomRef.current)) {
          incrementChatUnread(groupId);
        }

        const actorRefreshMode = getActorRefreshMode(ev);
        if (actorRefreshMode === "readonly") {
          void refreshActors(groupId, { includeUnread: false });
        } else if (actorRefreshMode === "unread") {
          scheduleActorUnreadRefresh(groupId, 400);
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
