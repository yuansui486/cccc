import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useDoneHubStore } from "../stores";
import { classNames } from "../utils/classNames";
import { getAppBrandName, getAppLogoPath } from "../utils/displayText";

interface DoneHubLoginGateProps {
  isDark: boolean;
}

export function DoneHubLoginGate({ isDark }: DoneHubLoginGateProps) {
  const { t } = useTranslation("modals");
  const initialized = useDoneHubStore((state) => state.initialized);
  const status = useDoneHubStore((state) => state.status);
  const savedLogin = useDoneHubStore((state) => state.savedLogin);
  const errorMessage = useDoneHubStore((state) => state.errorMessage);
  const connect = useDoneHubStore((state) => state.connect);
  const clearError = useDoneHubStore((state) => state.clearError);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberPassword, setRememberPassword] = useState(false);
  const appBrandName = getAppBrandName();
  const appLogoPath = getAppLogoPath();

  useEffect(() => {
    setUsername(savedLogin.username || "");
    setPassword(savedLogin.password || "");
    setRememberPassword(savedLogin.remember_password || Boolean(savedLogin.password));
  }, [savedLogin.password, savedLogin.remember_password, savedLogin.username]);

  useEffect(() => {
    clearError();
  }, [clearError]);

  const busy = !initialized || status === "authenticating" || status === "refreshing";
  const submitDisabled = busy || !username.trim() || !password;

  return (
    <div
      className={classNames(
        "relative flex min-h-screen items-center justify-center overflow-hidden px-6 py-10",
        isDark ? "bg-black text-slate-100" : "bg-gradient-to-br from-slate-50 via-white to-slate-100",
      )}
    >
      <div className="pointer-events-none absolute inset-0 hidden md:block">
        <div className="absolute -top-24 left-8 h-72 w-72 rounded-full bg-emerald-400/12 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-80 w-80 rounded-full bg-blue-500/12 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 h-64 w-64 rounded-full bg-blue-500/10 blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-md rounded-[32px] border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-5 shadow-[0_24px_80px_rgba(15,23,42,0.12)] backdrop-blur-xl md:p-6">
        <div className="rounded-[28px] border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-5 py-6 md:px-6">
          <div className="mb-6 flex items-center justify-center gap-3">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] shadow-[var(--glass-shadow)]">
              <img src={appLogoPath} alt={`${appBrandName} Logo`} className="h-9 w-9 object-contain" />
            </div>
            <div className="text-2xl font-semibold tracking-tight text-[var(--color-text-primary)]">{appBrandName}</div>
          </div>

          {busy ? (
            <div className="flex min-h-[320px] flex-col items-center justify-center text-center">
              <div className="h-12 w-12 animate-spin rounded-full border-2 border-emerald-400/25 border-t-blue-500" />
            </div>
          ) : (
            <>
              {errorMessage ? (
                <div className="rounded-2xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-500 dark:text-rose-300">
                  {errorMessage}
                </div>
              ) : null}

              <form
                className={classNames("space-y-4", errorMessage ? "mt-4" : "")}
                onSubmit={(event) => {
                  event.preventDefault();
                  void connect(username, password, rememberPassword);
                }}
              >
                <div>
                  <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                    {t("doneHub.username")}
                  </label>
                  <input
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    placeholder={t("doneHub.usernamePlaceholder")}
                    className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
                    autoComplete="username"
                  />
                </div>

                <div>
                  <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                    {t("doneHub.password")}
                  </label>
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder={t("doneHub.passwordPlaceholder")}
                    className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
                    autoComplete="current-password"
                  />
                </div>

                <label className="flex items-center gap-3 rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-3 text-sm text-[var(--color-text-primary)]">
                  <input
                    type="checkbox"
                    checked={rememberPassword}
                    onChange={(event) => setRememberPassword(event.target.checked)}
                    className="h-4 w-4 rounded border border-[var(--glass-border-subtle)]"
                  />
                  <span>{t("doneHub.rememberPassword")}</span>
                </label>

                <button
                  type="submit"
                  disabled={submitDisabled}
                  className="glass-btn-accent w-full rounded-2xl px-4 py-3 text-sm font-medium text-[var(--color-accent-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("doneHub.connectAction")}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
