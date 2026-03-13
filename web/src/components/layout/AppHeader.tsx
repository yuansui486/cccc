import { useTranslation } from 'react-i18next';
import { Actor, DoneHubStatus, GroupDoc, Theme } from "../../types";
import { getGroupStatusUnified } from "../../utils/groupStatus";
import { classNames } from "../../utils/classNames";
import { ThemeToggleCompact } from "../ThemeToggle";
import { LanguageSwitcher } from "../LanguageSwitcher";
import { formatDoneHubQuota } from "../../services/doneHub";
import {
  ClipboardIcon,
  SearchIcon,
  RocketIcon,
  PlayIcon,
  PauseIcon,
  StopIcon,
  SettingsIcon,
  EditIcon,
  MoreIcon,
  MenuIcon
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
  const doneHubStatus = doneHub?.status || "idle";
  const doneHubConnected = doneHubStatus === "connected" || doneHubStatus === "refreshing";
  const doneHubIsPro = String(doneHub?.group || "").trim().toLowerCase() === "pro";
  const doneHubQuota = doneHub && doneHub.quota != null
    ? formatDoneHubQuota(doneHub.quota)
    : formatDoneHubQuota(0);
  return (
    <header
      className="flex-shrink-0 z-20 px-4 h-14 flex items-center justify-between gap-3 glass-header"
    >
      <div className="flex items-center gap-3 min-w-0">
        <button
          className={classNames(
            "md:hidden p-2 -ml-2 rounded-xl transition-all glass-btn",
            "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
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
            {selectedGroupId &&
              groupDoc &&
              (() => {
                const status = getGroupStatusUnified(selectedGroupRunning, groupDoc.state);
                return (
                  <span
                    className={classNames(
                      "w-2 h-2 rounded-full ring-2",
                      status.dotClass,
                      "ring-black/10 dark:ring-white/10"
                    )}
                    title={status.label}
                  />
                );
              })()}
          </div>
        </div>

        {selectedGroupId && !webReadOnly && onOpenGroupEdit && (
          <button
            className={classNames(
              "hidden md:inline-flex items-center justify-center gap-1 text-xs px-2.5 py-1.5 rounded-xl transition-all glass-btn",
              "text-[var(--color-text-secondary)]"
            )}
            onClick={onOpenGroupEdit}
            title={t('editGroup')}
            aria-label={t('editGroup')}
          >
            <EditIcon size={14} />
          </button>
        )}
      </div>

      {/* Right Actions */}
      <div className="flex items-center gap-1">
        {!webReadOnly && (
          <>
            {/* Desktop Actions */}
            <div className="hidden md:flex items-center gap-1.5 mr-2">
              <button
                onClick={onOpenSearch}
                disabled={!selectedGroupId}
                className={classNames(
                  "p-2 rounded-xl transition-all glass-btn",
                  "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                )}
                title={t('searchMessages')}
              >
                <span className="sr-only">{t('searchMessages')}</span>
                <SearchIcon size={18} />
              </button>

              <button
                onClick={onOpenContext}
                disabled={!selectedGroupId}
                className={classNames(
                  "p-2 rounded-xl transition-all glass-btn",
                  "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                )}
                title={t('context')}
              >
                <span className="sr-only">{t('context')}</span>
                <ClipboardIcon size={18} />
              </button>

              <div className="w-px h-4 mx-1 bg-black/10 dark:bg-white/10" />

              <button
                onClick={onStartGroup}
                disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
                className={classNames(
                  "p-2 rounded-xl transition-all glass-btn border shadow-sm hover:-translate-y-px active:translate-y-0",
                  "border-emerald-200/70 bg-emerald-50/75 text-emerald-700 shadow-emerald-100/80 hover:bg-emerald-100/80 hover:shadow-emerald-200/70",
                  "dark:border-emerald-400/15 dark:bg-emerald-500/12 dark:text-emerald-300 dark:shadow-[0_8px_24px_-16px_rgba(16,185,129,0.45)] dark:hover:bg-emerald-500/18"
                )}
                title={t('launchAllAgents')}
              >
                <span className="sr-only">{t('launchAllAgents')}</span>
                <RocketIcon size={18} className="drop-shadow-[0_1px_3px_rgba(16,185,129,0.22)]" />
              </button>

              {groupDoc?.state === "paused" ? (
                <button
                  onClick={() => void onSetGroupState("active")}
                  disabled={!selectedGroupId || busy === "group-state"}
                  className={classNames(
                    "p-2 rounded-xl transition-all glass-btn",
                    "text-amber-600 dark:text-amber-400"
                  )}
                  title={t('resumeDelivery')}
                >
                  <span className="sr-only">{t('resumeDelivery')}</span>
                  <PlayIcon size={18} />
                </button>
              ) : (
                <button
                  onClick={() => void onSetGroupState("paused")}
                  disabled={!selectedGroupId || busy === "group-state"}
                  className={classNames(
                    "p-2 rounded-xl transition-all glass-btn",
                    "text-gray-400 hover:text-amber-600 dark:text-slate-400 dark:hover:text-amber-300"
                  )}
                  title={t('pauseDelivery')}
                >
                  <span className="sr-only">{t('pauseDelivery')}</span>
                  <PauseIcon size={18} />
                </button>
              )}

              <button
                onClick={onStopGroup}
                disabled={!selectedGroupId || busy === "group-stop"}
                className={classNames(
                  "p-2 rounded-xl transition-all glass-btn",
                  "text-gray-400 hover:text-rose-600 dark:text-slate-400 dark:hover:text-rose-400"
                )}
                title={t('stopAllAgents')}
              >
                <span className="sr-only">{t('stopAllAgents')}</span>
                <StopIcon size={18} />
              </button>
            </div>

            <div className="hidden md:flex items-center gap-1">
              <ThemeToggleCompact theme={theme} onThemeChange={onThemeChange} isDark={isDark} />
            </div>

            <button
              onClick={onOpenDoneHubAuth}
              className={classNames(
                "hidden md:flex min-w-0 max-w-[180px] items-center gap-2 rounded-xl border px-3 py-2 transition-all glass-btn",
                doneHubConnected
                  ? "border-cyan-400/25 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300"
                  : doneHubStatus === "error"
                    ? "border-rose-400/25 bg-rose-500/10 text-rose-700 dark:text-rose-300"
                    : "text-[var(--color-text-secondary)]"
              )}
              title={doneHubConnected
                ? t("doneHubBalanceTitle", { value: doneHubQuota })
                : t(doneHubStatus === "error" ? "doneHubNeedsAttention" : "doneHubConnect")}
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

            <button
              onClick={onOpenSettings}
              disabled={!selectedGroupId}
              className={classNames(
                "hidden md:flex p-2 rounded-xl transition-all glass-btn",
                "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
              )}
              title={t('settings')}
            >
              <SettingsIcon size={18} />
            </button>

            <div className="hidden md:block w-px h-4 bg-black/10 dark:bg-white/10" />
            <div className="hidden md:block">
              <LanguageSwitcher isDark={isDark} />
            </div>

            <button
              className={classNames(
                "md:hidden flex items-center justify-center w-11 h-11 rounded-xl transition-all glass-btn",
                "text-[var(--color-text-muted)]"
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
