import React, { useEffect, useMemo, useRef, useState } from "react";
import type { Actor } from "../types";
import { CHAT_TAB, COMPUTER_CONTROL_TAB, isWorkspaceTab } from "../utils/appTabs";

export function isChatViewportAtBottom(scrollHeight: number, scrollTop: number, clientHeight: number, threshold = 100): boolean {
  return scrollHeight - scrollTop - clientHeight < threshold;
}

type UseAppTabStateOptions = {
  activeTab: string;
  runtimeActors: Actor[];
  selectedGroupId: string;
  chatSessionAtBottom: boolean | undefined;
  isSmallScreen: boolean;
  setActiveTab: (tab: string) => void;
  setShowScrollButton: (groupId: string, value: boolean) => void;
  setChatUnreadCount: (groupId: string, value: number) => void;
};

type UseAppTabStateResult = {
  composerRef: React.RefObject<HTMLTextAreaElement | null>;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  eventContainerRef: React.MutableRefObject<HTMLDivElement | null>;
  contentRef: React.MutableRefObject<HTMLDivElement | null>;
  activeTabRef: React.MutableRefObject<string>;
  chatAtBottomRef: React.MutableRefObject<boolean>;
  actorsRef: React.MutableRefObject<Actor[]>;
  allTabs: string[];
  renderedActorIds: string[];
  resetMountedActorIds: () => void;
  handleTabChange: (newTab: string) => void;
};

export function useAppTabState({
  activeTab,
  runtimeActors,
  selectedGroupId,
  chatSessionAtBottom,
  isSmallScreen,
  setActiveTab,
  setShowScrollButton,
  setChatUnreadCount,
}: UseAppTabStateOptions): UseAppTabStateResult {
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const eventContainerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const activeTabRef = useRef<string>(CHAT_TAB);
  const chatAtBottomRef = useRef<boolean>(true);
  const actorsRef = useRef<Actor[]>([]);
  const [mountedActorIds, setMountedActorIds] = useState<string[]>([]);

  const allTabs = useMemo(() => [CHAT_TAB, COMPUTER_CONTROL_TAB, ...runtimeActors.map((actor) => actor.id)], [runtimeActors]);

  const handleTabChange = React.useCallback(
    (newTab: string) => {
      if (!isWorkspaceTab(newTab)) {
        setMountedActorIds((prev) => (prev.includes(newTab) ? prev : [...prev, newTab]));
      }
      setActiveTab(newTab);
    },
    [setActiveTab]
  );

  useEffect(() => {
    activeTabRef.current = activeTab;
    if (activeTab !== CHAT_TAB) return;
    if (!selectedGroupId) return;
    const el = eventContainerRef.current;
    if (!el) return;

    if (chatSessionAtBottom ?? chatAtBottomRef.current) {
      chatAtBottomRef.current = true;
      requestAnimationFrame(() => {
        el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
      });
      setShowScrollButton(selectedGroupId, false);
      setChatUnreadCount(selectedGroupId, 0);
      return;
    }

    const atBottom = isChatViewportAtBottom(el.scrollHeight, el.scrollTop, el.clientHeight);
    chatAtBottomRef.current = atBottom;
    setShowScrollButton(selectedGroupId, !atBottom);
    if (atBottom) setChatUnreadCount(selectedGroupId, 0);
  }, [activeTab, selectedGroupId, chatSessionAtBottom, setChatUnreadCount, setShowScrollButton]);

  useEffect(() => {
    if (activeTab !== CHAT_TAB) return;
    if (isSmallScreen) return;
    requestAnimationFrame(() => composerRef.current?.focus());
  }, [activeTab, isSmallScreen]);

  useEffect(() => {
    actorsRef.current = runtimeActors;
  }, [runtimeActors]);

  const renderedActorIds = useMemo(() => {
    const live = new Set(runtimeActors.map((actor) => String(actor.id || "")).filter((id) => id));
    const mountedLiveIds = mountedActorIds.filter((id) => live.has(id));
    if (!isWorkspaceTab(activeTab) && live.has(activeTab) && !mountedLiveIds.includes(activeTab)) {
      return [...mountedLiveIds, activeTab];
    }
    return mountedLiveIds;
  }, [mountedActorIds, activeTab, runtimeActors]);

  const resetMountedActorIds = React.useCallback(() => {
    setMountedActorIds([]);
  }, []);

  return {
    composerRef,
    fileInputRef,
    eventContainerRef,
    contentRef,
    activeTabRef,
    chatAtBottomRef,
    actorsRef,
    allTabs,
    renderedActorIds,
    resetMountedActorIds,
    handleTabChange,
  };
}
