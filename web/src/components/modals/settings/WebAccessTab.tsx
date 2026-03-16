import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import type { GroupMeta, RemoteAccessState, WebAccessSession } from "../../../types";
import { InfoIcon } from "../../Icons";
import { InfoPopover } from "./InfoPopover";
import * as api from "../../../services/api";
import {
  cardClass,
  inputClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
  settingsDialogBodyClass,
  settingsDialogFooterClass,
  settingsDialogHeaderClass,
} from "./types";
import { useModalA11y } from "../../../hooks/useModalA11y";

interface WebAccessTabProps {
  isDark: boolean;
  isActive?: boolean;
}

type WebModeState = {
  mode?: string;
  read_only?: boolean;
};

function statusChipClass(_isDark: boolean, tone: "neutral" | "good" | "warn") {
  if (tone === "good") {
    return "border-emerald-500/30 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
  }
  if (tone === "warn") {
    return "border-amber-500/30 bg-amber-500/15 text-amber-600 dark:text-amber-400";
  }
  return "border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]";
}

async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const el = document.createElement("textarea");
    el.value = value;
    el.style.position = "fixed";
    el.style.left = "-9999px";
    document.body.appendChild(el);
    el.focus();
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    return ok;
  }
}

export function WebAccessTab({ isDark, isActive = true }: WebAccessTabProps) {
  const { t } = useTranslation("settings");

  const [remoteState, setRemoteState] = useState<RemoteAccessState | null>(null);
  const [webMode, setWebMode] = useState<WebModeState | null>(null);
  const [accessTokens, setAccessTokens] = useState<api.AccessTokenEntry[]>([]);
  const [session, setSession] = useState<WebAccessSession | null>(null);
  const [groups, setGroups] = useState<GroupMeta[]>([]);

  const [busy, setBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [startBusy, setStartBusy] = useState(false);
  const [stopBusy, setStopBusy] = useState(false);
  const [signOutBusy, setSignOutBusy] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");

  const [provider, setProvider] = useState<"off" | "manual" | "tailscale">("off");
  const [mode, setMode] = useState("tailnet_only");
  const [requireAccessToken, setRequireAccessToken] = useState(true);
  const [webHost, setWebHost] = useState("127.0.0.1");
  const [webPort, setWebPort] = useState("8848");
  const [webPublicUrl, setWebPublicUrl] = useState("");

  const [userId, setUserId] = useState("");
  const [customToken, setCustomToken] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [selectedGroups, setSelectedGroups] = useState<Set<string>>(new Set());
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [newToken, setNewToken] = useState<string | null>(null);
  const [newTokenAutoBound, setNewTokenAutoBound] = useState(false);
  const [copiedNewToken, setCopiedNewToken] = useState(false);

  const [editingTokenId, setEditingTokenId] = useState<string | null>(null);
  const [editGroups, setEditGroups] = useState<Set<string>>(new Set());
  const [editBusy, setEditBusy] = useState(false);
  const [pendingDeleteTokenId, setPendingDeleteTokenId] = useState<string | null>(null);
  const [deleteBusyTokenId, setDeleteBusyTokenId] = useState<string | null>(null);
  const [copiedTokenId, setCopiedTokenId] = useState<string | null>(null);

  const accessTokenCount = accessTokens.length;
  const hasAdminToken = accessTokens.some((item) => item.is_admin);
  const knownAccessTokenCount = typeof session?.access_token_count === "number" ? session.access_token_count : accessTokenCount;
  const loginActive = Boolean(session?.login_active ?? (knownAccessTokenCount > 0));
  const canAccessGlobalSettings = Boolean(session?.can_access_global_settings ?? !loginActive);
  const effectiveRequireAccessToken = provider === "off" ? knownAccessTokenCount > 0 : requireAccessToken;

  const pushHint = (value: string) => {
    setHint(value);
    window.setTimeout(() => setHint(""), 1800);
  };

  const resetCreateDraft = useCallback(() => {
    setCreateError("");
    setNewToken(null);
    setNewTokenAutoBound(false);
    setCopiedNewToken(false);
    setUserId("");
    setCustomToken("");
    setIsAdmin(false);
    setSelectedGroups(new Set());
  }, []);

  const openCreateDialog = useCallback(() => {
    resetCreateDraft();
    setCreateDialogOpen(true);
  }, [resetCreateDraft]);

  const closeCreateDialog = useCallback(() => {
    setCreateDialogOpen(false);
    resetCreateDraft();
  }, [resetCreateDraft]);

  const { modalRef: createDialogRef } = useModalA11y(createDialogOpen, closeCreateDialog);

  const load = async () => {
    if (!isActive) return;
    setBusy(true);
    setError("");
    try {
      const [pingResp, remoteResp, groupsResp, sessionResp] = await Promise.all([
        api.fetchPing(),
        api.fetchRemoteAccessState(),
        api.fetchGroups(),
        api.fetchWebAccessSession(),
      ]);
      if (pingResp.ok) {
        setWebMode(pingResp.result?.web || null);
      }
      if (remoteResp.ok && remoteResp.result?.remote_access) {
        const state = remoteResp.result.remote_access;
        setRemoteState(state);
        setProvider((state.provider as "off" | "manual" | "tailscale") || "off");
        setMode(String(state.mode || "tailnet_only"));
        setRequireAccessToken(Boolean(state.require_access_token ?? true));
        setWebHost(String(state.config?.web_host || state.diagnostics?.web_host || "127.0.0.1"));
        setWebPort(String(state.config?.web_port || state.diagnostics?.web_port || 8848));
        setWebPublicUrl(String(state.config?.web_public_url || state.diagnostics?.web_public_url || ""));
      } else if (!remoteResp.ok) {
        setError(remoteResp.error?.message || t("webAccess.loadFailed"));
      }
      if (groupsResp.ok && groupsResp.result?.groups) {
        setGroups(groupsResp.result.groups);
      }
      let sessionState: WebAccessSession | null = null;
      if (sessionResp.ok && sessionResp.result?.web_access_session) {
        sessionState = sessionResp.result.web_access_session;
        setSession(sessionState);
      } else if (!sessionResp.ok) {
        setError(sessionResp.error?.message || t("webAccess.loadFailed"));
      }
      const canAccessGlobal = Boolean(sessionState?.can_access_global_settings ?? !(sessionState?.login_active ?? false));
      if (canAccessGlobal) {
        const tokensResp = await api.fetchAccessTokens();
        if (tokensResp.ok && tokensResp.result?.access_tokens) {
          setAccessTokens(tokensResp.result.access_tokens);
        } else if (!tokensResp.ok) {
          setError(tokensResp.error?.message || t("webAccess.loadFailed"));
        }
      } else {
        setAccessTokens([]);
        setEditingTokenId(null);
        setPendingDeleteTokenId(null);
      }
    } catch {
      setError(t("webAccess.loadFailed"));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive]);

  useEffect(() => {
    if (!hasAdminToken || isAdmin) {
      setSelectedGroups(new Set());
    }
  }, [hasAdminToken, isAdmin]);

  useEffect(() => {
    if (!canAccessGlobalSettings) {
      setCreateDialogOpen(false);
      setNewToken(null);
      setCopiedNewToken(false);
    }
  }, [canAccessGlobalSettings]);

  const reachabilitySummary = useMemo(() => {
    if (!remoteState || provider === "off") {
      return { label: t("webAccess.summary.localOnly"), detail: t("webAccess.summary.localOnlyHint"), tone: "neutral" as const };
    }
    if (remoteState.status === "running") {
      return {
        label: t("webAccess.summary.remoteEnabled"),
        detail: remoteState.endpoint || t("webAccess.summary.remoteEnabledHint", { provider: provider === "tailscale" ? "Tailscale" : t("webAccess.providers.manual") }),
        tone: "good" as const,
      };
    }
    return { label: t("webAccess.summary.remoteNeedsAttention"), detail: t("webAccess.summary.remoteNeedsAttentionHint"), tone: "warn" as const };
  }, [provider, remoteState, t]);

  const accessSummary = useMemo(() => {
    if (knownAccessTokenCount <= 0) {
      return { label: t("webAccess.summary.open"), detail: t("webAccess.summary.openHint"), tone: "neutral" as const };
    }
    return {
      label: t("webAccess.summary.protected"),
      detail: t("webAccess.summary.protectedHint", { count: knownAccessTokenCount }),
      tone: "good" as const,
    };
  }, [knownAccessTokenCount, t]);

  const modeSummary = useMemo(() => {
    const exhibit = Boolean(webMode?.read_only) || String(webMode?.mode || "").toLowerCase() === "exhibit";
    return exhibit
      ? { label: t("webAccess.summary.exhibit"), detail: t("webAccess.summary.exhibitHint"), tone: "warn" as const }
      : { label: t("webAccess.summary.normal"), detail: t("webAccess.summary.normalHint"), tone: "good" as const };
  }, [t, webMode]);

  const currentBrowserSummary = useMemo(() => {
    if (!loginActive) {
      return {
        label: t("webAccess.currentBrowserOpen"),
        detail: t("webAccess.currentBrowserOpenHint"),
        tone: "neutral" as const,
      };
    }
    if (session == null) {
      return {
        label: t("webAccess.currentBrowserChecking"),
        detail: t("webAccess.currentBrowserCheckingHint"),
        tone: "warn" as const,
      };
    }
    if (session.current_browser_signed_in) {
      return {
        label: t("webAccess.currentBrowserSignedIn"),
        detail: t("webAccess.currentBrowserSignedInHint", {
          userId: session.user_id || t("webAccess.unknownUser"),
          role: session.is_admin ? t("webAccess.adminBadge") : t("webAccess.scopedBadge"),
        }),
        tone: "good" as const,
      };
    }
    return {
      label: t("webAccess.currentBrowserNotSignedIn"),
      detail: t("webAccess.currentBrowserNotSignedInHint"),
      tone: "warn" as const,
    };
  }, [loginActive, session, t]);

  const handleSignOut = async () => {
    setError("");
    setSignOutBusy(true);
    try {
      const resp = await api.logoutWebAccess();
      if (!resp.ok) {
        setError(resp.error?.message || t("webAccess.loadFailed"));
        return;
      }
    } catch {
      setError(t("webAccess.loadFailed"));
      return;
    } finally {
      api.setForceTokenLogin();
      api.clearAuthToken();
      document.cookie = "cccc_access_token=; path=/; max-age=0";
    }
    setSession((prev) => prev ? {
      ...prev,
      current_browser_signed_in: false,
      principal_kind: "anonymous",
      user_id: "",
      is_admin: false,
      allowed_groups: [],
      can_access_global_settings: false,
    } : prev);
    pushHint(t("webAccess.signOutSuccess"));
    window.setTimeout(() => {
      window.location.replace(window.location.pathname + window.location.search + window.location.hash);
    }, 60);
  };

  const handleSaveReachability = async () => {
    setSaveBusy(true);
    setError("");
    try {
      const parsedPort = Number.parseInt(webPort, 10);
      if (!Number.isFinite(parsedPort) || parsedPort <= 0 || parsedPort > 65535) {
        setError(t("webAccess.invalidPort"));
        return;
      }
      const resp = await api.updateRemoteAccessConfig({
        provider,
        mode,
        requireAccessToken,
        webHost,
        webPort: parsedPort,
        webPublicUrl,
      });
      if (!resp.ok || !resp.result?.remote_access) {
        setError(resp.error?.message || t("webAccess.saveFailed"));
        return;
      }
      setRemoteState(resp.result.remote_access);
      if ((resp.result as Record<string, unknown>).restart_required) {
        pushHint(t("webAccess.restartRequired"));
      } else {
        pushHint(t("common:saved"));
      }
    } catch {
      setError(t("webAccess.saveFailed"));
    } finally {
      setSaveBusy(false);
    }
  };

  const handleStart = async () => {
    setStartBusy(true);
    setError("");
    try {
      const resp = await api.startRemoteAccess();
      if (!resp.ok || !resp.result?.remote_access) {
        setError(resp.error?.message || t("webAccess.startFailed"));
        return;
      }
      setRemoteState(resp.result.remote_access);
      pushHint(t("webAccess.started"));
    } catch {
      setError(t("webAccess.startFailed"));
    } finally {
      setStartBusy(false);
    }
  };

  const handleStop = async () => {
    setStopBusy(true);
    setError("");
    try {
      const resp = await api.stopRemoteAccess();
      if (!resp.ok || !resp.result?.remote_access) {
        setError(resp.error?.message || t("webAccess.stopFailed"));
        return;
      }
      setRemoteState(resp.result.remote_access);
      pushHint(t("webAccess.stopped"));
    } catch {
      setError(t("webAccess.stopFailed"));
    } finally {
      setStopBusy(false);
    }
  };

  const handleCreateAccessToken = async () => {
    const trimmedUserId = userId.trim();
    if (!trimmedUserId) {
      setCreateError(t("webAccess.userIdRequired"));
      return;
    }
    setCreateBusy(true);
    setCreateError("");
    try {
      const effectiveAdmin = !hasAdminToken || isAdmin;
      if (!effectiveAdmin && selectedGroups.size === 0) {
        setCreateError(t("webAccess.groupSelectionRequired"));
        return;
      }
      const allowedGroups = effectiveAdmin ? [] : [...selectedGroups];
      const shouldAdoptCreatedToken = knownAccessTokenCount === 0 && !session?.current_browser_signed_in;
      const resp = await api.createAccessToken(trimmedUserId, effectiveAdmin, allowedGroups, customToken.trim() || undefined);
      if (!resp.ok || !resp.result?.access_token) {
        setCreateError(resp.error?.message || t("webAccess.createFailed"));
        return;
      }
      const created = resp.result.access_token;
      setNewTokenAutoBound(shouldAdoptCreatedToken);
      if (created.token) {
        setNewToken(created.token);
      }
      if (shouldAdoptCreatedToken && created.token) {
        api.setAuthToken(created.token);
        setSession({
          login_active: true,
          current_browser_signed_in: true,
          principal_kind: "user",
          user_id: trimmedUserId,
          is_admin: effectiveAdmin,
          allowed_groups: allowedGroups,
          access_token_count: Math.max(knownAccessTokenCount + 1, 1),
          can_access_global_settings: true,
        });
      }
      await load();
    } catch {
      setCreateError(t("webAccess.createFailed"));
    } finally {
      setCreateBusy(false);
    }
  };

  const handleDeleteAccessToken = async (tokenId: string) => {
    if (!tokenId) return;
    setDeleteBusyTokenId(tokenId);
    setError("");
    try {
      const resp = await api.deleteAccessToken(tokenId);
      if (!resp.ok) {
        setError(resp.error?.message || t("webAccess.deleteFailed"));
        return;
      }
      setPendingDeleteTokenId(null);
      if (resp.result?.deleted_current_session || !resp.result?.access_tokens_remain) {
        document.cookie = "cccc_access_token=; path=/; max-age=0";
        api.clearAuthToken();
        window.location.reload();
        return;
      }
      await load();
    } catch {
      setError(t("webAccess.deleteFailed"));
    } finally {
      setDeleteBusyTokenId(null);
    }
  };

  const startEdit = (entry: api.AccessTokenEntry) => {
    if (entry.is_admin) return;
    setPendingDeleteTokenId(null);
    setEditingTokenId(entry.token_id || null);
    setEditGroups(new Set(entry.allowed_groups || []));
  };

  const saveEdit = async () => {
    if (!editingTokenId) return;
    if (editGroups.size === 0) {
      setError(t("webAccess.groupSelectionRequired"));
      return;
    }
    setEditBusy(true);
    try {
      const resp = await api.updateAccessToken(editingTokenId, { allowed_groups: [...editGroups] });
      if (!resp.ok) {
        setError(resp.error?.message || t("webAccess.updateFailed"));
        return;
      }
      setEditingTokenId(null);
      setEditGroups(new Set());
      await load();
    } catch {
      setError(t("webAccess.updateFailed"));
    } finally {
      setEditBusy(false);
    }
  };

  const revealAndCopy = async (tokenId: string) => {
    try {
      const resp = await api.revealAccessToken(tokenId);
      if (!resp.ok || !resp.result?.token) {
        setError(resp.error?.message || t("webAccess.revealFailed"));
        return;
      }
      const ok = await copyText(resp.result.token);
      if (ok) {
        setCopiedTokenId(tokenId);
        window.setTimeout(() => setCopiedTokenId(null), 1500);
      }
    } catch {
      setError(t("webAccess.revealFailed"));
    }
  };

  const copyNewToken = async () => {
    if (!newToken) return;
    const ok = await copyText(newToken);
    if (ok) {
      setCopiedNewToken(true);
      window.setTimeout(() => setCopiedNewToken(false), 1500);
    }
  };

  const remoteStatusTone: "neutral" | "good" | "warn" =
    remoteState?.status === "running"
      ? "good"
      : remoteState?.status && remoteState.status !== "stopped"
        ? "warn"
        : "neutral";
  const remoteStatusLabel = t(`webAccess.status.${remoteState?.status || "stopped"}`);

  const createDialog =
    canAccessGlobalSettings && createDialogOpen ? (
      <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4 sm:p-6 animate-fade-in">
        <button type="button" aria-label={t("webAccess.close")} onClick={() => closeCreateDialog()} className="absolute inset-0 glass-overlay" />
        <div
          ref={createDialogRef}
          role="dialog"
          aria-modal="true"
          aria-label={newToken ? t("webAccess.newTokenTitle") : t("webAccess.createAccessToken")}
          className="glass-modal relative z-[1001] flex max-h-[calc(100dvh-2rem)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-[var(--glass-border-subtle)] shadow-2xl text-[var(--color-text-primary)]"
        >
          <div className={settingsDialogHeaderClass}>
            <div>
              <h5 className={`text-base font-semibold text-[var(--color-text-primary)]`}>{newToken ? t("webAccess.newTokenTitle") : t("webAccess.createAccessToken")}</h5>
              <p className={`mt-1 text-xs leading-6 text-[var(--color-text-muted)]`}>
                {newToken ? t("webAccess.newTokenHint") : t("webAccess.accessControlDescription")}
              </p>
            </div>
            <button
              type="button"
              onClick={() => closeCreateDialog()}
              className={`${secondaryButtonClass("sm")} ml-auto`}
            >
              {t("webAccess.close")}
            </button>
          </div>

          {newToken ? (
            <>
              <div className={`${settingsDialogBodyClass} space-y-5`}>
                <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/15 p-4">
                  <div className="break-all rounded-lg border border-emerald-500/20 bg-[var(--color-bg-primary)] px-3 py-3 text-xs font-mono text-emerald-600 dark:text-emerald-400">{newToken}</div>
                  <div className="mt-3 space-y-1 text-xs leading-6 text-emerald-600 dark:text-emerald-400">
                    <div>{newTokenAutoBound ? t("webAccess.newTokenAutoLoginHint") : t("webAccess.newTokenNoAutoLoginHint")}</div>
                    <div>{t("webAccess.newTokenVerifyHint")}</div>
                  </div>
                </div>
              </div>
              <div className={settingsDialogFooterClass}>
                <button type="button" onClick={() => void copyNewToken()} className={primaryButtonClass(false)}>
                  {copiedNewToken ? t("webAccess.copied") : t("webAccess.copyToken")}
                </button>
                <button
                  type="button"
                  onClick={() => closeCreateDialog()}
                  className={secondaryButtonClass()}
                >
                  {t("webAccess.close")}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className={`${settingsDialogBodyClass} space-y-4`}>
                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="space-y-3">
                    <div>
                      <label className={labelClass()}>{t("webAccess.userIdLabel")}</label>
                      <input value={userId} onChange={(e) => setUserId(e.target.value)} className={inputClass()} placeholder={t("webAccess.userIdPlaceholder")} />
                    </div>

                    <div>
                      <label className={labelClass()}>{t("webAccess.customTokenLabel")}</label>
                      <input value={customToken} onChange={(e) => setCustomToken(e.target.value)} className={inputClass()} placeholder={t("webAccess.customTokenPlaceholder")} />
                    </div>
                  </div>

                  <div className="space-y-3">
                    <label className={`flex items-start gap-3 rounded-lg border px-3 py-3 border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]`}>
                      <input
                        type="checkbox"
                        checked={!hasAdminToken || isAdmin}
                        onChange={(e) => setIsAdmin(e.target.checked)}
                        disabled={!hasAdminToken}
                        className="mt-1"
                      />
                      <div>
                        <div className={`text-sm font-medium text-[var(--color-text-primary)]`}>{t("webAccess.adminTokenLabel")}</div>
                        <div className={`mt-1 text-xs text-[var(--color-text-muted)]`}>
                          {!hasAdminToken ? t("webAccess.firstTokenAdminHint") : t("webAccess.adminTokenHint")}
                        </div>
                      </div>
                    </label>
                  </div>
                </div>

                {hasAdminToken && !isAdmin ? (
                  <div className={`mt-4 rounded-lg border p-3 border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]`}>
                    <div className="flex flex-col gap-1">
                      <div className={`text-sm font-medium text-[var(--color-text-primary)]`}>{t("webAccess.groupScopeTitle")}</div>
                      <div className={`text-xs text-[var(--color-text-muted)]`}>{t("webAccess.scopedTokenGroupsHint")}</div>
                    </div>
                    <div className="mt-3 max-h-44 overflow-y-auto scrollbar-subtle space-y-2 pr-1">
                      {groups.length === 0 ? (
                        <div className={`text-xs text-[var(--color-text-muted)]`}>{t("webAccess.noGroups")}</div>
                      ) : groups.map((group) => {
                        const groupId = group.group_id;
                        return (
                          <label key={groupId} className="flex items-center gap-2 text-sm">
                            <input
                              type="checkbox"
                              checked={selectedGroups.has(groupId)}
                              onChange={(e) => {
                                setSelectedGroups((prev) => {
                                  const next = new Set(prev);
                                  if (e.target.checked) next.add(groupId); else next.delete(groupId);
                                  return next;
                                });
                              }}
                            />
                            <span className="text-[var(--color-text-primary)]">{group.title || groupId}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ) : null}

                {createError && <div className={`mt-3 text-xs text-red-600 dark:text-red-400`}>{createError}</div>}
              </div>
              <div className={settingsDialogFooterClass}>
                <button type="button" onClick={() => void handleCreateAccessToken()} disabled={createBusy} className={primaryButtonClass(createBusy)}>
                  {createBusy ? t("webAccess.creating") : t("webAccess.createAccessToken")}
                </button>
                <button
                  type="button"
                  onClick={() => closeCreateDialog()}
                  className={secondaryButtonClass()}
                >
                  {t("webAccess.cancel")}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    ) : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("webAccess.title")}</h3>
            <InfoPopover
              isDark={isDark}
              title={t("webAccess.howItWorksTitle")}
              content={
                <ul className="space-y-1">
                  <li>• {t("webAccess.howItWorksActivateLogin")}</li>
                  <li>• {t("webAccess.howItWorksAutoLogin")}</li>
                  <li>• {t("webAccess.howItWorksRemotePolicy")}</li>
                </ul>
              }
              placement="bottom-start"
              maxWidthClassName="max-w-[320px]"
            >
              {(getReferenceProps, setReference) => (
                <button
                  type="button"
                  ref={setReference as never}
                  {...getReferenceProps()}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)] transition-colors"
                  aria-label={t("webAccess.howItWorksTitle")}
                >
                  <InfoIcon size={14} />
                </button>
              )}
            </InfoPopover>
          </div>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">{t("webAccess.description")}</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy || saveBusy || startBusy || stopBusy || createBusy || editBusy}
          className="glass-btn px-3 py-2 rounded-lg text-xs min-h-[40px] transition-colors text-[var(--color-text-secondary)] disabled:opacity-50"
        >
          {busy ? t("common:loading") : t("webAccess.refresh")}
        </button>
      </div>

      {(error || hint) && (
        <div className={`rounded-lg border px-3 py-2 text-xs ${error
          ? "border-red-500/30 bg-red-500/15 text-red-600 dark:text-red-400"
          : "border-emerald-500/30 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
        }`}>
          {error || hint}
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className={cardClass()}>
          <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">{t("webAccess.cards.reachability")}</div>
          <div className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(isDark, reachabilitySummary.tone)}`}>{reachabilitySummary.label}</div>
          <div className="mt-3 text-sm leading-6 text-[var(--color-text-secondary)]">{reachabilitySummary.detail}</div>
        </div>

        <div className={cardClass()}>
          <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">{t("webAccess.cards.accessControl")}</div>
          <div className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(isDark, accessSummary.tone)}`}>{accessSummary.label}</div>
          <div className="mt-3 text-sm leading-6 text-[var(--color-text-secondary)]">{accessSummary.detail}</div>

          <div className="mt-4 rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11px] uppercase tracking-wide text-[var(--color-text-muted)]">{t("webAccess.currentBrowserTitle")}</div>
                <div className="mt-1 text-sm font-medium text-[var(--color-text-primary)]">{currentBrowserSummary.label}</div>
                <div className="mt-1 text-xs leading-5 text-[var(--color-text-muted)]">{currentBrowserSummary.detail}</div>
              </div>
              <InfoPopover
                isDark={isDark}
                title={t("webAccess.currentBrowserTitle")}
                content={
                  <div className="space-y-2">
                    {loginActive ? <div>{t("webAccess.currentBrowserVerifyHint")}</div> : null}
                    {session?.current_browser_signed_in ? <div>{t("webAccess.signOutHint")}</div> : null}
                  </div>
                }
              >
                {(getReferenceProps, setReference) => (
                  <button
                    type="button"
                    ref={setReference as never}
                    {...getReferenceProps()}
                    className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border transition-colors ${statusChipClass(isDark, currentBrowserSummary.tone)}`}
                    aria-label={t("webAccess.currentBrowserTitle")}
                  >
                    <InfoIcon size={12} />
                  </button>
                )}
              </InfoPopover>
            </div>
            {session?.current_browser_signed_in ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void handleSignOut()}
                  disabled={signOutBusy}
                  className="glass-btn px-3 py-2 rounded-lg text-xs text-[var(--color-text-secondary)] disabled:opacity-50"
                >
                  {signOutBusy ? t("common:loading") : t("webAccess.signOut")}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      <section className={cardClass()}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <h4 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("webAccess.accessControlTitle")}</h4>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">{t("webAccess.accessControlDescription")}</p>
          </div>
          {canAccessGlobalSettings && knownAccessTokenCount > 0 ? (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => openCreateDialog()}
                className={primaryButtonClass(false)}
              >
                {t("common:create")}
              </button>
            </div>
          ) : null}
        </div>

        <div className="mt-4 space-y-3">
          {!canAccessGlobalSettings ? (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/15 p-6 text-sm text-amber-600 dark:text-amber-400">
              <div className="font-medium">{t("webAccess.managementLockedTitle")}</div>
              <div className="mt-2 text-xs leading-6">{t("webAccess.managementLockedHint")}</div>
            </div>
          ) : accessTokens.length === 0 ? (
            <div className="rounded-xl border border-dashed border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-6 text-[var(--color-text-secondary)]">
              <div className="text-sm font-medium">{t("webAccess.noAccessTokens")}</div>
              <div className="mt-2 text-xs leading-6 text-[var(--color-text-muted)]">{t("webAccess.firstTokenAdminHint")}</div>
              <button
                type="button"
                onClick={() => openCreateDialog()}
                className={`mt-4 ${primaryButtonClass(false)}`}
              >
                {t("common:create")}
              </button>
            </div>
          ) : (
            accessTokens.map((token) => {
              const tokenId = token.token_id || "";
              const isEditing = editingTokenId === tokenId;
              return (
                <div key={tokenId || token.user_id} className="rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-3">
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <div className="text-sm font-semibold text-[var(--color-text-primary)]">{token.user_id}</div>
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] ${statusChipClass(isDark, token.is_admin ? "good" : "neutral")}`}>
                          {token.is_admin ? t("webAccess.adminBadge") : t("webAccess.scopedBadge")}
                        </span>
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] ${statusChipClass(isDark, "neutral")}`}>
                          {token.token_preview || "****"}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                        {token.is_admin
                          ? t("webAccess.allGroupsAccess")
                          : (token.allowed_groups || []).length > 0
                            ? t("webAccess.scopedGroupsHint", { count: token.allowed_groups.length })
                            : t("webAccess.noGroupsAssigned")}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {!token.is_admin ? (
                        <button
                          type="button"
                          onClick={() => { if (!tokenId) return; startEdit(token); }}
                          disabled={!tokenId || deleteBusyTokenId === tokenId}
                          className={`px-3 py-2 rounded-lg text-xs glass-btn text-[var(--color-text-secondary)] disabled:opacity-50`}
                        >
                          {t("webAccess.edit")}
                        </button>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => void revealAndCopy(tokenId)}
                        disabled={!tokenId || deleteBusyTokenId === tokenId}
                        className={`px-3 py-2 rounded-lg text-xs glass-btn text-[var(--color-text-secondary)] disabled:opacity-50`}
                      >
                        {copiedTokenId === tokenId ? t("webAccess.copied") : t("webAccess.copyToken")}
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (!tokenId) return;
                          setEditingTokenId(null);
                          setPendingDeleteTokenId((prev) => (prev === tokenId ? null : tokenId));
                        }}
                        disabled={!tokenId || deleteBusyTokenId === tokenId}
                        className="px-3 py-2 rounded-lg text-xs bg-red-500/15 text-red-600 dark:text-red-400 hover:bg-red-500/25 border border-red-500/30 disabled:opacity-50"
                      >
                        {t("webAccess.delete")}
                      </button>
                    </div>
                  </div>

                  {pendingDeleteTokenId === tokenId && (
                    <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/15 p-3">
                      <div className="text-xs text-red-600 dark:text-red-400">{t("webAccess.deleteConfirm")}</div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => void handleDeleteAccessToken(tokenId)}
                          disabled={deleteBusyTokenId === tokenId}
                          className="px-3 py-2 rounded-lg text-xs bg-red-600 hover:bg-red-700 text-white disabled:opacity-50"
                        >
                          {deleteBusyTokenId === tokenId ? t("common:loading") : t("webAccess.delete")}
                        </button>
                        <button
                          type="button"
                          onClick={() => setPendingDeleteTokenId(null)}
                          disabled={deleteBusyTokenId === tokenId}
                          className={`px-3 py-2 rounded-lg text-xs glass-btn text-[var(--color-text-secondary)] disabled:opacity-50`}
                        >
                          {t("webAccess.cancel")}
                        </button>
                      </div>
                    </div>
                  )}

                  {isEditing && (
                    <div className="mt-3 rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-3">
                      <div className="text-xs font-medium text-[var(--color-text-secondary)]">{t("webAccess.editScopeTitle")}</div>
                      <div className="mt-2 max-h-44 overflow-y-auto space-y-2 pr-1">
                        {groups.length === 0 ? (
                          <div className="text-xs text-[var(--color-text-muted)]">{t("webAccess.noGroups")}</div>
                        ) : groups.map((group) => {
                          const groupId = group.group_id;
                          return (
                            <label key={groupId} className="flex items-center gap-2 text-sm">
                              <input
                                type="checkbox"
                                checked={editGroups.has(groupId)}
                                onChange={(e) => {
                                  setEditGroups((prev) => {
                                    const next = new Set(prev);
                                    if (e.target.checked) next.add(groupId); else next.delete(groupId);
                                    return next;
                                  });
                                }}
                              />
                              <span className="text-[var(--color-text-primary)]">{group.title || groupId}</span>
                            </label>
                          );
                        })}
                      </div>
                      <div className="mt-3 flex gap-2">
                        <button type="button" onClick={() => void saveEdit()} disabled={editBusy} className={primaryButtonClass(editBusy)}>
                          {editBusy ? t("webAccess.saving") : t("webAccess.save")}
                        </button>
                        <button type="button" onClick={() => { setEditingTokenId(null); setEditGroups(new Set()); }} className={`px-3 py-2 rounded-lg text-xs glass-btn text-[var(--color-text-secondary)]`}>
                          {t("webAccess.cancel")}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </section>

      <section className={cardClass()}>
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("webAccess.reachabilityTitle")}</h4>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">{t("webAccess.reachabilityDescription")}</p>
          </div>
          <div className={`inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(isDark, remoteStatusTone)}`}>
            {remoteStatusLabel}
          </div>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div>
            <label className={labelClass()}>{t("webAccess.providerLabel")}</label>
            <select value={provider} onChange={(e) => setProvider((e.target.value as "off" | "manual" | "tailscale") || "off")} className={inputClass()}>
              <option value="off">{t("webAccess.providers.off")}</option>
              <option value="manual">{t("webAccess.providers.manual")}</option>
              <option value="tailscale">{t("webAccess.providers.tailscale")}</option>
            </select>
          </div>
          <div>
            <label className={labelClass()}>{t("webAccess.modeLabel")}</label>
            <input value={mode} onChange={(e) => setMode(e.target.value)} className={inputClass()} placeholder="tailnet_only" />
          </div>
          <div>
            <label className={labelClass()}>{t("webAccess.webHostLabel")}</label>
            <input value={webHost} onChange={(e) => setWebHost(e.target.value)} className={inputClass()} placeholder="127.0.0.1" />
          </div>
          <div>
            <label className={labelClass()}>{t("webAccess.webPortLabel")}</label>
            <input value={webPort} onChange={(e) => setWebPort(e.target.value)} className={inputClass()} inputMode="numeric" placeholder="8848" />
          </div>
          <div className="md:col-span-2">
            <label className={labelClass()}>{t("webAccess.webPublicUrlLabel")}</label>
            <input value={webPublicUrl} onChange={(e) => setWebPublicUrl(e.target.value)} className={inputClass()} placeholder="https://example.com/ui/" />
          </div>
        </div>

        <label className="mt-4 flex items-start gap-3 rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-3">
          <input type="checkbox" checked={requireAccessToken} onChange={(e) => setRequireAccessToken(e.target.checked)} className="mt-1" />
          <div>
            <div className="text-sm font-medium text-[var(--color-text-primary)]">{t("webAccess.requireAccessTokenLabel")}</div>
            <div className="mt-1 text-xs text-[var(--color-text-muted)]">{t("webAccess.requireAccessTokenHint")}</div>
          </div>
        </label>

        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" onClick={() => void handleSaveReachability()} disabled={saveBusy} className={primaryButtonClass(saveBusy)}>
            {saveBusy ? t("webAccess.saving") : t("webAccess.save")}
          </button>
          <button type="button" onClick={() => void handleStart()} disabled={startBusy || provider === "off"} className={`px-4 py-2 rounded-lg text-sm min-h-[44px] glass-btn text-[var(--color-text-secondary)] disabled:opacity-50`}>
            {startBusy ? t("common:loading") : t("webAccess.start")}
          </button>
          <button type="button" onClick={() => void handleStop()} disabled={stopBusy || remoteState?.enabled !== true} className={`px-4 py-2 rounded-lg text-sm min-h-[44px] glass-btn text-[var(--color-text-secondary)] disabled:opacity-50`}>
            {stopBusy ? t("common:loading") : t("webAccess.stop")}
          </button>
          {remoteState?.endpoint && (
            <button
              type="button"
              onClick={async () => {
                const ok = await copyText(remoteState.endpoint || "");
                if (ok) pushHint(t("webAccess.copied"));
              }}
              className={`px-4 py-2 rounded-lg text-sm min-h-[44px] glass-btn text-[var(--color-text-secondary)]`}
            >
              {t("webAccess.copyEndpoint")}
            </button>
          )}
        </div>

        <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className={`rounded-lg border p-3 border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]`}>
            <div className={`text-xs font-medium text-[var(--color-text-secondary)]`}>{t("webAccess.reachabilityStatusTitle")}</div>
            <div className={`mt-2 text-sm text-[var(--color-text-primary)]`}>{remoteState?.endpoint || t("webAccess.noEndpoint")}</div>
            <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
              <div>
                <dt className="text-[var(--color-text-muted)]">{t("webAccess.requirementLabel")}</dt>
                <dd className="text-[var(--color-text-primary)]">{effectiveRequireAccessToken ? t("webAccess.required") : t("webAccess.optional")}</dd>
              </div>
              <div>
                <dt className="text-[var(--color-text-muted)]">{t("webAccess.accessTokenPresenceLabel")}</dt>
                <dd className="text-[var(--color-text-primary)]">{remoteState?.config?.access_token_configured ? t("webAccess.configured") : t("webAccess.notConfigured")}</dd>
              </div>
              <div>
                <dt className="text-[var(--color-text-muted)]">{t("webAccess.bindingLabel")}</dt>
                <dd className="text-[var(--color-text-primary)]">{`${webHost}:${webPort}`}</dd>
              </div>
              <div>
                <dt className="text-[var(--color-text-muted)]">{t("webAccess.updatedAtLabel")}</dt>
                <dd className="text-[var(--color-text-primary)]">{remoteState?.updated_at || "-"}</dd>
              </div>
            </dl>
          </div>

          <div className={`rounded-lg border p-3 border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]`}>
            <div className={`text-xs font-medium text-[var(--color-text-secondary)]`}>{t("webAccess.nextStepsTitle")}</div>
            <ul className={`mt-2 space-y-2 text-xs text-[var(--color-text-secondary)]`}>
              {(remoteState?.next_steps || []).length > 0
                ? (remoteState?.next_steps || []).map((step) => <li key={step}>• {step}</li>)
                : <li>• {t("webAccess.noNextSteps")}</li>}
            </ul>
          </div>
        </div>
      </section>

      {createDialog && typeof document !== "undefined" ? createPortal(createDialog, document.body) : createDialog}

      <section className={cardClass()}>
        <h4 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("webAccess.interactionModeTitle")}</h4>
        <p className={`mt-1 text-xs text-[var(--color-text-muted)]`}>{t("webAccess.interactionModeDescription")}</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div className={`rounded-lg border p-3 border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]`}>
            <div className={`text-xs font-medium text-[var(--color-text-secondary)]`}>{t("webAccess.currentModeTitle")}</div>
            <div className={`mt-2 inline-flex rounded-full border px-2.5 py-1 text-xs ${statusChipClass(isDark, modeSummary.tone)}`}>{modeSummary.label}</div>
            <div className={`mt-3 text-sm text-[var(--color-text-secondary)]`}>{modeSummary.detail}</div>
          </div>
          <div className={`rounded-lg border p-3 border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]`}>
            <div className={`text-xs font-medium text-[var(--color-text-secondary)]`}>{t("webAccess.modeGuideTitle")}</div>
            <ul className={`mt-2 space-y-2 text-xs text-[var(--color-text-secondary)]`}>
              <li>• {t("webAccess.modeGuideNormal")}</li>
              <li>• {t("webAccess.modeGuideExhibit")}</li>
              <li>• {t("webAccess.modeGuideSwitch")}</li>
            </ul>
          </div>
        </div>
      </section>
    </div>
  );
}
