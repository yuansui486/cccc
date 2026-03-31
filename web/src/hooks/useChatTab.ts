// useChatTab - Encapsulates ChatTab business logic and state.
// Reduces prop drilling by providing state from stores and computed values directly.

import { useMemo, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import {
  useGroupStore,
  useUIStore,
  useComposerStore,
  useModalStore,
  useFormStore,
  selectChatBucketState,
} from "../stores";
import { getEffectiveComposerDestGroupId } from "../stores/useComposerStore";
import { getChatSession } from "../stores/useUIStore";
import { useChatOutboxStore, selectOutboxEntries } from "../stores/chatOutboxStore";
import type { Actor, LedgerEvent, ChatMessageData, MessageRef, OptimisticAttachment } from "../types";
import * as api from "../services/api";

interface UseChatTabOptions {
  selectedGroupId: string;
  actors: Actor[];
  recipientActors: Actor[];
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

export function useChatTab({
  selectedGroupId,
  actors,
  recipientActors,
  onMessageSent,
  composerRef,
  fileInputRef,
  chatAtBottomRef,
  scrollRef,
}: UseChatTabOptions) {
  const { t } = useTranslation(["chat", "common"]);
  // ============ Stores ============
  const { events, chatWindow, hasMoreHistory, hasLoadedTail, isLoadingHistory, isChatWindowLoading } = useGroupStore(
    useCallback((state) => selectChatBucketState(state, selectedGroupId), [selectedGroupId])
  );
  const appendEvent = useGroupStore((state) => state.appendEvent);
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
  const showError = useUIStore((s) => s.showError);
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
    toText,
    replyTarget,
    quotedPresentationRef,
    priority,
    replyRequired,
    destGroupId,
    setComposerText,
    setComposerFiles,
    setToText,
    setReplyTarget,
    setQuotedPresentationRef,
    setPriority,
    setReplyRequired,
    setDestGroupId,
    clearDraft,
    clearComposer,
  } = useComposerStore();

  const { setRecipientsModal, setRelayModal, openModal } = useModalStore();
  const { setNewActorRole } = useFormStore();

  // Outbox (optimistic pending messages) — stable selector, no new array allocation.
  const outboxEntries = useChatOutboxStore(
    useCallback((s) => selectOutboxEntries(s, selectedGroupId), [selectedGroupId])
  );
  const enqueueOutbox = useChatOutboxStore((s) => s.enqueue);
  const removeOutbox = useChatOutboxStore((s) => s.remove);
  const sendInFlightRef = useRef(false);

  // ============ Computed Values ============

  // Valid recipient tokens
  const validRecipientSet = useMemo(() => {
    const out = new Set<string>(["@all", "@foreman", "@peers"]);
    for (const a of recipientActors) {
      const id = String(a.id || "").trim();
      if (id) out.add(id);
    }
    return out;
  }, [recipientActors]);

  // Parse toText into validated tokens
  const toTokens = useMemo(() => {
    const raw = toText
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
  }, [toText, validRecipientSet]);

  // Mention suggestions
  const mentionSuggestions = useMemo(() => {
    const base = ["@all", "@foreman", "@peers"];
    const actorIds = recipientActors.map((a) => String(a.id || "")).filter((id) => id);
    return [...base, ...actorIds];
  }, [recipientActors]);

  // Send group ID (respects cross-group destination)
  const sendGroupId = useMemo(() => {
    return getEffectiveComposerDestGroupId(destGroupId, activeGroupId, selectedGroupId);
  }, [destGroupId, activeGroupId, selectedGroupId]);

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
  const selectedGroupRunning = useMemo(() => {
    const anyActorRunning = actors.some((a) => !!a.running);
    return anyActorRunning;
  }, [actors]);

  // Setup checklist conditions
  const needsScope = !!selectedGroupId && !projectRoot;
  const needsActors = !!selectedGroupId && actors.length === 0;
  const needsStart = !!selectedGroupId && actors.length > 0 && !selectedGroupRunning;
  const showSetupCard = needsScope || needsActors || needsStart;

  // In chat window mode
  const inChatWindow = useMemo(() => {
    return !!chatWindow && String(chatWindow.groupId || "") === String(selectedGroupId || "");
  }, [chatWindow, selectedGroupId]);

  // Filtered live chat messages (canonical + optimistic pending merged)
  const liveChatMessages = useMemo(() => {
    const all = events.filter((ev) => ev.kind === "chat.message");
    const canonicalClientIds = new Set(
      all
        .map((ev) => {
          const data = ev.data && typeof ev.data === "object" ? (ev.data as { client_id?: unknown }) : null;
          return data && typeof data.client_id === "string" ? data.client_id.trim() : "";
        })
        .filter((clientId) => clientId.length > 0)
    );
    const pendingEvents = outboxEntries
      .filter((entry) => !canonicalClientIds.has(entry.localId))
      .map((entry) => entry.event);
    const merged = pendingEvents.length > 0 ? [...all, ...pendingEvents] : all;

    if (chatFilter === "attention") {
      return merged.filter((ev) => {
        const d = ev.data as ChatMessageData | undefined;
        return String(d?.priority || "normal") === "attention";
      });
    }
    if (chatFilter === "task") {
      return merged.filter((ev) => {
        const d = ev.data as ChatMessageData | undefined;
        return !!d?.reply_required;
      });
    }
    if (chatFilter === "user") {
      return merged.filter((ev) => {
        const d = ev.data as ChatMessageData | undefined;
        const dst = typeof d?.dst_group_id === "string" ? String(d.dst_group_id || "").trim() : "";
        if (dst) return false;
        const to = Array.isArray(d?.to) ? d?.to : [];
        const by = String(ev.by || "").trim();
        return by === "user" || to.includes("user") || to.includes("@user");
      });
    }
    return merged;
  }, [events, chatFilter, outboxEntries]);

  // Chat messages (window or live)
  const chatMessages = useMemo(() => {
    if (inChatWindow && chatWindow) return chatWindow.events || [];
    return liveChatMessages;
  }, [chatWindow, inChatWindow, liveChatMessages]);

  const hasAnyChatMessages = useMemo(
    () => events.some((ev) => ev.kind === "chat.message") || outboxEntries.length > 0,
    [events, outboxEntries]
  );

  // Chat view key for VirtualMessageList
  const chatViewKey = useMemo(() => {
    if (inChatWindow && chatWindow) {
      return `${selectedGroupId}:window:${chatWindow.centerEventId}`;
    }
    return `${selectedGroupId}:live`;
  }, [inChatWindow, chatWindow, selectedGroupId]);

  const chatInitialScrollAnchorId = useMemo(() => {
    if (inChatWindow) return undefined;
    if (!scrollSnapshot || scrollSnapshot.atBottom || !scrollSnapshot.anchorId) return undefined;
    return scrollSnapshot.anchorId;
  }, [inChatWindow, scrollSnapshot]);

  const chatInitialScrollAnchorOffsetPx = useMemo(() => {
    if (inChatWindow) return undefined;
    if (!scrollSnapshot || scrollSnapshot.atBottom || !scrollSnapshot.anchorId) return undefined;
    return Number(scrollSnapshot.offsetPx || 0);
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
  const effectiveHasMoreHistory = inChatWindow ? false : (!hasLoadedTail || hasMoreHistory);

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
    const txt = composerText.trim();
    if (!selectedGroupId) return;
    if (!txt && composerFiles.length === 0) return;

    const dstGroup = String(sendGroupId || "").trim();
    const isCrossGroup = !!dstGroup && dstGroup !== selectedGroupId;

    const prio = replyRequired ? "attention" : (priority || "normal");
    const replyTargetSnapshot = replyTarget;
    const composerFilesSnapshot = composerFiles.slice();
    const quotedPresentationRefSnapshot = quotedPresentationRef;
    const refsSnapshot: MessageRef[] = quotedPresentationRefSnapshot ? [quotedPresentationRefSnapshot] : [];
    const prioritySnapshot = priority;
    const replyRequiredSnapshot = replyRequired;
    const toTextSnapshot = toText;

    // Generate a local ID for outbox tracking
    const localId = `local_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

    const restoreComposerState = () => {
      setComposerText(txt);
      setComposerFiles(composerFilesSnapshot);
      setReplyTarget(replyTargetSnapshot);
      setQuotedPresentationRef(quotedPresentationRefSnapshot);
      setPriority(prioritySnapshot);
      setReplyRequired(replyRequiredSnapshot);
      setToText(toTextSnapshot);
    };

    const applyImmediateComposerFeedback = () => {
      clearComposer();
      if (chatAtBottomRef) chatAtBottomRef.current = true;
      if (selectedGroupId) {
        setShowScrollButton(selectedGroupId, false);
      }
      const scrollEl = scrollRef?.current;
      if (scrollEl) {
        requestAnimationFrame(() => {
          scrollEl.scrollTo({ top: scrollEl.scrollHeight, behavior: "auto" });
        });
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
          text: txt,
          to: toTokens,
          priority: prio,
          reply_required: replyRequired,
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
    }

    applyImmediateComposerFeedback();
    sendInFlightRef.current = true;
    try {
      const to = toTokens;
      let resp;
      if (replyTargetSnapshot) {
        resp = await api.replyMessage(
          selectedGroupId,
          txt,
          to,
          replyTargetSnapshot.eventId,
          composerFilesSnapshot.length > 0 ? composerFilesSnapshot : undefined,
          prio,
          replyRequired,
          localId,
          refsSnapshot,
        );
      } else {
        if (isCrossGroup) {
          resp = await api.sendCrossGroupMessage(selectedGroupId, dstGroup, txt, to, prio, replyRequiredSnapshot);
        } else {
          resp = await api.sendMessage(
            selectedGroupId,
            txt,
            to,
            composerFilesSnapshot.length > 0 ? composerFilesSnapshot : undefined,
            prio,
            replyRequired,
            localId,
            refsSnapshot,
          );
        }
      }
      if (!resp.ok) {
        // Pending-only outbox: failed sends roll back to the composer.
        removeOutbox(selectedGroupId, localId);
        restoreComposerState();
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      const canonicalEvent =
        !isCrossGroup && resp.result && typeof resp.result === "object" && "event" in resp.result
          ? (resp.result.event as LedgerEvent | null | undefined)
          : undefined;

      // Cross-group sends do not deliver a canonical event into the current
      // group's stream, so clear the optimistic entry on HTTP success.
      //
      // Same-group sends keep the optimistic row until SSE reconciliation by
      // client_id. Replacing an optimistic attachment preview with the HTTP
      // response event causes a second image load/layout pass, which produces
      // a visible jump while the list is following bottom.
      if (isCrossGroup) {
        removeOutbox(selectedGroupId, localId);
      }
      // For same-group sends, rely on SSE to append the canonical event and
      // clear the matching optimistic row. Cross-group sends still need the
      // returned event because they do not stream back into the current group.
      if (canonicalEvent && isCrossGroup) {
        appendEvent(canonicalEvent, selectedGroupId);
      }
      setDestGroupId(selectedGroupId);
      clearDraft(selectedGroupId);
      if (fileInputRef?.current) fileInputRef.current.value = "";
      if (inChatWindow) {
        closeChatWindow();
        const url = new URL(window.location.href);
        url.searchParams.delete("event");
        url.searchParams.delete("tab");
        window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
      }
      if (selectedGroupId) {
        setChatUnreadCount(selectedGroupId, 0);
      }
      onMessageSent?.();
    } catch (error) {
      const message = error instanceof Error ? error.message : "send failed";
      // Pending-only outbox: failed sends roll back to the composer.
      removeOutbox(selectedGroupId, localId);
      restoreComposerState();
      showError(message);
    } finally {
      sendInFlightRef.current = false;
    }
  }, [
    composerText,
    composerFiles,
    selectedGroupId,
    sendGroupId,
    priority,
    replyRequired,
    toText,
    toTokens,
    replyTarget,
    quotedPresentationRef,
    inChatWindow,
    appendEvent,
    enqueueOutbox,
    removeOutbox,
    showError,
    clearComposer,
    setComposerText,
    setComposerFiles,
    setReplyTarget,
    setQuotedPresentationRef,
    setPriority,
    setReplyRequired,
    setToText,
    setDestGroupId,
    clearDraft,
    closeChatWindow,
    fileInputRef,
    chatAtBottomRef,
    scrollRef,
    setShowScrollButton,
    setChatUnreadCount,
    onMessageSent,
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
      let ok = false;
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch {
        try {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.left = "-9999px";
          ta.style.top = "0";
          document.body.appendChild(ta);
          ta.select();
          ok = document.execCommand("copy");
          document.body.removeChild(ta);
        } catch {
          ok = false;
        }
      }
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

      let ok = false;
      try {
        await navigator.clipboard.writeText(text);
        ok = true;
      } catch {
        try {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.left = "-9999px";
          ta.style.top = "0";
          document.body.appendChild(ta);
          ta.select();
          ok = document.execCommand("copy");
          document.body.removeChild(ta);
        } catch {
          ok = false;
        }
      }

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
      if (!ev.id || ev.kind !== "chat.message") return;
      const data = ev.data as ChatMessageData | undefined;
      const text = data?.text ? String(data.text) : "";

      // Reply is always in the current group.
      if (selectedGroupId) {
        setDestGroupId(selectedGroupId);
      }

      // Pre-fill recipients to match daemon default reply routing
      const by = String(ev.by || "").trim();
      const authorIsActor = by && by !== "user" && actors.some((a) => String(a.id || "") === by);
      const originalTo = Array.isArray(data?.to)
        ? data?.to.map((t) => String(t || "").trim()).filter((t) => t)
        : [];
      const policy = groupSettings?.default_send_to || "foreman";
      const defaultTo =
        authorIsActor
          ? [by]
          : originalTo.length > 0
            ? originalTo
            : policy === "foreman"
              ? ["@foreman"]
              : [];
      setToText(defaultTo.join(", "));

      setReplyTarget({
        eventId: String(ev.id),
        by: String(ev.by || "unknown"),
        text: text.slice(0, 100) + (text.length > 100 ? "..." : ""),
      });
      requestAnimationFrame(() => composerRef?.current?.focus());
    },
    [selectedGroupId, actors, groupSettings, setDestGroupId, setToText, setReplyTarget, composerRef]
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
      setChatScrollSnapshot(selectedGroupId, { atBottom: true, anchorId: "", offsetPx: 0 });
    }
  }, [selectedGroupId, chatAtBottomRef, setShowScrollButton, setChatUnreadCount, setChatScrollSnapshot]);

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
    (snap: { atBottom: boolean; anchorId: string; offsetPx: number }, overrideGroupId?: string) => {
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

    // Setup checklist
    showSetupCard,
    needsScope,
    needsActors,
    needsStart,
    hasForeman,

    // Composer state
    composerText,
    setComposerText,
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
    setPriority,
    setReplyRequired,
    destGroupId: sendGroupId,
    setDestGroupId,
    mentionSuggestions,

    // Agent state
    agentStates,

    // Actions
    sendMessage,
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
