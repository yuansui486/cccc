// useChatTab - Encapsulates ChatTab business logic and state.
// Reduces prop drilling by providing state from stores and computed values directly.

import { useMemo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useShallow } from "zustand/react/shallow";
import {
  useGroupStore,
  useUIStore,
  useComposerStore,
  useModalStore,
  useFormStore,
  selectChatBucketState,
} from "../stores";
import { getEffectiveComposerDestGroupId, isComposerGroupSettled } from "../stores/useComposerStore";
import { getChatSession } from "../stores/useUIStore";
import {
  useChatOutboxStore,
  selectOutboxEntries,
  mergeCanonicalAttachmentsWithOptimisticPreview,
  releaseTransferredPreviewUrls,
} from "../stores/chatOutboxStore";
import type {
  Actor,
  LedgerEvent,
  ChatMessageData,
  MessageRef,
  OptimisticAttachment,
  PresentationMessageRef,
  ReplyTarget,
  Task,
} from "../types";
import * as api from "../services/api";
import { buildReplyComposerState } from "../utils/chatReply";
import {
  formatSendMessageError,
  getGroupSendBlockedMessage,
  getGroupSendBlockedReason,
  isFormalChatMessageEvent,
  supportsChatStreamingPlaceholder,
} from "../utils/chatSend";
import { copyTextToClipboard } from "../utils/copy";
import { hasRenderableChatMessageContent } from "../utils/ledgerEventHandlers";
import { useSlashCommands } from "./useSlashCommands";
import {
  buildCapsuleSkillAttachmentDispatchText,
  buildCapsuleSkillDispatchText,
  parseSlashCommandInput,
  type SlashSkillScope,
} from "../utils/slashCommands";
import { useSlashSkillDispatch } from "./useSlashSkillDispatch";
import { buildComposerHistoryEntries } from "../pages/chat/chatComposerHistory";
import { buildComposerMentionSuggestions, type ComposerMentionSuggestion } from "../pages/chat/chatMentionSuggestions";
import { latestSuggestedUserMessage } from "../utils/suggestedUserMessage";
import {
  buildActiveGroupBridgeRemoteTargets,
  createGroupBridgeIdempotencyKey,
  groupBridgeApi,
  type GroupBridgeRemoteReceipt,
  type GroupBridgeRemoteTarget,
} from "../services/api/groupBridge";
import {
  GroupBridgeRemoteLifecycle,
  captureGroupBridgeRemoteComposerIntent,
  decideGroupBridgeRemoteSend,
  isGroupBridgeRemoteViewCurrent,
  isGroupBridgeRemoteComposerIntentCurrent,
  normalizeGroupBridgeRemoteGroupId,
  pollGroupBridgeRemoteReceipt,
  type GroupBridgeRemoteOperationToken,
} from "./groupBridgeRemoteOrchestration";

export const CHAT_SCROLL_SNAPSHOT_MAX_AGE_MS = 30 * 60 * 1000;

export function shouldRestoreDetachedScrollSnapshot(
  snapshot: { mode?: unknown; anchorId?: unknown; updatedAt?: unknown } | null | undefined,
  now = Date.now(),
): boolean {
  if (!snapshot || snapshot.mode !== "detached") return false;
  const anchorId = typeof snapshot.anchorId === "string" ? snapshot.anchorId.trim() : "";
  if (!anchorId) return false;
  const updatedAt = Number(snapshot.updatedAt);
  if (!Number.isFinite(updatedAt) || updatedAt <= 0) return false;
  return now - updatedAt <= CHAT_SCROLL_SNAPSHOT_MAX_AGE_MS;
}

function mergeStreamingCandidates(primary: LedgerEvent, secondary: LedgerEvent): LedgerEvent {
  const primaryData = primary.data && typeof primary.data === "object"
    ? primary.data as ChatMessageData & { pending_placeholder?: unknown; pending_event_id?: unknown; stream_id?: unknown }
    : {};
  const secondaryData = secondary.data && typeof secondary.data === "object"
    ? secondary.data as ChatMessageData & { pending_placeholder?: unknown; pending_event_id?: unknown; stream_id?: unknown }
    : {};
  const primaryText = typeof primaryData.text === "string" ? primaryData.text.trim() : "";
  const secondaryText = typeof secondaryData.text === "string" ? secondaryData.text.trim() : "";
  const primaryTs = String(primary.ts || "");
  const secondaryTs = String(secondary.ts || "");
  const primaryHasText = primaryText.length > 0;
  const secondaryHasText = secondaryText.length > 0;
  const primaryIsPlaceholder = Boolean(primaryData.pending_placeholder);
  const secondaryIsPlaceholder = Boolean(secondaryData.pending_placeholder);

  let display = primary;
  let support = secondary;
  if (secondaryHasText && !primaryHasText) {
    display = secondary;
    support = primary;
  } else if (secondaryHasText === primaryHasText) {
    if (primaryIsPlaceholder && !secondaryIsPlaceholder) {
      display = secondary;
      support = primary;
    } else if (primaryIsPlaceholder === secondaryIsPlaceholder && secondaryTs > primaryTs) {
      display = secondary;
      support = primary;
    }
  }

  const displayData = display.data && typeof display.data === "object"
    ? display.data as ChatMessageData & { pending_placeholder?: unknown; pending_event_id?: unknown; stream_id?: unknown }
    : {};
  const supportData = support.data && typeof support.data === "object"
    ? support.data as ChatMessageData & { pending_placeholder?: unknown; pending_event_id?: unknown; stream_id?: unknown }
    : {};
  const displayActivities = Array.isArray(displayData.activities) ? displayData.activities : [];
  const supportActivities = Array.isArray(supportData.activities) ? supportData.activities : [];
  return {
    ...support,
    ...display,
    ts: String(primary.ts || "") >= String(secondary.ts || "") ? primary.ts : secondary.ts,
    data: {
      ...supportData,
      ...displayData,
      text:
        typeof displayData.text === "string" ? displayData.text
          : (typeof supportData.text === "string" ? supportData.text : ""),
      activities: displayActivities.length > 0 ? displayActivities : supportActivities,
      pending_event_id:
        String(displayData.pending_event_id || "").trim() || String(supportData.pending_event_id || "").trim() || undefined,
      stream_id:
        String(displayData.stream_id || "").trim() || String(supportData.stream_id || "").trim() || undefined,
      pending_placeholder: Boolean(displayData.pending_placeholder),
    },
  };
}

function getNormalizedStreamPhase(data: { stream_phase?: unknown } | null | undefined): string {
  return String(data?.stream_phase || "").trim().toLowerCase();
}

function hasExplicitStreamingPhase(data: { stream_phase?: unknown } | null | undefined): boolean {
  const streamPhase = getNormalizedStreamPhase(data);
  return streamPhase === "commentary" || streamPhase === "final_answer";
}

function isPlaceholderLikeStreamingEvent(data: ChatMessageData & {
  pending_placeholder?: unknown;
  stream_id?: unknown;
  stream_phase?: unknown;
  text?: unknown;
  activities?: unknown;
}): boolean {
  const streamId = String(data.stream_id || "").trim();
  if (data.pending_placeholder) return true;

  if (hasExplicitStreamingPhase(data)) return false;

  const text = typeof data.text === "string" ? data.text.trim() : "";
  if (text) return false;
  if (!hasOnlyQueuedActivities(data.activities)) return false;

  return streamId.startsWith("local:") || streamId.startsWith("pending:");
}

function hasOnlyQueuedActivities(value: unknown): boolean {
  const activities = Array.isArray(value) ? value : [];
  return activities.length === 0 || activities.every((item) => {
    if (!item || typeof item !== "object") return true;
    const kind = String((item as { kind?: unknown }).kind || "").trim();
    const summary = String((item as { summary?: unknown }).summary || "").trim();
    return kind === "queued" && summary === "queued";
  });
}

function hasRichActivities(value: unknown): boolean {
  const activities = Array.isArray(value) ? value : [];
  return activities.some((item) => {
    if (!item || typeof item !== "object") return false;
    const kind = String((item as { kind?: unknown }).kind || "").trim();
    const summary = String((item as { summary?: unknown }).summary || "").trim();
    return kind !== "queued" || summary !== "queued";
  });
}

function getStreamingEventDedupeKey(event: LedgerEvent): string {
  const data = event.data && typeof event.data === "object"
    ? event.data as ChatMessageData & { pending_placeholder?: unknown; pending_event_id?: unknown; stream_id?: unknown }
    : {};
  const actorId = String(event.by || "").trim();
  const pendingEventId = String(data.pending_event_id || "").trim();
  const streamId = String(data.stream_id || "").trim();
  if (!actorId) return "";
  // Placeholder lifecycle events still collapse by pending reply slot, but a
  // real text-bearing stream must keep stream_id identity or short streaming
  // messages will overwrite each other before they ever reach the list.
  if (pendingEventId && (!hasRenderableChatMessageContent(event) || isPlaceholderLikeStreamingEvent(data))) {
    return `pending:${actorId}:${pendingEventId}`;
  }
  if (streamId) {
    return `stream:${actorId}:${streamId}`;
  }
  if (pendingEventId) {
    return `pending:${actorId}:${pendingEventId}`;
  }
  return "";
}

export function dedupeStreamingEvents(streamingEvents: LedgerEvent[]): LedgerEvent[] {
  const byKey = new Map<string, LedgerEvent>();
  const passthrough: LedgerEvent[] = [];

  for (const event of streamingEvents) {
    const data = event.data && typeof event.data === "object"
      ? event.data as ChatMessageData & { pending_placeholder?: unknown; pending_event_id?: unknown; stream_id?: unknown }
      : {};
    const streamId = String(data.stream_id || "").trim();
    const isPendingPlaceholder = Boolean(data.pending_placeholder);
    const dedupeKey = getStreamingEventDedupeKey(event);

    if (!dedupeKey) {
      passthrough.push(event);
      continue;
    }

    const existing = byKey.get(dedupeKey);
    if (!existing) {
      byKey.set(dedupeKey, event);
      continue;
    }

    const existingData = existing.data && typeof existing.data === "object"
      ? existing.data as ChatMessageData & { pending_placeholder?: unknown; pending_event_id?: unknown; stream_id?: unknown }
      : {};
    const existingIsPendingPlaceholder = Boolean(existingData.pending_placeholder);
    const preferCurrent =
      existingIsPendingPlaceholder && !isPendingPlaceholder
        ? true
        : existingIsPendingPlaceholder === isPendingPlaceholder && !!streamId && !String(existingData.stream_id || "").trim();

    byKey.set(
      dedupeKey,
      preferCurrent ? mergeStreamingCandidates(event, existing) : mergeStreamingCandidates(existing, event),
    );
  }

  return [...passthrough, ...byKey.values()];
}

export function collapseActorStreamingPlaceholders(streamingEvents: LedgerEvent[]): LedgerEvent[] {
  const eventsByActor = new Map<string, LedgerEvent[]>();
  for (const event of streamingEvents) {
    const actorId = String(event.by || "").trim();
    if (!actorId) continue;
    const bucket = eventsByActor.get(actorId);
    if (bucket) {
      bucket.push(event);
    } else {
      eventsByActor.set(actorId, [event]);
    }
  }

  const shouldDrop = new Set<LedgerEvent>();
  for (const actorEvents of eventsByActor.values()) {
    if (actorEvents.length <= 1) continue;

    const richReplySlots = new Set<string>();
    actorEvents.forEach((event) => {
      const data = event.data && typeof event.data === "object"
        ? event.data as ChatMessageData & { activities?: unknown[] }
        : {};
      const text = typeof data.text === "string" ? data.text.trim() : "";
      const activities = Array.isArray(data.activities) ? data.activities : [];
      const hasRichStreaming = text.length > 0 || activities.some((item) => {
        if (!item || typeof item !== "object") return false;
        const kind = String((item as { kind?: unknown }).kind || "").trim();
        const summary = String((item as { summary?: unknown }).summary || "").trim();
        return kind !== "queued" || summary !== "queued";
      });
      if (!hasRichStreaming) return;
      const slotKey = getReplySlotKey(event);
      if (slotKey) {
        richReplySlots.add(slotKey);
      }
    });

    if (richReplySlots.size > 0) {
      for (const event of actorEvents) {
        const slotKey = getReplySlotKey(event);
        if (!slotKey || !richReplySlots.has(slotKey)) continue;
        const data = event.data && typeof event.data === "object"
          ? event.data as ChatMessageData & { pending_placeholder?: unknown; activities?: unknown[]; stream_id?: unknown; stream_phase?: unknown }
          : {};
        const text = typeof data.text === "string" ? data.text.trim() : "";
        const onlyQueuedActivities = hasOnlyQueuedActivities(data.activities);
        const isPlaceholderLike = isPlaceholderLikeStreamingEvent(data);
        if (!text && !hasRichActivities(data.activities) && (isPlaceholderLike || (onlyQueuedActivities && !hasExplicitStreamingPhase(data)))) {
          shouldDrop.add(event);
        }
      }
      continue;
    }

    const placeholderOnlyEvents = actorEvents.filter((event) => {
      const data = event.data && typeof event.data === "object"
        ? event.data as ChatMessageData & { pending_placeholder?: unknown; stream_id?: unknown; stream_phase?: unknown }
        : {};
      const text = typeof data.text === "string" ? data.text.trim() : "";
      if (text) return false;
      const onlyQueuedActivities = hasOnlyQueuedActivities(data.activities);
      return (
        onlyQueuedActivities &&
        isPlaceholderLikeStreamingEvent(data)
      );
    });
    if (placeholderOnlyEvents.length <= 1) continue;
    const latestPlaceholder = placeholderOnlyEvents.reduce((latest, current) => {
      const latestTs = String(latest.ts || "");
      const currentTs = String(current.ts || "");
      return currentTs >= latestTs ? current : latest;
    });
    for (const event of placeholderOnlyEvents) {
      if (event !== latestPlaceholder) {
        shouldDrop.add(event);
      }
    }
  }

  return streamingEvents.filter((event) => !shouldDrop.has(event));
}

function dropOrphanQueuedPlaceholders(
  canonicalEvents: LedgerEvent[],
  streamingEvents: LedgerEvent[],
): LedgerEvent[] {
  const renderableCanonicalReplySlots = new Set(
    canonicalEvents
      .filter((event) => hasRenderableChatMessageContent(event))
      .map((event) => getReplySlotKey(event))
      .filter((slotKey) => slotKey.length > 0),
  );

  return streamingEvents.filter((event) => {
    const slotKey = getReplySlotKey(event);
    if (!slotKey || !renderableCanonicalReplySlots.has(slotKey)) return true;
    const data = event.data && typeof event.data === "object"
      ? event.data as ChatMessageData & { pending_placeholder?: unknown; stream_id?: unknown; stream_phase?: unknown }
      : {};
    const text = typeof data.text === "string" ? data.text.trim() : "";
    if (text) return true;
    const isPlaceholderLike = isPlaceholderLikeStreamingEvent(data);
    return !(isPlaceholderLike && !hasRichActivities(data.activities));
  });
}

function getCanonicalStreamingSupersededStreamIds(canonicalEvents: LedgerEvent[]): Set<string> {
  return new Set(
    canonicalEvents
      .filter((event) => hasRenderableChatMessageContent(event))
      .map((event) => {
        const data = event.data && typeof event.data === "object"
          ? event.data as { stream_id?: unknown }
          : null;
        return data && typeof data.stream_id === "string" ? data.stream_id.trim() : "";
      })
      .filter((streamId) => streamId.length > 0)
  );
}

export function getReplySlotKey(event: LedgerEvent): string {
  if (String(event.kind || "").trim() !== "chat.message") return "";
  const actorId = String(event.by || "").trim();
  if (!actorId || actorId === "user") return "";
  const data = event.data && typeof event.data === "object"
    ? event.data as ChatMessageData & { pending_event_id?: unknown; reply_to?: unknown }
    : undefined;
  const replyAnchor =
    typeof data?.pending_event_id === "string" && data.pending_event_id.trim()
      ? data.pending_event_id.trim()
      : typeof data?.reply_to === "string" && data.reply_to.trim()
        ? data.reply_to.trim()
        : "";
  if (!replyAnchor) return "";
  return `${actorId}:${replyAnchor}`;
}

function getReplyAnchorId(event: LedgerEvent): string {
  if (String(event.kind || "").trim() !== "chat.message") return "";
  const data = event.data && typeof event.data === "object"
    ? event.data as ChatMessageData & { pending_event_id?: unknown; reply_to?: unknown }
    : undefined;
  if (typeof data?.pending_event_id === "string" && data.pending_event_id.trim()) {
    return data.pending_event_id.trim();
  }
  if (typeof data?.reply_to === "string" && data.reply_to.trim()) {
    return data.reply_to.trim();
  }
  return "";
}

export function buildReplySlotTsMap(streamingEvents: LedgerEvent[]): Map<string, string> {
  const slotTsByKey = new Map<string, string>();
  for (const event of streamingEvents) {
    const slotKey = getReplySlotKey(event);
    if (!slotKey) continue;
    const ts = String(event.ts || "").trim();
    if (!ts) continue;
    const prev = slotTsByKey.get(slotKey) || "";
    if (!prev || ts < prev) {
      slotTsByKey.set(slotKey, ts);
    }
  }
  return slotTsByKey;
}

export function buildReplyAnchorTsMap(
  messages: LedgerEvent[],
  streamingEvents: LedgerEvent[],
): Map<string, string> {
  const slotTsByKey = buildReplySlotTsMap(streamingEvents);
  const anchorTsById = new Map<string, string>();

  for (const event of messages) {
    if (String(event.kind || "").trim() !== "chat.message") continue;
    const ts = String(event.ts || "").trim();
    if (!ts) continue;
    const eventId = String(event.id || "").trim();
    if (eventId) {
      const prev = anchorTsById.get(eventId) || "";
      if (!prev || ts < prev) anchorTsById.set(eventId, ts);
    }
    const data = event.data && typeof event.data === "object"
      ? event.data as ChatMessageData & { client_id?: unknown }
      : undefined;
    const clientId = typeof data?.client_id === "string" ? data.client_id.trim() : "";
    if (clientId) {
      const prev = anchorTsById.get(clientId) || "";
      if (!prev || ts < prev) anchorTsById.set(clientId, ts);
    }
  }

  for (const event of [...messages, ...streamingEvents]) {
    const slotKey = getReplySlotKey(event);
    if (!slotKey) continue;
    const anchorId = getReplyAnchorId(event);
    if (!anchorId) continue;
    const anchorTs = String(anchorTsById.get(anchorId) || "").trim();
    if (!anchorTs) continue;
    slotTsByKey.set(slotKey, anchorTs);
  }

  return slotTsByKey;
}

export function sortChatMessages(
  messages: LedgerEvent[],
  replySlotTsByKey: Map<string, string>,
): LedgerEvent[] {
  return messages
    .map((event, index) => {
      const slotKey = getReplySlotKey(event);
      const slotTs = slotKey ? String(replySlotTsByKey.get(slotKey) || "").trim() : "";
      const eventTs = String(event.ts || "").trim();
      return {
        event,
        index,
        hasReplySlot: slotKey.length > 0,
        sortTs: slotTs || eventTs,
        eventTs,
      };
    })
    .sort((a, b) => {
      if (a.sortTs && b.sortTs && a.sortTs !== b.sortTs) return a.sortTs.localeCompare(b.sortTs);
      if (a.sortTs && !b.sortTs) return -1;
      if (!a.sortTs && b.sortTs) return 1;
      if (a.sortTs && b.sortTs && a.sortTs === b.sortTs && a.hasReplySlot !== b.hasReplySlot) {
        return a.hasReplySlot ? 1 : -1;
      }
      if (a.eventTs && b.eventTs && a.eventTs !== b.eventTs) return a.eventTs.localeCompare(b.eventTs);
      return a.index - b.index;
    })
    .map((item) => item.event);
}

function getLogicalMessageOrderKey(event: LedgerEvent): string {
  if (String(event.kind || "").trim() !== "chat.message") {
    return `event:${String(event.id || "").trim() || String(event.ts || "").trim()}`;
  }
  const data = event.data && typeof event.data === "object"
    ? event.data as ChatMessageData & { client_id?: unknown; pending_event_id?: unknown; reply_to?: unknown; stream_id?: unknown }
    : undefined;
  const clientId = typeof data?.client_id === "string" ? data.client_id.trim() : "";
  if (clientId) return `client:${clientId}`;

  const actorId = String(event.by || "").trim();
  const replyAnchor =
    typeof data?.pending_event_id === "string" && data.pending_event_id.trim()
      ? data.pending_event_id.trim()
      : typeof data?.reply_to === "string" && data.reply_to.trim()
        ? data.reply_to.trim()
        : "";
  const streamId = typeof data?.stream_id === "string" ? data.stream_id.trim() : "";
  if (actorId && actorId !== "user" && replyAnchor && (event._streaming || !hasRenderableChatMessageContent(event) || streamId)) {
    return `reply:${actorId}:${replyAnchor}`;
  }

  if (streamId) return `stream:${streamId}`;

  const eventId = String(event.id || "").trim();
  if (eventId) return `event:${eventId}`;
  return `fallback:${actorId}:${String(event.ts || "").trim()}`;
}

function getLogicalMessageReplacementKey(event: LedgerEvent): string {
  if (String(event.kind || "").trim() !== "chat.message") {
    return `event:${String(event.id || "").trim() || String(event.ts || "").trim()}`;
  }
  const data = event.data && typeof event.data === "object"
    ? event.data as ChatMessageData & { client_id?: unknown; pending_event_id?: unknown; reply_to?: unknown; stream_id?: unknown }
    : undefined;
  const clientId = typeof data?.client_id === "string" ? data.client_id.trim() : "";
  if (clientId) return `client:${clientId}`;

  const actorId = String(event.by || "").trim();
  const replyAnchor =
    typeof data?.pending_event_id === "string" && data.pending_event_id.trim()
      ? data.pending_event_id.trim()
      : typeof data?.reply_to === "string" && data.reply_to.trim()
        ? data.reply_to.trim()
        : "";
  const streamId = typeof data?.stream_id === "string" ? data.stream_id.trim() : "";
  const placeholderLike = isPlaceholderLikeStreamingEvent((data || {}) as ChatMessageData & {
    pending_placeholder?: unknown;
    stream_id?: unknown;
  });
  if (actorId && actorId !== "user" && replyAnchor) {
    if (streamId && !placeholderLike) {
      return `stream:${streamId}`;
    }
    if (placeholderLike || !hasRenderableChatMessageContent(event)) {
      return `reply:${actorId}:${replyAnchor}`;
    }
  }

  if (streamId) return `stream:${streamId}`;

  const eventId = String(event.id || "").trim();
  if (eventId) return `event:${eventId}`;

  return `fallback:${String(event.by || "").trim()}:${String(event.ts || "").trim()}`;
}

function getLogicalMessagePriority(event: LedgerEvent): number {
  const isStreaming = !!event._streaming;
  const data = event.data && typeof event.data === "object"
    ? event.data as { _optimistic?: unknown }
    : undefined;
  const isOptimistic = Boolean(data?._optimistic);
  if (!isStreaming && !isOptimistic) return 3;
  if (isOptimistic) return 2;
  return 1;
}

function shouldReplaceLogicalMessage(existing: LedgerEvent, incoming: LedgerEvent): boolean {
  const existingRenderable = hasRenderableChatMessageContent(existing);
  const incomingRenderable = hasRenderableChatMessageContent(incoming);
  if (incomingRenderable !== existingRenderable) {
    return incomingRenderable;
  }

  if (!existingRenderable && !incomingRenderable && !!existing._streaming !== !!incoming._streaming) {
    return !!incoming._streaming;
  }

  return getLogicalMessagePriority(incoming) >= getLogicalMessagePriority(existing);
}

export function mergeLogicalMessagesWithStableOrder(
  candidates: LedgerEvent[],
  orderState: { map: Map<string, number>; next: number },
): LedgerEvent[] {
  const mergedByReplacementKey = new Map<string, { orderKey: string; event: LedgerEvent; index: number }>();
  candidates.forEach((event, index) => {
    const orderKey = getLogicalMessageOrderKey(event);
    if (!orderState.map.has(orderKey)) {
      orderState.map.set(orderKey, orderState.next);
      orderState.next += 1;
    }
    const replacementKey = getLogicalMessageReplacementKey(event);
    const existing = mergedByReplacementKey.get(replacementKey);
    if (!existing || shouldReplaceLogicalMessage(existing.event, event)) {
      mergedByReplacementKey.set(replacementKey, { orderKey, event, index });
    }
  });

  return Array.from(mergedByReplacementKey.values())
    .sort((a, b) => {
      const ao = orderState.map.get(a.orderKey) ?? Number.MAX_SAFE_INTEGER;
      const bo = orderState.map.get(b.orderKey) ?? Number.MAX_SAFE_INTEGER;
      if (ao !== bo) return ao - bo;
      const ats = String(a.event.ts || "").trim();
      const bts = String(b.event.ts || "").trim();
      if (ats && bts && ats !== bts) return ats.localeCompare(bts);
      return a.index - b.index;
    })
    .map((item) => item.event);
}

export function mergeVisibleChatMessages(
  canonicalEvents: LedgerEvent[],
  streamingEvents: LedgerEvent[],
  pendingEvents: LedgerEvent[],
  orderState: { map: Map<string, number>; next: number },
): LedgerEvent[] {
  const canonicalStreamIds = getCanonicalStreamingSupersededStreamIds(canonicalEvents);
  const canonicalReplySlots = new Set(
    canonicalEvents
      .filter((ev: LedgerEvent) => hasRenderableChatMessageContent(ev))
      .map((ev: LedgerEvent) => getReplySlotKey(ev))
      .filter((key: string) => key.length > 0),
  );
  const renderableStreamingReplySlots = new Set(
    streamingEvents
      .filter((ev: LedgerEvent) => hasRenderableChatMessageContent(ev))
      .map((ev: LedgerEvent) => getReplySlotKey(ev))
      .filter((key: string) => key.length > 0),
  );
  const liveStreaming = streamingEvents.filter((ev: LedgerEvent) => {
    const data = ev.data && typeof ev.data === "object"
      ? (ev.data as { stream_id?: unknown; pending_placeholder?: unknown; activities?: unknown })
      : null;
    const streamId = data && typeof data.stream_id === "string" ? data.stream_id.trim() : "";
    const slotKey = getReplySlotKey(ev);
    const renderable = hasRenderableChatMessageContent(ev);
    if (streamId && canonicalStreamIds.has(streamId)) return false;
    const hasRichActivityTimeline = hasRichActivities(data?.activities);
    // Backup: drop empty streaming events whose reply slot is covered by a canonical event,
    // but keep non-queued activity bubbles until the activity itself completes.
    if (!renderable) {
      if (slotKey && canonicalReplySlots.has(slotKey)) return hasRichActivityTimeline;
      if (slotKey && renderableStreamingReplySlots.has(slotKey)) {
        const placeholderLike = isPlaceholderLikeStreamingEvent(((data || {}) as ChatMessageData & {
          pending_placeholder?: unknown;
          stream_id?: unknown;
        }));
        if (!hasRichActivityTimeline && (placeholderLike || hasOnlyQueuedActivities(data?.activities))) return false;
      }
    }
    return true;
  });

  return mergeLogicalMessagesWithStableOrder(
    [...canonicalEvents, ...pendingEvents, ...liveStreaming],
    orderState,
  );
}

interface UseChatTabOptions {
  selectedGroupId: string;
  selectedGroupRunning: boolean;
  actors: Actor[];
  recipientActors: Actor[];
  groupLabelById: Record<string, string>;
  /** Callback for when message is sent */
  onMessageSent?: () => void;
  /** Refs for composer interactions */
  composerRef?: React.RefObject<HTMLTextAreaElement | null>;
  fileInputRef?: React.RefObject<HTMLInputElement | null>;
  /** Chat at bottom ref for scroll state */
  chatAtBottomRef?: React.MutableRefObject<boolean>;
  /** Scroll container ref for programmatic scrolling (e.g. after send) */
  scrollRef?: React.MutableRefObject<HTMLDivElement | null>;
}

type ChatEmptyState = "ready" | "hydrating" | "business_empty";

export type FailedSendComposerSnapshot = {
  originGroupId: string;
  composerText: string;
  composerFiles: File[];
  selectedSkillCommand: string;
  toText: string;
  replyTarget: ReplyTarget;
  quotedPresentationRef: PresentationMessageRef | null;
  priority: "normal" | "attention";
  replyRequired: boolean;
  collaborationRequired: boolean;
  computerControlEnabled: boolean;
  computerControlWorkflowId: string;
  computerControlActorId: string;
  computerControlPermissions: { publish: boolean; trust: boolean; unattendedTriggers: boolean };
};

type FailedSendComposerRestoreActions = Pick<
  ReturnType<typeof useComposerStore.getState>,
  | "setComposerText"
  | "setComposerFiles"
  | "setSelectedSkillCommand"
  | "setToText"
  | "setReplyTarget"
  | "setQuotedPresentationRef"
  | "setPriority"
  | "setReplyRequired"
  | "setCollaborationRequired"
  | "setComputerControlEnabled"
  | "setComputerControlWorkflowId"
  | "setComputerControlActorId"
  | "setComputerControlPermission"
  | "upsertDraft"
>;

export function restoreFailedSendComposerState(
  snapshot: FailedSendComposerSnapshot,
  actions?: FailedSendComposerRestoreActions,
): void {
  const originGroupId = String(snapshot.originGroupId || "").trim();
  if (!originGroupId) return;

  const composerState = useComposerStore.getState();
  const restoreActions = actions || composerState;
  const currentSelectedGroupId = String(useGroupStore.getState().selectedGroupId || "").trim();
  const currentActiveGroupId = String(composerState.activeGroupId || "").trim();
  const stillOnOriginGroup = currentSelectedGroupId === originGroupId && currentActiveGroupId === originGroupId;
  const computerControlPermissions = snapshot.computerControlPermissions || {
    publish: true,
    trust: true,
    unattendedTriggers: true,
  };

  if (stillOnOriginGroup) {
    restoreActions.setComposerText(snapshot.composerText);
    restoreActions.setComposerFiles(snapshot.composerFiles);
    restoreActions.setSelectedSkillCommand(snapshot.selectedSkillCommand);
    restoreActions.setReplyTarget(snapshot.replyTarget);
    restoreActions.setQuotedPresentationRef(snapshot.quotedPresentationRef);
    restoreActions.setPriority(snapshot.priority);
    restoreActions.setReplyRequired(snapshot.replyRequired);
    restoreActions.setCollaborationRequired(snapshot.collaborationRequired);
    restoreActions.setComputerControlEnabled(snapshot.computerControlEnabled);
    restoreActions.setComputerControlWorkflowId(snapshot.computerControlWorkflowId);
    restoreActions.setComputerControlActorId(snapshot.computerControlActorId);
    for (const [permission, value] of Object.entries(computerControlPermissions)) {
      restoreActions.setComputerControlPermission(permission as "publish" | "trust" | "unattendedTriggers", value);
    }
    restoreActions.setToText(snapshot.toText);
    return;
  }

  restoreActions.upsertDraft(originGroupId, () => ({
    composerText: snapshot.composerText,
    composerFiles: snapshot.composerFiles,
    selectedSkillCommand: snapshot.selectedSkillCommand,
    toText: snapshot.toText,
    replyTarget: snapshot.replyTarget,
    quotedPresentationRef: snapshot.quotedPresentationRef,
    priority: snapshot.priority,
    replyRequired: snapshot.replyRequired,
    collaborationRequired: snapshot.collaborationRequired,
    computerControlEnabled: snapshot.computerControlEnabled,
    computerControlWorkflowId: snapshot.computerControlWorkflowId,
    computerControlActorId: snapshot.computerControlActorId,
    computerControlPermissions,
  }));
}

export type ComposerSendRoutingSnapshot = {
  selectedGroupId: string;
  destGroupId: string;
  composerGroupSettled: boolean;
  isCrossGroup: boolean;
};

export function buildComposerSendRoutingSnapshot({
  selectedGroupId,
  activeGroupId,
  destGroupId,
}: {
  selectedGroupId: string;
  activeGroupId: string;
  destGroupId: string;
}): ComposerSendRoutingSnapshot {
  const selected = String(selectedGroupId || "").trim();
  const active = String(activeGroupId || "").trim();
  const dest = getEffectiveComposerDestGroupId(destGroupId, active, selected);
  const composerGroupSettled = isComposerGroupSettled(active, selected);
  return {
    selectedGroupId: selected,
    destGroupId: dest,
    composerGroupSettled,
    isCrossGroup: !!selected && !!dest && dest !== selected,
  };
}

export function parseComposerRecipientTokens(toText: string, validRecipientSet: Set<string>): string[] {
  const raw = String(toText || "")
    .split(",")
    .map((t) => t.trim())
    .filter((t) => t.length > 0);
  const out: string[] = [];
  const seen = new Set<string>();
  for (const token of raw) {
    if (token === "@") continue;
    if (!validRecipientSet.has(token)) continue;
    if (seen.has(token)) continue;
    seen.add(token);
    out.push(token);
  }
  return out;
}

export function useChatTab({
  selectedGroupId,
  selectedGroupRunning,
  actors,
  recipientActors,
  groupLabelById,
  onMessageSent,
  composerRef,
  fileInputRef,
  chatAtBottomRef,
  scrollRef,
}: UseChatTabOptions) {
  const { t } = useTranslation(["chat", "common"]);
  const [forceStickToBottomToken, setForceStickToBottomToken] = useState(0);
  // ============ Stores ============
  const { events, chatWindow, hasMoreHistory, hasLoadedTail, isLoadingHistory, isChatWindowLoading } = useGroupStore(
    useShallow(
      useCallback((state) => {
        const bucket = selectChatBucketState(state, selectedGroupId);
        return {
          events: bucket.events,
          chatWindow: bucket.chatWindow,
          hasMoreHistory: bucket.hasMoreHistory,
          hasLoadedTail: bucket.hasLoadedTail,
          isLoadingHistory: bucket.isLoadingHistory,
          isChatWindowLoading: bucket.isChatWindowLoading,
        };
      }, [selectedGroupId])
    )
  );
  const appendEvent = useGroupStore((state) => state.appendEvent);
  const upsertStreamingEvent = useGroupStore((state) => state.upsertStreamingEvent);
  const removeStreamingEventsByPrefix = useGroupStore((state) => state.removeStreamingEventsByPrefix);
  const promoteStreamingEventsByPrefix = useGroupStore((state) => state.promoteStreamingEventsByPrefix);
  const groupDoc = useGroupStore((state) => state.groupDoc);
  const groupContext = useGroupStore((state) => state.groupContext);
  const groupSettings = useGroupStore((state) => state.groupSettings);
  const closeChatWindow = useGroupStore((state) => state.closeChatWindow);
  const openChatWindow = useGroupStore((state) => state.openChatWindow);
  const loadMoreHistory = useGroupStore((state) => state.loadMoreHistory);

  const busy = useUIStore((s) => s.busy);
  const chatSessions = useUIStore((s) => s.chatSessions);
  const setChatFilter = useUIStore((s) => s.setChatFilter);
  const setShowScrollButton = useUIStore((s) => s.setShowScrollButton);
  const setChatUnreadCount = useUIStore((s) => s.setChatUnreadCount);
  const setChatScrollSnapshot = useUIStore((s) => s.setChatScrollSnapshot);
  const setChatMobileSurface = useUIStore((s) => s.setChatMobileSurface);
  const showError = useUIStore((s) => s.showError);

  const isCurrentScrollAtBottom = useCallback(() => {
    const el = scrollRef?.current;
    if (!el) return chatAtBottomRef ? chatAtBottomRef.current : true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  }, [chatAtBottomRef, scrollRef]);
  const showNotice = useUIStore((s) => s.showNotice);

  const chatSession = useMemo(
    () => getChatSession(selectedGroupId, chatSessions),
    [selectedGroupId, chatSessions]
  );
  const { chatFilter, showScrollButton, chatUnreadCount, scrollSnapshot } = chatSession;

  const {
    activeGroupId,
    composerText,
    composerFiles,
    selectedSkillCommand,
    toText,
    replyTarget,
    quotedPresentationRef,
    priority,
    replyRequired,
    collaborationRequired,
    computerControlEnabled,
    computerControlWorkflowId,
    computerControlActorId,
    computerControlPermissions,
    destGroupId,
    setComposerText,
    setComposerFiles,
    setSelectedSkillCommand,
    setToText,
    setReplyToText,
    setReplyTarget,
    setQuotedPresentationRef,
    setPriority,
    setReplyRequired,
    setCollaborationRequired,
    setComputerControlEnabled,
    setComputerControlWorkflowId,
    setComputerControlActorId,
    setComputerControlPermission,
    setDestGroupId,
    upsertDraft,
    clearDraft,
    clearComposer,
  } = useComposerStore();
  const composerGroupSettled = isComposerGroupSettled(activeGroupId, selectedGroupId);
  const { setRecipientsModal, setRelayModal, openModal } = useModalStore();
  const { setNewActorRole } = useFormStore();

  // Outbox (optimistic pending messages) — stable selector, no new array allocation.
  const outboxEntries = useChatOutboxStore(
    useCallback((s) => selectOutboxEntries(s, selectedGroupId), [selectedGroupId])
  );
  const enqueueOutbox = useChatOutboxStore((s) => s.enqueue);
  const removeOutbox = useChatOutboxStore((s) => s.remove);
  const sendInFlightRef = useRef(false);
  const remoteSendOwnerRef = useRef<GroupBridgeRemoteOperationToken | null>(null);
  const remoteLifecycleRef = useRef<GroupBridgeRemoteLifecycle | null>(null);
  if (!remoteLifecycleRef.current) remoteLifecycleRef.current = new GroupBridgeRemoteLifecycle();
  const remoteLifecycle = remoteLifecycleRef.current;
  const [slashSkillScope, setSlashSkillScope] = useState<SlashSkillScope>("team");
  const [remoteViewGroupId, setRemoteViewGroupId] = useState(() => (
    normalizeGroupBridgeRemoteGroupId(selectedGroupId)
  ));
  const [remoteTargets, setRemoteTargets] = useState<GroupBridgeRemoteTarget[]>([]);
  const [remoteTargetsBusy, setRemoteTargetsBusy] = useState(false);
  const [remoteTargetId, setRemoteTargetIdState] = useState("");
  const [remoteReceipt, setRemoteReceipt] = useState<GroupBridgeRemoteReceipt | null>(null);
  const [remoteStatusUnavailable, setRemoteStatusUnavailable] = useState(false);
  const [remoteRequest, setRemoteRequest] = useState<{
    groupId: string;
    registrationId: string;
    messageGeneration: number;
    idempotencyKey: string;
    receipt: GroupBridgeRemoteReceipt;
  } | null>(null);

  useLayoutEffect(() => {
    const groupId = normalizeGroupBridgeRemoteGroupId(selectedGroupId);
    remoteLifecycle.selectGroup(groupId);
    setRemoteViewGroupId(groupId);
    setRemoteTargets([]);
    setRemoteTargetIdState("");
    setRemoteReceipt(null);
    setRemoteRequest(null);
    setRemoteStatusUnavailable(false);
    setRemoteTargetsBusy(Boolean(groupId));
    if (!groupId) return undefined;

    const token = remoteLifecycle.begin("load", groupId);
    if (!token) return undefined;
    void (async () => {
      try {
        const [registrationsResponse, trustsResponse] = await Promise.all([
          groupBridgeApi.registrations(groupId, { signal: token.signal }),
          groupBridgeApi.trusts(groupId, { signal: token.signal }),
        ]);
        remoteLifecycle.commit(token, () => {
          if (!registrationsResponse.ok || !trustsResponse.ok) {
            remoteLifecycle.selectRoute(groupId, "");
            setRemoteTargets([]);
            setRemoteTargetIdState("");
            setRemoteReceipt(null);
            setRemoteRequest(null);
            setRemoteStatusUnavailable(false);
            return;
          }
          const targets = buildActiveGroupBridgeRemoteTargets(
            registrationsResponse.result.registrations || [],
            trustsResponse.result.trusts || [],
            groupId,
          );
          const currentRoute = remoteLifecycle.currentRoute(groupId);
          const nextRoute = targets.some((target) => target.registration_id === currentRoute)
            ? currentRoute
            : "";
          if (nextRoute !== currentRoute) {
            remoteLifecycle.selectRoute(groupId, nextRoute);
            setRemoteReceipt(null);
            setRemoteRequest(null);
            setRemoteStatusUnavailable(false);
          }
          setRemoteTargets(targets);
          setRemoteTargetIdState(nextRoute);
        });
      } catch {
        remoteLifecycle.commit(token, () => {
          remoteLifecycle.selectRoute(groupId, "");
          setRemoteTargets([]);
          setRemoteTargetIdState("");
          setRemoteReceipt(null);
          setRemoteRequest(null);
          setRemoteStatusUnavailable(false);
        });
      } finally {
        if (remoteLifecycle.finish(token)) setRemoteTargetsBusy(false);
      }
    })();

    return () => {
      remoteLifecycle.selectGroup("");
    };
  }, [remoteLifecycle, selectedGroupId]);

  const remoteViewCurrent = isGroupBridgeRemoteViewCurrent(selectedGroupId, remoteViewGroupId);
  const visibleRemoteTargets = useMemo(
    () => remoteViewCurrent ? remoteTargets : [],
    [remoteTargets, remoteViewCurrent],
  );
  const visibleRemoteTargetId = remoteViewCurrent ? remoteTargetId : "";
  const visibleRemoteReceipt = remoteViewCurrent ? remoteReceipt : null;
  const visibleRemoteStatusUnavailable = remoteViewCurrent ? remoteStatusUnavailable : false;
  const visibleRemoteTargetsBusy = remoteViewCurrent ? remoteTargetsBusy : false;

  const selectedRemoteTarget = useMemo(
    () => visibleRemoteTargets.find((target) => target.registration_id === visibleRemoteTargetId) || null,
    [visibleRemoteTargetId, visibleRemoteTargets],
  );

  const setRemoteTargetId = useCallback((registrationId: string) => {
    if (!remoteViewCurrent) return;
    const next = String(registrationId || "").trim();
    if (!remoteLifecycle.selectRoute(selectedGroupId, next)) return;
    setRemoteTargetIdState(next);
    setDestGroupId(selectedGroupId);
    setRemoteReceipt(null);
    setRemoteRequest(null);
    setRemoteStatusUnavailable(false);
  }, [remoteLifecycle, remoteViewCurrent, selectedGroupId, setDestGroupId]);

  useEffect(() => {
    const request = remoteRequest;
    if (!request || !remoteViewCurrent || request.groupId !== remoteViewGroupId) return undefined;
    const token = remoteLifecycle.begin(
        "poll",
        request.groupId,
        request.registrationId,
        request.messageGeneration,
    );
    if (!token) return undefined;
    void pollGroupBridgeRemoteReceipt({
      initialReceipt: request.receipt,
      signal: token.signal,
      getStatus: async (signal) => {
        const response = await groupBridgeApi.remoteStatus(
          request.groupId,
          request.registrationId,
          request.idempotencyKey,
          { signal },
        );
        return response.ok
          ? { ok: true as const, receipt: response.result.receipt }
          : { ok: false as const };
      },
      onReceipt: (receipt) => {
        remoteLifecycle.commit(token, () => {
          setRemoteReceipt(receipt);
          setRemoteStatusUnavailable(false);
        });
      },
    }).then((result) => {
      if (result.kind === "unavailable") {
        remoteLifecycle.commit(token, () => setRemoteStatusUnavailable(true));
      }
    }).finally(() => {
      remoteLifecycle.finish(token);
    });
    return () => {
      remoteLifecycle.abort(token);
    };
  }, [remoteLifecycle, remoteRequest, remoteViewCurrent, remoteViewGroupId]);

  // ============ Computed Values ============

  const resolveAssistantTargets = useCallback((tokens: string[]): Actor[] => {
    const normalized = tokens.map((token) => String(token || "").trim()).filter((token) => token);
    const resolved = new Map<string, Actor>();
    const policy = groupSettings?.default_send_to || "foreman";
    const effectiveTokens = normalized.length > 0 ? normalized : (policy === "foreman" ? ["@foreman"] : ["@all"]);
    const allActors = actors.filter((actor) => {
      const actorId = String(actor.id || "").trim();
      const internalKind = String(actor.internal_kind || "").trim();
      return actorId && actorId !== "user" && !internalKind;
    });
    const peers = allActors.filter((actor) => String(actor.role || "").trim() !== "foreman");
    const foremen = allActors.filter((actor) => String(actor.role || "").trim() === "foreman");

    const addActors = (items: Actor[]) => {
      for (const actor of items) {
        const actorId = String(actor.id || "").trim();
        if (!actorId || resolved.has(actorId)) continue;
        resolved.set(actorId, actor);
      }
    };

    for (const token of effectiveTokens) {
      if (token === "@all") {
        addActors(allActors);
        continue;
      }
      if (token === "@peers") {
        addActors(peers);
        continue;
      }
      if (token === "@foreman") {
        addActors(foremen);
        continue;
      }
      const actor = allActors.find((item) => String(item.id || "").trim() === token);
      if (actor) addActors([actor]);
    }

    return Array.from(resolved.values()).filter((actor) => String(actor.runtime || "").trim() === "codex");
  }, [actors, groupSettings?.default_send_to]);

  // Valid recipient tokens
  const validRecipientSet = useMemo(() => {
    const out = new Set<string>(["@all"]);
    for (const a of recipientActors) {
      const id = String(a.id || "").trim();
      if (id) out.add(id);
    }
    return out;
  }, [recipientActors]);

  // Parse toText into validated tokens
  const toTokens = useMemo(() => {
    return parseComposerRecipientTokens(toText, validRecipientSet);
  }, [toText, validRecipientSet]);

  // Mention suggestions
  const mentionSuggestions = useMemo<ComposerMentionSuggestion[]>(
    () => buildComposerMentionSuggestions(recipientActors, groupLabelById),
    [groupLabelById, recipientActors],
  );

  // Send group ID (respects cross-group destination)
  const sendGroupId = useMemo(() => {
    return getEffectiveComposerDestGroupId(destGroupId, activeGroupId, selectedGroupId);
  }, [destGroupId, activeGroupId, selectedGroupId]);

  useEffect(() => {
    const selected = String(selectedGroupId || "").trim();
    if (!selected || String(sendGroupId || "").trim() === selected) return;
    setDestGroupId(selected);
  }, [selectedGroupId, sendGroupId, setDestGroupId]);

  // Project root
  const projectRoot = useMemo(() => {
    if (!groupDoc) return "";
    const key = String(groupDoc.active_scope_key || "");
    if (!key) return "";
    const scopes = Array.isArray(groupDoc.scopes) ? groupDoc.scopes : [];
    const hit = scopes.find((s) => String(s.scope_key || "") === key);
    return String(hit?.url || "");
  }, [groupDoc]);

  // Has foreman
  const hasForeman = useMemo(() => actors.some((a) => a.role === "foreman"), [actors]);

  // Selected group running state
  // Setup checklist conditions
  const needsScope = !!selectedGroupId && !projectRoot;
  const needsActors = !!selectedGroupId && actors.length === 0;
  const needsStart = !!selectedGroupId && actors.length > 0 && !selectedGroupRunning;
  const showSetupCard = needsScope || needsActors || needsStart;
  const selectedGroupLifecycleState = useMemo(() => {
    if (!groupDoc || String(groupDoc.group_id || "") !== String(selectedGroupId || "")) return "";
    return String(groupDoc.runtime_status?.lifecycle_state || groupDoc.state || "").trim().toLowerCase();
  }, [groupDoc, selectedGroupId]);
  const groupSendBlockedReason = useMemo(
    () => getGroupSendBlockedReason({
      lifecycleState: selectedGroupLifecycleState,
      runtimeRunning: selectedGroupRunning,
      actorCount: actors.length,
    }),
    [actors.length, selectedGroupLifecycleState, selectedGroupRunning]
  );

  const dispatchSlashSkillMessage = useSlashSkillDispatch({
    selectedGroupId,
    toTokens,
    priority,
    replyRequired,
    collaborationRequired,
    groupSendBlockedReason,
    clearDraft,
    setToText,
    setChatUnreadCount,
    setChatFilter,
    setChatMobileSurface,
    enqueueOutbox,
    removeOutbox,
    appendEvent,
    showError,
    onMessageSent,
    t,
  });

  const { slashCommands, allSlashCommands, tryExecuteSlashCommand } = useSlashCommands({
    selectedGroupId,
    clearComposer,
    restoreComposerText: setComposerText,
    showError,
    showNotice,
    dispatchMessage: dispatchSlashSkillMessage,
    onExecuted: () => {
      if (fileInputRef?.current) fileInputRef.current.value = "";
    },
    skillScope: slashSkillScope,
    t,
  });

  // In chat window mode
  const inChatWindow = useMemo(() => {
    return !!chatWindow && String(chatWindow.groupId || "") === String(selectedGroupId || "");
  }, [chatWindow, selectedGroupId]);

  const chatViewKey = useMemo(() => {
    if (inChatWindow && chatWindow) {
      return `${selectedGroupId}:window:${chatWindow.centerEventId}`;
    }
    return `${selectedGroupId}:live`;
  }, [inChatWindow, chatWindow, selectedGroupId]);
  const logicalMessageOrderStateRef = useRef<{ viewKey: string; map: Map<string, number>; next: number }>({
    viewKey: "",
    map: new Map(),
    next: 0,
  });
  if (logicalMessageOrderStateRef.current.viewKey !== chatViewKey) {
    logicalMessageOrderStateRef.current = {
      viewKey: chatViewKey,
      map: new Map(),
      next: 0,
    };
  }

  // Filtered live chat messages (canonical + optimistic pending merged)
  const liveChatMessages = useMemo(() => {
    const all = events.filter(isFormalChatMessageEvent);
    const renderableCanonicalClientIds = new Set(
      all
        .filter((ev: LedgerEvent) => hasRenderableChatMessageContent(ev))
        .map((ev: LedgerEvent) => {
          const data = ev.data && typeof ev.data === "object" ? (ev.data as { client_id?: unknown }) : null;
          return data && typeof data.client_id === "string" ? data.client_id.trim() : "";
        })
        .filter((clientId: string) => clientId.length > 0)
    );
    const pendingEvents = outboxEntries
      .filter((entry) => !renderableCanonicalClientIds.has(entry.localId))
      .map((entry) => entry.event);
    const ordered = sortChatMessages(
      mergeVisibleChatMessages(all, [], pendingEvents, logicalMessageOrderStateRef.current),
      new Map(),
    );

    if (chatFilter === "attention") {
      return ordered.filter((ev: LedgerEvent) => {
        const d = ev.data as ChatMessageData | undefined;
        return String(d?.priority || "normal") === "attention";
      });
    }
    if (chatFilter === "task") {
      return ordered.filter((ev: LedgerEvent) => {
        const d = ev.data as ChatMessageData | undefined;
        return !!d?.reply_required;
      });
    }
    if (chatFilter === "user") {
      return ordered.filter((ev: LedgerEvent) => {
        const d = ev.data as ChatMessageData | undefined;
        const dst = typeof d?.dst_group_id === "string" ? String(d.dst_group_id || "").trim() : "";
        if (dst) return false;
        const to = Array.isArray(d?.to) ? d?.to : [];
        const by = String(ev.by || "").trim();
        return by === "user" || to.includes("user") || to.includes("@user");
      });
    }
    return ordered;
  }, [events, chatFilter, outboxEntries]);

  // Chat messages (window or live)
  const chatMessages = useMemo(() => {
    if (inChatWindow && chatWindow) return (chatWindow.events || []).filter(isFormalChatMessageEvent);
    return liveChatMessages;
  }, [chatWindow, inChatWindow, liveChatMessages]);

  const composerHistoryEntries = useMemo(
    () => buildComposerHistoryEntries(events),
    [events],
  );
  const suggestedUserMessage = useMemo(
    () => latestSuggestedUserMessage(events),
    [events],
  );

  const hasAnyChatMessages = useMemo(
    () => events.some(isFormalChatMessageEvent) || outboxEntries.length > 0,
    [events, outboxEntries]
  );
  const chatInitialScrollAnchorId = useMemo(() => {
    if (inChatWindow) return undefined;
    const snapshot = scrollSnapshot;
    if (!shouldRestoreDetachedScrollSnapshot(snapshot)) return undefined;
    return snapshot!.anchorId;
  }, [inChatWindow, scrollSnapshot]);

  const chatInitialScrollAnchorOffsetPx = useMemo(() => {
    if (inChatWindow) return undefined;
    const snapshot = scrollSnapshot;
    if (!shouldRestoreDetachedScrollSnapshot(snapshot)) return undefined;
    return Number(snapshot!.offsetPx || 0);
  }, [inChatWindow, scrollSnapshot]);

  // Chat window props (for jump-to mode)
  const chatWindowProps = useMemo(() => {
    if (!inChatWindow || !chatWindow) return null;
    return {
      centerEventId: chatWindow.centerEventId,
      hasMoreBefore: chatWindow.hasMoreBefore,
      hasMoreAfter: chatWindow.hasMoreAfter,
    };
  }, [inChatWindow, chatWindow]);

  // Initial scroll target (for window mode)
  const chatInitialScrollTargetId = useMemo(() => {
    if (inChatWindow && chatWindow) return chatWindow.centerEventId;
    return undefined;
  }, [inChatWindow, chatWindow]);

  // Highlight event ID (for window mode)
  const chatHighlightEventId = useMemo(() => {
    if (inChatWindow && chatWindow) return chatWindow.centerEventId;
    return undefined;
  }, [inChatWindow, chatWindow]);

  const effectiveIsLoadingHistory = inChatWindow ? isChatWindowLoading : isLoadingHistory;
  const effectiveHasMoreHistory = !selectedGroupId ? false : inChatWindow ? false : (!hasLoadedTail || hasMoreHistory);

  const hasHydratedGroupDoc = useMemo(() => {
    if (!groupDoc || String(groupDoc.group_id || "") !== String(selectedGroupId || "")) return false;
    // Shell docs only carry title/topic/state; fetched docs also carry scope fields.
    return (
      Object.prototype.hasOwnProperty.call(groupDoc, "scopes") ||
      Object.prototype.hasOwnProperty.call(groupDoc, "active_scope_key")
    );
  }, [groupDoc, selectedGroupId]);

  const hasSettledActorSnapshot = useMemo(() => {
    if (!selectedGroupId) return false;
    if (actors.length > 0) return true;
    // context/settings are loaded only after the first actor snapshot settles.
    return groupContext !== null || groupSettings !== null;
  }, [selectedGroupId, actors.length, groupContext, groupSettings]);

  const chatEmptyState = useMemo<ChatEmptyState>(() => {
    if (chatMessages.length > 0) return "ready";
    if (!selectedGroupId) return "business_empty";
    if (effectiveIsLoadingHistory || effectiveHasMoreHistory) return "hydrating";
    if (!hasHydratedGroupDoc) return "hydrating";
    if (needsActors && !hasSettledActorSnapshot) return "hydrating";
    return "business_empty";
  }, [
    chatMessages.length,
    selectedGroupId,
    effectiveIsLoadingHistory,
    effectiveHasMoreHistory,
    hasHydratedGroupDoc,
    needsActors,
    hasSettledActorSnapshot,
  ]);

  const updateChatFilter = useCallback(
    (nextFilter: ReturnType<typeof getChatSession>["chatFilter"]) => {
      if (!selectedGroupId) return;
      setChatFilter(selectedGroupId, nextFilter);
    },
    [selectedGroupId, setChatFilter]
  );

  // Agent state snapshot
  const agentStates = useMemo(
    () => groupContext?.agent_states || [],
    [groupContext]
  );
  const tasks = useMemo(
    () => (Array.isArray(groupContext?.coordination?.tasks) ? groupContext.coordination.tasks : []),
    [groupContext]
  );
  const taskById = useMemo(() => {
    const map = new Map<string, Task>();
    for (const task of tasks) {
      const taskId = String(task?.id || "").trim();
      if (taskId) map.set(taskId, task);
    }
    return map;
  }, [tasks]);

  // ============ Actions ============

  const toggleRecipient = useCallback(
    (token: string) => {
      const t = token.trim();
      if (!t) return;
      const cur = toTokens;
      const idx = cur.findIndex((x) => x === t);
      if (idx >= 0) {
        const next = cur.slice(0, idx).concat(cur.slice(idx + 1));
        setToText(next.join(", "));
      } else {
        setToText(cur.concat([t]).join(", "));
      }
    },
    [toTokens, setToText]
  );

  const clearRecipients = useCallback(() => setToText(""), [setToText]);

  const appendRecipientToken = useCallback(
    (token: string) => {
      setToText(toText ? toText + ", " + token : token);
    },
    [toText, setToText]
  );

  const removeComposerFile = useCallback(
    (idx: number) => {
      setComposerFiles(composerFiles.filter((_, i) => i !== idx));
    },
    [composerFiles, setComposerFiles]
  );

  const sendMessage = useCallback(async () => {
    if (sendInFlightRef.current) return; // keyboard shortcut can bypass UI state; keep send single-flight locally
    if (!selectedGroupId) return;
    const latestSelectedGroupId = String(useGroupStore.getState().selectedGroupId || "").trim();
    if (latestSelectedGroupId !== selectedGroupId) return;
    const composerStateSnapshot = useComposerStore.getState();
    const routingSnapshot = buildComposerSendRoutingSnapshot({
      selectedGroupId: latestSelectedGroupId,
      activeGroupId: composerStateSnapshot.activeGroupId,
      destGroupId: composerStateSnapshot.destGroupId,
    });
    if (!routingSnapshot.composerGroupSettled) return;
    const originGroupId = routingSnapshot.selectedGroupId;

    const txt = String(composerStateSnapshot.composerText || "").trim();
    const selectedSkillCommandSnapshot = String(composerStateSnapshot.selectedSkillCommand || "").trim();
    const composerFilesSnapshot = composerStateSnapshot.composerFiles.slice();
    const selectedSkillCandidates = [...allSlashCommands, ...slashCommands];
    const selectedSkillItem = selectedSkillCommandSnapshot
      ? selectedSkillCandidates.find((item) => item.capabilityId === selectedSkillCommandSnapshot)
        || selectedSkillCandidates.find((item) => item.command === selectedSkillCommandSnapshot)
        || null
      : null;
    const explicitSlashText = selectedSkillCommandSnapshot.startsWith("/")
      ? `${selectedSkillCommandSnapshot} ${txt}`.trim()
      : txt;
    const typedSkillCommand = selectedSkillItem ? null : parseSlashCommandInput(explicitSlashText, slashCommands);
    const typedSkillItem = typedSkillCommand?.item.sourceType === "capsule_skill" ? typedSkillCommand.item : null;
    const skillDispatchItem = selectedSkillItem || typedSkillItem;
    const skillDispatchArgsText = selectedSkillItem ? txt : (typedSkillCommand?.argsText || "");
    const skillDispatchText = skillDispatchItem
      ? buildCapsuleSkillDispatchText(skillDispatchItem, skillDispatchArgsText)
        || (composerFilesSnapshot.length > 0 ? buildCapsuleSkillAttachmentDispatchText(skillDispatchItem) : "")
      : "";
    // Keep the user's original text in the ledger and let the daemon attach
    // the stable runtime Skill directive using the structured capability id.
    const skillCapabilityIdSnapshot = String(skillDispatchItem?.capabilityId || "").trim();
    const messageTextSnapshot = skillCapabilityIdSnapshot ? txt : (skillDispatchText || txt);
    const sendText = !skillDispatchText && selectedSkillCommandSnapshot.startsWith("/") && txt
      ? explicitSlashText
      : messageTextSnapshot;
    if (!txt && composerFilesSnapshot.length === 0) return;

    const dstGroup = routingSnapshot.destGroupId;
    const isCrossGroup = routingSnapshot.isCrossGroup;
    const remoteDecision = decideGroupBridgeRemoteSend({
      groupId: selectedGroupId,
      remoteTargetId: visibleRemoteTargetId,
      remoteTarget: selectedRemoteTarget,
      attachmentCount: composerFilesSnapshot.length,
      hasReply: Boolean(composerStateSnapshot.replyTarget),
      hasQuote: Boolean(composerStateSnapshot.quotedPresentationRef),
      collaborationRequired: composerStateSnapshot.collaborationRequired,
      computerControlEnabled: composerStateSnapshot.computerControlEnabled,
      selectedSkillCommand: selectedSkillCommandSnapshot,
      text: txt,
    });
    if (remoteDecision.kind === "reject") {
      if (remoteDecision.reason === "target_unavailable") {
        showError("The selected Group Bridge target is no longer active.");
      } else {
        showError("Group Bridge remote messages do not support replies, quotes, attachments, skills, or computer control.");
      }
      return;
    }
    if (remoteDecision.kind === "remote") {
      const composerIntent = captureGroupBridgeRemoteComposerIntent(selectedGroupId, composerStateSnapshot);
      const token = remoteLifecycle.begin("send", selectedGroupId, remoteDecision.target.registration_id);
      if (!token) return;
      if (!remoteLifecycle.commit(token, () => {
        setRemoteReceipt(null);
        setRemoteRequest(null);
        setRemoteStatusUnavailable(false);
      })) return;

      sendInFlightRef.current = true;
      remoteSendOwnerRef.current = token;
      try {
        const idempotencyKey = createGroupBridgeIdempotencyKey();
        const response = await groupBridgeApi.remoteSend(
          selectedGroupId,
          remoteDecision.target.registration_id,
          idempotencyKey,
          {
            text: txt,
            format: "plain",
            priority: composerStateSnapshot.replyRequired ? "attention" : composerStateSnapshot.priority,
            reply_required: composerStateSnapshot.replyRequired,
          },
          { signal: token.signal },
        );
        if (!remoteLifecycle.isCurrent(token)) return;
        if (!response.ok) {
          showError(formatSendMessageError({
            code: response.error.code,
            message: response.error.message,
            groupSendBlockedReason,
            t,
          }));
          return;
        }
        const receipt = response.result.receipt;
        remoteLifecycle.commit(token, () => {
          const consumeComposerIntent = isGroupBridgeRemoteComposerIntentCurrent(
            composerIntent,
            useComposerStore.getState(),
          );
          setRemoteReceipt(receipt);
          setRemoteRequest({
            groupId: selectedGroupId,
            registrationId: remoteDecision.target.registration_id,
            messageGeneration: token.messageGeneration,
            idempotencyKey,
            receipt,
          });
          setRemoteStatusUnavailable(false);
          if (consumeComposerIntent) {
            clearComposer();
            clearDraft(selectedGroupId);
            if (fileInputRef?.current) fileInputRef.current.value = "";
            setDestGroupId(selectedGroupId);
          }
          onMessageSent?.();
        });
      } catch (error) {
        remoteLifecycle.commit(token, () => {
          showError(error instanceof Error ? error.message : "Group Bridge remote send failed");
        });
      } finally {
        remoteLifecycle.finish(token);
        if (remoteSendOwnerRef.current === token) {
          remoteSendOwnerRef.current = null;
          sendInFlightRef.current = false;
        }
      }
      return;
    }
    if (!skillDispatchText && await tryExecuteSlashCommand({
      text: sendText,
      composerFilesCount: composerFilesSnapshot.length,
      hasReplyTarget: !!composerStateSnapshot.replyTarget,
      replyTarget: composerStateSnapshot.replyTarget,
      replyRequired: composerStateSnapshot.replyRequired,
      hasQuotedPresentationRef: !!composerStateSnapshot.quotedPresentationRef,
      sendGroupId: dstGroup,
    })) {
      return;
    }
    if (groupSendBlockedReason) {
      showError(getGroupSendBlockedMessage(groupSendBlockedReason, t));
      return;
    }

    const replyTargetSnapshot = composerStateSnapshot.replyTarget;
    const quotedPresentationRefSnapshot = composerStateSnapshot.quotedPresentationRef;
    const refsSnapshot: MessageRef[] = quotedPresentationRefSnapshot ? [quotedPresentationRefSnapshot] : [];
    const prioritySnapshot = composerStateSnapshot.priority;
    const replyRequiredSnapshot = composerStateSnapshot.replyRequired;
    const collaborationRequiredSnapshot = composerStateSnapshot.collaborationRequired;
    const computerControlEnabledSnapshot = composerStateSnapshot.computerControlEnabled;
    const computerControlWorkflowIdSnapshot = composerStateSnapshot.computerControlWorkflowId;
    const requestedComputerActorId = composerStateSnapshot.computerControlActorId;
    const computerControlPermissionsSnapshot = composerStateSnapshot.computerControlPermissions;
    const computerControlActorIdSnapshot = String(
      (requestedComputerActorId && requestedComputerActorId !== "foreman" ? requestedComputerActorId : actors.find((actor) => actor.role === "foreman")?.id)
      || actors[0]?.id
      || requestedComputerActorId
      || "foreman",
    );
    const toTextSnapshot = composerStateSnapshot.toText;
    const toTokensSnapshot = computerControlEnabledSnapshot
      ? [computerControlActorIdSnapshot]
      : parseComposerRecipientTokens(toTextSnapshot, validRecipientSet);
    const prio = replyRequiredSnapshot ? "attention" : (prioritySnapshot || "normal");
    const assistantTargets = !isCrossGroup ? resolveAssistantTargets(toTokensSnapshot) : [];

    // Generate a local ID for outbox tracking
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const insertLocalAssistantPlaceholders = () => {
      const now = new Date().toISOString();
      for (const actor of assistantTargets) {
        const actorId = String(actor.id || "").trim();
        if (!actorId || !supportsChatStreamingPlaceholder(actor)) continue;
        upsertStreamingEvent(
          {
            id: `local:${localId}:${actorId}`,
            ts: now,
            kind: "chat.message",
            group_id: selectedGroupId,
            by: actorId,
            _streaming: true,
            data: {
              text: "",
              to: ["user"],
              stream_id: `local:${localId}:${actorId}`,
              pending_event_id: localId,
              pending_placeholder: true,
              activities: [
                {
                  id: `queued:${localId}:${actorId}`,
                  kind: "queued",
                  status: "started",
                  summary: "queued",
                  ts: now,
                },
              ],
            },
          },
          selectedGroupId,
        );
      }
    };

    const clearLocalAssistantPlaceholders = () => {
      removeStreamingEventsByPrefix(`local:${localId}:`, selectedGroupId);
    };

    const restoreComposerState = () => {
      restoreFailedSendComposerState(
        {
          originGroupId,
          composerText: txt,
          composerFiles: composerFilesSnapshot,
          selectedSkillCommand: selectedSkillCommandSnapshot,
          toText: toTextSnapshot,
          replyTarget: replyTargetSnapshot,
          quotedPresentationRef: quotedPresentationRefSnapshot,
          priority: prioritySnapshot,
          replyRequired: replyRequiredSnapshot,
          collaborationRequired: collaborationRequiredSnapshot,
          computerControlEnabled: computerControlEnabledSnapshot,
          computerControlWorkflowId: computerControlWorkflowIdSnapshot,
          computerControlActorId: computerControlActorIdSnapshot,
          computerControlPermissions: computerControlPermissionsSnapshot,
        },
        {
          setComposerText,
          setComposerFiles,
          setSelectedSkillCommand,
          setReplyTarget,
          setQuotedPresentationRef,
          setPriority,
          setReplyRequired,
          setCollaborationRequired,
          setComputerControlEnabled,
          setComputerControlWorkflowId,
          setComputerControlActorId,
          setComputerControlPermission,
          setToText,
          upsertDraft,
        },
      );
    };

    const applyImmediateComposerFeedback = () => {
      const shouldLockBottom = isCurrentScrollAtBottom();
      clearComposer();
      if (chatAtBottomRef) chatAtBottomRef.current = shouldLockBottom;
      if (selectedGroupId) {
        setShowScrollButton(selectedGroupId, !shouldLockBottom);
      }
      if (shouldLockBottom) {
        setForceStickToBottomToken((value) => value + 1);
      }
    };

    // Local validations that must pass before clearing the composer
    if (replyTargetSnapshot && isCrossGroup) {
      showError("Cross-group send does not support replies.");
      setDestGroupId(selectedGroupId);
      return;
    }
    if (quotedPresentationRefSnapshot && isCrossGroup) {
      showError("Cross-group send does not support quoted presentation views.");
      setDestGroupId(selectedGroupId);
      return;
    }
    if (!replyTargetSnapshot && isCrossGroup && composerFilesSnapshot.length > 0) {
      showError("Cross-group send does not support attachments yet.");
      return;
    }

    if (inChatWindow) {
      closeChatWindow(selectedGroupId);
      const url = new URL(window.location.href);
      url.searchParams.delete("event");
      url.searchParams.delete("tab");
      window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
    }

    // Optimistic: enqueue to outbox immediately for same-group sends.
    // If the request fails, we remove the pending entry and restore the composer.
    if (!isCrossGroup) {
      const optimisticAttachments: OptimisticAttachment[] = composerFilesSnapshot.map((file) => ({
        kind: "file",
        path: "",
        title: String(file.name || "file"),
        bytes: Number(file.size || 0),
        mime_type: String(file.type || ""),
        local_preview_url: String(URL.createObjectURL(file)),
      }));
      const optimisticEvent: LedgerEvent = {
        id: localId,
        kind: "chat.message",
        ts: new Date().toISOString(),
        by: "user",
        group_id: selectedGroupId,
        data: {
          text: messageTextSnapshot,
          to: toTokensSnapshot,
          priority: prio,
          reply_required: replyRequiredSnapshot,
          collaboration_required: collaborationRequiredSnapshot,
          computer_control_request: computerControlEnabledSnapshot ? {
            mode: computerControlWorkflowIdSnapshot ? "run_existing" : "create_and_run",
            workflow_id: computerControlWorkflowIdSnapshot || undefined,
            actor_id: computerControlActorIdSnapshot,
            inputs: {},
            allow_high_risk: true,
            allow_publish: computerControlPermissionsSnapshot.publish,
            allow_trust: computerControlPermissionsSnapshot.trust,
            allow_unattended_triggers: computerControlPermissionsSnapshot.unattendedTriggers,
            allow_workflow_edit: true,
          } : undefined,
          client_id: localId,
          reply_to: replyTargetSnapshot?.eventId || null,
          quote_text: replyTargetSnapshot?.text || undefined,
          refs: refsSnapshot,
          format: "plain",
          attachments: optimisticAttachments,
          _optimistic: true,
        } as LedgerEvent["data"],
      };
      enqueueOutbox(selectedGroupId, localId, optimisticEvent);
      insertLocalAssistantPlaceholders();
    }

    applyImmediateComposerFeedback();
    sendInFlightRef.current = true;
    try {
      const to = toTokensSnapshot;
      let resp;
      if (replyTargetSnapshot) {
        resp = await api.replyMessage(
          selectedGroupId,
          messageTextSnapshot,
          to,
          replyTargetSnapshot.eventId,
          composerFilesSnapshot.length > 0 ? composerFilesSnapshot : undefined,
          prio,
          replyRequiredSnapshot,
          collaborationRequiredSnapshot,
          localId,
          refsSnapshot,
          skillCapabilityIdSnapshot,
        );
      } else {
        if (isCrossGroup) {
          resp = await api.sendCrossGroupMessage(selectedGroupId, dstGroup, messageTextSnapshot, to, prio, replyRequiredSnapshot, collaborationRequiredSnapshot);
        } else {
          resp = await api.sendMessage(
            selectedGroupId,
            messageTextSnapshot,
            to,
            composerFilesSnapshot.length > 0 ? composerFilesSnapshot : undefined,
            prio,
            replyRequiredSnapshot,
            collaborationRequiredSnapshot,
            localId,
            refsSnapshot,
            computerControlEnabledSnapshot ? {
              mode: computerControlWorkflowIdSnapshot ? "run_existing" : "create_and_run",
              workflow_id: computerControlWorkflowIdSnapshot || undefined,
              actor_id: computerControlActorIdSnapshot,
              inputs: {},
              allow_high_risk: true,
              allow_publish: computerControlPermissionsSnapshot.publish,
              allow_trust: computerControlPermissionsSnapshot.trust,
              allow_unattended_triggers: computerControlPermissionsSnapshot.unattendedTriggers,
              allow_workflow_edit: true,
            } : undefined,
            skillCapabilityIdSnapshot,
          );
        }
      }
      if (!resp.ok) {
        // Pending-only outbox: failed sends roll back to the composer.
        removeOutbox(selectedGroupId, localId);
        clearLocalAssistantPlaceholders();
        restoreComposerState();
        showError(formatSendMessageError({
          code: resp.error.code,
          message: resp.error.message,
          groupSendBlockedReason,
          t,
        }));
        return;
      }
      const canonicalEvent =
        !isCrossGroup && resp.result && typeof resp.result === "object" && "event" in resp.result
          ? (resp.result.event as LedgerEvent | null | undefined)
          : undefined;

      // Cross-group sends do not deliver a canonical event into the current
      // group's stream, so clear the optimistic entry on HTTP success.
      if (isCrossGroup) {
        removeOutbox(selectedGroupId, localId);
      }
      // The HTTP response is the canonical acknowledgement. Reconcile it
      // immediately so a delayed/lost SSE frame cannot leave the optimistic
      // row looking unsent. SSE remains an idempotent follow-up.
      if (canonicalEvent && isCrossGroup) {
        appendEvent(canonicalEvent, selectedGroupId);
      } else if (canonicalEvent && !isCrossGroup) {
        const reconciled = mergeCanonicalAttachmentsWithOptimisticPreview(canonicalEvent, selectedGroupId);
        appendEvent(reconciled.event, selectedGroupId);
        removeOutbox(selectedGroupId, localId);
        releaseTransferredPreviewUrls(reconciled.transferredPreviewUrls);
        const canonicalEventId = String(canonicalEvent.id || "").trim();
        if (canonicalEventId) {
          promoteStreamingEventsByPrefix(`local:${localId}:`, canonicalEventId, selectedGroupId);
        }
      }
      setDestGroupId(selectedGroupId);
      clearDraft(selectedGroupId);
      setToText("");
      if (fileInputRef?.current) fileInputRef.current.value = "";
      if (selectedGroupId) {
        setChatUnreadCount(selectedGroupId, 0);
        setChatFilter(selectedGroupId, "all");
        setChatMobileSurface(selectedGroupId, "messages");
      }
      onMessageSent?.();
    } catch (error) {
      const message = error instanceof Error ? error.message : "send failed";
      // Pending-only outbox: failed sends roll back to the composer.
      removeOutbox(selectedGroupId, localId);
      clearLocalAssistantPlaceholders();
      restoreComposerState();
      showError(message);
    } finally {
      sendInFlightRef.current = false;
    }
  }, [
    selectedGroupId,
    groupSendBlockedReason,
    allSlashCommands,
    slashCommands,
    tryExecuteSlashCommand,
    validRecipientSet,
    inChatWindow,
    appendEvent,
    enqueueOutbox,
    removeOutbox,
    showError,
    clearComposer,
    setComposerText,
    setComposerFiles,
    setSelectedSkillCommand,
    setReplyTarget,
    setQuotedPresentationRef,
    setPriority,
    setReplyRequired,
    setCollaborationRequired,
    setComputerControlEnabled,
    setComputerControlWorkflowId,
    setComputerControlActorId,
    setComputerControlPermission,
    setToText,
    setDestGroupId,
    upsertDraft,
    clearDraft,
    closeChatWindow,
    fileInputRef,
    chatAtBottomRef,
    isCurrentScrollAtBottom,
    setChatFilter,
    setChatMobileSurface,
    setShowScrollButton,
    setChatUnreadCount,
    onMessageSent,
    promoteStreamingEventsByPrefix,
    removeStreamingEventsByPrefix,
    resolveAssistantTargets,
    upsertStreamingEvent,
    actors,
    visibleRemoteTargetId,
    selectedRemoteTarget,
    remoteLifecycle,
    t,
  ]);

  const copyMessageLink = useCallback(
    async (eventId: string) => {
      const eid = String(eventId || "").trim();
      if (!eid || !selectedGroupId) return;

      const url = new URL(window.location.origin + window.location.pathname);
      url.searchParams.set("group", selectedGroupId);
      url.searchParams.set("event", eid);
      url.searchParams.set("tab", "chat");

      const text = url.toString();
      const ok = await copyTextToClipboard(text);
      if (ok) {
        showNotice({ message: "Link copied" });
      } else {
        showError("Failed to copy link");
      }
    },
    [selectedGroupId, showNotice, showError]
  );

  const copyMessageText = useCallback(
    async (ev: LedgerEvent) => {
      if (ev.kind !== "chat.message") return;
      const data = ev.data as ChatMessageData | undefined;
      const text = String(data?.text || "");
      if (!text.trim()) return;

      const ok = await copyTextToClipboard(text);

      if (ok) {
        showNotice({ message: t("chat:contentCopied", { defaultValue: "Content copied" }) });
      } else {
        showError(t("common:copyFailed", { defaultValue: "Copy failed" }));
      }
    },
    [showError, showNotice, t]
  );

  const startReply = useCallback(
    (ev: LedgerEvent) => {
      const replyComposerState = buildReplyComposerState(ev, selectedGroupId, actors, groupSettings);
      if (!replyComposerState) {
        showError(t("replyTargetUnavailable", { defaultValue: "This message is not ready for replies yet." }));
        return;
      }

      // Reply is always in the current group.
      if (replyComposerState.destGroupId) {
        setDestGroupId(replyComposerState.destGroupId);
      }
      setReplyToText(replyComposerState.toText);
      setReplyTarget(replyComposerState.replyTarget);
      requestAnimationFrame(() => composerRef?.current?.focus());
    },
    [selectedGroupId, actors, groupSettings, setDestGroupId, setReplyToText, setReplyTarget, composerRef, showError, t]
  );

  const cancelReply = useCallback(() => setReplyTarget(null), [setReplyTarget]);

  const showRecipients = useCallback(
    (eventId: string) => setRecipientsModal(eventId),
    [setRecipientsModal]
  );

  const relayMessage = useCallback(
    (ev: LedgerEvent) => setRelayModal(ev.id ?? null, selectedGroupId, ev),
    [setRelayModal, selectedGroupId]
  );

  const openSourceMessage = useCallback(
    (srcGroupId: string, srcEventId: string) => {
      const gid = String(srcGroupId || "").trim();
      const eid = String(srcEventId || "").trim();
      if (!gid || !eid) return;

      const url = new URL(window.location.href);
      url.searchParams.set("group", gid);
      url.searchParams.set("event", eid);
      url.searchParams.set("tab", "chat");
      window.history.replaceState({}, "", url.pathname + "?" + url.searchParams.toString());

      if (selectedGroupId === gid) {
        useUIStore.getState().setActiveTab("chat");
        void openChatWindow(gid, eid);
      } else {
        // Queue deep link and switch groups
        useGroupStore.getState().setSelectedGroupId(gid);
        // Note: App.tsx handles the deep link effect
      }
    },
    [selectedGroupId, openChatWindow]
  );

  const exitChatWindow = useCallback(() => {
    closeChatWindow(selectedGroupId);
    const url = new URL(window.location.href);
    url.searchParams.delete("event");
    url.searchParams.delete("tab");
    window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
  }, [closeChatWindow, selectedGroupId]);

  const handleScrollButtonClick = useCallback(() => {
    if (chatAtBottomRef) chatAtBottomRef.current = true;
    if (selectedGroupId) {
      setShowScrollButton(selectedGroupId, false);
      setChatUnreadCount(selectedGroupId, 0);
      setChatScrollSnapshot(selectedGroupId, { mode: "follow", anchorId: "", offsetPx: 0, updatedAt: Date.now() });
    }
  }, [chatAtBottomRef, selectedGroupId, setShowScrollButton, setChatUnreadCount, setChatScrollSnapshot]);

  const handleScrollChange = useCallback(
    (isAtBottom: boolean) => {
      if (chatAtBottomRef) chatAtBottomRef.current = isAtBottom;
      if (!selectedGroupId) return;
      setShowScrollButton(selectedGroupId, !isAtBottom);
      if (isAtBottom) setChatUnreadCount(selectedGroupId, 0);
    },
    [chatAtBottomRef, selectedGroupId, setShowScrollButton, setChatUnreadCount]
  );

  const handleScrollSnapshot = useCallback(
    (
      snap: { mode: "follow" | "detached"; anchorId: string; offsetPx: number; updatedAt: number },
      overrideGroupId?: string,
    ) => {
      if (inChatWindow && !overrideGroupId) return;
      const gid = String(overrideGroupId || selectedGroupId || "").trim();
      if (!gid) return;
      setChatScrollSnapshot(gid, snap);
    },
    [inChatWindow, selectedGroupId, setChatScrollSnapshot]
  );

  const addAgent = useCallback(() => {
    setNewActorRole(hasForeman ? "peer" : "foreman");
    openModal("addActor");
  }, [hasForeman, openModal, setNewActorRole]);

  const loadCurrentGroupHistory = useCallback(() => {
    if (!selectedGroupId) return Promise.resolve();
    return loadMoreHistory(selectedGroupId);
  }, [selectedGroupId, loadMoreHistory]);

  // ============ Return ============

  return {
    // Chat state
    chatMessages,
    hasAnyChatMessages,
    chatFilter,
    setChatFilter: updateChatFilter,
    chatViewKey,
    chatWindowProps,
    chatInitialScrollTargetId,
    chatInitialScrollAnchorId,
    chatInitialScrollAnchorOffsetPx,
    chatHighlightEventId,
    inChatWindow,
    isLoadingHistory: effectiveIsLoadingHistory,
    hasMoreHistory: effectiveHasMoreHistory,
    loadMoreHistory: inChatWindow ? undefined : loadCurrentGroupHistory,
    chatEmptyState,

    // UI state
    busy,
    showScrollButton,
    chatUnreadCount,
    forceStickToBottomToken,

    // Setup checklist
    showSetupCard,
    needsScope,
    needsActors,
    needsStart,
    hasForeman,

    // Composer state
    composerText,
    setComposerText,
    selectedSkillCommand,
    setSelectedSkillCommand,
    composerFiles,
    setComposerFiles,
    removeComposerFile,
    replyTarget,
    quotedPresentationRef,
    cancelReply,
    clearQuotedPresentationRef: () => setQuotedPresentationRef(null),
    toTokens,
    toggleRecipient,
    clearRecipients,
    appendRecipientToken,
    priority,
    replyRequired,
    collaborationRequired,
    computerControlEnabled,
    computerControlWorkflowId,
    computerControlActorId,
    computerControlPermissions,
    setPriority,
    setReplyRequired,
    setCollaborationRequired,
    setComputerControlEnabled,
    setComputerControlWorkflowId,
    setComputerControlActorId,
    setComputerControlPermission,
    destGroupId: sendGroupId,
    setDestGroupId,
    composerGroupSettled,
    remoteTargets: visibleRemoteTargets,
    remoteTargetsBusy: visibleRemoteTargetsBusy,
    remoteTargetId: visibleRemoteTargetId,
    setRemoteTargetId,
    remoteReceipt: visibleRemoteReceipt,
    remoteStatusUnavailable: visibleRemoteStatusUnavailable,
    mentionSuggestions,
    composerHistoryEntries,
    suggestedUserMessage,
    slashSkillScope,
    setSlashSkillScope,

    // Agent state
    agentStates,
    taskById,

    // Actions
    sendMessage,
    slashCommands,
    allSlashCommands,
    copyMessageLink,
    copyMessageText,
    startReply,
    showRecipients,
    relayMessage,
    openSourceMessage,
    exitChatWindow,
    handleScrollButtonClick,
    handleScrollChange,
    handleScrollSnapshot,
    addAgent,
  };
}
