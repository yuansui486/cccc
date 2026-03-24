import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from 'react-i18next';
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
import { GroupMeta } from "../../types";
import { classNames } from "../../utils/classNames";
import { getAppBrandName, getAppLogoPath } from "../../utils/displayText";
import { CloseIcon, FolderIcon, ChevronLeftIcon, ChevronRightIcon, PlusIcon } from "../Icons";
import { SortableGroupItem } from "./SortableGroupItem";
import { SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH } from "../../stores/useUIStore";
import { useBrandingStore } from "../../stores";

export interface GroupSidebarProps {
  orderedGroups: GroupMeta[];
  groupOrder: string[];
  selectedGroupId: string;
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
  onReorder: (fromIndex: number, toIndex: number) => void;
}

export function GroupSidebar({
  orderedGroups,
  groupOrder,
  selectedGroupId,
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
  onReorder,
}: GroupSidebarProps) {
  const { t } = useTranslation('layout');
  const appBrandName = getAppBrandName();
  const appLogoPath = getAppLogoPath();
  const branding = useBrandingStore((s) => s.branding);
  const brandName = String(branding.product_name || "").trim() || appBrandName;
  const logoPath = String(branding.logo_icon_url || "").trim() || appLogoPath;
  const dragStateRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [isResizing, setIsResizing] = useState(false);

  const sortableIds = useMemo(
    () => orderedGroups.map((g) => String(g.group_id || "")),
    [orderedGroups]
  );

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

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIndex = groupOrder.indexOf(String(active.id));
      const newIndex = groupOrder.indexOf(String(over.id));
      if (oldIndex !== -1 && newIndex !== -1) {
        onReorder(oldIndex, newIndex);
      }
    }
  };

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

  const handleResizeStart = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
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
  }, [isCollapsed, sidebarWidth]);

  return (
    <>
      <aside
        className={classNames(
          "h-full flex flex-col glass-sidebar",
          "fixed md:relative z-40",
          isResizing ? "transition-none" : "transition-[width,transform] duration-300 ease-out",
          isCollapsed ? "w-[60px]" : "w-[280px] md:w-[var(--sidebar-width)]",
          isOpen ? "translate-x-0" : "-translate-x-full",
          "md:translate-x-0"
        )}
      >
        <div className="p-4 pb-2">
          <div
            className={classNames(
              "flex items-center",
              isCollapsed ? "justify-center" : "justify-between"
            )}
          >
            <div className={classNames("flex items-center min-w-0", isCollapsed ? "" : "flex-1 gap-3 pr-3")}>
              <div
                className={classNames(
                  "rounded-xl flex items-center justify-center overflow-hidden glass-btn",
                  isCollapsed ? "w-11 h-11" : "h-11 min-w-[44px] max-w-[164px] px-3",
                  "text-cyan-600 dark:text-cyan-400"
                )}
              >
                <img
                  src={logoPath}
                  alt={`${brandName} logo`}
                  className={classNames(
                    "object-contain",
                    isCollapsed ? "w-6 h-6" : "max-h-6 w-auto max-w-full"
                  )}
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
                      "text-[11px] px-3 py-1.5 rounded-lg font-medium transition-all min-h-[32px] glass-btn-accent",
                      isDark ? "text-gray-300" : "text-gray-800"
                    )}
                    onClick={onCreateGroup}
                    title={t('createNewGroup')}
                    aria-label={t('createNewGroup')}
                  >
                    {t('newGroup')}
                  </button>
                )}
                <button
                  className={classNames(
                    "hidden md:flex p-2 min-w-[36px] min-h-[36px] items-center justify-center rounded-xl transition-all glass-btn",
                    "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
                    isDark ? "hover:bg-[var(--glass-tab-bg-hover)]" : "hover:bg-black/5"
                  )}
                  onClick={onToggleCollapse}
                  aria-label={t('collapseSidebar')}
                  title={t('collapseSidebar')}
                >
                  <ChevronLeftIcon size={16} />
                </button>
                <button
                  className={classNames(
                    "md:hidden p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl transition-all glass-btn",
                    "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                  )}
                  onClick={onClose}
                  aria-label={t('closeSidebar')}
                >
                  <CloseIcon size={18} />
                </button>
              </div>
            )}
          </div>
        </div>

        {isCollapsed && (
          <div className="p-2 flex flex-col items-center gap-2">
            <button
              className={classNames(
                "w-11 h-11 rounded-xl flex items-center justify-center transition-all glass-btn",
                "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              )}
              onClick={onToggleCollapse}
              aria-label={t('expandSidebar')}
              title={t('expandSidebar')}
            >
              <ChevronRightIcon size={18} />
            </button>
            {!readOnly && onCreateGroup && (
              <button
                className={classNames(
                  "w-11 h-11 rounded-xl flex items-center justify-center transition-all glass-btn-accent",
                  "text-[var(--color-accent-primary)]"
                )}
                onClick={onCreateGroup}
                aria-label={t('createNewGroup')}
                title={t('createNewGroup')}
              >
                <PlusIcon size={18} />
              </button>
            )}
          </div>
        )}

        <div className={classNames("flex-1 overflow-auto", isCollapsed ? "p-2" : "p-3")}>
          {!isCollapsed && (
            <div className="text-[10px] font-semibold uppercase tracking-[0.15em] mb-3 px-2 text-[var(--color-text-tertiary)]">
              {t('workingGroups')}
            </div>
          )}

          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
              <div className={classNames(isCollapsed ? "flex flex-col items-center gap-2" : "space-y-1")}>
                {orderedGroups.map((g) => {
                  const gid = String(g.group_id || "");
                  const active = gid === selectedGroupId;
                  return (
                    <SortableGroupItem
                      key={gid}
                      group={g}
                      isActive={active}
                      isDark={isDark}
                      isCollapsed={isCollapsed}
                      dragDisabled={!!readOnly}
                      onSelect={() => {
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

          {!orderedGroups.length && !isCollapsed && (
            <div className="p-6 text-center">
              <div className={classNames(
                "w-16 h-16 mx-auto mb-4 rounded-2xl flex items-center justify-center glass-card",
                "text-[var(--color-text-tertiary)]"
              )}>
                <FolderIcon size={32} />
              </div>
              <div className="text-sm mb-2 font-medium text-[var(--color-text-secondary)]">{t('noGroupsYet')}</div>
              <div className="text-xs mb-5 max-w-[200px] mx-auto leading-relaxed text-[var(--color-text-tertiary)]">
                {t('noGroupsDescription')}
              </div>
              {!readOnly && onCreateGroup && (
                <button
                  className={classNames(
                    "text-sm px-5 py-2.5 rounded-xl font-medium min-h-[44px] transition-all glass-btn-accent",
                    "text-[var(--color-accent-primary)]"
                  )}
                  onClick={onCreateGroup}
                >
                  {t('createFirstGroup')}
                </button>
              )}
            </div>
          )}
        </div>

        {!isCollapsed && (
          <div
            className="absolute inset-y-0 right-0 z-20 hidden w-4 translate-x-1/2 cursor-col-resize items-center justify-center md:flex"
            onPointerDown={handleResizeStart}
            role="separator"
            aria-orientation="vertical"
            aria-label={t('resizeSidebar')}
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
          className="fixed inset-0 z-30 md:hidden glass-overlay animate-fade-in"
          onPointerDown={(e) => {
            if (e.target === e.currentTarget) onClose();
          }}
          aria-hidden="true"
        />
      )}
    </>
  );
}
