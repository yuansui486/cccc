import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { normalizeDoneHubBaseUrl } from "../services/doneHub";
import { useDoneHubStore } from "../stores";
import { classNames } from "../utils/classNames";

interface DoneHubLoginGateProps {
  isDark: boolean;
}

export function DoneHubLoginGate({ isDark }: DoneHubLoginGateProps) {
  const { t } = useTranslation(["modals", "common"]);
  const initialized = useDoneHubStore((state) => state.initialized);
  const status = useDoneHubStore((state) => state.status);
  const savedLogin = useDoneHubStore((state) => state.savedLogin);
  const errorMessage = useDoneHubStore((state) => state.errorMessage);
  const connect = useDoneHubStore((state) => state.connect);
  const clearError = useDoneHubStore((state) => state.clearError);

  const [baseUrl, setBaseUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberPassword, setRememberPassword] = useState(false);

  useEffect(() => {
    setBaseUrl(savedLogin.base_url || "");
    setUsername(savedLogin.username || "");
    setPassword(savedLogin.password || "");
    setRememberPassword(savedLogin.remember_password || Boolean(savedLogin.password));
  }, [savedLogin.base_url, savedLogin.password, savedLogin.remember_password, savedLogin.username]);

  useEffect(() => {
    clearError();
  }, [clearError]);

  const busy = !initialized || status === "authenticating" || status === "refreshing";
  const submitDisabled = busy || !normalizeDoneHubBaseUrl(baseUrl) || !username.trim() || !password;

  return (
    <div
      className={classNames(
        "relative flex min-h-screen items-center justify-center overflow-hidden px-6 py-10",
        isDark ? "bg-black text-slate-100" : "bg-gradient-to-br from-slate-50 via-white to-slate-100",
      )}
    >
      <div className="pointer-events-none absolute inset-0 hidden md:block">
        <div className="absolute -top-24 left-8 h-72 w-72 rounded-full bg-cyan-400/10 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-80 w-80 rounded-full bg-sky-500/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 h-64 w-64 rounded-full bg-indigo-500/10 blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-5xl rounded-[32px] border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-5 shadow-[0_24px_80px_rgba(15,23,42,0.12)] backdrop-blur-xl md:grid md:grid-cols-[1.1fr_0.9fr] md:gap-8 md:p-8">
        <div className="flex flex-col justify-between rounded-[28px] border border-[var(--glass-border-subtle)] bg-[linear-gradient(160deg,rgba(34,211,238,0.12),rgba(255,255,255,0.04))] px-6 py-7">
          <div>
            <div className="text-[11px] uppercase tracking-[0.32em] text-[var(--color-text-muted)]">done-hub</div>
            <h1 className="mt-4 max-w-md text-3xl font-semibold leading-tight text-[var(--color-text-primary)] md:text-4xl">
              {t("modals:doneHub.gateTitle")}
            </h1>
            <p className="mt-4 max-w-md text-sm leading-7 text-[var(--color-text-secondary)] md:text-base">
              {t("modals:doneHub.gateHint")}
            </p>
          </div>

          <div className="mt-8 grid gap-3 text-sm text-[var(--color-text-secondary)]">
            <div className="rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-4 py-4">
              <div className="text-[11px] uppercase tracking-[0.24em] text-[var(--color-text-muted)]">
                {t("modals:doneHub.gateFeatureBalance")}
              </div>
              <div className="mt-2 text-sm leading-6">{t("modals:doneHub.gateFeatureBalanceHint")}</div>
            </div>
            <div className="rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-4 py-4">
              <div className="text-[11px] uppercase tracking-[0.24em] text-[var(--color-text-muted)]">
                {t("modals:doneHub.gateFeatureSession")}
              </div>
              <div className="mt-2 text-sm leading-6">{t("modals:doneHub.gateFeatureSessionHint")}</div>
            </div>
          </div>
        </div>

        <div className="mt-5 rounded-[28px] border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-5 py-6 md:mt-0 md:px-6">
          {busy ? (
            <div className="flex min-h-[420px] flex-col items-center justify-center text-center">
              <div className="h-12 w-12 rounded-full border-2 border-cyan-400/30 border-t-cyan-500 animate-spin" />
              <div className="mt-6 text-lg font-semibold text-[var(--color-text-primary)]">
                {t("modals:doneHub.checkingConnection")}
              </div>
              <div className="mt-2 max-w-sm text-sm leading-6 text-[var(--color-text-secondary)]">
                {t("modals:doneHub.checkingConnectionHint")}
              </div>
            </div>
          ) : (
            <>
              <div>
                <div className="text-[11px] uppercase tracking-[0.24em] text-[var(--color-text-muted)]">
                  {t("modals:doneHub.connectTitle")}
                </div>
                <div className="mt-3 text-2xl font-semibold text-[var(--color-text-primary)]">
                  {t("modals:doneHub.gateFormTitle")}
                </div>
                <div className="mt-2 text-sm leading-6 text-[var(--color-text-secondary)]">
                  {t("modals:doneHub.gateFormHint")}
                </div>
              </div>

              {errorMessage ? (
                <div className="mt-5 rounded-2xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-sm text-rose-500 dark:text-rose-300">
                  {errorMessage}
                </div>
              ) : null}

              <form
                className="mt-6 space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void connect(baseUrl, username, password, rememberPassword);
                }}
              >
                <div>
                  <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                    {t("modals:doneHub.baseUrl")}
                  </label>
                  <input
                    value={baseUrl}
                    onChange={(event) => setBaseUrl(event.target.value)}
                    placeholder="https://peer.example.com"
                    className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
                    autoComplete="url"
                  />
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                      {t("modals:doneHub.username")}
                    </label>
                    <input
                      value={username}
                      onChange={(event) => setUsername(event.target.value)}
                      placeholder={t("modals:doneHub.usernamePlaceholder")}
                      className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
                      autoComplete="username"
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
                      autoComplete="current-password"
                    />
                  </div>
                </div>

                <label className="flex items-start gap-3 rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-3 text-sm text-[var(--color-text-secondary)]">
                  <input
                    type="checkbox"
                    checked={rememberPassword}
                    onChange={(event) => setRememberPassword(event.target.checked)}
                    className="mt-1 h-4 w-4 rounded border border-[var(--glass-border-subtle)]"
                  />
                  <span>
                    <span className="block font-medium text-[var(--color-text-primary)]">
                      {t("modals:doneHub.rememberPassword")}
                    </span>
                    <span className="mt-1 block text-xs leading-6 text-[var(--color-text-muted)]">
                      {t("modals:doneHub.rememberPasswordHint")}
                    </span>
                  </span>
                </label>

                <div className="rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-3 text-xs leading-6 text-[var(--color-text-secondary)]">
                  {t("modals:doneHub.scopeHint")}
                </div>

                <button
                  type="submit"
                  disabled={submitDisabled}
                  className="glass-btn-accent w-full rounded-2xl px-4 py-3 text-sm font-medium text-[var(--color-accent-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("modals:doneHub.connectAction")}
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
