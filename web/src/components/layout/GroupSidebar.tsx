import { useMemo } from "react";
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

export interface GroupSidebarProps {
  orderedGroups: GroupMeta[];
  groupOrder: string[];
  selectedGroupId: string;
  isOpen: boolean;
  isCollapsed: boolean;
  isDark: boolean;
  readOnly?: boolean;
  onSelectGroup: (groupId: string) => void;
  onWarmGroup?: (groupId: string) => void;
  onCreateGroup?: () => void;
  onClose: () => void;
  onToggleCollapse: () => void;
  onReorder: (fromIndex: number, toIndex: number) => void;
}

export function GroupSidebar({
  orderedGroups,
  groupOrder,
  selectedGroupId,
  isOpen,
  isCollapsed,
  isDark,
  readOnly,
  onSelectGroup,
  onWarmGroup,
  onCreateGroup,
  onClose,
  onToggleCollapse,
  onReorder,
}: GroupSidebarProps) {
  const { t } = useTranslation('layout');
  const appBrandName = getAppBrandName();
  const appLogoPath = getAppLogoPath();

  // Memoize sortable item IDs to avoid unnecessary re-renders
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

  return (
    <>
      <aside
        className={classNames(
          "h-full flex flex-col glass-sidebar",
          "fixed md:relative z-40 transition-[width,transform] duration-300 ease-out",
          isCollapsed ? "w-[60px]" : "w-[280px]",
          isOpen ? "translate-x-0" : "-translate-x-full",
          "md:translate-x-0"
        )}
      >
        {/* Header */}
        <div className="p-4 pb-2">
          <div
            className={classNames(
              "flex items-center",
              isCollapsed ? "justify-center" : "justify-between"
            )}
          >
            <div className={classNames("flex items-center min-w-0", isCollapsed ? "" : "flex-1 gap-3 pr-3")}>
              <div className={classNames(
                "w-12 h-12 rounded-2xl flex items-center justify-center glass-btn",
                "text-[var(--color-accent-primary)]"
              )}>
                <img src={appLogoPath} alt={`${appBrandName} Logo`} className="w-8 h-8 object-contain" />
              </div>
              {!isCollapsed && (
                <span className="min-w-0 truncate text-lg font-bold tracking-tight text-[var(--color-text-primary)]">{appBrandName}</span>
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
                {/* Collapse button - desktop only */}
                <button
                  className={classNames(
                    "hidden md:flex p-2 min-w-[36px] min-h-[36px] items-center justify-center rounded-xl transition-all glass-btn",
                    "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]",
                                isDark ? "hover:bg-white/5" : "hover:bg-black/5"
                  )}
                  onClick={onToggleCollapse}
                  aria-label={t('collapseSidebar')}
                  title={t('collapseSidebar')}
                >
                  <ChevronLeftIcon size={16} />
                </button>
                {/* Close button - mobile only */}
                <button
                  className={classNames(
                    "md:hidden p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl transition-all glass-btn",
                    "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
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

        {/* Collapsed: expand button and new button */}
        {isCollapsed && (
          <div className="p-2 flex flex-col items-center gap-2">
            <button
              className={classNames(
                "w-11 h-11 rounded-xl flex items-center justify-center transition-all glass-btn",
                "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
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

        {/* Group list */}
        <div className={classNames(
          "flex-1 overflow-auto",
          isCollapsed ? "p-2" : "p-3"
        )}>
          {!isCollapsed && (
            <div className="text-[10px] font-semibold uppercase tracking-[0.15em] mb-3 px-2 text-[var(--color-text-muted)]">
              {t('workingGroups')}
            </div>
          )}

          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={sortableIds}
              strategy={verticalListSortingStrategy}
            >
              <div className={classNames(
                isCollapsed ? "flex flex-col items-center gap-2" : "space-y-1"
              )}>
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

          {/* Empty state */}
          {!orderedGroups.length && !isCollapsed && (
            <div className="p-6 text-center">
              <div className={classNames(
                "w-16 h-16 mx-auto mb-4 rounded-2xl flex items-center justify-center glass-card",
                "text-[var(--color-text-muted)]"
              )}>
                <FolderIcon size={32} />
              </div>
              <div className="text-sm mb-2 font-medium text-[var(--color-text-secondary)]">{t('noGroupsYet')}</div>
              <div className="text-xs mb-5 max-w-[200px] mx-auto leading-relaxed text-[var(--color-text-muted)]">
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
      </aside>

      {/* Sidebar overlay for mobile */}
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
