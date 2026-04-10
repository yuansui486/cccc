import { useEffect, type CSSProperties } from "react";
import { ErrorBoundary } from "../ErrorBoundary";
import { AppHeader } from "../layout/AppHeader";
import { GroupSidebar } from "../layout/GroupSidebar";
import { ActorTab } from "../../pages/ActorTab";
import { ChatTab } from "../../pages/chat";
import type { Actor, GroupContext, GroupDoc, GroupMeta, GroupRuntimeStatus, TextScale } from "../../types";
import { SIDEBAR_COLLAPSED_WIDTH } from "../../stores/useUIStore";

type AppShellProps = {
  orderedGroups: GroupMeta[];
  archivedGroupIds: string[];
  groups: GroupMeta[];
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  groupContext: GroupContext | null;
  actors: Actor[];
  recipientActors: Actor[];
  recipientActorsBusy: boolean;
  destGroupScopeLabel: string;
  renderedActorIds: string[];
  activeTab: string;
  busy: string;
  doneHub?: {
    status: "idle" | "authenticating" | "connected" | "refreshing" | "error";
    displayName: string;
    group?: string;
    quota: number | null;
    errorMessage: string;
  };
  isTransitioning: boolean;
  sidebarOpen: boolean;
  sidebarCollapsed: boolean;
  sidebarWidth: number;
  isDark: boolean;
  isSmallScreen: boolean;
  webReadOnly: boolean;
  selectedGroupRunning: boolean;
  selectedGroupRuntimeStatus: GroupRuntimeStatus | null;
  selectedGroupActorsHydrating: boolean;
  theme: "light" | "dark" | "system";
  textScale: TextScale;
  sseStatus: "connected" | "connecting" | "disconnected";
  groupLabelById: Record<string, string>;
  chatUnreadCount: number;
  mentionSelectedIndex: number;
  showMentionMenu: boolean;
  composerRef: React.RefObject<HTMLTextAreaElement | null>;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  eventContainerRef: React.MutableRefObject<HTMLDivElement | null>;
  contentRef: React.MutableRefObject<HTMLDivElement | null>;
  chatAtBottomRef: React.MutableRefObject<boolean>;
  onThemeChange: (theme: "light" | "dark" | "system") => void;
  onTextScaleChange: (scale: TextScale) => void;
  onSelectGroup: (groupId: string) => void;
  onWarmGroup: (groupId: string) => void;
  onCreateGroup: (() => void) | undefined;
  onCloseSidebar: () => void;
  onToggleSidebar: () => void;
  onResizeSidebar: (width: number) => void;
  onReorderGroupsInSection: (section: "working" | "archived", fromIndex: number, toIndex: number) => void;
  onArchiveGroup: (groupId: string) => void;
  onRestoreGroup: (groupId: string) => void;
  onOpenSidebar: () => void;
  onOpenGroupEdit: (() => void) | undefined;
  onOpenSearch: () => void;
  onOpenContext: () => void;
  onStartGroup: () => void;
  onStopGroup: () => void;
  onSetGroupState: (state: "active" | "idle" | "paused") => void;
  onOpenSettings: () => void;
  onOpenDoneHubAuth: () => void;
  onOpenMobileMenu: () => void;
  onTabChange: (tab: string) => void;
  onAddAgent: (() => void) | undefined;
  appendComposerFiles: (files: File[]) => void;
  setMentionFilter: React.Dispatch<React.SetStateAction<string>>;
  setMentionSelectedIndex: React.Dispatch<React.SetStateAction<number>>;
  setShowMentionMenu: React.Dispatch<React.SetStateAction<boolean>>;
  getTermEpoch: (actorId: string) => number;
  onToggleActorEnabled: (actor: Actor) => void;
  onRelaunchActor: (actor: Actor) => void;
  onEditActor: (actor: Actor) => void;
  onRemoveActor: (actor: Actor, activeTab: string) => void;
  onOpenActorInbox: (actor: Actor) => void;
  onRefreshActors: () => void;
  onTouchStart: (event: React.TouchEvent) => void;
  onTouchEnd: (event: React.TouchEvent) => void;
};

export function AppShell({
  orderedGroups,
  archivedGroupIds,
  groups,
  selectedGroupId,
  groupDoc,
  groupContext,
  actors,
  recipientActors,
  recipientActorsBusy,
  destGroupScopeLabel,
  renderedActorIds,
  activeTab,
  busy,
  doneHub,
  isTransitioning,
  sidebarOpen,
  sidebarCollapsed,
  sidebarWidth,
  isDark,
  isSmallScreen,
  webReadOnly,
  selectedGroupRunning,
  selectedGroupRuntimeStatus,
  selectedGroupActorsHydrating,
  theme,
  textScale,
  sseStatus,
  groupLabelById,
  chatUnreadCount,
  mentionSelectedIndex,
  showMentionMenu,
  composerRef,
  fileInputRef,
  eventContainerRef,
  contentRef,
  chatAtBottomRef,
  onThemeChange,
  onTextScaleChange,
  onSelectGroup,
  onWarmGroup,
  onCreateGroup,
  onCloseSidebar,
  onToggleSidebar,
  onResizeSidebar,
  onReorderGroupsInSection,
  onArchiveGroup,
  onRestoreGroup,
  onOpenSidebar,
  onOpenGroupEdit,
  onOpenSearch,
  onOpenContext,
  onStartGroup,
  onStopGroup,
  onSetGroupState,
  onOpenSettings,
  onOpenDoneHubAuth,
  onOpenMobileMenu,
  onTabChange,
  onAddAgent,
  appendComposerFiles,
  setMentionFilter,
  setMentionSelectedIndex,
  setShowMentionMenu,
  getTermEpoch,
  onToggleActorEnabled,
  onRelaunchActor,
  onEditActor,
  onRemoveActor,
  onOpenActorInbox,
  onRefreshActors,
  onTouchStart,
  onTouchEnd,
}: AppShellProps) {
  const shellStyle = {
    "--sidebar-width": `${sidebarCollapsed ? SIDEBAR_COLLAPSED_WIDTH : sidebarWidth}px`,
  } as CSSProperties;

  useEffect(() => {
    if (!selectedGroupId || actors.length === 0) return;
    if (typeof window === "undefined") return;

    const nav = navigator as Navigator & {
      connection?: {
        saveData?: boolean;
        effectiveType?: string;
      };
    };
    const connection = nav.connection;
    if (connection?.saveData) return;
    if (typeof connection?.effectiveType === "string" && /(^|-)2g$/.test(connection.effectiveType)) return;

    let cancelled = false;
    let timeoutId: ReturnType<typeof globalThis.setTimeout> | null = null;
    let idleId: number | null = null;
    const preloadActorTab = () => {
      void import("../AgentTab").then(() => {
        if (cancelled) return;
      });
    };

    if ("requestIdleCallback" in window) {
      idleId = window.requestIdleCallback(() => preloadActorTab(), { timeout: 1500 });
    } else {
      timeoutId = globalThis.setTimeout(() => preloadActorTab(), 600);
    }

    return () => {
      cancelled = true;
      if (idleId !== null && "cancelIdleCallback" in window) {
        window.cancelIdleCallback(idleId);
      }
      if (timeoutId !== null) {
        globalThis.clearTimeout(timeoutId);
      }
    };
  }, [selectedGroupId, actors.length]);

  return (
    <div
      className="relative h-full min-h-0 transition-[grid-template-columns] duration-300 ease-out md:grid md:[grid-template-columns:var(--sidebar-width)_minmax(0,1fr)]"
      style={shellStyle}
    >
      <GroupSidebar
        orderedGroups={orderedGroups}
        archivedGroupIds={archivedGroupIds}
        selectedGroupId={selectedGroupId}
        actors={actors}
        activeTab={activeTab}
        unreadChatCount={chatUnreadCount}
        isOpen={sidebarOpen}
        isCollapsed={sidebarCollapsed}
        sidebarWidth={sidebarWidth}
        isDark={isDark}
        readOnly={webReadOnly}
        onSelectGroup={onSelectGroup}
        onWarmGroup={onWarmGroup}
        onCreateGroup={onCreateGroup}
        onClose={onCloseSidebar}
        onToggleCollapse={onToggleSidebar}
        onResizeWidth={onResizeSidebar}
        onReorderSection={onReorderGroupsInSection}
        onArchiveGroup={onArchiveGroup}
        onRestoreGroup={onRestoreGroup}
        onTabChange={onTabChange}
        onAddAgent={onAddAgent}
      />

      <main
        className={`absolute inset-0 flex h-full min-h-0 flex-col overflow-hidden md:relative md:inset-auto ${
          isDark ? "bg-black/75" : "bg-white/80"
        }`}
      >
        <AppHeader
          isDark={isDark}
          theme={theme}
          textScale={textScale}
          onThemeChange={onThemeChange}
          onTextScaleChange={onTextScaleChange}
          webReadOnly={webReadOnly}
          selectedGroupId={selectedGroupId}
          groupDoc={groupDoc}
          selectedGroupRunning={selectedGroupRunning}
          selectedGroupRuntimeStatus={selectedGroupRuntimeStatus}
          actors={actors}
          sseStatus={sseStatus}
          busy={busy}
          doneHub={doneHub}
          onOpenSidebar={onOpenSidebar}
          onOpenGroupEdit={onOpenGroupEdit}
          onOpenSearch={onOpenSearch}
          onOpenContext={onOpenContext}
          onStartGroup={onStartGroup}
          onStopGroup={onStopGroup}
          onSetGroupState={onSetGroupState}
          onOpenSettings={onOpenSettings}
          onOpenDoneHubAuth={onOpenDoneHubAuth}
          onOpenMobileMenu={onOpenMobileMenu}
        />

        <div
          ref={contentRef}
          className={`relative flex min-h-0 flex-1 flex-col overflow-hidden transition-opacity duration-150 ${
            isTransitioning ? "opacity-0" : "opacity-100"
          }`}
          onTouchStart={onTouchStart}
          onTouchEnd={onTouchEnd}
        >
          <div
            className={`absolute inset-0 flex min-h-0 flex-col ${
              activeTab === "chat" ? "" : "invisible pointer-events-none"
            }`}
            aria-hidden={activeTab !== "chat"}
          >
            <ErrorBoundary>
              <ChatTab
                isDark={isDark}
                isSmallScreen={isSmallScreen}
                readOnly={webReadOnly}
                selectedGroupId={selectedGroupId}
                selectedGroupRunning={selectedGroupRunning}
                selectedGroupActorsHydrating={selectedGroupActorsHydrating}
                groupLabelById={groupLabelById}
                actors={actors}
                runtimeActors={[]}
                groups={groups}
                recipientActors={recipientActors}
                recipientActorsBusy={recipientActorsBusy}
                destGroupScopeLabel={destGroupScopeLabel}
                scrollRef={eventContainerRef}
                composerRef={composerRef}
                fileInputRef={fileInputRef}
                chatAtBottomRef={chatAtBottomRef}
                appendComposerFiles={appendComposerFiles}
                onStartGroup={onStartGroup}
                onOpenRuntimeActor={onTabChange}
                showMentionMenu={showMentionMenu}
                setShowMentionMenu={setShowMentionMenu}
                mentionSelectedIndex={mentionSelectedIndex}
                setMentionSelectedIndex={setMentionSelectedIndex}
                setMentionFilter={setMentionFilter}
              />
            </ErrorBoundary>
          </div>

          <div
            className={`absolute inset-0 flex min-h-0 flex-col ${
              activeTab === "chat" ? "invisible pointer-events-none" : ""
            }`}
            aria-hidden={activeTab === "chat"}
          >
            {renderedActorIds.map((actorId) => {
              const actor = actors.find((item) => item.id === actorId) || null;
              const isVisible = activeTab === actorId && activeTab !== "chat";
              const agentState =
                (groupContext?.agent_states || []).find((item) => item.id === (actor?.id || "")) || null;

              return (
                <div key={actorId} className={isVisible ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
                  <ErrorBoundary>
                    <ActorTab
                      actor={actor}
                      groupId={selectedGroupId}
                      agentState={agentState}
                      termEpoch={actor ? getTermEpoch(actor.id) : 0}
                      busy={busy}
                      isDark={isDark}
                      isSmallScreen={isSmallScreen}
                      isVisible={isVisible}
                      readOnly={webReadOnly}
                      onToggleEnabled={() => actor && onToggleActorEnabled(actor)}
                      onRelaunch={() => actor && onRelaunchActor(actor)}
                      onEdit={() => actor && onEditActor(actor)}
                      onRemove={() => actor && onRemoveActor(actor, activeTab)}
                      onInbox={() => actor && onOpenActorInbox(actor)}
                      onStatusChange={onRefreshActors}
                    />
                  </ErrorBoundary>
                </div>
              );
            })}
          </div>
        </div>
      </main>
    </div>
  );
}
