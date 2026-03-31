import { useCallback, useState } from "react";
import {
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  useClick,
  useDismiss,
  useFloating,
  useInteractions,
  useRole,
} from "@floating-ui/react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GroupMeta } from "../../types";
import { classNames } from "../../utils/classNames";
import { getGroupStatusUnified } from "../../utils/groupStatus";
import { GripIcon, MoreIcon } from "../Icons";

interface SortableGroupItemProps {
  group: GroupMeta;
  isActive: boolean;
  isDark: boolean;
  isCollapsed: boolean;
  isArchived?: boolean;
  dragDisabled?: boolean;
  menuActionLabel?: string;
  menuAriaLabel?: string;
  onMenuAction?: () => void;
  onSelect: () => void;
  onWarm?: () => void;
}

export function SortableGroupItem({
  group,
  isActive,
  isDark: _isDark,
  isCollapsed,
  isArchived = false,
  dragDisabled = false,
  menuActionLabel,
  menuAriaLabel,
  onMenuAction,
  onSelect,
  onWarm,
}: SortableGroupItemProps) {
  const gid = String(group.group_id || "");
  const [menuOpen, setMenuOpen] = useState(false);

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: gid, disabled: dragDisabled });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const status = getGroupStatusUnified(group.running ?? false, group.state);
  const { refs, floatingStyles, context } = useFloating({
    open: menuOpen,
    onOpenChange: setMenuOpen,
    placement: "bottom-end",
    middleware: [offset(8), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
    strategy: "fixed",
  });
  const click = useClick(context);
  const dismiss = useDismiss(context);
  const role = useRole(context, { role: "menu" });
  const { getReferenceProps, getFloatingProps } = useInteractions([click, dismiss, role]);
  const setReference = useCallback((node: HTMLElement | null) => refs.setReference(node), [refs]);
  const setFloating = useCallback((node: HTMLElement | null) => refs.setFloating(node), [refs]);

  if (isCollapsed) {
    const initial = (group.title || gid).charAt(0).toUpperCase();
    return (
      <div ref={setNodeRef} style={style} {...attributes}>
        <button
          className={classNames(
            "w-11 h-11 rounded-xl flex items-center justify-center transition-all relative",
            isDragging && "opacity-50 shadow-lg",
            isActive
              ? "glass-group-item-active glow-pulse"
              : "glass-group-item hover:scale-105"
          )}
          onClick={onSelect}
          onMouseEnter={onWarm}
          onFocus={onWarm}
          title={group.title || gid}
        >
          <span
            className={classNames(
              "text-sm font-semibold",
              isActive
                ? "text-sky-700 dark:text-sky-300"
                : "text-[var(--color-text-secondary)]"
            )}
          >
            {initial}
          </span>
          {/* Status dot */}
          <span className={classNames(
            "absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-[var(--color-bg-primary)]",
            status.dotClass
          )} />
        </button>
      </div>
    );
  }

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      className={classNames(
        "group/item relative",
        isDragging && "z-50"
      )}
    >
      <div
        className={classNames(
          "w-full text-left px-3 py-3 rounded-xl transition-all min-h-[48px] flex items-center gap-2 relative",
          isDragging && "opacity-70 shadow-lg ring-2 ring-cyan-500/30",
          isActive
            ? "glass-group-item-active glow-pulse"
            : "glass-group-item",
          isArchived && !isActive && "opacity-90"
        )}
      >
        {/* Drag handle */}
        {!dragDisabled && (
          <div
            {...listeners}
            className={classNames(
              "flex-shrink-0 cursor-grab active:cursor-grabbing p-1 -ml-1 rounded transition-opacity touch-none",
              "hidden md:block md:opacity-0 md:group-hover/item:opacity-100",
              isDragging && "!block !opacity-100",
              "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <GripIcon size={14} />
          </div>
        )}

        <button
          type="button"
          className="flex-1 min-w-0 flex items-center justify-between gap-2 text-left"
          onClick={onSelect}
          onMouseEnter={onWarm}
          onFocus={onWarm}
        >
          <div className="flex items-center gap-2 min-w-0">
            {/* Status dot */}
            <span className={classNames(
              "w-2.5 h-2.5 rounded-full flex-shrink-0",
              status.dotClass
            )} />
            <span
              className={classNames(
                "text-sm font-medium truncate",
                isActive
                  ? "text-sky-700 dark:text-sky-300"
                  : "text-[var(--color-text-primary)] group-hover/item:text-[var(--color-text-primary)]"
              )}
            >
              {group.title || gid}
            </span>
          </div>
          <span
            className={classNames(
              "text-[9px] px-2.5 py-1 rounded-full font-semibold flex-shrink-0 uppercase",
              status.pillClass
            )}
          >
            {status.label}
          </span>
        </button>

        {onMenuAction && menuActionLabel && (
          <>
            <button
              type="button"
              ref={setReference}
              {...getReferenceProps({
                className: classNames(
                  "flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition-colors glass-btn",
                  isActive
                    ? "text-cyan-700 dark:text-cyan-300"
                    : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
                ),
                "aria-label": menuAriaLabel || menuActionLabel,
                title: menuAriaLabel || menuActionLabel,
              })}
            >
              <MoreIcon size={16} />
            </button>
            <FloatingPortal>
              {menuOpen && (
                <div
                  ref={setFloating}
                  style={floatingStyles}
                  {...getFloatingProps()}
                  className="z-max min-w-[160px] rounded-xl p-1.5 shadow-2xl glass-panel"
                >
                  <button
                    type="button"
                    className={classNames(
                      "w-full rounded-lg px-3 py-2.5 text-left text-sm transition-colors",
                      "text-[var(--color-text-primary)] hover:bg-[var(--glass-tab-bg-hover)]"
                    )}
                    onClick={() => {
                      setMenuOpen(false);
                      onMenuAction();
                    }}
                  >
                    {menuActionLabel}
                  </button>
                </div>
              )}
            </FloatingPortal>
          </>
        )}
      </div>
    </div>
  );
}
