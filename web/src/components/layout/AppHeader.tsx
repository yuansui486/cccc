import { useTranslation } from "react-i18next";
import { DoneHubStatus, GroupDoc, GroupRuntimeStatus, TextScale, Theme } from "../../types";
import { getGroupStatusFromSource } from "../../utils/groupStatus";
import { classNames } from "../../utils/classNames";
import {
  EditIcon,
  MoreIcon,
  MenuIcon,
} from "../Icons";

export interface AppHeaderProps {
  isDark: boolean;
  theme: Theme;
  textScale: TextScale;
  onThemeChange: (theme: Theme) => void;
  onTextScaleChange: (scale: TextScale) => void;
  webReadOnly?: boolean;
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  selectedGroupRunning: boolean;
  selectedGroupRuntimeStatus: GroupRuntimeStatus | null;
  actors: unknown[];
  sseStatus: "connected" | "connecting" | "disconnected";
  busy: string;
  doneHub?: {
    status: DoneHubStatus;
    displayName: string;
    group?: string;
    quota: number | null;
    errorMessage: string;
  };
  onOpenSidebar: () => void;
  onOpenGroupEdit?: (groupId?: string) => void;
  onOpenSearch: () => void;
  onOpenContext: () => void;
  onStartGroup: () => void;
  onStopGroup: () => void;
  onSetGroupState: (state: "active" | "paused" | "idle") => void | Promise<void>;
  onOpenSettings: () => void;
  onOpenDoneHubAuth: () => void;
  onOpenMobileMenu: () => void;
}

export function AppHeader({
  isDark: _isDark,
  theme: _theme,
  textScale: _textScale,
  onThemeChange: _onThemeChange,
  onTextScaleChange: _onTextScaleChange,
  webReadOnly,
  selectedGroupId,
  groupDoc,
  selectedGroupRunning,
  selectedGroupRuntimeStatus,
  actors: _actors,
  busy: _busy,
  doneHub: _doneHub,
  onOpenSidebar,
  onOpenGroupEdit,
  onOpenSearch: _onOpenSearch,
  onOpenContext: _onOpenContext,
  onStartGroup: _onStartGroup,
  onStopGroup: _onStopGroup,
  onSetGroupState: _onSetGroupState,
  onOpenSettings: _onOpenSettings,
  onOpenDoneHubAuth: _onOpenDoneHubAuth,
  onOpenMobileMenu,
  sseStatus,
}: AppHeaderProps) {
  const { t } = useTranslation("layout");
  const headerIconButtonBaseClass =
    "flex items-center justify-center w-11 h-11 rounded-xl transition-all shrink-0";
  const selectedStatus = selectedGroupId ? getGroupStatusFromSource({
    running: selectedGroupRunning,
    state: (selectedGroupRuntimeStatus?.lifecycle_state as GroupDoc["state"] | undefined) || groupDoc?.state,
    runtime_status: selectedGroupRuntimeStatus || undefined,
  }) : null;

  return (
    <header className="flex-shrink-0 z-20 px-4 h-14 flex items-center justify-between gap-3 glass-header">
      <div className="flex items-center gap-3 min-w-0">
        <button
          className={classNames(
            "md:hidden -ml-1",
            headerIconButtonBaseClass,
            "glass-btn",
            "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          )}
          onClick={onOpenSidebar}
          aria-label={t("openSidebar")}
        >
          <MenuIcon size={18} />
        </button>

        <div className="min-w-0 flex flex-col">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-semibold truncate text-[var(--color-text-primary)]">
              {groupDoc?.title || (selectedGroupId ? selectedGroupId : t("selectGroup"))}
            </h1>
            {selectedStatus && (
              <span
                className={classNames(
                  "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold flex-shrink-0",
                  selectedStatus.pillClass
                )}
                title={selectedStatus.label}
              >
                {selectedStatus.label}
              </span>
            )}
            {selectedGroupId && sseStatus !== "connected" && (
              <span
                className={classNames(
                  "flex-shrink-0 w-2 h-2 rounded-full",
                  sseStatus === "connecting" ? "bg-amber-400 animate-pulse" : "bg-rose-500"
                )}
                title={sseStatus === "connecting" ? t("reconnecting") : t("disconnected")}
              />
            )}
          </div>
        </div>

        {selectedGroupId && !webReadOnly && onOpenGroupEdit && (
          <button
            className={classNames(
              "hidden md:inline-flex items-center justify-center gap-1 text-xs px-2.5 py-1.5 rounded-xl transition-all glass-btn",
              "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            )}
            onClick={() => onOpenGroupEdit()}
            title={t("editGroup")}
            aria-label={t("editGroup")}
          >
            <EditIcon size={14} />
          </button>
        )}
      </div>

      <div className="flex items-center gap-1">
        {!webReadOnly && (
          <>
            <button
              className={classNames(
                "md:hidden",
                headerIconButtonBaseClass,
                "glass-btn",
                "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              )}
              onClick={onOpenMobileMenu}
              title={t("menu")}
            >
              <MoreIcon size={18} />
            </button>
          </>
        )}
      </div>
    </header>
  );
}
