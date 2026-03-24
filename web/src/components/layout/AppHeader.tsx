import { useTranslation } from 'react-i18next';
import { Actor, DoneHubStatus, GroupDoc, Theme } from "../../types";
import { getGroupStatusUnified } from "../../utils/groupStatus";
import { getGroupControlVisual, getLaunchControlMode } from "../../utils/groupControls";
import { classNames } from "../../utils/classNames";
import { ThemeToggleCompact } from "../ThemeToggle";
import { LanguageSwitcher } from "../LanguageSwitcher";
import { formatDoneHubQuota } from "../../services/doneHub";
import {
  ClipboardIcon,
  SearchIcon,
  RocketIcon,
  PauseIcon,
  StopIcon,
  SettingsIcon,
  EditIcon,
  MoreIcon,
  MenuIcon,
} from "../Icons";

export interface AppHeaderProps {
  isDark: boolean;
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  webReadOnly?: boolean;
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  selectedGroupRunning: boolean;
  actors: Actor[];
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
  onOpenGroupEdit?: () => void;
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
  isDark,
  theme,
  onThemeChange,
  webReadOnly,
  selectedGroupId,
  groupDoc,
  selectedGroupRunning,
  actors,
  busy,
  doneHub,
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
  sseStatus,
}: AppHeaderProps) {
  const { t } = useTranslation('layout');
  const headerIconButtonBaseClass =
    "flex items-center justify-center w-11 h-11 rounded-xl transition-all shrink-0";
  const headerRailClass =
    "flex items-center gap-1 rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-1 shadow-sm backdrop-blur-xl";
  const headerRailButtonClass =
    "flex items-center justify-center w-10 h-10 rounded-xl transition-all shrink-0 border border-transparent bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)] hover:text-[var(--color-text-primary)] disabled:opacity-45 disabled:text-[var(--color-text-tertiary)] disabled:hover:bg-transparent disabled:hover:text-[var(--color-text-tertiary)]";
  const selectedStatus = selectedGroupId ? getGroupStatusUnified(selectedGroupRunning, groupDoc?.state) : null;
  const selectedStatusKey = selectedStatus?.key ?? null;
  const launchMode = getLaunchControlMode(selectedStatusKey);
  const launchControl = getGroupControlVisual(selectedStatusKey, "launch", busy);
  const pauseControl = getGroupControlVisual(selectedStatusKey, "pause", busy);
  const stopControl = getGroupControlVisual(selectedStatusKey, "stop", busy);
  const isGroupBusy = busy.startsWith("group-");
  const launchHardUnavailable = !selectedGroupId || actors.length === 0;
  const pauseHardUnavailable = !selectedGroupId || !selectedGroupRunning;
  const stopHardUnavailable = !selectedGroupId;
  const launchDisabled = launchHardUnavailable || isGroupBusy;
  const pauseDisabled = pauseHardUnavailable || isGroupBusy;
  const stopDisabled = stopHardUnavailable || isGroupBusy;

  const doneHubStatus = doneHub?.status || "idle";
  const doneHubConnected = doneHubStatus === "connected" || doneHubStatus === "refreshing";
  const doneHubIsPro = String(doneHub?.group || "").trim().toLowerCase() === "pro";
  const doneHubQuota = doneHub?.quota != null ? formatDoneHubQuota(doneHub.quota) : formatDoneHubQuota(0);

  const handleLaunchClick = () => {
    if (launchDisabled || selectedStatusKey === "run") return;
    if (launchMode === "activate") {
      void onSetGroupState("active");
      return;
    }
    onStartGroup();
  };

  const handlePauseClick = () => {
    if (pauseDisabled || selectedStatusKey === "paused") return;
    void onSetGroupState("paused");
  };

  const handleStopClick = () => {
    if (stopDisabled || selectedStatusKey === "stop") return;
    onStopGroup();
  };

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
          aria-label={t('openSidebar')}
        >
          <MenuIcon size={18} />
        </button>

        <div className="min-w-0 flex flex-col">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-semibold truncate text-[var(--color-text-primary)]">
              {groupDoc?.title || (selectedGroupId ? selectedGroupId : t('selectGroup'))}
            </h1>
            {selectedGroupId && sseStatus !== "connected" && (
              <span
                className={classNames(
                  "flex-shrink-0 w-2 h-2 rounded-full",
                  sseStatus === "connecting" ? "bg-amber-400 animate-pulse" : "bg-rose-500"
                )}
                title={sseStatus === "connecting" ? t('reconnecting') : t('disconnected')}
              />
            )}
            {selectedStatus && (
              <span
                className={classNames(
                  "w-2 h-2 rounded-full ring-2",
                  selectedStatus.dotClass,
                  "ring-black/10 dark:ring-white/10"
                )}
                title={selectedStatus.label}
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
            onClick={onOpenGroupEdit}
            title={t('editGroup')}
            aria-label={t('editGroup')}
          >
            <EditIcon size={14} />
          </button>
        )}
      </div>

      <div className="flex items-center gap-1">
        {!webReadOnly && (
          <>
            <div className="hidden md:flex items-center gap-1.5 mr-2">
              <div className={headerRailClass}>
                <button
                  onClick={onOpenSearch}
                  disabled={!selectedGroupId}
                  className={headerRailButtonClass}
                  title={t('searchMessages')}
                >
                  <span className="sr-only">{t('searchMessages')}</span>
                  <SearchIcon size={18} />
                </button>

                <button
                  onClick={onOpenContext}
                  disabled={!selectedGroupId}
                  className={headerRailButtonClass}
                  title={t('context')}
                >
                  <span className="sr-only">{t('context')}</span>
                  <ClipboardIcon size={18} />
                </button>
              </div>

              <div className={headerRailClass}>
                <button
                  onClick={handleLaunchClick}
                  disabled={launchDisabled}
                  className={classNames(
                    "flex items-center justify-center w-10 h-10 rounded-xl transition-all shrink-0",
                    launchControl.className,
                    launchHardUnavailable && "opacity-45"
                  )}
                  title={launchMode === "activate" ? t('resumeDelivery') : t('launchAllAgents')}
                  aria-pressed={launchControl.active}
                >
                  <span className="sr-only">{launchMode === "activate" ? t('resumeDelivery') : t('launchAllAgents')}</span>
                  <RocketIcon size={18} />
                </button>

                <button
                  onClick={handlePauseClick}
                  disabled={pauseDisabled}
                  className={classNames(
                    "flex items-center justify-center w-10 h-10 rounded-xl transition-all shrink-0",
                    pauseControl.className,
                    pauseHardUnavailable && "opacity-45"
                  )}
                  title={t('pauseDelivery')}
                  aria-pressed={pauseControl.active}
                >
                  <span className="sr-only">{t('pauseDelivery')}</span>
                  <PauseIcon size={18} />
                </button>

                <button
                  onClick={handleStopClick}
                  disabled={stopDisabled}
                  className={classNames(
                    "flex items-center justify-center w-10 h-10 rounded-xl transition-all shrink-0",
                    stopControl.className,
                    stopHardUnavailable && "opacity-45"
                  )}
                  title={t('stopAllAgents')}
                  aria-pressed={stopControl.active}
                >
                  <span className="sr-only">{t('stopAllAgents')}</span>
                  <StopIcon size={18} />
                </button>
              </div>

              <button
                onClick={onOpenDoneHubAuth}
                className={classNames(
                  "hidden md:flex min-w-0 max-w-[180px] items-center gap-2 rounded-xl border px-3 py-2 transition-all glass-btn",
                  doneHubConnected
                    ? "border-sky-300/45 bg-sky-400/12 text-sky-700 dark:border-sky-400/20 dark:bg-sky-500/12 dark:text-sky-300"
                    : doneHubStatus === "error"
                      ? "border-rose-400/25 bg-rose-500/10 text-rose-700 dark:text-rose-300"
                      : "text-[var(--color-text-secondary)]"
                )}
                title={
                  doneHubConnected
                    ? t("doneHubBalanceTitle", { value: doneHubQuota })
                    : t(doneHubStatus === "error" ? "doneHubNeedsAttention" : "doneHubConnect")
                }
              >
                {doneHubIsPro ? (
                  <span className="rounded-full border border-amber-300/65 bg-amber-400/18 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.22em] text-amber-700 shadow-[0_0_0_1px_rgba(245,158,11,0.18)] dark:border-amber-300/30 dark:bg-amber-300/14 dark:text-amber-200">
                    PRO
                  </span>
                ) : null}
                <span className="min-w-0 truncate text-sm font-medium">
                  {doneHubConnected
                    ? t("doneHubBalanceInline", { value: doneHubQuota })
                    : t(doneHubStatus === "error" ? "doneHubErrorShort" : "doneHubConnect")}
                </span>
              </button>

              <div className={headerRailClass}>
                <ThemeToggleCompact theme={theme} onThemeChange={onThemeChange} isDark={isDark} variant="rail" />
                <LanguageSwitcher isDark={isDark} variant="rail" />
                <button
                  onClick={onOpenSettings}
                  disabled={!selectedGroupId}
                  className={headerRailButtonClass}
                  title={t('settings')}
                >
                  <span className="sr-only">{t('settings')}</span>
                  <SettingsIcon size={18} />
                </button>
              </div>
            </div>

            <button
              className={classNames(
                "md:hidden",
                headerIconButtonBaseClass,
                "glass-btn",
                "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
              )}
              onClick={onOpenMobileMenu}
              title={t('menu')}
            >
              <MoreIcon size={18} />
            </button>
          </>
        )}
      </div>
    </header>
  );
}
