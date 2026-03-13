import { useTranslation } from 'react-i18next';
import { Actor, DoneHubStatus, GroupDoc } from "../../types";
import { getGroupStatusUnified } from "../../utils/groupStatus";
import { classNames } from "../../utils/classNames";
import { useModalA11y } from "../../hooks/useModalA11y";
import { LanguageSwitcher } from "../LanguageSwitcher";
import {
  SearchIcon,
  ClipboardIcon,
  SettingsIcon,
  SunIcon,
  MoonIcon,
  EditIcon,
  PlayIcon,
  StopIcon,
  PauseIcon,
  CloseIcon,
} from "../Icons";

export interface MobileMenuSheetProps {
  isOpen: boolean;
  isDark: boolean;
  selectedGroupId: string;
  groupDoc: GroupDoc | null;
  selectedGroupRunning: boolean;
  actors: Actor[];
  busy: string;
  doneHub?: {
    status: DoneHubStatus;
    displayName: string;
    quota: number | null;
    errorMessage: string;
  };
  onClose: () => void;
  onToggleTheme: () => void;
  onOpenSearch: () => void;
  onOpenContext: () => void;
  onOpenSettings: () => void;
  onOpenDoneHubAuth: () => void;
  onOpenGroupEdit?: () => void;
  onStartGroup: () => void;
  onStopGroup: () => void;
  onSetGroupState: (state: "active" | "paused" | "idle") => void | Promise<void>;
}

export function MobileMenuSheet({
  isOpen,
  isDark,
  selectedGroupId,
  groupDoc,
  selectedGroupRunning,
  actors,
  busy,
  doneHub,
  onClose,
  onToggleTheme,
  onOpenSearch,
  onOpenContext,
  onOpenSettings,
  onOpenDoneHubAuth,
  onOpenGroupEdit,
  onStartGroup,
  onStopGroup,
  onSetGroupState,
}: MobileMenuSheetProps) {
  const { modalRef } = useModalA11y(isOpen, onClose);
  const { t } = useTranslation('layout');
  const doneHubStatus = doneHub?.status || "idle";
  const doneHubConnected = doneHubStatus === "connected" || doneHubStatus === "refreshing";
  const doneHubQuota = doneHub && doneHub.quota != null
    ? new Intl.NumberFormat().format(Number(doneHub.quota || 0))
    : "0";
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 md:hidden animate-fade-in">
      <div
        className="absolute inset-0 glass-overlay"
        onPointerDown={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
        aria-hidden="true"
      />

      <div
        ref={modalRef}
        className="absolute bottom-0 left-0 right-0 rounded-t-3xl glass-modal animate-slide-up transform transition-transform"
        role="dialog"
        aria-modal="true"
        aria-label={t('menu')}
      >
        <div className="flex justify-center pt-3 pb-1" onClick={onClose}>
          <div className="w-12 h-1.5 rounded-full bg-black/15 dark:bg-white/20" />
        </div>

        <div className="px-6 pb-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className={classNames("text-lg font-bold truncate", "text-[var(--color-text-primary)]")}>
              {groupDoc?.title || (selectedGroupId ? selectedGroupId : t('menu'))}
            </div>
            {selectedGroupId && groupDoc && (
              <div className="flex items-center gap-2 mt-1">
                {(() => {
                  const status = getGroupStatusUnified(selectedGroupRunning, groupDoc.state);
                  return (
                    <span className={classNames("text-xs px-2 py-0.5 rounded-full font-medium", status.pillClass)}>
                      {status.label}
                    </span>
                  );
                })()}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className={classNames(
              "p-2 rounded-full transition-colors glass-btn",
              "text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
            )}
            aria-label={t('closeMenu')}
          >
            <CloseIcon size={20} />
          </button>
        </div>

        <div className="p-4 space-y-2 safe-area-inset-bottom">
          {!selectedGroupId && (
            <div className={classNames("text-sm px-1 pb-2", "text-[var(--color-text-tertiary)]")}>
              {t('selectGroupToEnable')}
            </div>
          )}

          <button
            className={classNames(
              "w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50 glass-btn",
              "text-[var(--color-text-primary)]"
            )}
            onClick={() => {
              onClose();
              onOpenSearch();
            }}
            disabled={!selectedGroupId}
          >
            <SearchIcon size={18} />
            <span>{t('searchMessagesButton')}</span>
          </button>

          <div className="grid grid-cols-2 gap-2">
            <button
              className={classNames(
                "w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50 glass-btn",
                "text-[var(--color-text-primary)]"
              )}
              onClick={() => {
                onClose();
                onOpenContext();
              }}
              disabled={!selectedGroupId}
            >
              <ClipboardIcon size={18} />
              <span>{t('contextButton')}</span>
            </button>

            <button
              className={classNames(
                "w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50 glass-btn",
                "text-[var(--color-text-primary)]"
              )}
              onClick={() => {
                onClose();
                onOpenSettings();
              }}
              disabled={!selectedGroupId}
            >
              <SettingsIcon size={18} />
              <span>{t('settingsButton')}</span>
            </button>
          </div>

          <button
            className={classNames(
              "w-full flex items-center justify-between gap-3 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] glass-btn border",
              doneHubConnected
                ? "border-cyan-400/25 bg-cyan-500/10 text-cyan-700 dark:text-cyan-300"
                : doneHubStatus === "error"
                  ? "border-rose-400/25 bg-rose-500/10 text-rose-700 dark:text-rose-300"
                  : "border-[var(--glass-border-subtle)] text-[var(--color-text-primary)]"
            )}
            onClick={() => {
              onClose();
              onOpenDoneHubAuth();
            }}
          >
            <span>{t("doneHubEntry")}</span>
            <span className="text-xs font-semibold">
              {doneHubConnected
                ? t("doneHubBalanceInline", { value: doneHubQuota })
                : t(doneHubStatus === "error" ? "doneHubErrorShort" : "doneHubConnect")}
            </span>
          </button>

          <div className="grid grid-cols-2 gap-2">
            <LanguageSwitcher
              isDark={isDark}
              showLabel
              className="text-[var(--color-text-primary)]"
            />

            <button
              className={classNames(
                "w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50 glass-btn",
                "text-[var(--color-text-primary)]"
              )}
              onClick={onToggleTheme}
            >
              {isDark ? <SunIcon size={18} /> : <MoonIcon size={18} />}
              <span>{isDark ? t('lightMode') : t('darkMode')}</span>
            </button>
          </div>

          {onOpenGroupEdit ? (
            <button
              className={classNames(
                "w-full flex items-center justify-center gap-3 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50 glass-btn",
                "text-[var(--color-text-primary)]"
              )}
              onClick={() => {
                onClose();
                onOpenGroupEdit();
              }}
              disabled={!selectedGroupId}
            >
              <EditIcon size={18} />
              <span>{t('editGroupDetails')}</span>
            </button>
          ) : null}

          <div className="h-px my-3 mx-2 bg-[var(--glass-border-subtle)]" />

          <div className="grid grid-cols-2 gap-2">
            <button
              className="w-full flex flex-col items-center justify-center gap-2 px-2 py-3 rounded-2xl text-sm font-medium transition-all min-h-[64px] disabled:opacity-50 glass-btn-accent text-emerald-700 dark:text-emerald-300"
              style={{
                '--glass-accent-bg': 'var(--glass-accent-emerald-bg, rgba(16, 185, 129, 0.1))',
                '--glass-accent-border': 'var(--glass-accent-emerald-border, rgba(16, 185, 129, 0.2))',
                '--glass-accent-glow': 'var(--glass-accent-emerald-glow, 0 0 16px rgba(16, 185, 129, 0.1))',
              } as React.CSSProperties}
              onClick={() => {
                onClose();
                onStartGroup();
              }}
              disabled={!selectedGroupId || busy === "group-start" || actors.length === 0}
            >
              <PlayIcon size={20} />
              <span>{t('launchAll')}</span>
            </button>

            <button
              className={classNames(
                "w-full flex flex-col items-center justify-center gap-2 px-2 py-3 rounded-2xl text-sm font-medium transition-all min-h-[64px] disabled:opacity-50 glass-btn",
                "text-[var(--color-text-secondary)]"
              )}
              onClick={() => {
                onClose();
                onStopGroup();
              }}
              disabled={!selectedGroupId || busy === "group-stop"}
            >
              <StopIcon size={20} />
              <span>{t('quitAll')}</span>
            </button>
          </div>

          {groupDoc?.state === "paused" ? (
            <button
              className="w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50 glass-btn-accent text-amber-700 dark:text-amber-300"
              style={{
                '--glass-accent-bg': 'var(--glass-accent-amber-bg, rgba(245, 158, 11, 0.1))',
                '--glass-accent-border': 'var(--glass-accent-amber-border, rgba(245, 158, 11, 0.2))',
                '--glass-accent-glow': 'var(--glass-accent-amber-glow, 0 0 16px rgba(245, 158, 11, 0.1))',
              } as React.CSSProperties}
              onClick={() => {
                onClose();
                void onSetGroupState("active");
              }}
              disabled={!selectedGroupId || busy === "group-state"}
            >
              <PlayIcon size={18} />
              <span>{t('resumeMessageDelivery')}</span>
            </button>
          ) : (
            <button
              className={classNames(
                "w-full flex items-center justify-center gap-2 px-4 py-3.5 rounded-2xl text-sm font-medium transition-all min-h-[52px] disabled:opacity-50 glass-btn",
                "text-[var(--color-text-secondary)]"
              )}
              onClick={() => {
                onClose();
                void onSetGroupState("paused");
              }}
              disabled={!selectedGroupId || busy === "group-state"}
            >
              <PauseIcon size={18} />
              <span>{t('pauseMessageDelivery')}</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
