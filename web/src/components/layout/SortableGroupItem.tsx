import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GroupMeta } from "../../types";
import { classNames } from "../../utils/classNames";
import { getGroupStatusLabel } from "../../utils/displayText";
import { GripIcon } from "../Icons";

interface SortableGroupItemProps {
  group: GroupMeta;
  isActive: boolean;
  isDark: boolean;
  isCollapsed: boolean;
  dragDisabled?: boolean;
  onSelect: () => void;
  onWarm?: () => void;
}

type StatusKey = "run" | "paused" | "idle" | "stop";

function getStatusInfo(running: boolean, state?: string): { label: string; key: StatusKey; dotClass: string } {
  if (!running) {
    return { label: getGroupStatusLabel("stop"), key: "stop", dotClass: "bg-gray-400 dark:bg-slate-500" };
  }
  switch (state) {
    case "paused":
      return { label: getGroupStatusLabel("paused"), key: "paused", dotClass: "bg-amber-500 dark:bg-amber-400" };
    case "idle":
      return { label: getGroupStatusLabel("idle"), key: "idle", dotClass: "bg-blue-500 dark:bg-blue-400" };
    default:
      return { label: getGroupStatusLabel("run"), key: "run", dotClass: "bg-emerald-500 dark:bg-emerald-400 run-indicator-glow" };
  }
}

export function SortableGroupItem({
  group,
  isActive,
  isDark: _isDark,
  isCollapsed,
  dragDisabled = false,
  onSelect,
  onWarm,
}: SortableGroupItemProps) {
  const gid = String(group.group_id || "");

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

  const status = getStatusInfo(group.running ?? false, group.state);

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
      <button
        className={classNames(
          "w-full text-left px-3 py-3 rounded-xl transition-all min-h-[48px] flex items-center gap-2 relative",
          isDragging && "opacity-70 shadow-lg ring-2 ring-sky-500/25",
          isActive
            ? "glass-group-item-active glow-pulse"
            : "glass-group-item"
        )}
        onClick={onSelect}
        onMouseEnter={onWarm}
        onFocus={onWarm}
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

        <div className="flex-1 min-w-0 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {/* Status dot */}
            <span className={classNames(
              "w-2 h-2 rounded-full flex-shrink-0",
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
              "glass-status-pill text-[9px] px-2.5 py-1 rounded-full font-semibold flex-shrink-0 uppercase",
              `glass-status-pill-${status.key}`
            )}
          >
            {status.label}
          </span>
        </div>
      </button>
    </div>
  );
}
