import React, { useEffect, useMemo, useRef, useState } from "react";
import type { Actor } from "../types";

type UseAppTabStateOptions = {
  activeTab: string;
  actors: Actor[];
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
  actors,
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
  const activeTabRef = useRef<string>("chat");
  const chatAtBottomRef = useRef<boolean>(true);
  const actorsRef = useRef<Actor[]>([]);
  const [mountedActorIds, setMountedActorIds] = useState<string[]>([]);

  const allTabs = useMemo(() => ["chat", ...actors.map((actor) => actor.id)], [actors]);

  const handleTabChange = React.useCallback(
    (newTab: string) => {
      if (newTab !== "chat") {
        setMountedActorIds((prev) => (prev.includes(newTab) ? prev : [...prev, newTab]));
      }
      setActiveTab(newTab);
    },
    [setActiveTab]
  );

  useEffect(() => {
    activeTabRef.current = activeTab;
    if (activeTab !== "chat") return;
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

    const threshold = 100;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
    chatAtBottomRef.current = atBottom;
    setShowScrollButton(selectedGroupId, !atBottom);
    if (atBottom) setChatUnreadCount(selectedGroupId, 0);
  }, [activeTab, selectedGroupId, chatSessionAtBottom, setChatUnreadCount, setShowScrollButton]);

  useEffect(() => {
    if (activeTab !== "chat") return;
    if (isSmallScreen) return;
    requestAnimationFrame(() => composerRef.current?.focus());
  }, [activeTab, isSmallScreen]);

  useEffect(() => {
    actorsRef.current = actors;
  }, [actors]);

  const renderedActorIds = useMemo(() => {
    const live = new Set(actors.map((actor) => String(actor.id || "")).filter((id) => id));
    const mountedLiveIds = mountedActorIds.filter((id) => live.has(id));
    if (activeTab !== "chat" && live.has(activeTab) && !mountedLiveIds.includes(activeTab)) {
      return [...mountedLiveIds, activeTab];
    }
    return mountedLiveIds;
  }, [mountedActorIds, activeTab, actors]);

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
