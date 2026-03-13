import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ModalFrame } from "./ModalFrame";
import { useModalA11y } from "../../hooks/useModalA11y";
import { formatDoneHubQuota } from "../../services/doneHub";
import { useDoneHubStore } from "../../stores";

interface DoneHubAuthModalProps {
  isOpen: boolean;
  isDark: boolean;
  onClose: () => void;
}

export function DoneHubAuthModal({ isOpen, isDark: _isDark, onClose }: DoneHubAuthModalProps) {
  const { t } = useTranslation(["modals", "common"]);
  const { modalRef } = useModalA11y(isOpen, onClose);
  const status = useDoneHubStore((state) => state.status);
  const session = useDoneHubStore((state) => state.session);
  const savedLogin = useDoneHubStore((state) => state.savedLogin);
  const errorMessage = useDoneHubStore((state) => state.errorMessage);
  const connect = useDoneHubStore((state) => state.connect);
  const refresh = useDoneHubStore((state) => state.refresh);
  const disconnect = useDoneHubStore((state) => state.disconnect);
  const clearError = useDoneHubStore((state) => state.clearError);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberPassword, setRememberPassword] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setUsername(session?.username || savedLogin.username || "");
    setPassword(savedLogin.password || "");
    setRememberPassword(savedLogin.remember_password || Boolean(savedLogin.password));
    clearError();
  }, [clearError, isOpen, savedLogin.password, savedLogin.remember_password, savedLogin.username, session?.username]);

  const connected = status === "connected" || status === "refreshing";
  const hasSession = !!session;
  const showConnectedView = connected && hasSession;
  const busy = status === "authenticating" || status === "refreshing";
  const submitDisabled = busy || !username.trim() || !password;
  const title = showConnectedView ? t("modals:doneHub.connectedTitle") : t("modals:doneHub.connectTitle");
  const quotaValue = useMemo(() => formatDoneHubQuota(session?.quota), [session?.quota]);
  const usedQuotaValue = useMemo(() => formatDoneHubQuota(session?.used_quota), [session?.used_quota]);

  if (!isOpen) return null;

  return (
    <ModalFrame
      isDark={_isDark}
      onClose={onClose}
      titleId="account-auth-title"
      title={title}
      closeAriaLabel={t("common:close")}
      panelClassName="w-full sm:max-w-xl min-h-[420px] max-h-[min(88dvh,720px)]"
      modalRef={modalRef}
    >
      <div className="flex-1 overflow-y-auto px-5 py-5">
        <div className="rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="text-lg font-semibold text-[var(--color-text-primary)]">
                {showConnectedView ? t("modals:doneHub.connectedState") : t("modals:doneHub.idleState")}
              </div>
              <div className="mt-1 text-sm text-[var(--color-text-secondary)]">
                {showConnectedView ? t("modals:doneHub.connectedHint") : t("modals:doneHub.connectHint")}
              </div>
            </div>
            <div className="rounded-full border border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)] px-3 py-1 text-xs text-[var(--color-text-secondary)]">
              {busy
                ? status === "refreshing"
                  ? t("modals:doneHub.refreshing")
                  : t("modals:doneHub.connecting")
                : connected
                  ? t("modals:doneHub.connectedBadge")
                  : t("modals:doneHub.disconnectedBadge")}
            </div>
          </div>

          {showConnectedView && session ? (
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">
                  {t("modals:doneHub.balance")}
                </div>
                <div className="mt-2 text-2xl font-semibold text-[var(--color-text-primary)]">{quotaValue}</div>
                <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                  {t("modals:doneHub.usedQuotaInline", { value: usedQuotaValue })}
                </div>
              </div>
              <div className="rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">
                  {t("modals:doneHub.account")}
                </div>
                <div className="mt-2 text-sm font-medium text-[var(--color-text-primary)]">
                  {session.display_name || session.username || t("modals:doneHub.unknownUser")}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        {errorMessage ? (
          <div className="mt-4 rounded-xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-500 dark:text-rose-300">
            {errorMessage}
          </div>
        ) : null}

        {!showConnectedView ? (
          <form
            className="mt-4 space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void connect(username, password, rememberPassword).then((ok) => {
                if (ok && !rememberPassword) {
                  setPassword("");
                }
              });
            }}
          >
            <div>
              <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                {t("modals:doneHub.username")}
              </label>
              <input
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder={t("modals:doneHub.usernamePlaceholder")}
                className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
              />
            </div>

            <div>
              <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                {t("modals:doneHub.password")}
              </label>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={t("modals:doneHub.passwordPlaceholder")}
                className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
              />
            </div>

            <label className="flex items-center gap-3 rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-3 text-sm text-[var(--color-text-primary)]">
              <input
                type="checkbox"
                checked={rememberPassword}
                onChange={(event) => setRememberPassword(event.target.checked)}
                className="h-4 w-4 rounded border border-[var(--glass-border-subtle)]"
              />
              <span>{t("modals:doneHub.rememberPassword")}</span>
            </label>

            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="glass-btn rounded-xl px-4 py-2.5 text-sm text-[var(--color-text-secondary)]"
              >
                {t("common:cancel")}
              </button>
              <button
                type="submit"
                disabled={submitDisabled}
                className="glass-btn-accent rounded-xl px-4 py-2.5 text-sm font-medium text-[var(--color-accent-primary)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {status === "authenticating" ? t("modals:doneHub.connecting") : t("modals:doneHub.connectAction")}
              </button>
            </div>
          </form>
        ) : (
          <div className="mt-4 rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-4">
            <div className="text-sm text-[var(--color-text-secondary)]">{t("modals:doneHub.disconnectHint")}</div>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={busy}
                className="glass-btn rounded-xl px-4 py-2.5 text-sm text-[var(--color-text-secondary)] disabled:opacity-50"
              >
                {status === "refreshing" ? t("modals:doneHub.refreshing") : t("modals:doneHub.refreshAction")}
              </button>
              <button
                type="button"
                onClick={() => {
                  disconnect();
                  onClose();
                }}
                className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-600 transition-colors hover:bg-rose-500/15 dark:text-rose-300"
              >
                {t("modals:doneHub.disconnectAction")}
              </button>
            </div>
          </div>
        )}
      </div>
    </ModalFrame>
  );
}
