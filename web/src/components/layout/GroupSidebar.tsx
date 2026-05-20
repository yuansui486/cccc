import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { Actor, GroupDoc, GroupMeta, GroupRuntimeStatus, TextScale, Theme } from "../../types";
import { classNames } from "../../utils/classNames";
import { getAppBrandName, getAppLogoPath } from "../../utils/displayText";
import { getActorDisplayWorkingState } from "../../utils/terminalWorkingState";
import { getRuntimeIndicatorState } from "../../utils/statusIndicators";
import { getGroupStatusFromSource } from "../../utils/groupStatus";
import { getGroupControlVisual, getLaunchControlMode, resolveGroupControls } from "../../utils/groupControls";
import { formatDoneHubQuota } from "../../services/doneHub";
import { updateGroup } from "../../services/api";
import {
  useBrandingStore,
  useGroupStore,
  useTerminalSignalsStore,
  getTerminalSignalKey,
} from "../../stores";
import {
  CloseIcon,
  FolderIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ClipboardIcon,
  MoreIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RocketIcon,
  SendIcon,
  SettingsIcon,
  SparklesIcon,
  StopIcon,
  TeamIcon,
} from "../Icons";
import { TextScaleSwitcher } from "../TextScaleSwitcher";
import { ThemeToggleCompact } from "../ThemeToggle";
import { ActorAvatar } from "../ActorAvatar";
import { SortableGroupItem } from "./SortableGroupItem";
import { SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH } from "../../stores/useUIStore";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";

export interface GroupSidebarProps {
  orderedGroups: GroupMeta[];
  archivedGroupIds: string[];
  selectedGroupId: string;
  actors: Actor[];
  activeTab: string;
  unreadChatCount: number;
  theme: Theme;
  textScale: TextScale;
  doneHub?: {
    status: "idle" | "authenticating" | "connected" | "refreshing" | "error";
    displayName: string;
    group?: string;
    quota: number | null;
    errorMessage: string;
  };
  groupDoc: GroupDoc | null;
  activeTaskCount: number;
  selectedGroupRunning: boolean;
  selectedGroupRuntimeStatus: GroupRuntimeStatus | null;
  busy: string;
  sseStatus: "connected" | "connecting" | "disconnected";
  isOpen: boolean;
  isCollapsed: boolean;
  sidebarWidth: number;
  isDark: boolean;
  readOnly?: boolean;
  onThemeChange: (theme: Theme) => void;
  onTextScaleChange: (scale: TextScale) => void;
  onSelectGroup: (groupId: string) => void;
  onWarmGroup?: (groupId: string) => void;
  onCreateGroup?: () => void;
  onClose: () => void;
  onToggleCollapse: () => void;
  onResizeWidth: (width: number) => void;
  onReorderSection: (section: "working" | "archived", fromIndex: number, toIndex: number) => void;
  onArchiveGroup: (groupId: string) => void;
  onRestoreGroup: (groupId: string) => void;
  onTabChange: (tab: string) => void;
  onAddAgent?: () => void;
  onOpenContext: () => void;
  onOpenContextProject: () => void;
  onOpenContextSummary: () => void;
  onOpenSkillManagement: () => void;
  onOpenSettings: () => void;
  onOpenDoneHubAuth: () => void;
  onOpenGroupEdit?: (groupId?: string) => void;
  onStartGroup: () => void;
  onStopGroup: () => void;
  onSetGroupState: (state: "active" | "idle" | "paused") => void | Promise<void>;
}

export function GroupSidebar({
  orderedGroups,
  archivedGroupIds,
  selectedGroupId,
  actors,
  activeTab,
  unreadChatCount,
  theme,
  textScale,
  doneHub,
  groupDoc,
  activeTaskCount,
  selectedGroupRunning,
  selectedGroupRuntimeStatus,
  busy,
  sseStatus,
  isOpen,
  isCollapsed,
  sidebarWidth,
  isDark,
  readOnly,
  onThemeChange,
  onTextScaleChange,
  onSelectGroup,
  onWarmGroup,
  onCreateGroup,
  onClose,
  onToggleCollapse,
  onResizeWidth,
  onReorderSection,
  onArchiveGroup,
  onRestoreGroup,
  onTabChange,
  onAddAgent,
  onOpenContext,
  onOpenContextProject,
  onOpenContextSummary,
  onOpenSkillManagement,
  onOpenSettings,
  onOpenDoneHubAuth,
  onOpenGroupEdit,
  onStartGroup,
  onStopGroup,
  onSetGroupState,
}: GroupSidebarProps) {
  const { t } = useTranslation("layout");
  const appBrandName = getAppBrandName();
  const appLogoPath = getAppLogoPath();
  const branding = useBrandingStore((s) => s.branding);
  const refreshGroups = useGroupStore((s) => s.refreshGroups);
  const setGroups = useGroupStore((s) => s.setGroups);
  const setGroupDoc = useGroupStore((s) => s.setGroupDoc);
  const terminalSignals = useTerminalSignalsStore((state) => state.signals);
  const brandName = String(branding.product_name || "").trim() || appBrandName;
  const logoPath = String(branding.logo_icon_url || "").trim() || appLogoPath;
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const switcherContainerRef = useRef<HTMLElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const renameSavingRef = useRef(false);
  const skipRenameBlurCommitRef = useRef(false);
  const switcherButtonRef = useRef<HTMLDivElement | null>(null);
  const switcherPanelRef = useRef<HTMLDivElement | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [isRenamingGroupTitle, setIsRenamingGroupTitle] = useState(false);
  const [groupTitleDraft, setGroupTitleDraft] = useState("");
  const [groupTitleSaving, setGroupTitleSaving] = useState(false);
  const archivedSet = useMemo(() => new Set(archivedGroupIds), [archivedGroupIds]);
  const workingGroups = useMemo(
    () => orderedGroups.filter((g) => !archivedSet.has(String(g.group_id || "").trim())),
    [archivedSet, orderedGroups]
  );
  const archivedGroups = useMemo(
    () => orderedGroups.filter((g) => archivedSet.has(String(g.group_id || "").trim())),
    [archivedSet, orderedGroups]
  );
  const [archivedOpen, setArchivedOpen] = useState(
    () =>
      archivedGroups.some((g) => String(g.group_id || "").trim() === String(selectedGroupId || "").trim()) ||
      (orderedGroups.length > 0 && workingGroups.length === 0 && archivedGroups.length > 0)
  );
  const selectedArchived = useMemo(
    () => archivedGroups.some((g) => String(g.group_id || "").trim() === String(selectedGroupId || "").trim()),
    [archivedGroups, selectedGroupId]
  );
  const selectedGroup = useMemo(
    () => orderedGroups.find((g) => String(g.group_id || "").trim() === String(selectedGroupId || "").trim()) || null,
    [orderedGroups, selectedGroupId]
  );
  const autoArchivedOpen = selectedArchived || (orderedGroups.length > 0 && workingGroups.length === 0 && archivedGroups.length > 0);
  const archivedPanelOpen = archivedOpen || autoArchivedOpen;
  const selectedStatus = selectedGroupId ? getGroupStatusFromSource({
    running: selectedGroupRunning,
    state: (selectedGroupRuntimeStatus?.lifecycle_state as GroupDoc["state"] | undefined) || groupDoc?.state,
    runtime_status: selectedGroupRuntimeStatus || undefined,
  }) : null;
  const selectedStatusKey = selectedStatus?.key ?? null;
  const currentGroupTitle = String(selectedGroup?.title || groupDoc?.title || selectedGroupId || "").trim();
  const currentGroupTopic = String(groupDoc?.topic || selectedGroup?.topic || "").trim();
  const doneHubStatus = doneHub?.status || "idle";
  const doneHubConnected = doneHubStatus === "connected" || doneHubStatus === "refreshing";
  const doneHubIsPro = String(doneHub?.group || "").trim().toLowerCase() === "pro";
  const doneHubQuota = doneHub?.quota != null ? formatDoneHubQuota(doneHub.quota) : formatDoneHubQuota(0);
  const doneHubLabel = doneHubConnected
    ? t("doneHubBalanceInline", { value: doneHubQuota })
    : t(doneHubStatus === "error" ? "doneHubErrorShort" : "doneHubConnect");
  const launchMode = getLaunchControlMode(selectedStatusKey);
  const launchControl = getGroupControlVisual(selectedStatusKey, "launch", busy);
  const pauseControl = getGroupControlVisual(selectedStatusKey, "pause", busy);
  const stopControl = getGroupControlVisual(selectedStatusKey, "stop", busy);
  const {
    launchHardUnavailable,
    pauseHardUnavailable,
    stopHardUnavailable,
    launchDisabled,
    pauseDisabled,
    stopDisabled,
  } = resolveGroupControls({
    selectedGroupId,
    actorCount: actors.length,
    statusKey: selectedStatusKey,
    busy,
  });
  const deliveryToggleIsPause = selectedStatusKey === "run";
  const deliveryToggleControl = deliveryToggleIsPause ? pauseControl : launchControl;
  const deliveryToggleDisabled = deliveryToggleIsPause ? pauseDisabled : launchDisabled;
  const deliveryToggleHardUnavailable = deliveryToggleIsPause ? pauseHardUnavailable : launchHardUnavailable;
  const deliveryToggleTitle = deliveryToggleIsPause
    ? t("pauseDelivery")
    : launchMode === "activate"
      ? t("resumeDelivery")
      : t("launchAllAgents");

  useEffect(() => {
    if (!isRenamingGroupTitle) return;
    renameInputRef.current?.focus({ preventScroll: true });
    renameInputRef.current?.select();
  }, [isRenamingGroupTitle]);

  useEffect(() => {
    setIsRenamingGroupTitle(false);
    setGroupTitleSaving(false);
    renameSavingRef.current = false;
    skipRenameBlurCommitRef.current = false;
    setGroupTitleDraft("");
  }, [selectedGroupId]);

  const beginGroupTitleRename = useCallback(() => {
    if (readOnly || !selectedGroupId || groupTitleSaving) return;
    setGroupTitleDraft(currentGroupTitle);
    setSwitcherOpen(false);
    setIsRenamingGroupTitle(true);
  }, [currentGroupTitle, groupTitleSaving, readOnly, selectedGroupId]);

  const cancelGroupTitleRename = useCallback(() => {
    if (renameSavingRef.current) return;
    skipRenameBlurCommitRef.current = true;
    setGroupTitleDraft(currentGroupTitle);
    setIsRenamingGroupTitle(false);
  }, [currentGroupTitle]);

  const commitGroupTitleRename = useCallback(async () => {
    const gid = String(selectedGroupId || "").trim();
    if (!gid || readOnly || renameSavingRef.current) return;

    const nextTitle = String(groupTitleDraft || "").trim();
    const prevTitle = currentGroupTitle;
    if (!nextTitle || nextTitle === prevTitle) {
      setGroupTitleDraft(prevTitle);
      setIsRenamingGroupTitle(false);
      return;
    }

    renameSavingRef.current = true;
    setGroupTitleSaving(true);
    try {
      const resp = await updateGroup(gid, nextTitle, currentGroupTopic);
      if (!resp.ok) {
        console.error(`Failed to rename group ${gid}:`, resp.error);
        window.requestAnimationFrame(() => {
          renameInputRef.current?.focus({ preventScroll: true });
          renameInputRef.current?.select();
        });
        return;
      }

      const store = useGroupStore.getState();
      setGroups(
        store.groups.map((group) =>
          String(group.group_id || "").trim() === gid ? { ...group, title: nextTitle } : group
        )
      );
      if (store.groupDoc && String(store.groupDoc.group_id || "").trim() === gid) {
        setGroupDoc({ ...store.groupDoc, title: nextTitle });
      }
      void refreshGroups();
      setGroupTitleDraft(nextTitle);
      setIsRenamingGroupTitle(false);
    } catch (error) {
      console.error(`Failed to rename group ${gid}:`, error);
      window.requestAnimationFrame(() => {
        renameInputRef.current?.focus({ preventScroll: true });
        renameInputRef.current?.select();
      });
    } finally {
      renameSavingRef.current = false;
      setGroupTitleSaving(false);
    }
  }, [
    currentGroupTitle,
    currentGroupTopic,
    groupTitleDraft,
    readOnly,
    refreshGroups,
    selectedGroupId,
    setGroupDoc,
    setGroups,
  ]);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(TouchSensor, {
      activationConstraint: {
        delay: 200,
        tolerance: 5,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleDragEnd = useCallback(
    (section: "working" | "archived", groups: GroupMeta[]) => (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const ids = groups.map((g) => String(g.group_id || ""));
      const oldIndex = ids.indexOf(String(active.id));
      const newIndex = ids.indexOf(String(over.id));
      if (oldIndex !== -1 && newIndex !== -1) {
        onReorderSection(section, oldIndex, newIndex);
      }
    },
    [onReorderSection]
  );

  useEffect(() => {
    if (!isResizing) return undefined;

    const handlePointerMove = (event: PointerEvent) => {
      const drag = dragStateRef.current;
      if (!drag) return;
      onResizeWidth(drag.startWidth + (event.clientX - drag.startX));
    };

    const finishResize = () => {
      dragStateRef.current = null;
      setIsResizing(false);
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", finishResize);
    window.addEventListener("pointercancel", finishResize);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", finishResize);
      window.removeEventListener("pointercancel", finishResize);
      finishResize();
    };
  }, [isResizing, onResizeWidth]);

  useEffect(() => {
    if (!switcherOpen) return undefined;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (switcherContainerRef.current?.contains(target)) return;
      if (switcherPanelRef.current?.contains(target)) return;
      if (switcherButtonRef.current?.contains(target)) return;
      setSwitcherOpen(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSwitcherOpen(false);
    };

    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [switcherOpen]);

  useEffect(() => {
    if (!isOpen) {
      setSwitcherOpen(false);
      setMoreOpen(false);
    }
  }, [isOpen]);

  const handleResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (isCollapsed) return;
      event.preventDefault();
      event.stopPropagation();
      dragStateRef.current = {
        startX: event.clientX,
        startWidth: sidebarWidth,
      };
      setIsResizing(true);
      document.body.style.setProperty("cursor", "col-resize");
      document.body.style.setProperty("user-select", "none");
    },
    [isCollapsed, sidebarWidth]
  );

  const handleTabSelect = useCallback(
    (tab: string) => {
      onTabChange(tab);
      if (window.matchMedia("(max-width: 767px)").matches) onClose();
    },
    [onClose, onTabChange]
  );

  const handleLaunchClick = useCallback(() => {
    if (launchDisabled || selectedStatusKey === "run") return;
    if (launchMode === "activate") {
      void onSetGroupState("active");
      return;
    }
    onStartGroup();
  }, [launchDisabled, launchMode, onSetGroupState, onStartGroup, selectedStatusKey]);

  const handlePauseClick = useCallback(() => {
    if (pauseDisabled || selectedStatusKey === "paused") return;
    void onSetGroupState("paused");
  }, [onSetGroupState, pauseDisabled, selectedStatusKey]);

  const handleStopClick = useCallback(() => {
    if (stopDisabled || selectedStatusKey === "stop") return;
    onStopGroup();
  }, [onStopGroup, selectedStatusKey, stopDisabled]);

  const renderGroupList = useCallback(
    (groups: GroupMeta[], section: "working" | "archived") => {
      const sortableIds = groups.map((g) => String(g.group_id || ""));
      const isArchivedSection = section === "archived";
      return (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd(section, groups)}
        >
          <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
            <div className="space-y-1">
              {groups.map((g) => {
                const gid = String(g.group_id || "");
                const active = gid === selectedGroupId;
                return (
                  <SortableGroupItem
                    key={gid}
                    group={g}
                    isActive={active}
                    isDark={isDark}
                    isCollapsed={false}
                    isArchived={isArchivedSection}
                    dragDisabled={!!readOnly}
                    menuInlineAction
                    menuActionLabel={isArchivedSection ? t("restoreGroup") : t("archiveGroup")}
                    menuAriaLabel={`${t("groupActions")} · ${g.title || gid}`}
                    secondaryInlineActionLabel={!isArchivedSection && !readOnly && onOpenGroupEdit ? t("edit") : undefined}
                    secondaryInlineActionAriaLabel={!isArchivedSection && !readOnly && onOpenGroupEdit ? `${t("editGroup")} · ${g.title || gid}` : undefined}
                    onSecondaryInlineAction={!isArchivedSection && !readOnly && onOpenGroupEdit ? () => onOpenGroupEdit(gid) : undefined}
                    onMenuAction={
                      isArchivedSection
                        ? () => onRestoreGroup(gid)
                        : () => {
                            setArchivedOpen(true);
                            onArchiveGroup(gid);
                          }
                    }
                    onSelect={() => {
                      setSwitcherOpen(false);
                      onSelectGroup(gid);
                      onTabChange("chat");
                      if (window.matchMedia("(max-width: 767px)").matches) onClose();
                    }}
                    onWarm={active ? undefined : () => onWarmGroup?.(gid)}
                  />
                );
              })}
            </div>
          </SortableContext>
        </DndContext>
      );
    },
    [
      handleDragEnd,
      isDark,
      onArchiveGroup,
      onClose,
      onOpenGroupEdit,
      onRestoreGroup,
      onSelectGroup,
      onTabChange,
      onWarmGroup,
      readOnly,
      selectedGroupId,
      sensors,
      t,
    ]
  );

  const getActorIndicator = useCallback(
    (actor: Actor) => {
      const terminalSignal = terminalSignals[getTerminalSignalKey(selectedGroupId, actor.id)];
      const workingState = getActorDisplayWorkingState(actor, terminalSignal);
      const isRunning = actor.running ?? actor.enabled ?? false;
      const indicator = getRuntimeIndicatorState({ isRunning: Boolean(isRunning), workingState });
      const tone = indicator.tone === "working" ? "working" : indicator.tone === "run" ? "run" : "stop";
      return {
        ...indicator,
        statusLabel: t(`actorStatus.${tone}`),
        statusBadgeClass:
          tone === "working"
            ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
            : tone === "run"
              ? "bg-sky-500/10 text-sky-700 dark:text-sky-300"
              : "bg-slate-500/12 text-[var(--color-text-tertiary)]",
      };
    },
    [selectedGroupId, t, terminalSignals]
  );

  const navButtonClass = useCallback(
    (isActive: boolean) =>
      classNames(
        "flex min-h-[52px] w-full items-center gap-4 rounded-xl px-3 py-2 text-left text-sm font-medium transition-colors",
        isActive
          ? "bg-black/[0.08] text-[var(--color-text-secondary)] dark:bg-white/[0.10]"
          : "text-[var(--color-text-secondary)] hover:bg-black/[0.045] dark:hover:bg-white/[0.07]"
      ),
    []
  );

  const renderCountBadge = useCallback(
    (count: number, options?: { showZero?: boolean }) => {
      const safeCount = Math.max(0, Math.floor(Number(count) || 0));
      if (safeCount <= 0 && !options?.showZero) return null;
      return (
        <span className="rounded-full bg-indigo-500/15 px-1.5 py-0.5 text-[10px] font-bold text-indigo-500 dark:text-indigo-300">
          {safeCount}
        </span>
      );
    },
    []
  );

  const renderGroupControlButtons = useCallback(
    () => (
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={() => {
            if (!deliveryToggleDisabled) {
              (deliveryToggleIsPause ? handlePauseClick : handleLaunchClick)();
            }
          }}
          disabled={deliveryToggleDisabled}
          className={classNames(
            "flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-secondary)] transition-colors hover:bg-black/[0.05] hover:text-[var(--color-text-primary)] disabled:pointer-events-none disabled:opacity-45 dark:hover:bg-white/[0.08]",
            deliveryToggleHardUnavailable && "opacity-45"
          )}
          title={deliveryToggleTitle}
          aria-label={deliveryToggleTitle}
          aria-pressed={deliveryToggleControl.active}
        >
          {deliveryToggleIsPause ? <PauseIcon size={18} /> : <PlayIcon size={18} />}
        </button>
        <button
          type="button"
          onClick={() => {
            if (!stopDisabled) handleStopClick();
          }}
          disabled={stopDisabled}
          className={classNames(
            "flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-secondary)] transition-colors hover:bg-black/[0.05] hover:text-[var(--color-text-primary)] disabled:pointer-events-none disabled:opacity-45 dark:hover:bg-white/[0.08]",
            stopHardUnavailable && "opacity-45"
          )}
          title={t("stopAllAgents")}
          aria-label={t("stopAllAgents")}
          aria-pressed={stopControl.active}
        >
          <StopIcon size={18} />
        </button>
      </div>
    ),
    [
      deliveryToggleControl.active,
      deliveryToggleDisabled,
      deliveryToggleHardUnavailable,
      deliveryToggleIsPause,
      deliveryToggleTitle,
      handleLaunchClick,
      handlePauseClick,
      handleStopClick,
      stopControl.active,
      stopDisabled,
      stopHardUnavailable,
      t,
    ]
  );

  const renderInlineGroupSwitcherContent = useCallback(
    () => (
      <div className="max-h-[min(52vh,560px)] overflow-auto px-2 pb-2 pt-2">
        {renderGroupList(workingGroups, "working")}

        {archivedGroups.length > 0 && (
          <div className="mt-3 border-t border-[var(--glass-border-subtle)] pt-2">
            <button
              type="button"
              className={classNames(
                "flex w-full items-center justify-between rounded-xl px-2 py-2 transition-colors",
                "text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)] hover:text-[var(--color-text-primary)]"
              )}
              onClick={() => setArchivedOpen((prev) => !prev)}
              aria-expanded={archivedPanelOpen}
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[var(--color-text-tertiary)]">
                  {t("archivedGroups")}
                </span>
                <span className="rounded-full bg-[var(--glass-panel-bg)] px-2 py-0.5 text-[10px] font-semibold text-[var(--color-text-secondary)]">
                  {archivedGroups.length}
                </span>
              </div>
              <ChevronDownIcon
                size={16}
                className={classNames("transition-transform", archivedPanelOpen ? "rotate-180" : "")}
              />
            </button>
            {archivedPanelOpen && <div className="mt-2">{renderGroupList(archivedGroups, "archived")}</div>}
          </div>
        )}

      </div>
    ),
    [
      archivedGroups,
      archivedPanelOpen,
      renderGroupList,
      t,
      workingGroups,
    ]
  );

  const renderWorkspaceNav = useCallback(() => {
    if (!selectedGroupId) {
      const hasGroups = orderedGroups.length > 0;

      return (
        <div className={classNames(
          "rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]",
          isCollapsed ? "p-3" : "p-4"
        )}>
          {isCollapsed ? (
            !hasGroups && !readOnly && onCreateGroup ? (
              <button
                type="button"
                className="glass-btn-accent flex h-11 w-11 items-center justify-center rounded-xl text-[var(--color-accent-primary)] transition-all"
                onClick={onCreateGroup}
                aria-label={t("createFirstGroup")}
                title={t("createFirstGroup")}
              >
                <PlusIcon size={18} />
              </button>
            ) : null
          ) : hasGroups ? (
            <>
              <div className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[var(--color-text-tertiary)]">
                {t("groupPages")}
              </div>
              <div className="mt-2 text-sm text-[var(--color-text-secondary)]">{t("selectGroup")}</div>
            </>
          ) : (
            <div className="text-center">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--glass-panel-bg)] text-[var(--color-text-tertiary)] shadow-[inset_0_0_0_1px_var(--glass-border-subtle)]">
                <TeamIcon size={24} strokeWidth={1.8} />
              </div>
              <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("noGroupsYet")}</div>
              <div className="mx-auto mt-2 max-w-[210px] text-xs leading-relaxed text-[var(--color-text-tertiary)]">
                {t("noGroupsDescription")}
              </div>
              {!readOnly && onCreateGroup && (
                <button
                  type="button"
                  className="glass-btn-accent mt-4 inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-[var(--color-accent-primary)] transition-all"
                  onClick={onCreateGroup}
                >
                  <PlusIcon size={16} />
                  <span>{t("createFirstGroup")}</span>
                </button>
              )}
            </div>
          )}
        </div>
      );
    }

    if (isCollapsed) {
      return (
        <div className="flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={() => handleTabSelect("chat")}
            title={t("groupChat")}
            aria-label={t("groupChat")}
            className={classNames(
              "relative flex h-11 w-11 items-center justify-center rounded-xl transition-all",
              activeTab === "chat" ? "glass-group-item-active glow-pulse" : "glass-group-item hover:scale-105"
            )}
          >
            <SendIcon size={18} />
            {unreadChatCount > 0 && (
              <span className="absolute -right-1 -top-1 min-w-[18px] rounded-full border border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--color-accent-primary)]">
                {unreadChatCount}
              </span>
            )}
          </button>

          {actors.map((actor) => {
            const indicator = getActorIndicator(actor);
            const isActive = activeTab === actor.id;
            return (
              <button
                key={actor.id}
                type="button"
                onClick={() => handleTabSelect(actor.id)}
                title={actor.title || actor.id}
                aria-label={actor.title || actor.id}
                className={classNames(
                  "relative flex h-11 w-11 items-center justify-center rounded-xl transition-all",
                  isActive ? "glass-group-item-active glow-pulse" : "glass-group-item hover:scale-105"
                )}
              >
                <span className="relative flex h-8 w-8 items-center justify-center">
                  <ActorAvatar
                    avatarUrl={actor.avatar_url || undefined}
                    runtime={actor.runtime || undefined}
                    command={actor.command}
                    env={actor.env}
                    title={actor.title || actor.id}
                    isDark={isDark}
                    sizeClassName="h-8 w-8"
                    textClassName="text-xs"
                    className={classNames(
                      "shadow-[0_10px_22px_-18px_rgba(15,23,42,0.85)]",
                      isActive ? "ring-1 ring-white/15" : ""
                    )}
                  />
                  <span
                    className={classNames(
                      "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--color-bg-primary)]",
                      indicator.dotClass
                    )}
                  />
                </span>
                {(actor.unread_count ?? 0) > 0 && (
                  <span className="absolute -right-1 -top-1 min-w-[18px] rounded-full bg-indigo-500/15 px-1.5 py-0.5 text-[10px] font-bold text-indigo-500 dark:text-indigo-300">
                    {actor.unread_count}
                  </span>
                )}
              </button>
            );
          })}

          {!readOnly && onAddAgent && (
            <button
              type="button"
              className="glass-btn-accent flex h-11 w-11 items-center justify-center rounded-xl text-[var(--color-accent-primary)] transition-all"
              onClick={onAddAgent}
              aria-label={t("addAgent")}
              title={t("addAgent")}
            >
              <PlusIcon size={18} />
            </button>
          )}
        </div>
      );
    }

    return (
      <div className="space-y-4">
        <section ref={switcherContainerRef} className="space-y-1">
          <div className="flex items-center justify-between gap-2 px-3 pb-0 pt-1">
            <div className="text-[13px] font-semibold text-[var(--color-text-primary)]">
              {t("currentGroup")}
            </div>
            {!readOnly && onCreateGroup && (
              <button
                type="button"
                onClick={() => {
                  setSwitcherOpen(false);
                  onCreateGroup();
                }}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-secondary)] transition-colors hover:bg-black/[0.05] hover:text-[var(--color-text-primary)] dark:hover:bg-white/[0.08]"
                aria-label={t("createNewGroup")}
                title={t("createNewGroup")}
              >
                <PlusIcon size={18} />
              </button>
            )}
          </div>
          <div
            ref={switcherButtonRef}
            role="button"
            tabIndex={0}
            className={classNames(
              "flex min-h-[52px] w-full items-center gap-4 rounded-xl px-3 py-2 text-left text-sm font-medium transition-colors",
              "text-[var(--color-text-secondary)] hover:bg-black/[0.045] dark:hover:bg-white/[0.07]"
            )}
            onClick={() => {
              if (isRenamingGroupTitle) return;
              setSwitcherOpen((prev) => !prev);
            }}
            onKeyDown={(event) => {
              if (event.target !== event.currentTarget || isRenamingGroupTitle) return;
              if (event.key !== "Enter" && event.key !== " ") return;
              event.preventDefault();
              setSwitcherOpen((prev) => !prev);
            }}
            aria-expanded={switcherOpen}
            aria-label={t("switchGroup")}
            title={t("switchGroup")}
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center text-[var(--color-text-secondary)]">
              <TeamIcon size={24} strokeWidth={1.8} />
            </span>
            <span className="min-w-0 flex-1" onClick={(event) => event.stopPropagation()}>
              {isRenamingGroupTitle ? (
                <input
                  ref={renameInputRef}
                  value={groupTitleDraft}
                  readOnly={groupTitleSaving}
                  onChange={(event) => setGroupTitleDraft(event.target.value)}
                  onBlur={() => {
                    if (skipRenameBlurCommitRef.current) {
                      skipRenameBlurCommitRef.current = false;
                      return;
                    }
                    void commitGroupTitleRename();
                  }}
                  onKeyDown={(event) => {
                    event.stopPropagation();
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void commitGroupTitleRename();
                    } else if (event.key === "Escape") {
                      event.preventDefault();
                      cancelGroupTitleRename();
                    }
                  }}
                  className={classNames(
                    "-ml-2 block h-8 w-[calc(100%+0.5rem)] min-w-0 rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] px-2 text-sm font-medium text-[var(--color-text-primary)] outline-none transition-all",
                    "shadow-sm focus:border-[var(--color-accent-primary)] focus:shadow-[0_0_0_3px_rgba(37,99,235,0.16)]",
                    groupTitleSaving && "opacity-70"
                  )}
                  aria-label={t("editGroup")}
                />
              ) : (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    beginGroupTitleRename();
                  }}
                  className={classNames(
                    "-ml-2 block max-w-[calc(100%+0.5rem)] rounded-lg px-2 py-1 text-left transition-all duration-150",
                    "hover:bg-black/[0.035] hover:shadow-[0_10px_24px_-18px_rgba(15,23,42,0.95)] dark:hover:bg-white/[0.06] dark:hover:shadow-[0_10px_24px_-16px_rgba(0,0,0,0.8)]",
                    readOnly ? "cursor-default" : "cursor-text"
                  )}
                  title={currentGroupTitle || selectedGroupId}
                  aria-label={t("editGroup")}
                >
                  <span className="block min-w-0 truncate">{currentGroupTitle || selectedGroupId}</span>
                </button>
              )}
            </span>
            {selectedStatus && <span className={classNames("rounded-full px-2 py-0.5 text-[10px] font-semibold", selectedStatus.pillClass)}>{selectedStatus.label}</span>}
            {sseStatus !== "connected" && (
              <span className="h-2 w-2 shrink-0 rounded-full bg-rose-500" title={sseStatus === "connecting" ? t("reconnecting") : t("disconnected")} />
            )}
            <ChevronDownIcon size={18} className={classNames("shrink-0 transition-transform text-[var(--color-text-tertiary)]", switcherOpen ? "rotate-180" : "")} />
          </div>

          {switcherOpen && (
            <div className="mt-2 rounded-xl bg-black/[0.025] px-2 py-2 dark:bg-white/[0.035]">
              {renderInlineGroupSwitcherContent()}
            </div>
          )}
        </section>

        <section className="space-y-1">
          <div className="flex items-center justify-between gap-2 px-3 pb-0 pt-1">
            <div className="text-[13px] font-semibold text-[var(--color-text-primary)]">
              {t("memberManagement")}
            </div>
            {!readOnly && onAddAgent && (
              <button
                type="button"
                onClick={onAddAgent}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-[var(--color-text-secondary)] transition-colors hover:bg-black/[0.05] hover:text-[var(--color-text-primary)] dark:hover:bg-white/[0.08]"
                aria-label={t("addAgent")}
                title={t("addAgent")}
              >
                <PlusIcon size={18} />
              </button>
            )}
          </div>

          <div className="space-y-1">
            <button
              type="button"
              onClick={() => handleTabSelect("chat")}
              className={navButtonClass(activeTab === "chat")}
            >
              <span
                className={classNames(
                  "flex h-8 w-8 items-center justify-center text-[var(--color-text-secondary)]"
                )}
              >
                <SendIcon size={24} strokeWidth={1.8} />
              </span>
              <span className="min-w-0 flex-1 truncate">{t("groupChat")}</span>
              {unreadChatCount > 0 && (
                <span className="rounded-full border border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg)] px-1.5 py-0.5 text-[10px] font-bold text-[var(--color-accent-primary)]">
                  {unreadChatCount}
                </span>
              )}
            </button>

            {actors.map((actor) => {
              const indicator = getActorIndicator(actor);
              const isActive = activeTab === actor.id;
              return (
                <button
                  key={actor.id}
                  type="button"
                  onClick={() => handleTabSelect(actor.id)}
                  className={navButtonClass(isActive)}
                >
                  <span
                    className="relative flex h-8 w-8 items-center justify-center"
                  >
                    <ActorAvatar
                      avatarUrl={actor.avatar_url || undefined}
                      runtime={actor.runtime || undefined}
                      command={actor.command}
                      env={actor.env}
                      title={actor.title || actor.id}
                      isDark={isDark}
                      sizeClassName="h-8 w-8"
                      textClassName="text-xs"
                      className="shadow-[0_10px_24px_-18px_rgba(15,23,42,0.8)]"
                    />
                    <span
                      className={classNames(
                        "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--color-bg-primary)]",
                        indicator.dotClass
                      )}
                    />
                  </span>
                  <span className={classNames("min-w-0 flex-1 truncate", indicator.labelClass || "text-[var(--color-text-secondary)]")}>{actor.title || actor.id}</span>
                  <span className={classNames("shrink-0 rounded px-1.5 py-0.5 text-[9px] font-semibold", indicator.statusBadgeClass)}>
                    {indicator.statusLabel}
                  </span>
                  {renderCountBadge(actor.unread_count ?? 0)}
                </button>
              );
            })}

          </div>
        </section>

        <section className="space-y-1">
          <div className="flex items-center justify-between gap-2 px-3 pb-0 pt-1">
            <div className="text-[13px] font-semibold text-[var(--color-text-primary)]">
              {t("goalManagement")}
            </div>
            <button
              type="button"
              onClick={onOpenContext}
              disabled={!selectedGroupId}
              className="rounded px-1 text-[13px] font-semibold text-transparent outline-none transition-colors focus-visible:text-[var(--color-text-tertiary)] disabled:pointer-events-none"
              aria-label={t("projectContext")}
              title={t("projectContext")}
            >
              上下文
            </button>
          </div>
          <div className="space-y-1">
            <button
              type="button"
              onClick={onOpenContextProject}
              disabled={!selectedGroupId}
              className={navButtonClass(false)}
            >
              <span className="flex h-8 w-8 items-center justify-center text-[var(--color-text-secondary)]">
                <ClipboardIcon size={24} strokeWidth={1.8} />
              </span>
              <span className="min-w-0 flex-1 truncate">{t("teamGoal")}</span>
            </button>
            <button
              type="button"
              onClick={onOpenSkillManagement}
              disabled={!selectedGroupId}
              className={navButtonClass(false)}
            >
              <span className="flex h-8 w-8 items-center justify-center text-[var(--color-text-secondary)]">
                <SparklesIcon size={24} strokeWidth={1.8} />
              </span>
              <span className="min-w-0 flex-1 truncate">{t("skillManagement")}</span>
            </button>
            <button
              type="button"
              onClick={onOpenContextSummary}
              disabled={!selectedGroupId}
              className={navButtonClass(false)}
            >
              <span className="flex h-8 w-8 items-center justify-center text-[var(--color-text-secondary)]">
                <RocketIcon size={24} strokeWidth={1.8} />
              </span>
              <span className="min-w-0 flex-1 truncate">{t("taskExecution")}</span>
              {renderCountBadge(activeTaskCount)}
            </button>
          </div>
        </section>
      </div>
    );
  }, [
    activeTab,
    actors,
    busy,
    activeTaskCount,
    getActorIndicator,
    groupDoc,
    isCollapsed,
    navButtonClass,
    onAddAgent,
    onCreateGroup,
    onOpenContext,
    orderedGroups.length,
    renderInlineGroupSwitcherContent,
    handleTabSelect,
    onOpenContextProject,
    onOpenContextSummary,
    onOpenSkillManagement,
    readOnly,
    renderCountBadge,
    selectedGroup,
    selectedGroupId,
    selectedStatus,
    sseStatus,
    switcherOpen,
    t,
    unreadChatCount,
  ]);

  const renderGroupSwitcherContent = useCallback(() => {
    if (!orderedGroups.length) {
      return (
        <div className="p-4 text-center">
          <div className={classNames(
            "mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl glass-card",
            "text-[var(--color-text-tertiary)]"
          )}>
            <FolderIcon size={28} />
          </div>
          <div className="text-sm font-medium text-[var(--color-text-secondary)]">{t("noGroupsYet")}</div>
          <div className="mx-auto mt-2 max-w-[220px] text-xs leading-relaxed text-[var(--color-text-tertiary)]">
            {t("noGroupsDescription")}
          </div>
          {!readOnly && onCreateGroup && (
            <button
              type="button"
              className="glass-btn-accent mt-4 rounded-xl px-4 py-2.5 text-sm font-medium text-[var(--color-accent-primary)]"
              onClick={() => {
                setSwitcherOpen(false);
                onCreateGroup();
              }}
            >
              {t("createFirstGroup")}
            </button>
          )}
        </div>
      );
    }

    return (
      <>
        <div className="max-h-[min(52vh,560px)] overflow-auto p-3">
          {renderGroupList(workingGroups, "working")}

          {archivedGroups.length > 0 && (
            <div className="mt-4">
              <button
                type="button"
                className={classNames(
                  "flex w-full items-center justify-between rounded-xl px-2 py-2 transition-colors",
                  "text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)] hover:text-[var(--color-text-primary)]"
                )}
                onClick={() => setArchivedOpen((prev) => !prev)}
                aria-expanded={archivedPanelOpen}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[var(--color-text-tertiary)]">
                    {t("archivedGroups")}
                  </span>
                  <span className="rounded-full bg-[var(--glass-panel-bg)] px-2 py-0.5 text-[10px] font-semibold text-[var(--color-text-secondary)]">
                    {archivedGroups.length}
                  </span>
                </div>
                <ChevronDownIcon
                  size={16}
                  className={classNames("transition-transform", archivedPanelOpen ? "rotate-180" : "")}
                />
              </button>
              {archivedPanelOpen && <div className="mt-2">{renderGroupList(archivedGroups, "archived")}</div>}
            </div>
          )}

          {!readOnly && onCreateGroup && (
            <button
              type="button"
              className="glass-btn-accent mt-4 flex w-full items-center justify-center gap-2 rounded-xl px-3 py-3 text-sm font-medium text-[var(--color-accent-primary)] transition-all"
              onClick={() => {
                setSwitcherOpen(false);
                onCreateGroup();
              }}
            >
              <PlusIcon size={16} />
              <span>{t("createNewGroup")}</span>
            </button>
          )}
        </div>
      </>
    );
  }, [
    archivedGroups,
    archivedPanelOpen,
    onCreateGroup,
    orderedGroups.length,
    readOnly,
    renderGroupList,
    t,
    workingGroups,
  ]);

  return (
    <>
      <aside
        className={classNames(
          "fixed z-40 flex h-full min-h-0 flex-col border-r border-black/10 bg-[#f7f7f6] text-[var(--color-text-primary)] md:relative dark:border-white/10 dark:bg-[#121214]",
          "onecolleague-sidebar",
          isResizing ? "transition-none" : "transition-[width,transform] duration-300 ease-out",
          isCollapsed ? "w-[60px]" : "w-[280px] md:w-[var(--sidebar-width)]",
          isOpen ? "translate-x-0" : "-translate-x-full",
          "md:translate-x-0"
        )}
      >
        <div className="shrink-0 px-4 py-4 pb-2">
          <div
            className={classNames(
              "flex items-center",
              isCollapsed ? "justify-center" : "justify-between"
            )}
          >
            <div className={classNames("flex items-center min-w-0", isCollapsed ? "" : "flex-1 gap-3 pr-3")}>
              <div
                className={classNames(
                  "flex items-center justify-center overflow-hidden",
                  isCollapsed ? "h-12 w-12" : "h-12 min-w-[48px] max-w-[164px]"
                )}
              >
                <img
                  src={logoPath}
                  alt={`${brandName} logo`}
                  className={classNames("object-contain", isCollapsed ? "h-9 w-9" : "h-10 w-10")}
                />
              </div>
              {!isCollapsed && (
                <span className="min-w-0 truncate text-xl font-semibold text-[var(--color-text-primary)]">
                  {brandName}
                </span>
              )}
            </div>

            {!isCollapsed && renderGroupControlButtons()}

            {!isCollapsed && (
              <button
                className={classNames(
                  "glass-btn flex min-h-[44px] min-w-[44px] items-center justify-center rounded-xl p-2 transition-all md:hidden",
                  "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                )}
                onClick={onClose}
                aria-label={t("closeSidebar")}
              >
                <CloseIcon size={18} />
              </button>
            )}
          </div>
        </div>

        <div className={classNames("min-h-0 flex-1 overflow-auto", isCollapsed ? "p-2" : "px-4 py-3")}>
          {renderWorkspaceNav()}
        </div>

        <div className={classNames("shrink-0 border-t border-black/10 dark:border-white/10", isCollapsed ? "p-2" : "px-4 py-3")}>
          <div className={classNames("flex gap-2", isCollapsed ? "flex-col items-center" : "items-center justify-between")}>
            {isCollapsed && (
              <button
                className={classNames(
                  "flex h-11 w-11 items-center justify-center rounded-xl transition-colors hover:bg-black/[0.045] dark:hover:bg-white/[0.07]",
                  "text-[var(--color-text-primary)]"
                )}
                onClick={onToggleCollapse}
                aria-label={t("expandSidebar")}
                title={t("expandSidebar")}
              >
                <ChevronRightIcon size={18} />
              </button>
            )}

            <Popover open={moreOpen} onOpenChange={setMoreOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className={classNames(
                    "flex items-center rounded-xl transition-colors hover:bg-black/[0.045] dark:hover:bg-white/[0.07]",
                    isCollapsed ? "h-11 w-11 justify-center" : "min-h-[52px] min-w-0 flex-1 gap-3 px-3 py-2 text-left"
                  )}
                  aria-expanded={moreOpen}
                  aria-label={isCollapsed ? t("moreActions") : t("workspaceBench")}
                  title={isCollapsed ? t("moreActions") : t("workspaceBench")}
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex h-8 w-8 items-center justify-center text-[var(--color-text-primary)]">
                      <MoreIcon size={24} strokeWidth={1.8} />
                    </span>
                    {!isCollapsed && (
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">{t("workspaceBench")}</div>
                      </div>
                    )}
                  </div>
                </button>
              </PopoverTrigger>
              <PopoverContent
                side={isCollapsed ? "right" : "top"}
                align={isCollapsed ? "end" : "start"}
                sideOffset={12}
                collisionPadding={12}
                className={classNames(
                  "z-[1002] overflow-hidden rounded-[1.25rem] p-2 shadow-2xl",
                  isCollapsed ? "w-[240px]" : "w-[var(--radix-popover-trigger-width)]"
                )}
              >
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false);
                    onOpenDoneHubAuth();
                  }}
                  className={classNames(
                    "flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-sm font-medium transition-all hover:bg-[var(--glass-tab-bg-hover)]",
                    doneHubConnected
                      ? "text-sky-700 dark:text-sky-300"
                      : doneHubStatus === "error"
                        ? "text-rose-700 dark:text-rose-300"
                        : "text-[var(--color-text-primary)]"
                  )}
                  title={
                    doneHubConnected
                      ? t("doneHubBalanceTitle", { value: doneHubQuota })
                      : t(doneHubStatus === "error" ? "doneHubNeedsAttention" : "doneHubConnect")
                  }
                >
                  <span className="flex h-5 min-w-5 items-center justify-center">
                    {doneHubIsPro ? (
                      <span className="rounded-full border border-amber-300/65 bg-amber-400/18 px-1.5 py-0.5 text-[9px] font-semibold leading-none text-amber-700 dark:border-amber-300/30 dark:bg-amber-300/14 dark:text-amber-200">
                        PRO
                      </span>
                    ) : (
                      <FolderIcon size={17} />
                    )}
                  </span>
                  <span className="min-w-0 truncate">{doneHubLabel}</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMoreOpen(false);
                    onOpenSettings();
                  }}
                  disabled={!selectedGroupId}
                  className="flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-sm font-medium text-[var(--color-text-primary)] transition-all hover:bg-[var(--glass-tab-bg-hover)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <SettingsIcon size={17} />
                  <span className="min-w-0 truncate">{t("settings")}</span>
                </button>
              </PopoverContent>
            </Popover>

            {!isCollapsed && (
              <div className="flex shrink-0 items-center gap-1">
                <ThemeToggleCompact
                  theme={theme}
                  onThemeChange={onThemeChange}
                  isDark={isDark}
                  variant="rail"
                />
                <TextScaleSwitcher
                  textScale={textScale}
                  onTextScaleChange={onTextScaleChange}
                  variant="rail"
                />
                <button
                  type="button"
                  className={classNames(
                    "flex h-9 w-9 min-h-[36px] min-w-[36px] items-center justify-center rounded-[14px] transition-all",
                    "text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)] hover:text-[var(--color-text-primary)]"
                  )}
                  onClick={onToggleCollapse}
                  aria-label={t("collapseSidebar")}
                  title={t("collapseSidebar")}
                >
                  <ChevronLeftIcon size={16} />
                </button>
              </div>
            )}

          </div>
        </div>

        {switcherOpen && isCollapsed && (
          <div
            ref={switcherPanelRef}
            className={classNames(
              "glass-panel absolute z-30 overflow-hidden rounded-[1.5rem] border border-[var(--glass-border-subtle)] shadow-2xl",
              "bottom-3 left-[72px] w-[280px]"
            )}
          >
            {renderGroupSwitcherContent()}
          </div>
        )}

        {!isCollapsed && (
          <div
            className="absolute inset-y-0 right-0 z-20 hidden w-4 translate-x-1/2 cursor-col-resize items-center justify-center md:flex"
            onPointerDown={handleResizeStart}
            role="separator"
            aria-orientation="vertical"
            aria-label={t("resizeSidebar")}
            aria-valuemin={SIDEBAR_MIN_WIDTH}
            aria-valuemax={SIDEBAR_MAX_WIDTH}
            aria-valuenow={sidebarWidth}
          >
            <div
              className={classNames(
                "h-14 w-[3px] rounded-full transition-all",
                isResizing
                  ? "bg-cyan-500 shadow-[0_0_0_4px_rgba(6,182,212,0.12)]"
                  : "bg-black/10 hover:bg-cyan-500/70 dark:bg-white/10 dark:hover:bg-cyan-400/75"
              )}
            />
          </div>
        )}
      </aside>

      {isOpen && (
        <div
          className="glass-overlay fixed inset-0 z-30 animate-fade-in md:hidden"
          onPointerDown={(e) => {
            if (e.target === e.currentTarget) onClose();
          }}
          aria-hidden="true"
        />
      )}
    </>
  );
}
