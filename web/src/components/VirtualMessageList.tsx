import { memo, useRef, useEffect, useCallback, useMemo } from "react";
import type { MutableRefObject } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useTranslation } from "react-i18next";
import { LedgerEvent, Actor, AgentState, PresentationMessageRef } from "../types";
import { MessageBubble } from "./MessageBubble";
import { useActorDisplayNameMap } from "../hooks/useActorDisplayName";

function getStableMessageKey(message: LedgerEvent | undefined, index: number): string | number {
  if (message?.kind === "chat.message" && message.data && typeof message.data === "object") {
    const clientId = typeof (message.data as { client_id?: unknown }).client_id === "string"
      ? String((message.data as { client_id?: string }).client_id || "").trim()
      : "";
    if (clientId) return `client:${clientId}`;
  }
  const eventId = typeof message?.id === "string" ? String(message.id || "").trim() : "";
  return eventId || index;
}

export interface VirtualMessageListProps {
  messages: LedgerEvent[];
  actors: Actor[];
  agentStates: AgentState[];
  isDark: boolean;
  readOnly?: boolean;
  groupId: string;
  groupLabelById: Record<string, string>;
  viewKey?: string;
  initialScrollTargetId?: string;
  initialScrollAnchorId?: string;
  initialScrollAnchorOffsetPx?: number;
  highlightEventId?: string;
  scrollRef?: MutableRefObject<HTMLDivElement | null>;
  followBottomRef?: MutableRefObject<boolean>;
  onReply: (ev: LedgerEvent) => void;
  onShowRecipients: (eventId: string) => void;
  onCopyLink?: (eventId: string) => void;
  onCopyContent?: (ev: LedgerEvent) => void;
  onRelay?: (ev: LedgerEvent) => void;
  onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
  onOpenPresentationRef?: (ref: PresentationMessageRef, event: LedgerEvent) => void;
  showScrollButton: boolean;
  onScrollButtonClick: () => void;
  chatUnreadCount: number;
  onScrollChange?: (isAtBottom: boolean) => void;
  onScrollSnapshot?: (snap: { atBottom: boolean; anchorId: string; offsetPx: number }, groupId?: string) => void;
  // History loading
  isLoadingHistory?: boolean;
  hasMoreHistory?: boolean;
  onLoadMore?: () => void;
}

type VirtualMessageListInnerProps = VirtualMessageListProps & {
  resetKey: string;
};

type VirtualMessageRowProps = {
  virtualRow: { key: React.Key; index: number; start: number };
  message: LedgerEvent;
  actorById: Map<string, Actor>;
  actors: Actor[];
  displayNameMap: Map<string, string>;
  agentState: AgentState | null;
  isDark: boolean;
  readOnly?: boolean;
  groupId: string;
  groupLabelById: Record<string, string>;
  highlightEventId?: string;
  onReply: (ev: LedgerEvent) => void;
  onShowRecipients: (eventId: string) => void;
  onCopyLink?: (eventId: string) => void;
  onCopyContent?: (ev: LedgerEvent) => void;
  onRelay?: (ev: LedgerEvent) => void;
  onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
  onOpenPresentationRef?: (ref: PresentationMessageRef, event: LedgerEvent) => void;
  measureElement: (node: Element | null) => void;
  onRowLayoutChange: (node: HTMLDivElement | null) => void;
};

const VirtualMessageRow = memo(function VirtualMessageRow({
  virtualRow,
  message,
  actorById,
  actors,
  displayNameMap,
  agentState,
  isDark,
  readOnly,
  groupId,
  groupLabelById,
  highlightEventId,
  onReply,
  onShowRecipients,
  onCopyLink,
  onCopyContent,
  onRelay,
  onOpenSource,
  onOpenPresentationRef,
  measureElement,
  onRowLayoutChange,
}: VirtualMessageRowProps) {
  const rowRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = rowRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    let rafId: number | null = null;
    const observer = new ResizeObserver(() => {
      if (rafId != null) window.cancelAnimationFrame(rafId);
      rafId = window.requestAnimationFrame(() => {
        rafId = null;
        measureElement(el);
        onRowLayoutChange(el);
      });
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (rafId != null) window.cancelAnimationFrame(rafId);
    };
  }, [measureElement, onRowLayoutChange]);

  return (
    <div
      data-index={virtualRow.index}
      data-message-row="true"
      data-message-id={message.id ? String(message.id) : ""}
      ref={(node) => {
        rowRef.current = node;
        measureElement(node);
      }}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        transform: `translateY(${virtualRow.start}px)`,
      }}
      className="pb-6"
    >
      <MessageBubble
        event={message}
        actorById={actorById}
        actors={actors}
        displayNameMap={displayNameMap}
        agentState={agentState}
        isDark={isDark}
        readOnly={readOnly}
        groupId={groupId}
        groupLabelById={groupLabelById}
        isHighlighted={!!highlightEventId && String(message.id || "") === String(highlightEventId)}
        onReply={() => onReply(message)}
        onShowRecipients={() => {
          if (message.id) {
            onShowRecipients(String(message.id));
          }
        }}
        onCopyLink={onCopyLink}
        onCopyContent={onCopyContent}
        onRelay={onRelay}
        onOpenSource={onOpenSource}
        onOpenPresentationRef={onOpenPresentationRef}
      />
    </div>
  );
});

const VirtualMessageListInner = function VirtualMessageListInner({
  messages,
  actors,
  agentStates,
  isDark,
  readOnly,
  groupId,
  groupLabelById,
  viewKey: _viewKey,
  initialScrollTargetId,
  initialScrollAnchorId,
  initialScrollAnchorOffsetPx,
  highlightEventId,
  scrollRef,
  followBottomRef,
  onReply,
  onShowRecipients,
  onCopyLink,
  onCopyContent,
  onRelay,
  onOpenSource,
  onOpenPresentationRef,
  showScrollButton,
  onScrollButtonClick,
  chatUnreadCount,
  onScrollChange,
  onScrollSnapshot,
  isLoadingHistory = false,
  hasMoreHistory = true,
  onLoadMore,
  resetKey,
}: VirtualMessageListInnerProps) {
  const { t } = useTranslation(["chat", "common"]);
  const parentRef = useRef<HTMLDivElement | null>(null);
  const remeasureRafRef = useRef<number | null>(null);
  const shouldVirtualize = messages.length >= 80;

  const agentStateById = useMemo(() => {
    const m = new Map<string, AgentState>();
    for (const p of agentStates || []) m.set(String(p.id || ""), p);
    return m;
  }, [agentStates]);

  const actorById = useMemo(() => {
    const map = new Map<string, Actor>();
    for (const actor of actors || []) {
      const actorId = String(actor.id || "").trim();
      if (actorId) map.set(actorId, actor);

      const actorTitle = String(actor.title || "").trim();
      if (actorTitle && !map.has(actorTitle)) map.set(actorTitle, actor);

      const actorIdLower = actorId.toLowerCase();
      if (actorIdLower && !map.has(actorIdLower)) map.set(actorIdLower, actor);

      const actorTitleLower = actorTitle.toLowerCase();
      if (actorTitleLower && !map.has(actorTitleLower)) map.set(actorTitleLower, actor);
    }
    return map;
  }, [actors]);

  // Create display name map once at the list level (not per-message)
  const displayNameMap = useActorDisplayNameMap(actors);

  // Stable ref for messages — used by getEstimatedSize to avoid rebuilding
  // the callback (and thus the virtualizer) on every messages change.
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  const prevMessageCountRef = useRef(messages.length);
  const prevTailMessageIdRef = useRef<string>(messages[messages.length - 1]?.id ? String(messages[messages.length - 1]?.id) : "");
  const isAtBottomRef = useRef(true);
  const shouldFollowBottomRef = useRef(true);
  const didInitialScrollRef = useRef(false);
  const scrollTimeoutRef = useRef<number | null>(null);
  const scrollRafRef = useRef<number | null>(null);
  const followupScrollTimeoutRef = useRef<number | null>(null);
  const scrollTokenRef = useRef(0);
  const scrollRafScheduledRef = useRef(false);
  const snapshotFlushTimerRef = useRef<number | null>(null);
  // For history loading scroll position preservation (prepend older messages)
  const topLoadArmedRef = useRef(true);
  const pendingRestoreRef = useRef(false);
  const pendingRestoreSeqRef = useRef<number | null>(null);
  const lastScrollEventAtRef = useRef(0);
  const scrollEventSeqRef = useRef(0);
  const anchorMessageIdRef = useRef<string>("");
  const anchorOffsetRef = useRef(0);
  const lastScrollTopRef = useRef(0);
  const touchClientYRef = useRef<number | null>(null);
  const userScrollIntentUntilRef = useRef(0);
  // 标记容器正在处理 resize（如 footer 回复栏出现/消失），
  // 防止 handleScroll 将浏览器裁剪 scrollTop 误判为用户上滑
  const isContainerResizingRef = useRef(false);

  // Track previous resetKey for scroll snapshot before group switch
  const prevResetKeyRef = useRef<string | undefined>(undefined);
  // Store latest scroll snapshot for saving on group switch
  const latestSnapshotRef = useRef<{ atBottom: boolean; anchorId: string; offsetPx: number } | null>(null);

  // Dynamic height estimation based on message content
  // This reduces layout shift during scrolling by providing better initial estimates
  const getEstimatedSize = useCallback(
    (index: number): number => {
      const message = messagesRef.current[index];
      if (!message) return 100;

      const data = message.data as { text?: string; attachments?: unknown[] } | undefined;
      const text = String(data?.text || "");
      const attachments = Array.isArray(data?.attachments) ? data.attachments : [];

      // Base height: avatar + header + padding
      let height = 72;

      // Text content estimation
      if (text) {
        const lineCount = text.split("\n").length;
        // Average ~20 chars per line on mobile, ~60 on desktop
        const avgCharsPerLine = 40;
        const wrapLines = Math.ceil(text.length / avgCharsPerLine);
        const estimatedLines = Math.max(lineCount, wrapLines);

        // ~20px per line of text, with a minimum for short messages
        height += Math.min(estimatedLines * 20, 400); // Cap at 400px for very long messages

        // Code blocks detection (triple backticks)
        const codeBlockCount = (text.match(/```/g) || []).length / 2;
        if (codeBlockCount > 0) {
          // Code blocks add significant height due to monospace font and padding
          height += codeBlockCount * 80;
        }
      }

      // Attachments add height
      if (attachments.length > 0) {
        // Images are ~200px, files are ~48px
        for (const att of attachments) {
          const mime = String((att as { mime_type?: string })?.mime_type || "");
          if (mime.startsWith("image/")) {
            height += 200;
          } else {
            height += 48;
          }
        }
      }

      // Quoted reply adds ~60px
      const quoteText = (data as { quote_text?: string })?.quote_text;
      if (quoteText) {
        height += 60;
      }

      return height;
    },
    [] // Stable ref — reads from messagesRef.current, no dep on messages array
  );

  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    getItemKey: (index) => getStableMessageKey(messages[index], index),
    estimateSize: getEstimatedSize,
    overscan: 10,
    paddingStart: 72,
  });


  // Direct ref — synchronous measurement eliminates the jitter caused by
  // the old queueMicrotask wrapper (which deferred measurement by one frame,
  // making the first paint use the stale estimate height).
  const measureElement = virtualizer.measureElement;

  const getMessageRowById = useCallback((eventId: string): HTMLDivElement | null => {
    const container = parentRef.current;
    if (!container || !eventId) return null;
    return container.querySelector(`[data-message-row="true"][data-message-id="${CSS.escape(eventId)}"]`);
  }, []);

  const getAnchorSnapshot = useCallback((scrollTop: number) => {
    const container = parentRef.current;
    if (!container) return null;

    if (shouldVirtualize) {
      const vItems = virtualizer.getVirtualItems();
      if (vItems.length <= 0) return null;
      const anchorItem = vItems.find((v) => v.start + v.size > scrollTop + 1) || vItems[0];
      const msg = messages[anchorItem.index];
      const anchorId = msg?.id ? String(msg.id) : "";
      if (!anchorId) return null;
      return {
        anchorId,
        offsetPx: Math.max(0, scrollTop - anchorItem.start),
      };
    }

    const rows = Array.from(container.querySelectorAll<HTMLDivElement>('[data-message-row="true"]'));
    if (rows.length <= 0) return null;
    const anchorRow =
      rows.find((row) => row.offsetTop + row.offsetHeight > scrollTop + 1) || rows[0];
    const anchorId = String(anchorRow.dataset.messageId || "").trim();
    if (!anchorId) return null;
    return {
      anchorId,
      offsetPx: Math.max(0, scrollTop - anchorRow.offsetTop),
    };
  }, [messages, shouldVirtualize, virtualizer]);

  const setFollowBottom = useCallback((next: boolean) => {
    shouldFollowBottomRef.current = next;
    if (followBottomRef) followBottomRef.current = next;
  }, [followBottomRef]);

  const setAtBottom = useCallback((next: boolean) => {
    isAtBottomRef.current = next;
  }, []);

  const stickToBottomIfNeeded = useCallback(() => {
    // 将 ref 检查推迟到 rAF 内执行，确保 handleScroll 中的同步方向检测
    // 有机会先将 shouldFollowBottomRef 设为 false，避免竞态导致弹回抖动
    window.requestAnimationFrame(() => {
      // 容器 resize 期间（如回复栏出现/消失）不做任何滚动
      if (isContainerResizingRef.current) return;
      if (performance.now() < userScrollIntentUntilRef.current) return;
      if (!shouldFollowBottomRef.current) return;
      const el = parentRef.current;
      if (!el) return;
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    });
  }, []);

  const scrollToMessageAnchor = useCallback((eventId: string, offsetPx = 0) => {
    const el = parentRef.current;
    if (!el || !eventId) return false;

    if (shouldVirtualize) {
      const idx = messages.findIndex((m) => String(m?.id || "") === String(eventId));
      if (idx < 0) return false;
      const offsetInfo = virtualizer.getOffsetForIndex(idx, "start");
      if (offsetInfo) {
        virtualizer.scrollToOffset(offsetInfo[0] + Math.max(0, offsetPx), { align: "start", behavior: "auto" });
      } else {
        virtualizer.scrollToIndex(idx, { align: "start", behavior: "auto" });
      }
      return true;
    }

    const row = getMessageRowById(String(eventId));
    if (!row) return false;
    el.scrollTo({ top: row.offsetTop + Math.max(0, offsetPx), behavior: "auto" });
    return true;
  }, [getMessageRowById, messages, shouldVirtualize, virtualizer]);

  const handleRowLayoutChange = useCallback((node: HTMLDivElement | null) => {
    if (shouldVirtualize && node) {
      measureElement(node);
    }
    // 容器 resize 期间跳过 stickToBottom，
    // 避免滚动条出现/消失导致的行宽变化触发不必要的滚动
    if (!isContainerResizingRef.current) {
      stickToBottomIfNeeded();
    }
  }, [measureElement, shouldVirtualize, stickToBottomIfNeeded]);

  const checkIsAtBottom = useCallback(() => {
    const el = parentRef.current;
    if (!el) return true;
    // Increased threshold to reduce false negatives when near bottom
    // (e.g., during layout shifts or when switching groups)
    const threshold = 200;
    return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = parentRef.current;
    if (!el || messages.length <= 0) return;
    requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    });
  }, [messages.length]);

  const cancelScheduledScroll = useCallback(() => {
    const id = scrollTimeoutRef.current;
    if (id != null) {
      scrollTimeoutRef.current = null;
      window.clearTimeout(id);
    }
    const rid = scrollRafRef.current;
    if (rid != null) {
      scrollRafRef.current = null;
      window.cancelAnimationFrame(rid);
    }
    const fid = followupScrollTimeoutRef.current;
    if (fid != null) {
      followupScrollTimeoutRef.current = null;
      window.clearTimeout(fid);
    }
  }, []);

  const interruptBottomFollow = useCallback(() => {
    userScrollIntentUntilRef.current = performance.now() + 280;
    setAtBottom(false);
    setFollowBottom(false);
    cancelScheduledScroll();
  }, [cancelScheduledScroll, setAtBottom, setFollowBottom]);

  const scheduleScroll = useCallback(
    (fn: () => void) => {
      cancelScheduledScroll();
      // Use setTimeout(0) to avoid flushSync warning during React lifecycle.
      scrollTimeoutRef.current = window.setTimeout(() => {
        scrollTimeoutRef.current = null;
        fn();
      }, 0);
    },
    [cancelScheduledScroll]
  );

  const scheduleScrollToBottom = useCallback(
    (opts?: { requireAtBottom?: boolean }) => {
      scheduleScroll(() => {
        if (opts?.requireAtBottom && !isAtBottomRef.current) return;
        scrollToBottom();
      });
    },
    [scheduleScroll, scrollToBottom]
  );

  const scrollToIndexStable = useCallback(
    (idx: number) => {
      cancelScheduledScroll();
      const token = scrollTokenRef.current;
      const doScroll = () => {
        virtualizer.scrollToIndex(idx, { align: "center", behavior: "auto" });
      };
      doScroll();

      scrollRafRef.current = window.requestAnimationFrame(() => {
        scrollRafRef.current = null;
        if (scrollTokenRef.current !== token) return;
        doScroll();

        followupScrollTimeoutRef.current = window.setTimeout(() => {
          followupScrollTimeoutRef.current = null;
          if (scrollTokenRef.current !== token) return;
          doScroll();
        }, 120);
      });
    },
    [cancelScheduledScroll, virtualizer]
  );

  const scrollToAnchorStable = useCallback(
    (idx: number, offsetPx: number) => {
      cancelScheduledScroll();
      const token = scrollTokenRef.current;
      const doScroll = () => {
        const offsetInfo = virtualizer.getOffsetForIndex(idx, "start");
        if (offsetInfo) {
          virtualizer.scrollToOffset(offsetInfo[0] + Math.max(0, offsetPx), { align: "start", behavior: "auto" });
        } else {
          virtualizer.scrollToIndex(idx, { align: "start", behavior: "auto" });
        }
      };
      doScroll();

      scrollRafRef.current = window.requestAnimationFrame(() => {
        scrollRafRef.current = null;
        if (scrollTokenRef.current !== token) return;
        doScroll();

        followupScrollTimeoutRef.current = window.setTimeout(() => {
          followupScrollTimeoutRef.current = null;
          if (scrollTokenRef.current !== token) return;
          doScroll();
        }, 120);
      });
    },
    [cancelScheduledScroll, virtualizer]
  );

  const handleScroll = useCallback(() => {
    // 同步检测上滑方向，立即解除底部跟随，防止与 stickToBottomIfNeeded 竞态
    // 跳过容器 resize 引起的 scrollTop 变化（如回复栏出现/消失）
    const elSync = parentRef.current;
    if (elSync && !isContainerResizingRef.current) {
      const curTopSync = elSync.scrollTop;
      const prevTopSync = lastScrollTopRef.current;
      if (curTopSync < prevTopSync - 4) {
        setFollowBottom(false);
        setAtBottom(false);
      }
    }

    if (scrollRafScheduledRef.current) return;
    scrollRafScheduledRef.current = true;

    window.requestAnimationFrame(() => {
      scrollRafScheduledRef.current = false;

    const el = parentRef.current;
    if (!el) return;

    scrollEventSeqRef.current += 1;
    lastScrollEventAtRef.current = performance.now();
    const curTop = el.scrollTop;
    const prevTop = lastScrollTopRef.current;
    lastScrollTopRef.current = curTop;

    const atBottom = checkIsAtBottom();
    const scrollingUp = !isContainerResizingRef.current && curTop < prevTop - 4;
    if (scrollingUp) {
      userScrollIntentUntilRef.current = performance.now() + 280;
      setAtBottom(false);
      setFollowBottom(false);
      onScrollChange?.(false);
    } else if (atBottom) {
      setFollowBottom(true);
    }
    // Only notify parent when atBottom state actually changes (not on every scroll event)
    // to avoid triggering store updates and re-renders during inertia scrolling.
    const wasAtBottom = isAtBottomRef.current;
    setAtBottom(atBottom);
    if (atBottom !== wasAtBottom) {
      onScrollChange?.(atBottom);
    }

    // Capture a stable "anchor" (first visible message id + offset into that row)
    // so the parent can restore scroll position when switching groups.
    // Save to ref only during scroll; flush to store via debounce (not every frame)
    // to prevent zustand state churn that kills browser scroll inertia.
    const anchor = getAnchorSnapshot(curTop);
    if (anchor) {
      const snap = { atBottom, anchorId: anchor.anchorId, offsetPx: anchor.offsetPx };
      latestSnapshotRef.current = snap;
      // Debounced flush to store — only after 300ms idle
      if (snapshotFlushTimerRef.current) window.clearTimeout(snapshotFlushTimerRef.current);
      snapshotFlushTimerRef.current = window.setTimeout(() => {
        snapshotFlushTimerRef.current = null;
        if (latestSnapshotRef.current) {
          onScrollSnapshot?.(latestSnapshotRef.current);
        }
      }, 300);
    }

    // Top detection for loading more history.
    //
    // Use a hysteresis "arm/disarm" gate instead of relying on scroll direction.
    // This prevents repeated loads when the scroll position jitters near the top
    // (e.g. due to browser scroll anchoring or dynamic row measurement).
    const topTriggerPx = 80;
    const topRearmPx = 240;
    if (curTop > topRearmPx) topLoadArmedRef.current = true;

    const atTop = curTop < topTriggerPx;
    if (atTop && topLoadArmedRef.current && hasMoreHistory && !isLoadingHistory && onLoadMore) {
      topLoadArmedRef.current = false;
      pendingRestoreRef.current = true;
      pendingRestoreSeqRef.current = scrollEventSeqRef.current;

      const pendingAnchor = getAnchorSnapshot(curTop);
      anchorMessageIdRef.current = pendingAnchor?.anchorId || "";
      anchorOffsetRef.current = pendingAnchor?.offsetPx || 0;

      onLoadMore();
    }
    });
  }, [checkIsAtBottom, getAnchorSnapshot, hasMoreHistory, isLoadingHistory, onLoadMore, onScrollChange, onScrollSnapshot, setAtBottom, setFollowBottom]);

  // When switching views (group or window-mode), reset internal scroll bookkeeping.
  //
  // Important: this must run before the auto-scroll effects below, otherwise it may
  // cancel their scheduled scrolls (breaking deep-link jump precision).
  useEffect(() => {
    const prevKey = prevResetKeyRef.current;

    // Only reset state when resetKey actually changes (not on re-renders with same key)
    if (prevKey === resetKey) {
      return;
    }

    // Before resetting, save the scroll snapshot from previous group (if any)
    if (prevKey && latestSnapshotRef.current) {
      // Extract groupId from prevKey (format: "groupId:live" or "groupId:window:eventId")
      const prevGroupId = prevKey.split(":")[0];
      if (prevGroupId) {
        // Save the last known scroll position for the previous group
        onScrollSnapshot?.(latestSnapshotRef.current, prevGroupId);
      }
    }

    prevResetKeyRef.current = resetKey;
    latestSnapshotRef.current = null;

    scrollTokenRef.current += 1;
    prevMessageCountRef.current = 0;
    prevTailMessageIdRef.current = "";
    setAtBottom(true);
    setFollowBottom(true);
    didInitialScrollRef.current = false;
    topLoadArmedRef.current = true;
    cancelScheduledScroll();
    if (snapshotFlushTimerRef.current) {
      window.clearTimeout(snapshotFlushTimerRef.current);
      snapshotFlushTimerRef.current = null;
    }
    pendingRestoreRef.current = false;
    pendingRestoreSeqRef.current = null;
    anchorMessageIdRef.current = "";
    anchorOffsetRef.current = 0;
    lastScrollTopRef.current = 0;

    // Without key-based remount, the virtualizer keeps stale measurement
    // caches from the previous group. Force a full re-measure so item
    // sizes are recalculated for the new messages.
    if (shouldVirtualize) {
      virtualizer.measure();
    }
  }, [resetKey, cancelScheduledScroll, onScrollSnapshot, setAtBottom, setFollowBottom, shouldVirtualize, virtualizer]);

  useEffect(() => {
    const prevCount = prevMessageCountRef.current;
    const prevTailId = prevTailMessageIdRef.current;
    const nextTailId = messages[messages.length - 1]?.id ? String(messages[messages.length - 1]?.id) : "";

    // Only auto-follow when the tail actually advances while the user is still
    // considered at the bottom. Re-renders, re-measurement, or same-size list
    // replacements should not steal scroll when the user is trying to read up.
    const appendedAtTail =
      messages.length > 0 &&
      (
        messages.length > prevCount ||
        (nextTailId !== "" && nextTailId !== prevTailId)
      );

    prevMessageCountRef.current = messages.length;
    prevTailMessageIdRef.current = nextTailId;

    const shouldAutoFollow = isAtBottomRef.current;
    if (appendedAtTail && shouldAutoFollow) {
      setAtBottom(true);
      setFollowBottom(true);
      scheduleScrollToBottom({ requireAtBottom: true });
    }
  }, [messages, scheduleScrollToBottom, setAtBottom, setFollowBottom]);

  useEffect(() => {
    if (didInitialScrollRef.current) return;
    if (messages.length <= 0) return;
    didInitialScrollRef.current = true;
    setFollowBottom(!initialScrollTargetId && !initialScrollAnchorId);
    scheduleScroll(() => {
      if (initialScrollTargetId) {
        setAtBottom(false);
        setFollowBottom(false);
        if (shouldVirtualize) {
          const idx = messages.findIndex((m) => String(m?.id || "") === String(initialScrollTargetId));
          if (idx >= 0) {
            scrollToIndexStable(idx);
            return;
          }
        } else if (scrollToMessageAnchor(String(initialScrollTargetId), 0)) {
          return;
        }
      }
      if (initialScrollAnchorId) {
        if (shouldVirtualize) {
          const idx = messages.findIndex((m) => String(m?.id || "") === String(initialScrollAnchorId));
          if (idx >= 0) {
            setAtBottom(false);
            setFollowBottom(false);
            scrollToAnchorStable(idx, Number(initialScrollAnchorOffsetPx || 0));
            return;
          }
        } else if (scrollToMessageAnchor(String(initialScrollAnchorId), Number(initialScrollAnchorOffsetPx || 0))) {
          setAtBottom(false);
          setFollowBottom(false);
          return;
        }
      }
      scrollToBottom();
    });
  }, [initialScrollAnchorId, initialScrollAnchorOffsetPx, initialScrollTargetId, messages, scheduleScroll, scrollToAnchorStable, scrollToBottom, scrollToIndexStable, scrollToMessageAnchor, setAtBottom, setFollowBottom, shouldVirtualize]);

  useEffect(() => cancelScheduledScroll, [cancelScheduledScroll]);

  useEffect(() => {
    if (!shouldVirtualize) return;
    const el = parentRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    const observer = new ResizeObserver(() => {
      // 容器高度变化（如 footer 回复栏出现/消失）时，不做任何滚动，
      // 让消息列表的可视位置完全不变。
      // virtualizer 内部已有自己的 ResizeObserver 处理视口重算，无需重复调用 measure()。
      isContainerResizingRef.current = true;

      // 同步更新 lastScrollTopRef，避免浏览器裁剪 scrollTop 后
      // handleScroll 将其误判为用户上滑方向
      lastScrollTopRef.current = el.scrollTop;

      // 双帧 rAF 延迟清除标记，确保覆盖行 ResizeObserver 的异步回调
      // （行宽可能因滚动条出现/消失而变化，触发 handleRowLayoutChange）
      window.requestAnimationFrame(() => {
        lastScrollTopRef.current = el.scrollTop;
        window.requestAnimationFrame(() => {
          isContainerResizingRef.current = false;
          lastScrollTopRef.current = el.scrollTop;
        });
      });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [shouldVirtualize, virtualizer]);

  useEffect(() => {
    return () => {
      if (remeasureRafRef.current != null) {
        window.cancelAnimationFrame(remeasureRafRef.current);
        remeasureRafRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    return () => {
      // Cancel pending debounced flush
      if (snapshotFlushTimerRef.current) {
        window.clearTimeout(snapshotFlushTimerRef.current);
        snapshotFlushTimerRef.current = null;
      }
      // Immediate flush on unmount
      if (latestSnapshotRef.current) {
        const currentGroupId = resetKey.split(":")[0];
        if (currentGroupId) {
          onScrollSnapshot?.(latestSnapshotRef.current, currentGroupId);
        }
      }
      if (scrollRef) {
        scrollRef.current = null;
      }
    };
  }, [onScrollSnapshot, resetKey, scrollRef]);

  // Restore scroll position after loading older messages (prepend).
  //
  // The old implementation used a scrollEventSeq check to detect "user scrolled
  // away during loading, so skip restore". But layout-triggered scroll events
  // from the virtualizer (Loading indicator appearing, row re-measurement, etc.)
  // also increment the seq, causing the restore to be falsely skipped — leaving
  // scrollTop near 0 and immediately re-triggering onLoadMore in a loop.
  //
  // The arm/disarm gate (topLoadArmedRef) already prevents duplicate loads, so
  // the seq check is unnecessary. Removed.
  const restorePendingAnchor = useCallback(() => {
    if (isLoadingHistory) return;
    if (!pendingRestoreRef.current) return;
    const el = parentRef.current;
    if (!el) return;

    const idleMs = performance.now() - lastScrollEventAtRef.current;
    if (idleMs < 120) {
      const remaining = Math.max(20, 120 - idleMs);
      window.setTimeout(() => restorePendingAnchor(), remaining);
      return;
    }

    pendingRestoreRef.current = false;
    pendingRestoreSeqRef.current = null;

    const anchorId = anchorMessageIdRef.current;
    const offsetInRow = anchorOffsetRef.current;
    anchorMessageIdRef.current = "";
    anchorOffsetRef.current = 0;

    if (anchorId) {
      const restored = scrollToMessageAnchor(anchorId, offsetInRow);
      if (restored) {
        // Keep topLoad disarmed so the restored position (which may still be
        // near the top) doesn't immediately re-trigger another load.
        topLoadArmedRef.current = false;
        setFollowBottom(false);
      }
    }
  }, [isLoadingHistory, scrollToMessageAnchor, setFollowBottom]);

  useEffect(() => {
    if (isLoadingHistory) return;
    if (!pendingRestoreRef.current) return;
    restorePendingAnchor();
  }, [isLoadingHistory, restorePendingAnchor]);

  return (
    <div
      ref={(el) => {
        parentRef.current = el;
        if (scrollRef) scrollRef.current = el;
      }}
      className="flex-1 min-h-0 overflow-auto px-4 py-4 relative"
      style={{ overflowAnchor: "none" }}
      onWheelCapture={(event) => {
        if (event.deltaY < -2) interruptBottomFollow();
      }}
      onTouchStart={(event) => {
        touchClientYRef.current = event.touches[0]?.clientY ?? null;
      }}
      onTouchMove={(event) => {
        const nextY = event.touches[0]?.clientY;
        const prevY = touchClientYRef.current;
        touchClientYRef.current = nextY ?? null;
        if (typeof nextY === "number" && typeof prevY === "number" && nextY > prevY + 4) {
          interruptBottomFollow();
        }
      }}
      onTouchEnd={() => {
        touchClientYRef.current = null;
      }}
      onScroll={messages.length > 0 ? handleScroll : undefined}
      role="log"
      aria-label="Chat messages"
    >
      {messages.length === 0 ? (
        (isLoadingHistory || hasMoreHistory) ? (
          <div className="flex flex-col items-center justify-center h-full text-center pb-20">
            <div
              className={`flex items-center gap-2 px-3 py-1.5 rounded-full shadow-md ${isDark ? "bg-slate-800 text-slate-300" : "bg-white text-gray-600"
                }`}
            >
              <div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
              <span className="text-xs">{t("common:loading")}</span>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center pb-20 opacity-50">
            <div className="text-4xl mb-4 grayscale">💬</div>
            <p className={`text-sm font-medium ${isDark ? "text-slate-400" : "text-gray-500"}`}>
              {t("chat:noMessagesYet")}
            </p>
            <p className={`text-xs mt-1 ${isDark ? "text-slate-600" : "text-gray-400"}`}>
              {t("chat:teamReady")}
            </p>
          </div>
        )
      ) : (
        <>
          {/* Loading indicator for history - sticky positioned for visibility */}
          {isLoadingHistory && (
            <div className="sticky top-0 z-10 flex justify-center py-3">
              <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full shadow-md ${isDark ? "bg-slate-800 text-slate-300" : "bg-white text-gray-600"
                }`}>
                <div className="animate-spin w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
                <span className="text-xs">{t("common:loading")}</span>
              </div>
            </div>
          )}

          {/* No more history indicator */}
          {!hasMoreHistory && !isLoadingHistory && (
            <div className={`text-center py-4 text-sm ${isDark ? "text-slate-500" : "text-gray-400"}`}>
              {t("chat:noMoreMessages")}
            </div>
          )}

          {shouldVirtualize ? (
            <div
              style={{
                height: `${virtualizer.getTotalSize()}px`,
                width: "100%",
                position: "relative",
                contain: "layout paint",
              }}
            >
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const message = messages[virtualRow.index];
                return (
                  <VirtualMessageRow
                    key={virtualRow.key}
                    virtualRow={virtualRow}
                    message={message}
                    actorById={actorById}
                    actors={actors}
                    displayNameMap={displayNameMap}
                    agentState={agentStateById.get(String(message.by || "")) || null}
                    isDark={isDark}
                    readOnly={readOnly}
                    groupId={groupId}
                    groupLabelById={groupLabelById}
                    highlightEventId={highlightEventId}
                    onReply={onReply}
                    onShowRecipients={onShowRecipients}
                    onCopyLink={onCopyLink}
                    onCopyContent={onCopyContent}
                    onRelay={onRelay}
                    onOpenSource={onOpenSource}
                    onOpenPresentationRef={onOpenPresentationRef}
                    measureElement={measureElement}
                    onRowLayoutChange={handleRowLayoutChange}
                  />
                );
              })}
            </div>
          ) : (
            <div className="w-full">
              {messages.map((message) => (
                <div
                  key={message.id ? String(message.id) : `${message.ts}-${String(message.by || "")}`}
                  data-message-row="true"
                  data-message-id={message.id ? String(message.id) : ""}
                  className="pb-6"
                >
                  <MessageBubble
                    event={message}
                    actorById={actorById}
                    actors={actors}
                    displayNameMap={displayNameMap}
                    agentState={agentStateById.get(String(message.by || "")) || null}
                    isDark={isDark}
                    readOnly={readOnly}
                    groupId={groupId}
                    groupLabelById={groupLabelById}
                    isHighlighted={!!highlightEventId && String(message.id || "") === String(highlightEventId)}
                    onReply={() => onReply(message)}
                    onShowRecipients={() => {
                      if (message.id) {
                        onShowRecipients(String(message.id));
                      }
                    }}
                    onCopyLink={onCopyLink}
                  onCopyContent={onCopyContent}
                  onRelay={onRelay}
                  onOpenSource={onOpenSource}
                  onOpenPresentationRef={onOpenPresentationRef}
                />
              </div>
            ))}
            </div>
          )}

          {/* Scroll Button */}
          {!readOnly && showScrollButton && (
            <button
              className={`fixed bottom-36 right-6 p-3 rounded-full shadow-xl transition-all z-10 ${isDark
                ? "bg-slate-800 text-white hover:bg-slate-700 border border-slate-700"
                : "bg-white text-gray-600 hover:bg-gray-50 border border-gray-100"
                }`}
              onClick={() => {
                scrollToBottom();
                onScrollButtonClick();
              }}
              aria-label="Scroll to bottom"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
              </svg>
              {chatUnreadCount > 0 && (
                <span className="absolute -top-1 -right-1 flex h-4 w-4">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-60"></span>
                  <span className="relative inline-flex rounded-full h-4 w-4 bg-indigo-500 text-[9px] text-white items-center justify-center font-bold">
                    {chatUnreadCount > 9 ? "!" : chatUnreadCount}
                  </span>
                </span>
              )}
            </button>
          )}
        </>
      )}
    </div>
  );
};

export const VirtualMessageList = memo(function VirtualMessageList(props: VirtualMessageListProps) {
  const resetKey = props.viewKey ?? props.groupId;
  // No `key` prop — avoid full unmount/remount on group switch which causes
  // a visible flash (virtualizer needs 1-2 frames to measure after mount).
  // Internal state is reset via the resetKey useEffect instead.
  return <VirtualMessageListInner {...props} resetKey={resetKey} />;
});
