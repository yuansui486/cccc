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
import { Actor, GroupMeta } from "../../types";
import { classNames } from "../../utils/classNames";
import { getAppBrandName, getAppLogoPath } from "../../utils/displayText";
import { getActorDisplayWorkingState } from "../../utils/terminalWorkingState";
import { getRuntimeIndicatorState } from "../../utils/statusIndicators";
import {
  useBrandingStore,
  useTerminalSignalsStore,
  getTerminalSignalKey,
} from "../../stores";
import {
  CloseIcon,
  FolderIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  PlusIcon,
  SendIcon,
} from "../Icons";
import { SortableGroupItem } from "./SortableGroupItem";
import { SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH } from "../../stores/useUIStore";

export interface GroupSidebarProps {
  orderedGroups: GroupMeta[];
  archivedGroupIds: string[];
  selectedGroupId: string;
  actors: Actor[];
  activeTab: string;
  unreadChatCount: number;
  isOpen: boolean;
  isCollapsed: boolean;
  sidebarWidth: number;
  isDark: boolean;
  readOnly?: boolean;
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
}

export function GroupSidebar({
  orderedGroups,
  archivedGroupIds,
  selectedGroupId,
  actors,
  activeTab,
  unreadChatCount,
  isOpen,
  isCollapsed,
  sidebarWidth,
  isDark,
  readOnly,
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
}: GroupSidebarProps) {
  const { t } = useTranslation("layout");
  const appBrandName = getAppBrandName();
  const appLogoPath = getAppLogoPath();
  const branding = useBrandingStore((s) => s.branding);
  const terminalSignals = useTerminalSignalsStore((state) => state.signals);
  const brandName = String(branding.product_name || "").trim() || appBrandName;
  const logoPath = String(branding.logo_icon_url || "").trim() || appLogoPath;
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const switcherButtonRef = useRef<HTMLButtonElement | null>(null);
  const switcherPanelRef = useRef<HTMLDivElement | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const [switcherOpen, setSwitcherOpen] = useState(false);
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
    if (!isOpen) setSwitcherOpen(false);
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
      onRestoreGroup,
      onSelectGroup,
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
      return getRuntimeIndicatorState({ isRunning: Boolean(isRunning), workingState });
    },
    [selectedGroupId, terminalSignals]
  );

  const renderWorkspaceNav = useCallback(() => {
    if (!selectedGroupId) {
      return (
        <div className={classNames(
          "rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]",
          isCollapsed ? "p-3" : "p-4"
        )}>
          {!isCollapsed && (
            <>
              <div className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[var(--color-text-tertiary)]">
                {t("groupPages")}
              </div>
              <div className="mt-2 text-sm text-[var(--color-text-secondary)]">{t("selectGroup")}</div>
            </>
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
            title={t("chat")}
            aria-label={t("chat")}
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
            const initial = (actor.title || actor.id).charAt(0).toUpperCase();
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
                <span className={classNames("text-sm font-semibold", indicator.labelClass || "text-[var(--color-text-primary)]")}>
                  {initial}
                </span>
                <span className={classNames("absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-[var(--color-bg-primary)]", indicator.dotClass)} />
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
      <div className="space-y-3">
        <div className="rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-3">
          <div className="text-[10px] font-semibold uppercase tracking-[0.15em] text-[var(--color-text-tertiary)]">
            {t("currentGroup")}
          </div>
          <div className="mt-2 truncate text-sm font-semibold text-[var(--color-text-primary)]">
            {selectedGroup?.title || selectedGroupId}
          </div>
        </div>

        <div className="rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-2">
          <div className="px-2 pb-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.15em] text-[var(--color-text-tertiary)]">
            {t("groupPages")}
          </div>

          <div className="space-y-1">
            <button
              type="button"
              onClick={() => handleTabSelect("chat")}
              className={classNames(
                "glass-tab flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm font-medium transition-all",
                activeTab === "chat"
                  ? "glass-tab-active text-[var(--color-text-primary)]"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              )}
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--glass-tab-bg-hover)] text-[var(--color-text-primary)]">
                <SendIcon size={16} />
              </span>
              <span className="min-w-0 flex-1 truncate">{t("chat")}</span>
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
                  className={classNames(
                    "glass-tab flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm font-medium transition-all",
                    isActive
                      ? "glass-tab-active text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                  )}
                >
                  <span className="relative flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--glass-tab-bg-hover)]">
                    <span className={classNames("h-2.5 w-2.5 rounded-full", indicator.dotClass)} />
                  </span>
                  <span className={classNames("min-w-0 flex-1 truncate", indicator.labelClass)}>{actor.title || actor.id}</span>
                  {actor.role === "foreman" && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] text-amber-500 dark:text-amber-400">
                      F
                    </span>
                  )}
                  {(actor.unread_count ?? 0) > 0 && (
                    <span className="rounded-full bg-indigo-500/15 px-1.5 py-0.5 text-[10px] font-bold text-indigo-500 dark:text-indigo-300">
                      {actor.unread_count}
                    </span>
                  )}
                </button>
              );
            })}

            {!readOnly && onAddAgent && (
              <button
                type="button"
                onClick={onAddAgent}
                className="glass-btn-accent flex w-full items-center justify-center gap-2 rounded-xl px-3 py-3 text-sm font-medium text-[var(--color-accent-primary)] transition-all"
              >
                <PlusIcon size={16} />
                <span>{t("addAgent")}</span>
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }, [
    activeTab,
    actors,
    getActorIndicator,
    isCollapsed,
    onAddAgent,
    handleTabSelect,
    onTabChange,
    readOnly,
    selectedGroup,
    selectedGroupId,
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
        <div className="flex items-center justify-between gap-3 border-b border-[var(--glass-border-subtle)] px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("switchGroup")}</div>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("workingGroups")}</div>
          </div>
          {!readOnly && onCreateGroup && (
            <button
              type="button"
              className="glass-btn-accent flex h-9 w-9 items-center justify-center rounded-xl text-[var(--color-accent-primary)]"
              onClick={() => {
                setSwitcherOpen(false);
                onCreateGroup();
              }}
              aria-label={t("createNewGroup")}
              title={t("createNewGroup")}
            >
              <PlusIcon size={16} />
            </button>
          )}
        </div>

        <div className="max-h-[min(52vh,560px)] overflow-auto p-3">
          <div className="mb-3 px-2 text-[10px] font-semibold uppercase tracking-[0.15em] text-[var(--color-text-tertiary)]">
            {t("workingGroups")}
          </div>
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
        </div>
      </>
    );
  }, [
    archivedGroups,
    archivedPanelOpen,
    onCreateGroup,
    readOnly,
    renderGroupList,
    t,
    workingGroups,
  ]);

  return (
    <>
      <aside
        className={classNames(
          "glass-sidebar fixed z-40 flex h-full flex-col md:relative",
          isResizing ? "transition-none" : "transition-[width,transform] duration-300 ease-out",
          isCollapsed ? "w-[60px]" : "w-[280px] md:w-[var(--sidebar-width)]",
          isOpen ? "translate-x-0" : "-translate-x-full",
          "md:translate-x-0"
        )}
      >
        <div className="px-3 py-4 pb-2">
          <div
            className={classNames(
              "flex items-center",
              isCollapsed ? "justify-center" : "justify-between"
            )}
          >
            <div className={classNames("flex items-center min-w-0", isCollapsed ? "" : "flex-1 gap-3 pr-3")}>
              <div
                className={classNames(
                  "glass-btn flex items-center justify-center overflow-hidden rounded-xl",
                  isCollapsed ? "h-11 w-11" : "h-11 min-w-[44px] max-w-[164px] px-3",
                  "text-cyan-600 dark:text-cyan-400"
                )}
              >
                <img
                  src={logoPath}
                  alt={`${brandName} logo`}
                  className={classNames("object-contain", isCollapsed ? "h-6 w-6" : "h-6 w-6")}
                />
              </div>
              {!isCollapsed && (
                <span className="min-w-0 truncate text-lg font-bold tracking-tight text-[var(--color-text-primary)]">
                  {brandName}
                </span>
              )}
            </div>

            {!isCollapsed && (
              <div className="flex shrink-0 items-center gap-2">
                {!readOnly && onCreateGroup && (
                  <button
                    className={classNames(
                      "glass-btn-accent min-h-[32px] rounded-lg px-3 py-1.5 text-[11px] font-medium transition-all",
                      isDark ? "text-gray-300" : "text-gray-800"
                    )}
                    onClick={onCreateGroup}
                    title={t("createNewGroup")}
                    aria-label={t("createNewGroup")}
                  >
                    {t("newGroup")}
                  </button>
                )}
                <button
                  className={classNames(
                    "glass-btn hidden min-h-[36px] min-w-[36px] items-center justify-center rounded-xl p-2 transition-all md:flex",
                    "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
                    isDark ? "hover:bg-[var(--glass-tab-bg-hover)]" : "hover:bg-black/5"
                  )}
                  onClick={onToggleCollapse}
                  aria-label={t("collapseSidebar")}
                  title={t("collapseSidebar")}
                >
                  <ChevronLeftIcon size={16} />
                </button>
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
              </div>
            )}
          </div>
        </div>

        {isCollapsed && (
          <div className="flex flex-col items-center gap-2 p-2">
            <button
              className={classNames(
                "glass-btn flex h-11 w-11 items-center justify-center rounded-xl transition-all",
                "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              )}
              onClick={onToggleCollapse}
              aria-label={t("expandSidebar")}
              title={t("expandSidebar")}
            >
              <ChevronRightIcon size={18} />
            </button>
            {!readOnly && onCreateGroup && (
              <button
                className={classNames(
                  "glass-btn-accent flex h-11 w-11 items-center justify-center rounded-xl transition-all",
                  "text-[var(--color-accent-primary)]"
                )}
                onClick={onCreateGroup}
                aria-label={t("createNewGroup")}
                title={t("createNewGroup")}
              >
                <PlusIcon size={18} />
              </button>
            )}
          </div>
        )}

        <div className={classNames("flex-1 overflow-auto", isCollapsed ? "p-2" : "p-3")}>
          {renderWorkspaceNav()}
        </div>

        <div className={classNames("border-t border-[var(--glass-border-subtle)]", isCollapsed ? "p-2" : "p-3 pt-2")}>
          <button
            ref={switcherButtonRef}
            type="button"
            className={classNames(
              "glass-btn flex items-center rounded-xl transition-all",
              isCollapsed ? "h-11 w-11 justify-center" : "w-full justify-between gap-3 px-3 py-3 text-left"
            )}
            onClick={() => setSwitcherOpen((prev) => !prev)}
            aria-expanded={switcherOpen}
            aria-label={t("switchGroup")}
            title={t("switchGroup")}
          >
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-[var(--glass-tab-bg-hover)] text-[var(--color-text-primary)]">
                <FolderIcon size={16} />
              </span>
              {!isCollapsed && (
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-[var(--color-text-primary)]">{t("switchGroup")}</div>
                  <div className="truncate text-xs text-[var(--color-text-tertiary)]">
                    {selectedGroup?.title || t("selectGroup")}
                  </div>
                </div>
              )}
            </div>
            {!isCollapsed && (
              <ChevronDownIcon size={16} className={classNames("transition-transform", switcherOpen ? "rotate-180" : "")} />
            )}
          </button>
        </div>

        {switcherOpen && (
          <div
            ref={switcherPanelRef}
            className={classNames(
              "glass-panel absolute z-30 overflow-hidden rounded-[1.5rem] border border-[var(--glass-border-subtle)] shadow-2xl",
              isCollapsed ? "bottom-3 left-[72px] w-[280px]" : "bottom-[84px] left-3 right-3"
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
