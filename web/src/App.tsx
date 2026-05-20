import React, { lazy, Suspense, useCallback, useEffect, useMemo } from "react";
import { DropOverlay } from "./components/DropOverlay";
const AppModals = lazy(() => import("./components/AppModals").then((m) => ({ default: m.AppModals })));
import { DoneHubLoginGate } from "./components/DoneHubLoginGate";
const WebPet = lazy(() => import("./features/webPet/WebPet").then((m) => ({ default: m.WebPet })));
import { AppBackground } from "./components/app/AppBackground";
import { AppFeedback } from "./components/app/AppFeedback";
import { AppShell } from "./components/app/AppShell";
import { useTextScale } from "./hooks/useTextScale";
import { useTheme } from "./hooks/useTheme";
import { useActorActions } from "./hooks/useActorActions";
import { useSelectedGroupRuntime } from "./hooks/useSelectedGroupRuntime";
import { useSSE } from "./hooks/useSSE";
import { useDragDrop } from "./hooks/useDragDrop";
import { useGroupActions } from "./hooks/useGroupActions";
import { useSwipeNavigation } from "./hooks/useSwipeNavigation";
import { useCrossGroupRecipients } from "./hooks/useCrossGroupRecipients";
import { useDeepLink } from "./hooks/useDeepLink";
import { useGlobalEvents } from "./hooks/useGlobalEvents";
import { useViewportHeight } from "./hooks/useViewportHeight";
import { useAppChrome } from "./hooks/useAppChrome";
import { useAppGroupLifecycle } from "./hooks/useAppGroupLifecycle";
import { useAppTabState } from "./hooks/useAppTabState";
import * as api from "./services/api";
import { getEffectiveComposerDestGroupId } from "./stores/useComposerStore";
import { getChatSession } from "./stores/useUIStore";
import { buildReplyComposerState } from "./utils/chatReply";
import { subscribeCapabilityChanged } from "./utils/capabilityEvents";
import { filterVisibleRuntimeActors } from "./utils/runtimeVisibility";
import {
  useGroupStore,
  useUIStore,
  useModalStore,
  useComposerStore,
  useFormStore,
  useObservabilityStore,
  useDoneHubStore,
} from "./stores";
import { useChatOutboxStore } from "./stores/chatOutboxStore";
import type { CapabilityOverviewItem, CapabilityStateResult, LedgerEvent, Task, TaskBoardEntry } from "./types";

function countTaskBoardEntries(value: number | TaskBoardEntry[] | undefined): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.max(0, value);
  return Array.isArray(value) ? value.length : 0;
}

function countActiveTasksFromTasks(tasks: Task[] | undefined): number {
  if (!Array.isArray(tasks)) return 0;
  let count = 0;
  for (const task of tasks) {
    if (String(task?.status || "").trim().toLowerCase() === "active") count += 1;
    count += countActiveTasksFromTasks(task?.children);
  }
  return count;
}

function cleanCapabilityId(value: unknown): string {
  return String(value || "").trim();
}

function countEnabledGroupSkillsFromState(
  state: CapabilityStateResult | null | undefined,
  items: CapabilityOverviewItem[] | null | undefined,
): number {
  const groupEnabledIds = new Set<string>();
  const entries = Array.isArray(state?.enabled) ? state.enabled : [];
  for (const row of entries) {
    if (cleanCapabilityId(row?.scope).toLowerCase() !== "group") continue;
    const id = cleanCapabilityId(row?.capability_id);
    if (id) groupEnabledIds.add(id);
  }
  if (!Array.isArray(items) || items.length === 0) return groupEnabledIds.size;
  let count = 0;
  for (const row of items) {
    if (cleanCapabilityId(row?.kind).toLowerCase() !== "skill") continue;
    if (groupEnabledIds.has(cleanCapabilityId(row?.capability_id))) count += 1;
  }
  return count;
}

export default function App() {
  const { theme, setTheme, isDark } = useTheme();
  const { textScale, setTextScale } = useTextScale();
  useViewportHeight();

  const groups = useGroupStore((state) => state.groups);
  const groupOrder = useGroupStore((state) => state.groupOrder);
  const archivedGroupIds = useGroupStore((state) => state.archivedGroupIds);
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const groupDoc = useGroupStore((state) => state.groupDoc);
  const actors = useGroupStore((state) => state.actors);
  const internalRuntimeActorsByGroup = useGroupStore((state) => state.internalRuntimeActorsByGroup);
  const groupContext = useGroupStore((state) => state.groupContext);
  const groupSettings = useGroupStore((state) => state.groupSettings);
  const selectedGroupActorsHydrating = useGroupStore((state) => state.selectedGroupActorsHydrating);
  const setSelectedGroupId = useGroupStore((state) => state.setSelectedGroupId);
  const refreshGroups = useGroupStore((state) => state.refreshGroups);
  const refreshActors = useGroupStore((state) => state.refreshActors);
  const refreshInternalRuntimeActors = useGroupStore((state) => state.refreshInternalRuntimeActors);
  const loadGroup = useGroupStore((state) => state.loadGroup);
  const warmGroup = useGroupStore((state) => state.warmGroup);
  const openChatWindow = useGroupStore((state) => state.openChatWindow);
  const closeChatWindow = useGroupStore((state) => state.closeChatWindow);
  const reorderGroupsInSection = useGroupStore((state) => state.reorderGroupsInSection);
  const archiveGroup = useGroupStore((state) => state.archiveGroup);
  const restoreGroup = useGroupStore((state) => state.restoreGroup);
  const getOrderedGroups = useGroupStore((state) => state.getOrderedGroups);

  const busy = useUIStore((s) => s.busy);
  const errorMsg = useUIStore((s) => s.errorMsg);
  const notice = useUIStore((s) => s.notice);
  const isTransitioning = useUIStore((s) => s.isTransitioning);
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed);
  const sidebarWidth = useUIStore((s) => s.sidebarWidth);
  const activeTab = useUIStore((s) => s.activeTab);
  const chatSessions = useUIStore((s) => s.chatSessions);
  const isSmallScreen = useUIStore((s) => s.isSmallScreen);
  const webReadOnly = useUIStore((s) => s.webReadOnly);
  const showError = useUIStore((s) => s.showError);
  const dismissError = useUIStore((s) => s.dismissError);
  const dismissNotice = useUIStore((s) => s.dismissNotice);
  const setSidebarOpen = useUIStore((s) => s.setSidebarOpen);
  const toggleSidebarCollapsed = useUIStore((s) => s.toggleSidebarCollapsed);
  const setSidebarWidth = useUIStore((s) => s.setSidebarWidth);
  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const setShowScrollButton = useUIStore((s) => s.setShowScrollButton);
  const setChatUnreadCount = useUIStore((s) => s.setChatUnreadCount);
  const setSmallScreen = useUIStore((s) => s.setSmallScreen);
  const setWebReadOnly = useUIStore((s) => s.setWebReadOnly);
  const sseStatus = useUIStore((s) => s.sseStatus);

  const openModal = useModalStore((s) => s.openModal);
  const openContextTarget = useModalStore((s) => s.openContextTarget);
  const openSettingsTarget = useModalStore((s) => s.openSettingsTarget);
  const modalFlags = useModalStore((s) => s.modals);
  const editingActor = useModalStore((s) => s.editingActor);
  const peerRuntimeVisibility = useObservabilityStore((state) => state.peerRuntimeVisibility);
  const petRuntimeVisibility = useObservabilityStore((state) => state.petRuntimeVisibility);

  const doneHubStatus = useDoneHubStore((state) => state.status);
  const doneHubSession = useDoneHubStore((state) => state.session);
  const doneHubErrorMessage = useDoneHubStore((state) => state.errorMessage);
  const doneHubInitialized = useDoneHubStore((state) => state.initialized);
  const initializeDoneHub = useDoneHubStore((state) => state.initialize);
  const refreshDoneHub = useDoneHubStore((state) => state.refresh);
  const doneHubHasSession = Boolean(doneHubSession);
  const doneHubConnected = doneHubStatus === "connected" || doneHubStatus === "refreshing";

  const {
    activeGroupId,
    destGroupId,
    composerFiles,
    replyTarget,
    setDestGroupId,
    setReplyTarget,
    setToText,
  } = useComposerStore();

  const { setNewActorRole, setEditGroupId, setEditGroupTitle, setEditGroupTopic, setDirSuggestions } = useFormStore();
  const clearAllOutbox = useChatOutboxStore((state) => state.clearAll);

  const {
    getTermEpoch,
    toggleActorEnabled,
    relaunchActor,
    editActor,
    removeActor,
    openActorInbox,
  } = useActorActions(selectedGroupId);

  const chatSession = useMemo(
    () => getChatSession(selectedGroupId, chatSessions),
    [selectedGroupId, chatSessions]
  );
  const chatUnreadCount = chatSession.chatUnreadCount;
  const chatSessionAtBottom = chatSession.scrollSnapshot?.mode === "follow";

  const [showMentionMenu, setShowMentionMenu] = React.useState(false);
  const [_mentionFilter, setMentionFilter] = React.useState("");
  const [mentionSelectedIndex, setMentionSelectedIndex] = React.useState(0);
  const [enabledSkillCount, setEnabledSkillCount] = React.useState(0);
  const internalRuntimeActors = useMemo(
    () => internalRuntimeActorsByGroup[String(selectedGroupId || "").trim()] || [],
    [internalRuntimeActorsByGroup, selectedGroupId]
  );
  const refreshRuntimeActors = useCallback(
    async (groupIdArg?: string, opts?: { includeUnread?: boolean }) => {
      const gid = String(groupIdArg || selectedGroupId || "").trim();
      if (!gid) return;
      await refreshActors(gid, opts);
    },
    [refreshActors, selectedGroupId],
  );
  const visibleRuntimeActors = useMemo(
    () =>
      filterVisibleRuntimeActors(
        [
          ...actors,
          ...internalRuntimeActors.filter(
            (actor) => !actors.some((existing) => String(existing.id || "") === String(actor.id || ""))
          ),
        ],
        {
          peerRuntimeVisibility,
          petRuntimeVisibility,
        }
      ),
    [actors, internalRuntimeActors, peerRuntimeVisibility, petRuntimeVisibility]
  );

  useEffect(() => {
    const handlePageHide = () => clearAllOutbox();
    window.addEventListener("pagehide", handlePageHide);
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      clearAllOutbox();
    };
  }, [clearAllOutbox]);

  const {
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
  } = useAppTabState({
    activeTab,
    runtimeActors: visibleRuntimeActors,
    selectedGroupId,
    chatSessionAtBottom,
    isSmallScreen,
    setActiveTab,
    setShowScrollButton,
    setChatUnreadCount,
  });

  React.useEffect(() => {
    if (activeTab === "chat") return;
    if (visibleRuntimeActors.some((actor) => String(actor.id || "") === activeTab)) return;
    setActiveTab("chat");
  }, [activeTab, setActiveTab, visibleRuntimeActors]);

  React.useEffect(() => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid || petRuntimeVisibility !== "visible") {
      return undefined;
    }
    void refreshInternalRuntimeActors(gid);
    const interval = window.setInterval(() => {
      void refreshInternalRuntimeActors(gid);
    }, 60000);
    return () => window.clearInterval(interval);
  }, [selectedGroupId, petRuntimeVisibility, refreshInternalRuntimeActors]);

  const { connectStream, fetchContext, cleanup: cleanupSSE } = useSSE({
    activeTabRef,
    chatAtBottomRef,
    actorsRef,
  });

  const { dropOverlayOpen, handleAppendComposerFiles, resetDragDrop, WEB_MAX_FILE_MB } = useDragDrop({
    selectedGroupId,
  });

  const { handleStartGroup, handleStopGroup, handleSetGroupState } = useGroupActions();

  const computedSendGroupId = getEffectiveComposerDestGroupId(destGroupId, activeGroupId, selectedGroupId);
  const activeTaskCount = useMemo(() => {
    const summaryActive = groupContext?.tasks_summary?.active;
    if (typeof summaryActive === "number" && Number.isFinite(summaryActive)) return Math.max(0, summaryActive);
    const boardActive = countTaskBoardEntries(groupContext?.board?.active);
    if (boardActive > 0) return boardActive;
    return countActiveTasksFromTasks(groupContext?.coordination?.tasks);
  }, [groupContext]);

  const refreshEnabledSkillCount = useCallback(async () => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid) {
      setEnabledSkillCount(0);
      return;
    }
    try {
      const [stateResp, overviewResp] = await Promise.all([
        api.fetchGroupCapabilityState(gid, "user", { noCache: true }),
        api.fetchCapabilityOverview({ includeIndexed: true, limit: 1200 }),
      ]);
      if (!stateResp.ok) {
        setEnabledSkillCount(0);
        return;
      }
      setEnabledSkillCount(countEnabledGroupSkillsFromState(
        stateResp.result,
        overviewResp.ok && Array.isArray(overviewResp.result?.items) ? overviewResp.result.items : null,
      ));
    } catch {
      setEnabledSkillCount(0);
    }
  }, [selectedGroupId]);

  useEffect(() => {
    void refreshEnabledSkillCount();
  }, [refreshEnabledSkillCount]);

  useEffect(() => {
    return subscribeCapabilityChanged(selectedGroupId, () => {
      void refreshEnabledSkillCount();
    });
  }, [refreshEnabledSkillCount, selectedGroupId]);

  useEffect(() => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid || groupContext) return;
    void fetchContext(gid, { detail: "summary" });
  }, [fetchContext, groupContext, selectedGroupId]);

  const { recipientActors, recipientActorsBusy } = useCrossGroupRecipients({
    actors,
    groupDoc,
    selectedGroupId,
    composerGroupId: activeGroupId,
    sendGroupId: computedSendGroupId,
  });
  const sendGroupId = computedSendGroupId;

  const startReply = React.useCallback(
    (ev: LedgerEvent) => {
      const replyComposerState = buildReplyComposerState(ev, selectedGroupId, actors, groupSettings);
      if (!replyComposerState) return;

      if (replyComposerState.destGroupId) {
        setDestGroupId(replyComposerState.destGroupId);
      }
      setToText(replyComposerState.toText);
      setReplyTarget(replyComposerState.replyTarget);
      requestAnimationFrame(() => composerRef.current?.focus());
    },
    [selectedGroupId, actors, composerRef, groupSettings, setDestGroupId, setReplyTarget, setToText]
  );

  const { parseUrlDeepLink } = useDeepLink({
    groups,
    selectedGroupId,
    setSelectedGroupId,
    setActiveTab,
    openChatWindow,
    showError,
  });

  useGlobalEvents({
    refreshGroups,
    refreshActors: refreshRuntimeActors,
    selectedGroupId,
  });

  const { canManageGroups, ccccHome, fetchDirSuggestions } = useAppChrome({
    parseUrlDeepLink,
    refreshGroups,
    setWebReadOnly,
    setSmallScreen,
    showError,
    setDirSuggestions,
    groupEditOpen: modalFlags.groupEdit,
    addActorOpen: modalFlags.addActor,
    editingActor,
  });

  const { handleTouchStart, handleTouchEnd } = useSwipeNavigation({
    tabs: allTabs,
    activeTab,
    onTabChange: handleTabChange,
  });

  const {
    selectedGroupRunning,
    selectedGroupRuntimeStatus,
  } = useSelectedGroupRuntime({
    groups,
    selectedGroupId,
    groupDoc,
    actors,
  });

  const hasForeman = useMemo(() => actors.some((a) => a.role === "foreman"), [actors]);
  const orderedGroups = useMemo(() => getOrderedGroups(), [groups, groupOrder, getOrderedGroups]);

  const groupLabelById = useMemo(() => {
    const out: Record<string, string> = {};
    for (const g of groups || []) {
      const gid = String(g.group_id || "").trim();
      if (!gid) continue;
      const title = String(g.title || "").trim();
      out[gid] = title || gid;
    }
    return out;
  }, [groups]);

  const hasReplyTarget = !!replyTarget;
  const hasComposerFiles = composerFiles.length > 0;

  useAppGroupLifecycle({
    selectedGroupId,
    destGroupId,
    sendGroupId,
    hasReplyTarget,
    hasComposerFiles,
    setDestGroupId,
    fileInputRef,
    resetDragDrop,
    resetMountedActorIds,
    setActiveTab,
    closeChatWindow,
    loadGroup,
    connectStream,
    cleanupSSE,
  });

  React.useEffect(() => {
    void initializeDoneHub();
    void useObservabilityStore.getState().load();
  }, [initializeDoneHub]);

  React.useEffect(() => {
    if (!doneHubInitialized) return;
    if (!(doneHubConnected || doneHubHasSession)) return;
    const timer = window.setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      void refreshDoneHub();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [doneHubConnected, doneHubHasSession, doneHubInitialized, refreshDoneHub]);

  const doneHub = useMemo(() => ({
    status: doneHubStatus,
    displayName: doneHubSession?.display_name || doneHubSession?.username || "",
    group: doneHubSession?.group || "",
    quota: doneHubSession?.quota ?? null,
    errorMessage: doneHubErrorMessage,
  }), [doneHubErrorMessage, doneHubSession?.display_name, doneHubSession?.group, doneHubSession?.quota, doneHubSession?.username, doneHubStatus]);

  if (!doneHubInitialized || (!doneHubConnected && !doneHubHasSession)) {
    return <DoneHubLoginGate isDark={isDark} />;
  }

  return (
    <div
      className={`relative w-full overflow-hidden ${
        isDark ? "bg-black text-slate-100" : "bg-gradient-to-br from-slate-50 via-white to-slate-100"
      }`}
      style={{ height: "calc(100% - var(--vk-offset, 0px))" }}
    >
      <AppBackground isDark={isDark} />

      <AppShell
        orderedGroups={orderedGroups}
        archivedGroupIds={archivedGroupIds}
        selectedGroupId={selectedGroupId}
        groupDoc={groupDoc}
        groupContext={groupContext}
        actors={actors}
        runtimeActors={visibleRuntimeActors}
        recipientActors={recipientActors}
        recipientActorsBusy={recipientActorsBusy}
        renderedActorIds={renderedActorIds}
        activeTab={activeTab}
        busy={busy}
        doneHub={doneHub}
        isTransitioning={isTransitioning}
        sidebarOpen={sidebarOpen}
        sidebarCollapsed={sidebarCollapsed}
        sidebarWidth={sidebarWidth}
        isDark={isDark}
        isSmallScreen={isSmallScreen}
        webReadOnly={webReadOnly}
        selectedGroupRunning={selectedGroupRunning}
        selectedGroupRuntimeStatus={selectedGroupRuntimeStatus}
        selectedGroupActorsHydrating={selectedGroupActorsHydrating}
        theme={theme}
        textScale={textScale}
        sseStatus={sseStatus}
        groupLabelById={groupLabelById}
        chatUnreadCount={chatUnreadCount}
        enabledSkillCount={enabledSkillCount}
        activeTaskCount={activeTaskCount}
        mentionSelectedIndex={mentionSelectedIndex}
        showMentionMenu={showMentionMenu}
        composerRef={composerRef}
        fileInputRef={fileInputRef}
        eventContainerRef={eventContainerRef}
        contentRef={contentRef}
        chatAtBottomRef={chatAtBottomRef}
        onThemeChange={setTheme}
        onTextScaleChange={setTextScale}
        onSelectGroup={setSelectedGroupId}
        onWarmGroup={(gid) => void warmGroup(gid)}
        onCreateGroup={
          !webReadOnly && canManageGroups
            ? () => {
                openModal("createGroup");
                void fetchDirSuggestions();
              }
            : undefined
        }
        onCloseSidebar={() => setSidebarOpen(false)}
        onToggleSidebar={toggleSidebarCollapsed}
        onResizeSidebar={setSidebarWidth}
        onReorderGroupsInSection={reorderGroupsInSection}
        onArchiveGroup={archiveGroup}
        onRestoreGroup={restoreGroup}
        onOpenSidebar={() => setSidebarOpen(true)}
        onOpenGroupEdit={
          canManageGroups
            ? (groupId?: string) => {
                const targetGroupId = String(groupId || selectedGroupId || "").trim();
                const targetGroup = groups.find((g) => String(g.group_id || "").trim() === targetGroupId);
                const targetDoc = targetGroupId === selectedGroupId ? groupDoc : null;
                if (!targetGroupId || (!targetGroup && !targetDoc)) return;
                setEditGroupId(targetGroupId);
                setEditGroupTitle(targetDoc?.title || targetGroup?.title || "");
                setEditGroupTopic(targetDoc?.topic || targetGroup?.topic || "");
                openModal("groupEdit");
              }
            : undefined
        }
        onOpenSearch={() => openModal("search")}
        onOpenContext={() => {
          if (selectedGroupId && !groupContext) void fetchContext(selectedGroupId);
          openModal("context");
        }}
        onOpenContextProject={() => {
          if (selectedGroupId && !groupContext) void fetchContext(selectedGroupId);
          openContextTarget({ tab: "project", projectMode: "edit" });
        }}
        onOpenContextSummary={() => {
          if (selectedGroupId && !groupContext) void fetchContext(selectedGroupId);
          openContextTarget({ tab: "tasks" });
        }}
        onOpenSkillManagement={() => {
          if (selectedGroupId && !groupContext) void fetchContext(selectedGroupId);
          openContextTarget({ tab: "skills" });
        }}
        onStartGroup={handleStartGroup}
        onStopGroup={handleStopGroup}
        onSetGroupState={handleSetGroupState}
        onOpenSettings={() => openModal("settings")}
        onOpenDoneHubAuth={() => openModal("doneHubAuth")}
        onOpenMobileMenu={() => openModal("mobileMenu")}
        onTabChange={handleTabChange}
        onAddAgent={
          webReadOnly
            ? undefined
            : () => {
                setNewActorRole(hasForeman ? "peer" : "foreman");
                openModal("addActor");
              }
        }
        appendComposerFiles={handleAppendComposerFiles}
        setMentionFilter={setMentionFilter}
        setMentionSelectedIndex={setMentionSelectedIndex}
        setShowMentionMenu={setShowMentionMenu}
        getTermEpoch={getTermEpoch}
        onToggleActorEnabled={toggleActorEnabled}
        onRelaunchActor={relaunchActor}
        onEditActor={editActor}
        onRemoveActor={removeActor}
        onOpenActorInbox={openActorInbox}
        onRefreshActors={() => void refreshRuntimeActors(selectedGroupId)}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
      />

      {selectedGroupId ? (
        <Suspense fallback={null}>
          <WebPet key={selectedGroupId} groupId={selectedGroupId} />
        </Suspense>
      ) : null}

      <AppFeedback
        isDark={isDark}
        webReadOnly={webReadOnly}
        errorMsg={errorMsg}
        notice={notice}
        dismissError={dismissError}
        dismissNotice={dismissNotice}
      />

      <Suspense fallback={null}>
        <AppModals
          isDark={isDark}
          theme={theme}
          textScale={textScale}
          readOnly={webReadOnly}
          ccccHome={ccccHome}
          composerRef={composerRef}
          onStartReply={startReply}
          onThemeChange={setTheme}
          onTextScaleChange={setTextScale}
          onStartGroup={handleStartGroup}
          onStopGroup={handleStopGroup}
          onSetGroupState={handleSetGroupState}
          fetchContext={fetchContext}
          canManageGroups={canManageGroups}
        />
      </Suspense>

      <DropOverlay isOpen={dropOverlayOpen} isDark={isDark} maxFileMb={WEB_MAX_FILE_MB} />
    </div>
  );
}
