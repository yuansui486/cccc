// SettingsModal renders the settings modal.
import { lazy, Suspense, useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { Actor, GroupDoc, GroupSettings, WebAccessSession } from "../types";
import * as api from "../services/api";
import { formatDoneHubQuota } from "../services/doneHub";
import { useObservabilityStore } from "../stores";
import { useDoneHubStore } from "../stores/useDoneHubStore";
import type { RuntimeVisibilityMode } from "../utils/runtimeVisibility";
import {
  SettingsScope,
  GroupTabId,
  GlobalTabId,
} from "./modals/settings/types";
import { ModalFrame } from "./modals/ModalFrame";
import { SettingsNavigation } from "./modals/settings/SettingsNavigation";
import { useModalA11y } from "../hooks/useModalA11y";
import { copyTextToClipboard } from "../utils/copy";

const BlueprintTab = lazy(() => import("./modals/settings/BlueprintTab").then((module) => ({ default: module.BlueprintTab })));
const GuidanceTab = lazy(() => import("./modals/settings/GuidanceTab").then((module) => ({ default: module.GuidanceTab })));
const CapabilitiesTab = lazy(() => import("./modals/settings/CapabilitiesTab").then((module) => ({ default: module.CapabilitiesTab })));
const ActorProfilesTab = lazy(() => import("./modals/settings/ActorProfilesTab").then((module) => ({ default: module.ActorProfilesTab })));
const DeveloperTab = lazy(() => import("./modals/settings/DeveloperTab").then((module) => ({ default: module.DeveloperTab })));

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  settings: GroupSettings | null;
  onUpdateSettings: (settings: Partial<GroupSettings>) => Promise<boolean | void>;
  onRegistryChanged?: () => Promise<void> | void;
  busy: boolean;
  isDark: boolean;
  groupId?: string;
  groupDoc?: GroupDoc | null;
  initialTarget?: { scope?: SettingsScope; tab?: string; nonce: number } | null;
  onOpenDoneHubAuth?: () => void;
}

function SettingsTabFallback({ isDark }: { isDark: boolean }) {
  return (
    <div
      className={`rounded-2xl border p-5 text-sm ${
        isDark ? "border-slate-700 bg-slate-900/70 text-slate-200" : "border-slate-200 bg-slate-50 text-slate-600"
      }`}
    >
      Loading...
    </div>
  );
}

export function SettingsModal({
  isOpen,
  onClose,
  settings,
  onUpdateSettings,
  onRegistryChanged,
  busy,
  isDark,
  groupId,
  groupDoc,
  initialTarget,
  onOpenDoneHubAuth,
}: SettingsModalProps) {
  const { t } = useTranslation("settings");
  const { modalRef } = useModalA11y(isOpen, onClose);
  const [scope, setScope] = useState<SettingsScope>("global");
  const [groupTab, setGroupTab] = useState<GroupTabId>("blueprint");
  const [globalTab, setGlobalTab] = useState<GlobalTabId>(groupId ? "blueprint" : "capabilities");
  const [hiddenMenuUnlocked, setHiddenMenuUnlocked] = useState(false);
  const [canAccessGlobalSettings, setCanAccessGlobalSettings] = useState<boolean | null>(null);
  const [webAccessSession, setWebAccessSession] = useState<WebAccessSession | null>(null);
  const doneHubStatus = useDoneHubStore((state) => state.status);
  const doneHubSession = useDoneHubStore((state) => state.session);

  useEffect(() => {
    if (!isOpen || !initialTarget) return;
    if (initialTarget.scope === "global") {
      setScope("global");
      if (initialTarget.tab === "blueprint" || initialTarget.tab === "guidance" || initialTarget.tab === "capabilities" || initialTarget.tab === "selfEvolvingSkills" || initialTarget.tab === "actorProfiles" || initialTarget.tab === "myProfiles" || initialTarget.tab === "branding" || initialTarget.tab === "webAccess" || initialTarget.tab === "webModels" || initialTarget.tab === "developer") {
        setGlobalTab(initialTarget.tab);
      }
      return;
    }
    if (initialTarget.scope === "group") {
      if (initialTarget.tab === "blueprint") {
        setScope("global");
        setGlobalTab("blueprint");
      }
    }
  }, [initialTarget, isOpen]);

  // Delivery settings state
  const [autoMarkOnDelivery, setAutoMarkOnDelivery] = useState(false);

  // Messaging policy
  const [defaultSendTo, setDefaultSendTo] = useState<"foreman" | "broadcast">("foreman");

  // Terminal transcript (group-scoped policy)
  const [terminalVisibility, setTerminalVisibility] = useState<"off" | "foreman" | "all">("foreman");
  const [terminalNotifyTail, setTerminalNotifyTail] = useState(false);
  const [terminalNotifyLines, setTerminalNotifyLines] = useState(20);

  // Terminal transcript tail viewer
  const [tailActorId, setTailActorId] = useState("");
  const [tailMaxChars, setTailMaxChars] = useState(8000);
  const [tailStripAnsi, setTailStripAnsi] = useState(true);
  const [tailCompact, setTailCompact] = useState(true);
  const [tailText, setTailText] = useState("");
  const [tailHint, setTailHint] = useState("");
  const [tailErr, setTailErr] = useState("");
  const [tailBusy, setTailBusy] = useState(false);
  const [tailCopyInfo, setTailCopyInfo] = useState("");

  const contentScrollRef = useRef<HTMLDivElement | null>(null);

  // Global observability (developer mode)
  const [developerMode, setDeveloperMode] = useState(false);
  const [logLevel, setLogLevel] = useState<"INFO" | "DEBUG">("INFO");
  const [terminalBacklogMiB, setTerminalBacklogMiB] = useState(10);
  const [terminalScrollbackLines, setTerminalScrollbackLines] = useState(8000);
  const [peerRuntimeVisibility, setPeerRuntimeVisibility] = useState<RuntimeVisibilityMode>("visible");
  const [petRuntimeVisibility, setPetRuntimeVisibility] = useState<RuntimeVisibilityMode>("hidden");
  const [obsBusy, setObsBusy] = useState(false);

  // Developer-mode debug views
  const [devActors, setDevActors] = useState<Actor[]>([]);
  const [debugSnapshot, setDebugSnapshot] = useState("");
  const [debugSnapshotErr, setDebugSnapshotErr] = useState("");
  const [debugSnapshotBusy, setDebugSnapshotBusy] = useState(false);
  const [runtimeVersion, setRuntimeVersion] = useState("");
  const [daemonVersion, setDaemonVersion] = useState("");
  const [runtimeInfoErr, setRuntimeInfoErr] = useState("");

  const [logComponent, setLogComponent] = useState<"daemon" | "web" | "im">("daemon");
  const [logLines, setLogLines] = useState(200);
  const [logText, setLogText] = useState("");
  const [logErr, setLogErr] = useState("");
  const [logBusy, setLogBusy] = useState(false);

  // Registry maintenance (global)
  const [registryBusy, setRegistryBusy] = useState(false);
  const [registryErr, setRegistryErr] = useState("");
  const [registryResult, setRegistryResult] = useState<api.RegistryReconcileResult | null>(null);
  const developerClickWindowRef = useRef({ count: 0, firstClickAt: 0 });

  // ============ Effects ============

  useEffect(() => {
    if (isOpen && settings) {
      setAutoMarkOnDelivery(Boolean(settings.auto_mark_on_delivery));
      setDefaultSendTo(settings.default_send_to || "foreman");
      setTerminalVisibility(settings.terminal_transcript_visibility || "foreman");
      setTerminalNotifyTail(Boolean(settings.terminal_transcript_notify_tail));
      setTerminalNotifyLines(Number(settings.terminal_transcript_notify_lines || 20));
    }
  }, [isOpen, settings]);

  useEffect(() => {
    if (!isOpen) return;
    if (initialTarget) return;
    setScope("global");
    setGlobalTab(groupId ? "blueprint" : "capabilities");
  }, [initialTarget, isOpen, groupId]);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    const loadWebAccessSession = async () => {
      try {
        const resp = await api.fetchWebAccessSession();
        if (cancelled) return;
        const session = resp.ok ? resp.result?.web_access_session ?? null : null;
        setWebAccessSession(session);
        const allowed = Boolean(session?.can_access_global_settings ?? !(session?.login_active ?? false));
        setCanAccessGlobalSettings(allowed);
        const allowGlobalScope = Boolean(allowed || session?.current_browser_signed_in);
        if (!allowGlobalScope && groupId) setScope("group");
      } catch {
        if (!cancelled) {
          setWebAccessSession(null);
          setCanAccessGlobalSettings(true);
        }
      }
    };
    void loadWebAccessSession();
    return () => {
      cancelled = true;
    };
  }, [isOpen, groupId]);

  useEffect(() => {
    if (isOpen && canAccessGlobalSettings === true) loadObservability();
  }, [isOpen, canAccessGlobalSettings]);

  useEffect(() => {
    if (isOpen && canAccessGlobalSettings === true && scope === "global" && globalTab === "developer") {
      void loadRuntimeInfo();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Runtime info only needs refresh when the Developer tab is opened.
  }, [isOpen, canAccessGlobalSettings, scope, globalTab]);

  useEffect(() => {
    if (isOpen && groupId) loadDevActors();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Only load when the modal opens or groupId changes.
  }, [isOpen, groupId]);

  // ============ Data Loading ============

  const loadObservability = async () => {
    try {
      const resp = await api.fetchObservability();
      if (resp.ok && resp.result?.observability) {
        const obs = resp.result.observability;
        useObservabilityStore.getState().setFromObs(obs);
        setDeveloperMode(Boolean(obs.developer_mode));
        const lvl = String(obs.log_level || "INFO").toUpperCase();
        setLogLevel(lvl === "DEBUG" ? "DEBUG" : "INFO");
        const perActorBytes = Number(obs.terminal_transcript?.per_actor_bytes || 0);
        if (Number.isFinite(perActorBytes) && perActorBytes > 0) {
          setTerminalBacklogMiB(Math.max(1, Math.round(perActorBytes / (1024 * 1024))));
        }
        const scrollbackLines = Number(obs.terminal_ui?.scrollback_lines || 0);
        if (Number.isFinite(scrollbackLines) && scrollbackLines > 0) {
          setTerminalScrollbackLines(Math.max(1000, Math.round(scrollbackLines)));
        }
        setPeerRuntimeVisibility(
          String(obs.runtime_visibility?.peer_runtime || "").trim().toLowerCase() === "hidden" ? "hidden" : "visible"
        );
        setPetRuntimeVisibility(
          String(obs.runtime_visibility?.pet_runtime || "").trim().toLowerCase() === "visible" ? "visible" : "hidden"
        );
      }
    } catch (e) {
      console.error("Failed to load observability settings:", e);
    }
  };

  const loadDevActors = async () => {
    if (!groupId) return;
    try {
      const resp = await api.fetchActors(groupId, false);
      if (resp.ok && resp.result?.actors) {
        const actors = Array.isArray(resp.result.actors) ? resp.result.actors : [];
        setDevActors(actors);
        if (!tailActorId && actors.length > 0) {
          setTailActorId(actors[0].id);
        }
      }
    } catch (e) {
      console.error("Failed to load developer actor list:", e);
    }
  };

  // ============ Handlers ============

  const handleSaveDeliverySettings = async () => {
    await onUpdateSettings({
      auto_mark_on_delivery: autoMarkOnDelivery,
    });
  };

  const handleAutoSave = async (field: string, value: number | boolean) => {
    await onUpdateSettings({ [field]: value });
  };

  const handleSaveTranscriptSettings = async () => {
    await onUpdateSettings({
      terminal_transcript_visibility: terminalVisibility,
      terminal_transcript_notify_tail: terminalNotifyTail,
      terminal_transcript_notify_lines: terminalNotifyLines,
    });
  };

  const handleSaveMessagingSettings = async () => {
    await onUpdateSettings({
      default_send_to: defaultSendTo,
    });
  };

  const copyTailLastLines = async (lineCount: number) => {
    const n = Math.max(1, Math.min(200, Number(lineCount || 0) || 50));
    const text = String(tailText || "");
    if (!text.trim()) return;
    const lines = text.split("\n");
    const payload = lines.slice(Math.max(0, lines.length - n)).join("\n").trimEnd();
    if (!payload) return;

    const setToast = (msg: string) => {
      setTailCopyInfo(msg);
      window.setTimeout(() => setTailCopyInfo(""), 1200);
    };

    const ok = await copyTextToClipboard(payload);
    setToast(ok ? t("automation.copiedLines", { n }) : t("common:copyFailed"));
  };

  const loadTerminalTail = async () => {
    if (!groupId || !tailActorId) return;
    setTailBusy(true);
    setTailErr("");
    try {
      const resp = await api.fetchTerminalTail(groupId, tailActorId, tailMaxChars || 8000, tailStripAnsi, tailCompact);
      if (resp.ok) {
        setTailText(String(resp.result?.text || ""));
        setTailHint(String(resp.result?.hint || ""));
      } else {
        setTailText("");
        setTailHint("");
        setTailErr(resp.error?.message || t("automation.failedToLoadTranscript"));
      }
    } catch {
      setTailText("");
      setTailHint("");
      setTailErr(t("automation.failedToLoadTranscript"));
    } finally {
      setTailBusy(false);
    }
  };

  const clearTail = async () => {
    if (!groupId || !tailActorId) return;
    setTailBusy(true);
    setTailErr("");
    try {
      const resp = await api.clearTerminalTail(groupId, tailActorId);
      if (!resp.ok) {
        setTailErr(resp.error?.message || t("automation.failedToClearTranscript"));
        return;
      }
      setTailText("");
      setTailHint("");
    } catch {
      setTailErr(t("automation.failedToClearTranscript"));
    } finally {
      setTailBusy(false);
    }
  };

  const handleSaveObservability = async () => {
    setObsBusy(true);
    try {
      const perActorBytes = Math.max(1, Math.min(50, Number(terminalBacklogMiB || 0))) * 1024 * 1024;
      const scrollbackLines = Math.max(1000, Math.min(200000, Number(terminalScrollbackLines || 0)));
      const resp = await api.updateObservability({
        developerMode,
        logLevel,
        terminalTranscriptPerActorBytes: perActorBytes,
        terminalUiScrollbackLines: scrollbackLines,
        peerRuntimeVisibility,
        petRuntimeVisibility,
      });
      if (resp.ok && resp.result?.observability) {
        const obs = resp.result.observability;
        useObservabilityStore.getState().setFromObs(obs);
        setDeveloperMode(Boolean(obs.developer_mode));
        const lvl = String(obs.log_level || "INFO").toUpperCase();
        setLogLevel(lvl === "DEBUG" ? "DEBUG" : "INFO");
        const bytes = Number(obs.terminal_transcript?.per_actor_bytes || 0);
        if (Number.isFinite(bytes) && bytes > 0) {
          setTerminalBacklogMiB(Math.max(1, Math.round(bytes / (1024 * 1024))));
        }
        const lines = Number(obs.terminal_ui?.scrollback_lines || 0);
        if (Number.isFinite(lines) && lines > 0) {
          setTerminalScrollbackLines(Math.max(1000, Math.round(lines)));
        }
        setPeerRuntimeVisibility(
          String(obs.runtime_visibility?.peer_runtime || "").trim().toLowerCase() === "hidden" ? "hidden" : "visible"
        );
        setPetRuntimeVisibility(
          String(obs.runtime_visibility?.pet_runtime || "").trim().toLowerCase() === "visible" ? "visible" : "hidden"
        );
      } else if (resp.ok) {
        await loadObservability();
      }
    } catch {
      // ignore
    } finally {
      setObsBusy(false);
    }
  };

  const loadDebugSnapshot = async () => {
    if (!groupId) return;
    setDebugSnapshotBusy(true);
    setDebugSnapshotErr("");
    try {
      const resp = await api.fetchDebugSnapshot(groupId);
      if (resp.ok) {
        setDebugSnapshot(JSON.stringify(resp.result ?? {}, null, 2));
      } else {
        setDebugSnapshot("");
        setDebugSnapshotErr(resp.error?.message || "Failed to load debug snapshot");
      }
    } catch {
      setDebugSnapshot("");
      setDebugSnapshotErr("Failed to load debug snapshot");
    } finally {
      setDebugSnapshotBusy(false);
    }
  };

  const loadRuntimeInfo = async () => {
    setRuntimeInfoErr("");
    try {
      const resp = await api.fetchPing();
      if (!resp.ok) {
        setRuntimeVersion("");
        setDaemonVersion("");
        setRuntimeInfoErr(resp.error?.message || "Failed to load runtime info");
        return;
      }
      const result = resp.result || {};
      const daemon = result.daemon && typeof result.daemon === "object" && !Array.isArray(result.daemon)
        ? result.daemon as Record<string, unknown>
        : null;
      setRuntimeVersion(String(result.version || "").trim());
      setDaemonVersion(String(daemon?.version || "").trim());
    } catch {
      setRuntimeVersion("");
      setDaemonVersion("");
      setRuntimeInfoErr("Failed to load runtime info");
    }
  };

  const loadLogTail = async () => {
    if (logComponent === "im" && !groupId) {
      setLogText("");
      setLogErr(t("developer.imLogsRequireGroup"));
      return;
    }
    setLogBusy(true);
    setLogErr("");
    try {
      const resp = await api.fetchLogTail(logComponent, groupId || "", logLines || 200);
      if (resp.ok) {
        const lines = Array.isArray(resp.result?.lines) ? resp.result.lines : [];
        setLogText(lines.join("\n"));
      } else {
        setLogText("");
        setLogErr(resp.error?.message || "Failed to tail logs");
      }
    } catch {
      setLogText("");
      setLogErr("Failed to tail logs");
    } finally {
      setLogBusy(false);
    }
  };

  const handleClearLogs = async () => {
    if (!developerMode) return;
    if (logComponent === "im" && !groupId) {
      setLogErr(t("developer.imLogsRequireGroup"));
      return;
    }
    setLogBusy(true);
    setLogErr("");
    try {
      const resp = await api.clearLogs(logComponent, groupId || "");
      if (!resp.ok) {
        setLogErr(resp.error?.message || "Failed to clear logs");
        return;
      }
      setLogText("");
    } catch {
      setLogErr("Failed to clear logs");
    } finally {
      setLogBusy(false);
    }
  };

  const loadRegistryPreview = async () => {
    setRegistryBusy(true);
    setRegistryErr("");
    try {
      const resp = await api.previewRegistryReconcile();
      if (resp.ok) {
        setRegistryResult(resp.result);
      } else {
        setRegistryErr(resp.error?.message || "Failed to scan registry");
      }
    } catch {
      setRegistryErr("Failed to scan registry");
    } finally {
      setRegistryBusy(false);
    }
  };

  const handleReconcileRegistry = async () => {
    const missingCount = registryResult?.missing_group_ids?.length || 0;
    if (missingCount <= 0) {
      await loadRegistryPreview();
      return;
    }
    if (!window.confirm(t("automation.removeRegistryConfirm", { count: missingCount }))) {
      return;
    }
    setRegistryBusy(true);
    setRegistryErr("");
    try {
      const resp = await api.executeRegistryReconcile(true);
      if (resp.ok) {
        setRegistryResult(resp.result);
        if (onRegistryChanged) {
          await onRegistryChanged();
        }
        await loadRegistryPreview();
      } else {
        setRegistryErr(resp.error?.message || "Failed to clean registry");
      }
    } catch {
      setRegistryErr("Failed to clean registry");
    } finally {
      setRegistryBusy(false);
    }
  };

  // ============ Derived state (must be before early return to keep hooks stable) ============

  const globalSettingsEnabled = canAccessGlobalSettings === true;
  const currentBrowserSignedIn = Boolean(webAccessSession?.current_browser_signed_in);
  const globalScopeEnabled = Boolean(groupId) || globalSettingsEnabled || currentBrowserSignedIn;

  const globalTabs = useMemo<{ id: GlobalTabId; label: string }[]>(() => [
    ...(groupId ? [{ id: "blueprint" as const, label: t("tabs.currentTeam", { defaultValue: "当前团队" }) }] : []),
    ...(globalSettingsEnabled ? [
      { id: "capabilities" as const, label: t("tabs.capabilities") },
      { id: "selfEvolvingSkills" as const, label: t("tabs.selfEvolvingSkills") },
      { id: "actorProfiles" as const, label: t("tabs.actorProfiles") },
      { id: "developer" as const, label: t("tabs.developer") },
    ] : []),
    // Non-admin signed-in users see My Profiles; admin already has Actor Profiles covering all
    ...(currentBrowserSignedIn && !globalSettingsEnabled ? [{ id: "myProfiles" as const, label: t("tabs.myProfiles") }] : []),
  ], [globalSettingsEnabled, currentBrowserSignedIn, groupId, t]);

  const hiddenTabs = useMemo<{ id: GlobalTabId; label: string }[]>(() => {
    if (!hiddenMenuUnlocked || !groupId) return [];
    return [{ id: "guidance" as const, label: t("tabs.guidance") }];
  }, [groupId, hiddenMenuUnlocked, t]);

  useEffect(() => {
    if (scope !== "global") return;
    const availableTabs = [...globalTabs, ...hiddenTabs];
    if (!availableTabs.length) return;
    if (!availableTabs.some((tab) => tab.id === globalTab)) {
      setGlobalTab(globalTabs[0].id);
    }
  }, [globalTab, globalTabs, hiddenTabs, scope]);

  const groupTabs: { id: GroupTabId; label: string }[] = [];

  useEffect(() => {
    if (scope !== "group") return;
    setScope("global");
  }, [groupTab, groupTabs, scope]);

  const tabs = scope === "group" ? groupTabs : (globalScopeEnabled ? globalTabs : []);
  const visibleHiddenTabs = scope === "group" ? [] : (globalScopeEnabled ? hiddenTabs : []);
  const availableTabs = [...tabs, ...visibleHiddenTabs];
  const activeTab = scope === "group" ? groupTab : globalTab;
  const doneHubConnected = doneHubStatus === "connected" || doneHubStatus === "refreshing";
  const doneHubIsPro = String(doneHubSession?.group || "").trim().toLowerCase() === "pro";
  const doneHubQuota = doneHubSession?.quota != null ? formatDoneHubQuota(doneHubSession.quota) : formatDoneHubQuota(0);
  const doneHubAccount = onOpenDoneHubAuth ? {
    label: doneHubConnected
      ? t("account.balanceInline", { value: doneHubQuota })
      : t(doneHubStatus === "error" ? "account.errorShort" : "account.connect"),
    title: doneHubConnected
      ? t("account.balanceTitle", { value: doneHubQuota })
      : t(doneHubStatus === "error" ? "account.needsAttention" : "account.connect"),
    status: doneHubStatus,
    isPro: doneHubIsPro,
    onClick: onOpenDoneHubAuth,
  } : undefined;
  const setActiveTab = (tab: GroupTabId | GlobalTabId) => {
    if (scope === "global") {
      if (tab === "developer") {
        const now = Date.now();
        const windowState = developerClickWindowRef.current;
        if (!windowState.firstClickAt || now - windowState.firstClickAt > 3000) {
          windowState.firstClickAt = now;
          windowState.count = 1;
        } else {
          windowState.count += 1;
        }
        if (windowState.count >= 6) {
          setHiddenMenuUnlocked(true);
          windowState.firstClickAt = 0;
          windowState.count = 0;
        }
      } else {
        developerClickWindowRef.current = { count: 0, firstClickAt: 0 };
      }
    }
    if (scope === "group") setGroupTab(tab as GroupTabId);
    else setGlobalTab(tab as GlobalTabId);
  };

  useEffect(() => {
    const el = contentScrollRef.current;
    if (!isOpen || !el) return;
    requestAnimationFrame(() => {
      el.scrollTo({ top: 0, behavior: "auto" });
    });
  }, [isOpen, scope, activeTab]);

  // ============ Render ============

  if (!isOpen) return null;

  const scopeRootUrl = (() => {
    if (!groupDoc || String(groupDoc.group_id || "") !== String(groupId || "")) return "";
    const scopes = Array.isArray(groupDoc.scopes) ? groupDoc.scopes : [];
    const activeKey = String(groupDoc.active_scope_key || "");
    const active = scopes.find((s) => String(s?.scope_key || "") === activeKey && String(s?.url || "").trim());
    const first = scopes.find((s) => String(s?.url || "").trim());
    return String((active || first)?.url || "").trim();
  })();

  return (
    <ModalFrame
      isDark={isDark}
      onClose={onClose}
      titleId="settings-modal-title"
      title={(
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--color-text-muted)]">
            Workspace Settings
          </div>
          <h2 className="mt-1 truncate text-[1.15rem] font-semibold text-[var(--color-text-primary)]">
            {t("title")}
          </h2>
          <div className="mt-1 text-xs leading-5 text-[var(--color-text-tertiary)]">
            {activeTab === "blueprint"
              ? t("navigation.groupScopeContent", { scopeRoot: scopeRootUrl || groupId || "—" })
              : scope === "group"
                ? t("navigation.groupScopeContent", { scopeRoot: scopeRootUrl || groupId || "—" })
                : (globalScopeEnabled ? t("navigation.globalScopeContent") : t("navigation.globalLockedContent"))}
          </div>
        </div>
      )}
      closeAriaLabel={t("closeAriaLabel")}
      panelClassName="w-full h-full sm:h-[min(90dvh,920px)] sm:max-w-[min(1280px,calc(100vw-2rem))] sm:max-h-[90dvh]"
      headerActions={(
        <div className="hidden sm:flex items-center gap-2">
          <span className="rounded-full border border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)] px-3 py-1 text-[11px] font-medium text-[var(--color-text-secondary)]">
            {scope === "group" ? t("navigation.thisGroup") : t("navigation.global")}
          </span>
          {groupDoc?.title && scope === "group" ? (
            <span className="max-w-[18rem] truncate rounded-full border border-[var(--glass-border-subtle)] bg-transparent px-3 py-1 text-[11px] font-medium text-[var(--color-text-tertiary)]">
              {groupDoc.title}
            </span>
          ) : null}
        </div>
      )}
      modalRef={modalRef}
    >
      <div className="min-h-0 flex-1 flex flex-col sm:flex-row overflow-hidden">
        <SettingsNavigation
          isDark={isDark}
          tabs={tabs}
          hiddenTabs={visibleHiddenTabs}
          activeTab={activeTab}
          onTabChange={(tab) => setActiveTab(tab as GroupTabId | GlobalTabId)}
          account={doneHubAccount}
        />

        {/* Main Content Area */}
        <div
          ref={contentScrollRef}
          className={`min-h-0 flex-1 overflow-y-auto scrollbar-subtle flex flex-col [scrollbar-gutter:stable] ${
            isDark
              ? "bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.05),transparent_32%),linear-gradient(180deg,rgba(17,18,22,0.98),rgba(11,12,15,1))]"
              : "bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.96),rgba(255,255,255,0)_34%),linear-gradient(180deg,rgb(255,255,255),rgb(246,248,251))]"
          }`}
        >
          <div className="p-4 pb-6 sm:p-5 lg:p-6 sm:pb-7 space-y-4 lg:space-y-5">
            {scope === "global" && activeTab !== "blueprint" && !globalSettingsEnabled && !currentBrowserSignedIn ? (
              <div className={`rounded-xl border p-6 ${isDark ? "border-amber-700/40 bg-amber-900/10 text-amber-200" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
                <div className="text-sm font-semibold">{t("navigation.globalLockedTitle")}</div>
                <div className="mt-2 text-sm leading-6">{t("navigation.globalLockedContent")}</div>
              </div>
            ) : !availableTabs.some((tab) => tab.id === activeTab) ? null : (
              <Suspense fallback={<SettingsTabFallback isDark={isDark} />}>
              {activeTab === "blueprint" && <BlueprintTab isDark={isDark} groupId={groupId} groupTitle={groupDoc?.title || ""} />}

              {activeTab === "guidance" && <GuidanceTab isDark={isDark} groupId={groupId} />}

              {activeTab === "capabilities" && (
                <CapabilitiesTab
                  isDark={isDark}
                  isActive={scope === "global" && activeTab === "capabilities"}
                  groupId={groupId}
                  surface="global"
                />
              )}

              {activeTab === "selfEvolvingSkills" && (
                <CapabilitiesTab
                  isDark={isDark}
                  isActive={scope === "global" && activeTab === "selfEvolvingSkills"}
                  groupId={groupId}
                  surface="selfEvolving"
                />
              )}

              {activeTab === "actorProfiles" && (
                <ActorProfilesTab
                  isDark={isDark}
                  isActive={scope === "global" && activeTab === "actorProfiles"}
                  scope="global"
                />
              )}

              {activeTab === "myProfiles" && (
                <ActorProfilesTab
                  isDark={isDark}
                  isActive={scope === "global" && activeTab === "myProfiles"}
                  scope="my"
                />
              )}

              {activeTab === "developer" && (
                <DeveloperTab
                  isDark={isDark}
                  groupId={groupId}
                  runtimeVersion={runtimeVersion}
                  daemonVersion={daemonVersion}
                  runtimeInfoErr={runtimeInfoErr}
                  developerMode={developerMode}
                  setDeveloperMode={setDeveloperMode}
                  logLevel={logLevel}
                  setLogLevel={setLogLevel}
                  terminalBacklogMiB={terminalBacklogMiB}
                  setTerminalBacklogMiB={setTerminalBacklogMiB}
                  terminalScrollbackLines={terminalScrollbackLines}
                  setTerminalScrollbackLines={setTerminalScrollbackLines}
                  peerRuntimeVisibility={peerRuntimeVisibility}
                  setPeerRuntimeVisibility={setPeerRuntimeVisibility}
                  petRuntimeVisibility={petRuntimeVisibility}
                  setPetRuntimeVisibility={setPetRuntimeVisibility}
                  obsBusy={obsBusy}
                  onSaveObservability={() => void handleSaveObservability()}
                  debugSnapshot={debugSnapshot}
                  debugSnapshotErr={debugSnapshotErr}
                  debugSnapshotBusy={debugSnapshotBusy}
                  onLoadDebugSnapshot={() => void loadDebugSnapshot()}
                  onClearDebugSnapshot={() => setDebugSnapshot("")}
                  logComponent={logComponent}
                  setLogComponent={setLogComponent}
                  logLines={logLines}
                  setLogLines={setLogLines}
                  logText={logText}
                  logErr={logErr}
                  logBusy={logBusy}
                  onLoadLogTail={() => void loadLogTail()}
                  onClearLogs={() => void handleClearLogs()}
                  registryBusy={registryBusy}
                  registryErr={registryErr}
                  registryResult={registryResult}
                  onPreviewRegistry={() => void loadRegistryPreview()}
                  onReconcileRegistry={() => void handleReconcileRegistry()}
                />
              )}

              </Suspense>
            )}
          </div>
        </div>
      </div>
    </ModalFrame>
  );
}
