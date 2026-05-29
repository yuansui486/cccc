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

  const [usernameDraft, setUsernameDraft] = useState<string | null>(null);
  const [tenantCodeDraft, setTenantCodeDraft] = useState<string | null>(null);
  const [passwordDraft, setPasswordDraft] = useState<string | null>(null);
  const [rememberPasswordDraft, setRememberPasswordDraft] = useState<boolean | null>(null);
  const appBrandName = getAppBrandName();
  const appLogoPath = getAppLogoPath();
  const username = usernameDraft ?? savedLogin.username ?? "";
  const tenantCode = tenantCodeDraft ?? savedLogin.tenant_code ?? "";
  const password = passwordDraft ?? savedLogin.password ?? "";
  const rememberPassword = rememberPasswordDraft ?? (savedLogin.remember_password || Boolean(savedLogin.password));

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
        <div className="absolute -top-24 left-8 h-72 w-72 rounded-full bg-sky-300/18 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-80 w-80 rounded-full bg-blue-400/14 blur-3xl" />
        <div className="absolute bottom-0 left-1/3 h-64 w-64 rounded-full bg-indigo-300/12 blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-md">
        <div className="mb-5 flex items-center justify-center gap-4">
          <div className="flex h-16 w-16 items-center justify-center rounded-[26px] border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] shadow-[0_18px_40px_rgba(15,23,42,0.12)] backdrop-blur-xl">
            <img src={appLogoPath} alt={`${appBrandName} Logo`} className="h-11 w-11 object-contain" />
          </div>
          <div className="text-[2rem] font-semibold tracking-tight text-[var(--color-text-primary)]">{appBrandName}</div>
        </div>

        <div className="rounded-[32px] border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-5 shadow-[0_24px_80px_rgba(15,23,42,0.12)] backdrop-blur-xl md:p-6">
          <div className="rounded-[28px] border border-[var(--glass-border-subtle)] bg-[var(--color-bg-primary)] px-5 py-6 md:px-6">
          {busy ? (
            <div className="flex min-h-[320px] flex-col items-center justify-center text-center">
              <div className="h-12 w-12 animate-spin rounded-full border-2 border-sky-300/35 border-t-blue-500" />
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
                  void connect(username, password, rememberPassword, tenantCode);
                }}
              >
                <div>
                  <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                    租户
                  </label>
                  <input
                    value={tenantCode}
                    onChange={(event) => setTenantCodeDraft(event.target.value)}
                    placeholder="请输入租户编码"
                    className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
                    autoComplete="organization"
                  />
                </div>

                <div>
                  <label className="mb-2 block text-xs font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
                    {t("doneHub.username")}
                  </label>
                  <input
                    value={username}
                    onChange={(event) => setUsernameDraft(event.target.value)}
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
                    onChange={(event) => setPasswordDraft(event.target.value)}
                    placeholder={t("doneHub.passwordPlaceholder")}
                    className="glass-input w-full px-4 py-3 text-sm text-[var(--color-text-primary)]"
                    autoComplete="current-password"
                  />
                </div>

                <label className="flex items-center gap-3 rounded-2xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-4 py-3 text-sm text-[var(--color-text-primary)]">
                  <input
                    type="checkbox"
                    checked={rememberPassword}
                    onChange={(event) => setRememberPasswordDraft(event.target.checked)}
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
    </div>
  );
}
