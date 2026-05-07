import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import * as api from "../../../services/api";
import {
  Actor,
  CapabilityBlockEntry,
  CapabilityOverviewItem,
  CapabilityReadinessPreview,
  CapabilityStateResult,
  OneColleagueCapabilitySource,
  OneColleaguePendingCapability,
} from "../../../types";
import { cardClass } from "./types";

interface CapabilitiesTabProps {
  isDark: boolean;
  isActive: boolean;
  groupId?: string;
}

type RegistryKindFilter = "all" | "pack" | "mcp" | "skill";
type RegistryPolicyFilter = "all" | "actionable" | "blocked" | "indexed";
type ExternalCapabilitySafetyMode = "normal" | "conservative";

const REGISTRY_PAGE_SIZE_OPTIONS = [20, 40, 80];
const ONECOLLEAGUE_SOURCE_ID = "onecolleague_skill_library";
const VISIBLE_SOURCE_IDS = ["cccc_builtin", ONECOLLEAGUE_SOURCE_ID] as const;
const VISIBLE_SOURCE_ID_SET = new Set<string>(VISIBLE_SOURCE_IDS);
const SOURCE_PRIORITY: Record<string, number> = {
  cccc_builtin: 0,
  mcp_registry_official: 1,
  anthropic_skills: 2,
  github_skills_curated: 3,
  openclaw_skills_remote: 4,
  clawskills_remote: 5,
  clawhub_remote: 6,
  skillsmp_remote: 7,
  manual_import: 8,
  onecolleague_skill_library: 9,
};
const EXTERNAL_SOURCE_IDS = [
  "manual_import",
  "mcp_registry_official",
  "anthropic_skills",
  "github_skills_curated",
  "skillsmp_remote",
  "clawhub_remote",
  "openclaw_skills_remote",
  "clawskills_remote",
] as const;

const DEFAULT_ENABLE_ACTOR_ID = "user";

function normalizeExternalCapabilitySafetyMode(value: unknown): ExternalCapabilitySafetyMode {
  return String(value || "").trim().toLowerCase() === "conservative" ? "conservative" : "normal";
}

function normalizeReadinessPreview(value: unknown): CapabilityReadinessPreview | null {
  return value && typeof value === "object" ? (value as CapabilityReadinessPreview) : null;
}

function firstRecommendationLine(value?: string[]) {
  return Array.isArray(value) ? String(value[0] || "").trim() : "";
}

function asResultArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    : [];
}

function pendingImportOk(item: OneColleaguePendingCapability): boolean {
  const result = item.import_result && typeof item.import_result === "object"
    ? item.import_result as Record<string, unknown>
    : {};
  return Boolean(result.ok);
}

function versionLabel(value: unknown): string {
  return String(value || "").trim() || "-";
}

export function CapabilitiesTab({ isDark: _isDark, isActive, groupId = "" }: CapabilitiesTabProps) {
  const { t } = useTranslation("settings");
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [err, setErr] = useState("");
  const [storeErr, setStoreErr] = useState("");
  const [query, setQuery] = useState("");
  const [registryKind, setRegistryKind] = useState<RegistryKindFilter>("all");
  const [registryPolicy, setRegistryPolicy] = useState<RegistryPolicyFilter>("all");
  const [registrySource, setRegistrySource] = useState("all");
  const [registryPageSize, setRegistryPageSize] = useState(40);
  const [registryPage, setRegistryPage] = useState(1);
  const [items, setItems] = useState<CapabilityOverviewItem[]>([]);
  const [blocked, setBlocked] = useState<CapabilityBlockEntry[]>([]);
  const [externalSafetyMode, setExternalSafetyMode] = useState<ExternalCapabilitySafetyMode>("normal");
  const [oneColleagueSource, setOneColleagueSource] = useState<OneColleagueCapabilitySource | null>(null);
  const [pendingItems, setPendingItems] = useState<OneColleaguePendingCapability[]>([]);
  const [storeSummary, setStoreSummary] = useState<Record<string, number>>({});
  const [storeInvalidCount, setStoreInvalidCount] = useState(0);
  const [actors, setActors] = useState<Actor[]>([]);
  const [enableActorId, setEnableActorId] = useState(DEFAULT_ENABLE_ACTOR_ID);
  const [actorCapabilityState, setActorCapabilityState] = useState<CapabilityStateResult | null>(null);

  const load = useCallback(async () => {
    if (!isActive) return;
    setLoading(true);
    setErr("");
    try {
      const [overviewResp, allowlistResp, sourceResp, pendingResp, actorsResp, stateResp] = await Promise.all([
        api.fetchCapabilityOverview({ includeIndexed: true, limit: 1200 }),
        api.fetchCapabilityAllowlist(),
        api.fetchOneColleagueCapabilitySource(),
        api.fetchOneColleaguePendingCapabilities(),
        groupId ? api.fetchActors(groupId, false, { noCache: true }) : Promise.resolve(null),
        groupId ? api.fetchGroupCapabilityState(groupId, enableActorId) : Promise.resolve(null),
      ]);
      if (!overviewResp.ok) {
        setErr(overviewResp.error?.message || t("capabilities.failedLoad"));
        setItems([]);
        setBlocked([]);
      } else {
        setItems(Array.isArray(overviewResp.result?.items) ? overviewResp.result.items : []);
        setBlocked(
          Array.isArray(overviewResp.result?.blocked_capabilities)
            ? overviewResp.result.blocked_capabilities
            : []
        );
      }
      if (allowlistResp.ok) {
        setExternalSafetyMode(
          normalizeExternalCapabilitySafetyMode(allowlistResp.result?.external_capability_safety_mode)
        );
      }
      if (sourceResp.ok) {
        setOneColleagueSource(sourceResp.result?.source || null);
        setStoreSummary(sourceResp.result?.source?.last_summary || {});
      }
      if (pendingResp.ok) {
        setPendingItems(Array.isArray(pendingResp.result?.items) ? pendingResp.result.items : []);
      }
      if (actorsResp?.ok) {
        const nextActors = Array.isArray(actorsResp.result?.actors) ? actorsResp.result.actors : [];
        setActors(nextActors);
      } else if (!groupId) {
        setActors([]);
      }
      if (stateResp?.ok) {
        setActorCapabilityState(stateResp.result || null);
      } else if (!groupId) {
        setActorCapabilityState(null);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("capabilities.failedLoad"));
      setItems([]);
      setBlocked([]);
    } finally {
      setLoading(false);
    }
  }, [enableActorId, groupId, isActive, t]);

  useEffect(() => {
    if (!isActive) return;
    void load();
  }, [isActive, load]);

  const oneColleagueRows = useMemo(() => {
    return items
      .filter((row) => String(row.source_id || "").trim() === ONECOLLEAGUE_SOURCE_ID)
      .sort((a, b) => String(a.name || a.capability_id || "").localeCompare(String(b.name || b.capability_id || "")));
  }, [items]);

  const activePendingItems = useMemo(() => {
    return pendingItems.filter((item) => {
      const status = String(item.status || "").trim();
      return status && !["imported", "ignored", "rolled_back"].includes(status);
    });
  }, [pendingItems]);

  const importablePendingItems = useMemo(() => {
    return activePendingItems.filter((item) => item.record && typeof item.record === "object");
  }, [activePendingItems]);

  const importedPendingItems = useMemo(() => {
    return pendingItems.filter((item) => String(item.status || "").trim() === "imported" || pendingImportOk(item));
  }, [pendingItems]);

  const enabledCapabilitySet = useMemo(() => {
    const out = new Set<string>();
    const enabled = actorCapabilityState?.enabled_capabilities;
    if (Array.isArray(enabled)) {
      for (const capId of enabled) {
        const id = String(capId || "").trim();
        if (id) out.add(id);
      }
    }
    const entries = actorCapabilityState?.enabled;
    if (Array.isArray(entries)) {
      for (const row of entries) {
        const id = String(row.capability_id || "").trim();
        if (id) out.add(id);
      }
    }
    return out;
  }, [actorCapabilityState]);

  const activeSkillSet = useMemo(() => {
    const out = new Set<string>();
    const rows = actorCapabilityState?.active_capsule_skills;
    if (Array.isArray(rows)) {
      for (const row of rows) {
        const id = String(row.capability_id || "").trim();
        if (id) out.add(id);
      }
    }
    return out;
  }, [actorCapabilityState]);

  const actorOptions = useMemo(() => {
    const rows = actors
      .filter((actor) => String(actor.internal_kind || "").trim() === "")
      .map((actor) => ({
        actor_id: String(actor.id || "").trim(),
        label: String(actor.title || actor.id || "").trim(),
      }))
      .filter((actor) => actor.actor_id);
    return rows.length ? rows : [{ actor_id: DEFAULT_ENABLE_ACTOR_ID, label: t("capabilities.store.userActor") }];
  }, [actors, t]);

  useEffect(() => {
    if (!isActive || !groupId) return;
    const actorIds = actorOptions.map((actor) => actor.actor_id);
    if (actorIds.length === 0) return;
    if (!actorIds.includes(enableActorId)) {
      setEnableActorId(actorIds[0]);
    }
  }, [actorOptions, enableActorId, groupId, isActive]);

  const storeCounts = useMemo(() => {
    const enabled = oneColleagueRows.filter((row) => enabledCapabilitySet.has(String(row.capability_id || "").trim())).length;
    const active = oneColleagueRows.filter((row) => activeSkillSet.has(String(row.capability_id || "").trim())).length;
    return {
      published: Number(oneColleagueSource?.last_summary?.new || 0) + Number(oneColleagueSource?.last_summary?.updated || 0),
      pending: importablePendingItems.length,
      imported: oneColleagueRows.length,
      enabled,
      active,
      importedHistory: importedPendingItems.length,
    };
  }, [activeSkillSet, enabledCapabilitySet, importablePendingItems.length, importedPendingItems.length, oneColleagueRows, oneColleagueSource]);

  const registrySourceOptions = useMemo(() => {
    const out = new Set<string>();
    for (const row of items) {
      const sid = String(row.source_id || "").trim();
      if (VISIBLE_SOURCE_ID_SET.has(sid)) out.add(sid);
    }
    return Array.from(out).sort((a, b) => {
      const aPriority = SOURCE_PRIORITY[a] ?? 99;
      const bPriority = SOURCE_PRIORITY[b] ?? 99;
      if (aPriority !== bPriority) return aPriority - bPriority;
      return a.localeCompare(b);
    });
  }, [items]);

  const readinessBadgeClass = (status: string) => {
    if (status === "blocked") {
      return "bg-rose-500/15 text-rose-600 dark:text-rose-400";
    }
    if (status === "enableable") {
      return "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400";
    }
    return "bg-amber-500/15 text-amber-600 dark:text-amber-400";
  };

  const renderReadinessPreview = (preview: CapabilityReadinessPreview | null) => {
    if (!preview) return null;
    const status = String(preview.preview_status || "").trim().toLowerCase() || "needs_inspect";
    const nextStep = String(preview.next_step || "").trim();
    const missingEnv = Array.isArray(preview.missing_env)
      ? preview.missing_env.map((x) => String(x || "").trim()).filter(Boolean)
      : [];
    const blockedBySafetyMode =
      String(preview.policy_source || "").trim() === "external_capability_safety_mode" &&
      String(preview.policy_mode || "").trim() === "conservative";
    const blockReason = String(preview.enable_block_reason || "").trim();
    const statusLabel = t(`capabilities.readiness.status.${status}`, {
      defaultValue: status.replace(/_/g, " "),
    });
    const nextLabel = nextStep
      ? t(`capabilities.readiness.next.${nextStep}`, { defaultValue: nextStep.replace(/_/g, " ") })
      : "";
    const reasonLabel = blockedBySafetyMode
      ? t("capabilities.readiness.blockedBySafetyMode")
      : blockReason
        ? t(`capabilities.readiness.reason.${blockReason}`, { defaultValue: blockReason.replace(/_/g, " ") })
        : "";

    return (
      <div className="mt-2 rounded-md border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-2 py-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`px-1.5 py-0.5 rounded text-[10px] ${readinessBadgeClass(status)}`}>{statusLabel}</span>
          {reasonLabel ? (
            <span className="text-[11px] text-[var(--color-text-secondary)]">{reasonLabel}</span>
          ) : null}
        </div>
        {nextLabel ? (
          <div className="text-[11px] mt-1 text-[var(--color-text-tertiary)]">
            {t("capabilities.readiness.nextLabel")}: {nextLabel}
          </div>
        ) : null}
        {missingEnv.length ? (
          <div className="text-[11px] mt-1 text-[var(--color-text-tertiary)]">
            {t("capabilities.readiness.missingEnv", { names: missingEnv.join(", ") })}
          </div>
        ) : null}
      </div>
    );
  };

  const filteredRegistry = useMemo(() => {
    const q = String(query || "").trim().toLowerCase();
    const rows = items.filter((row) => {
      const capId = String(row.capability_id || "").trim();
      const sourceId = String(row.source_id || "").trim();
      if (!VISIBLE_SOURCE_ID_SET.has(sourceId)) return false;
      const kind = String(row.kind || "").trim().toLowerCase();
      const blockedNow = Boolean(row.blocked_global);
      const policyLevel = String(row.policy_level || "").trim().toLowerCase();
      const policyVisible = policyLevel !== "indexed";
      const readinessPreview = normalizeReadinessPreview(row.readiness_preview);
      const previewStatus = String(readinessPreview?.preview_status || "").trim().toLowerCase();
      const actionableNow = previewStatus ? previewStatus === "enableable" : (policyVisible && !blockedNow);
      const blockedByReadiness = blockedNow || previewStatus === "blocked";

      if (registryKind === "pack" && kind !== "pack") return false;
      if (registryKind === "mcp" && kind !== "mcp_toolpack") return false;
      if (registryKind === "skill" && kind !== "skill") return false;

      if (registryPolicy === "actionable" && !actionableNow) return false;
      if (registryPolicy === "blocked" && !blockedByReadiness) return false;
      if (registryPolicy === "indexed" && policyLevel !== "indexed") return false;

      if (registrySource !== "all" && String(row.source_id || "").trim() !== registrySource) return false;

      if (!q) return true;
      const text = [
        capId,
        String(row.name || ""),
        String(row.description_short || ""),
        ...(Array.isArray(row.use_when) ? row.use_when.map((x) => String(x || "")) : []),
        ...(Array.isArray(row.avoid_when) ? row.avoid_when.map((x) => String(x || "")) : []),
        ...(Array.isArray(row.gotchas) ? row.gotchas.map((x) => String(x || "")) : []),
        String(row.evidence_kind || ""),
        String(row.source_id || ""),
        ...(Array.isArray(row.tags) ? row.tags.map((x) => String(x || "")) : []),
      ]
        .join(" ")
        .toLowerCase();
      return text.includes(q);
    });
    rows.sort((a, b) => {
      const aBlocked = (a.blocked_global || String(normalizeReadinessPreview(a.readiness_preview)?.preview_status || "").trim().toLowerCase() === "blocked") ? 1 : 0;
      const bBlocked = (b.blocked_global || String(normalizeReadinessPreview(b.readiness_preview)?.preview_status || "").trim().toLowerCase() === "blocked") ? 1 : 0;
      if (aBlocked !== bBlocked) return aBlocked - bBlocked;
      const aPolicy = String(a.policy_level || "").toLowerCase() === "indexed" ? 1 : 0;
      const bPolicy = String(b.policy_level || "").toLowerCase() === "indexed" ? 1 : 0;
      if (aPolicy !== bPolicy) return aPolicy - bPolicy;
      const aRecent = Number(a.recent_success?.success_count || 0);
      const bRecent = Number(b.recent_success?.success_count || 0);
      if (aRecent !== bRecent) return bRecent - aRecent;
      return String(a.name || a.capability_id || "").localeCompare(String(b.name || b.capability_id || ""));
    });
    return rows;
  }, [items, query, registryKind, registryPolicy, registrySource]);

  const registryTotalPages = useMemo(
    () => Math.max(1, Math.ceil(Math.max(1, filteredRegistry.length) / Math.max(1, registryPageSize))),
    [filteredRegistry.length, registryPageSize]
  );

  useEffect(() => {
    setRegistryPage(1);
  }, [query, registryKind, registryPolicy, registrySource, registryPageSize]);

  useEffect(() => {
    if (registrySource !== "all" && !registrySourceOptions.includes(registrySource)) {
      setRegistrySource("all");
    }
  }, [registrySource, registrySourceOptions]);

  useEffect(() => {
    setRegistryPage((prev) => (prev <= registryTotalPages ? prev : registryTotalPages));
  }, [registryTotalPages]);

  const pagedRegistry = useMemo(() => {
    const safePage = Math.max(1, Math.min(registryPage, registryTotalPages));
    const start = (safePage - 1) * registryPageSize;
    return filteredRegistry.slice(start, start + registryPageSize);
  }, [filteredRegistry, registryPage, registryPageSize, registryTotalPages]);

  const registryRange = useMemo(() => {
    if (!filteredRegistry.length) return { from: 0, to: 0 };
    const safePage = Math.max(1, Math.min(registryPage, registryTotalPages));
    const from = (safePage - 1) * registryPageSize + 1;
    const to = from + pagedRegistry.length - 1;
    return { from, to };
  }, [filteredRegistry.length, registryPage, registryPageSize, registryTotalPages, pagedRegistry.length]);

  const updateExternalCapabilitySafetyMode = async (nextMode: ExternalCapabilitySafetyMode) => {
    if (nextMode === externalSafetyMode) return;
    setBusyKey("policy");
    setErr("");
    try {
      const nextLevel = nextMode === "conservative" ? "indexed" : "mounted";
      const sourceLevelPatch = Object.fromEntries(EXTERNAL_SOURCE_IDS.map((sourceId) => [sourceId, nextLevel]));
      const resp = await api.updateCapabilityAllowlist({
        patch: {
          defaults: {
            source_level: sourceLevelPatch,
          },
        },
      });
      if (!resp.ok) {
        setErr(resp.error?.message || t("capabilities.failedSafetyMode"));
        return;
      }
      setExternalSafetyMode(normalizeExternalCapabilitySafetyMode(resp.result?.external_capability_safety_mode));
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("capabilities.failedSafetyMode"));
    } finally {
      setBusyKey("");
    }
  };

  const refreshStore = async () => {
    setBusyKey("store:refresh");
    setStoreErr("");
    try {
      const resp = await api.refreshOneColleagueCapabilitySource({ limit: 200 });
      if (!resp.ok) {
        setStoreErr(resp.error?.message || t("capabilities.store.failedRefresh"));
        return;
      }
      setStoreSummary(resp.result?.summary || {});
      setStoreInvalidCount(Array.isArray(resp.result?.invalid) ? resp.result.invalid.length : 0);
      await load();
    } catch (e) {
      setStoreErr(e instanceof Error ? e.message : t("capabilities.store.failedRefresh"));
    } finally {
      setBusyKey("");
    }
  };

  const testStoreSource = async () => {
    setBusyKey("store:test");
    setStoreErr("");
    try {
      const resp = await api.testOneColleagueCapabilitySource();
      if (!resp.ok) {
        setStoreErr(resp.error?.message || t("capabilities.store.failedTest"));
        return;
      }
      await load();
    } catch (e) {
      setStoreErr(e instanceof Error ? e.message : t("capabilities.store.failedTest"));
    } finally {
      setBusyKey("");
    }
  };

  const confirmPending = async (pendingId: string) => {
    const pid = String(pendingId || "").trim();
    if (!pid || !groupId) return;
    setBusyKey(`store:confirm:${pid}`);
    setStoreErr("");
    try {
      const resp = await api.confirmOneColleaguePendingCapabilities(groupId, [pid], enableActorId);
      if (!resp.ok) {
        setStoreErr(resp.error?.message || t("capabilities.store.failedConfirm"));
        return;
      }
      const failed = asResultArray(resp.result?.results).find((row) => row.ok === false);
      if (failed) {
        const error = failed.error && typeof failed.error === "object" ? failed.error as Record<string, unknown> : {};
        setStoreErr(String(error.message || t("capabilities.store.failedConfirm")));
        return;
      }
      await load();
    } catch (e) {
      setStoreErr(e instanceof Error ? e.message : t("capabilities.store.failedConfirm"));
    } finally {
      setBusyKey("");
    }
  };

  const confirmAllPending = async () => {
    if (!groupId || importablePendingItems.length === 0) return;
    setBusyKey("store:confirmAll");
    setStoreErr("");
    try {
      const resp = await api.confirmOneColleaguePendingCapabilities(
        groupId,
        importablePendingItems.map((item) => String(item.pending_id || "").trim()).filter(Boolean),
        enableActorId,
      );
      if (!resp.ok) {
        setStoreErr(resp.error?.message || t("capabilities.store.failedConfirm"));
        return;
      }
      const failed = asResultArray(resp.result?.results).filter((row) => row.ok === false);
      if (failed.length) {
        setStoreErr(t("capabilities.store.partialConfirmFailed", { count: failed.length }));
      }
      await load();
    } catch (e) {
      setStoreErr(e instanceof Error ? e.message : t("capabilities.store.failedConfirm"));
    } finally {
      setBusyKey("");
    }
  };

  const toggleCapabilityEnabled = async (capabilityId: string, nextEnabled: boolean) => {
    const capId = String(capabilityId || "").trim();
    if (!capId || !groupId) return;
    setBusyKey(`store:enable:${capId}`);
    setStoreErr("");
    try {
      const resp = await api.enableGroupCapability(groupId, capId, {
        enabled: nextEnabled,
        scope: enableActorId === DEFAULT_ENABLE_ACTOR_ID ? "session" : "actor",
        actorId: enableActorId,
        ttlSeconds: nextEnabled && enableActorId === DEFAULT_ENABLE_ACTOR_ID ? 3600 : 0,
        reason: "onecolleague_skill_store",
      });
      if (!resp.ok) {
        setStoreErr(resp.error?.message || t("capabilities.failedEnable"));
        return;
      }
      await load();
    } catch (e) {
      setStoreErr(e instanceof Error ? e.message : t("capabilities.failedEnable"));
    } finally {
      setBusyKey("");
    }
  };

  const toggleBlock = async (row: CapabilityOverviewItem | CapabilityBlockEntry, nextBlocked: boolean) => {
    const capabilityId = String(row.capability_id || "").trim();
    if (!capabilityId) return;
    let reason = "";
    if (nextBlocked) {
      reason = String(window.prompt(t("capabilities.blockReasonPrompt"), (row as CapabilityOverviewItem).blocked_reason || (row as CapabilityBlockEntry).reason || "") || "").trim();
    }
    setBusyKey(`block:${capabilityId}`);
    setErr("");
    try {
      const resp = await api.blockCapabilityGlobal(capabilityId, nextBlocked, reason);
      if (!resp.ok) {
        setErr(resp.error?.message || t("capabilities.failedBlock"));
        return;
      }
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : t("capabilities.failedBlock"));
    } finally {
      setBusyKey("");
    }
  };

  return (
    <div className="space-y-4">
      <div className={cardClass()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("capabilities.title")}</div>
            <div className="text-xs mt-1 text-[var(--color-text-muted)]">{t("capabilities.subtitle")}</div>
          </div>
          <button
            type="button"
            className="glass-btn px-3 py-2 rounded-lg text-sm min-h-[40px] text-[var(--color-text-secondary)]"
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? t("common:loading") : t("capabilities.refresh")}
          </button>
        </div>
        <div className="text-xs mt-3 text-[var(--color-text-tertiary)]">{t("capabilities.pageGuide")}</div>
        {err ? (
          <div className="mt-3 text-xs text-rose-600 dark:text-rose-400" role="alert">{err}</div>
        ) : null}
      </div>

      <div className={cardClass()}>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("capabilities.store.title")}</div>
            <div className="text-xs mt-1 text-[var(--color-text-muted)]">{t("capabilities.store.hint")}</div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                {t("capabilities.store.sourceStatus", {
                  status: oneColleagueSource?.enabled ? t("capabilities.sourceEnabled") : t("capabilities.sourceDisabled"),
                })}
              </span>
              <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                {t("capabilities.store.pendingCount", { count: storeCounts.pending })}
              </span>
              <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                {t("capabilities.store.importedCount", { count: storeCounts.imported })}
              </span>
              <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                {t("capabilities.store.enabledCount", { count: storeCounts.enabled })}
              </span>
            </div>
            {oneColleagueSource?.base_url ? (
              <div className="mt-2 text-[11px] break-all text-[var(--color-text-tertiary)]">
                {t("capabilities.store.baseUrl")}: {oneColleagueSource.base_url}
              </div>
            ) : null}
            {oneColleagueSource?.last_success_at ? (
              <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                {t("capabilities.store.lastSynced")}: {oneColleagueSource.last_success_at}
              </div>
            ) : null}
            {oneColleagueSource?.last_error ? (
              <div className="mt-1 text-[11px] text-rose-600 dark:text-rose-400">
                {t("capabilities.store.lastError")}: {oneColleagueSource.last_error}
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2 lg:justify-end">
            <button
              type="button"
              className="glass-btn px-3 py-2 rounded-lg text-xs min-h-[38px] text-[var(--color-text-secondary)]"
              disabled={busyKey === "store:test"}
              onClick={() => void testStoreSource()}
            >
              {busyKey === "store:test" ? t("common:loading") : t("capabilities.store.test")}
            </button>
            <button
              type="button"
              className="glass-btn px-3 py-2 rounded-lg text-xs min-h-[38px] text-[var(--color-text-secondary)]"
              disabled={busyKey === "store:refresh"}
              onClick={() => void refreshStore()}
            >
              {busyKey === "store:refresh" ? t("common:loading") : t("capabilities.store.refresh")}
            </button>
            <button
              type="button"
              className="px-3 py-2 rounded-lg text-xs min-h-[38px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 disabled:opacity-50"
              disabled={!groupId || importablePendingItems.length === 0 || busyKey === "store:confirmAll"}
              onClick={() => void confirmAllPending()}
            >
              {busyKey === "store:confirmAll" ? t("common:loading") : t("capabilities.store.importAll")}
            </button>
          </div>
        </div>

        <div className="mt-3 grid gap-2 md:grid-cols-4">
          {(["new", "updated", "unchanged", "invalid"] as const).map((key) => (
            <div key={key} className="rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-2">
              <div className="text-[10px] uppercase text-[var(--color-text-muted)]">{t(`capabilities.store.summary.${key}`)}</div>
              <div className="text-lg font-semibold text-[var(--color-text-primary)]">
                {key === "invalid" ? storeInvalidCount || Number(storeSummary.invalid || 0) : Number(storeSummary[key] || 0)}
              </div>
            </div>
          ))}
        </div>

        <div className="mt-3 grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(180px,240px)] md:items-center">
          <div className="text-[11px] text-[var(--color-text-tertiary)]">{t("capabilities.store.actorHint")}</div>
          <select
            value={enableActorId}
            onChange={(e) => setEnableActorId(e.target.value || DEFAULT_ENABLE_ACTOR_ID)}
            className="glass-input rounded-lg px-2 py-2 text-xs min-h-[38px] text-[var(--color-text-primary)]"
          >
            {actorOptions.map((actor) => (
              <option key={actor.actor_id} value={actor.actor_id}>{actor.label}</option>
            ))}
          </select>
        </div>

        {!groupId ? (
          <div className="mt-3 text-xs text-amber-600 dark:text-amber-300">{t("capabilities.store.requireGroup")}</div>
        ) : null}
        {storeErr ? (
          <div className="mt-3 text-xs text-rose-600 dark:text-rose-400" role="alert">{storeErr}</div>
        ) : null}

        <div className="mt-4">
          <div className="text-xs font-semibold text-[var(--color-text-primary)]">{t("capabilities.store.pendingTitle")}</div>
          <div className="mt-2 space-y-2">
            {activePendingItems.length === 0 ? (
              <div className="text-xs text-[var(--color-text-muted)]">{t("capabilities.store.noPending")}</div>
            ) : (
              activePendingItems.map((item) => {
                const pendingId = String(item.pending_id || "");
                const status = String(item.status || "");
                const capId = String(item.capability_id || "");
                return (
                  <div key={pendingId} className="rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-2">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate text-[var(--color-text-primary)]">{String(item.name || capId)}</div>
                        <div className="text-[11px] truncate text-[var(--color-text-tertiary)]">{capId}</div>
                        <div className="mt-1 flex flex-wrap gap-1">
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-600 dark:text-amber-300">{status}</span>
                          {item.kind ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">{item.kind}</span> : null}
                          {item.risk_level ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">{item.risk_level}</span> : null}
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                            {t("capabilities.store.versionChange", { old: versionLabel(item.old_version), next: versionLabel(item.new_version) })}
                          </span>
                        </div>
                      </div>
                      <button
                        type="button"
                        className="px-2.5 py-1.5 rounded text-xs min-h-[34px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 disabled:opacity-50"
                        disabled={!groupId || !item.record || busyKey === `store:confirm:${pendingId}`}
                        onClick={() => void confirmPending(pendingId)}
                      >
                        {busyKey === `store:confirm:${pendingId}` ? t("common:loading") : t("capabilities.store.importOne")}
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="mt-4">
          <div className="text-xs font-semibold text-[var(--color-text-primary)]">{t("capabilities.store.importedTitle")}</div>
          <div className="mt-2 max-h-72 overflow-auto space-y-2">
            {oneColleagueRows.length === 0 ? (
              <div className="text-xs text-[var(--color-text-muted)]">{t("capabilities.store.noImported")}</div>
            ) : (
              oneColleagueRows.map((row) => {
                const capId = String(row.capability_id || "");
                const enabledNow = enabledCapabilitySet.has(capId);
                const activeNow = activeSkillSet.has(capId);
                return (
                  <div key={capId} className="rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-2">
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate text-[var(--color-text-primary)]">{String(row.name || capId)}</div>
                        <div className="text-[11px] truncate text-[var(--color-text-tertiary)]">{capId}</div>
                        {String(row.description_short || "").trim() ? (
                          <div className="text-[11px] mt-1 text-[var(--color-text-tertiary)]">{String(row.description_short || "")}</div>
                        ) : null}
                        <div className="mt-1 flex flex-wrap gap-1">
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">{t("capabilities.store.imported")}</span>
                          {enabledNow ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-sky-500/15 text-sky-600 dark:text-sky-300">{t("capabilities.store.enabled")}</span> : null}
                          {activeNow ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-violet-500/15 text-violet-600 dark:text-violet-300">{t("capabilities.store.active")}</span> : null}
                          {row.policy_level ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">{row.policy_level}</span> : null}
                        </div>
                      </div>
                      <button
                        type="button"
                        className={`px-2.5 py-1.5 rounded text-xs min-h-[34px] border disabled:opacity-50 ${enabledNow ? "bg-rose-500/15 text-rose-600 dark:text-rose-300 border-rose-500/30" : "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30"}`}
                        disabled={!groupId || busyKey === `store:enable:${capId}`}
                        onClick={() => void toggleCapabilityEnabled(capId, !enabledNow)}
                      >
                        {busyKey === `store:enable:${capId}` ? t("common:loading") : enabledNow ? t("capabilities.disableForGroup") : t("capabilities.enableForGroup")}
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      <div className={cardClass()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("capabilities.safetyModeTitle")}</div>
            <div className="text-xs mt-1 text-[var(--color-text-muted)]">{t("capabilities.safetyModeHint")}</div>
          </div>
          <div className="text-[11px] text-[var(--color-text-tertiary)]">
            {t("capabilities.safetyModeCurrent", { mode: t(`capabilities.safetyMode.${externalSafetyMode}.label`) })}
          </div>
        </div>
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {(["normal", "conservative"] as ExternalCapabilitySafetyMode[]).map((mode) => {
            const selected = externalSafetyMode === mode;
            return (
              <button
                key={mode}
                type="button"
                className={`rounded-lg border px-3 py-3 text-left ${selected
                  ? "border-emerald-500/30 bg-emerald-500/15"
                  : "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)]"} ${busyKey === "policy" ? "opacity-60 cursor-not-allowed" : ""}`}
                disabled={busyKey === "policy" || selected}
                onClick={() => void updateExternalCapabilitySafetyMode(mode)}
              >
                <div className="text-sm font-medium text-[var(--color-text-primary)]">{t(`capabilities.safetyMode.${mode}.label`)}</div>
                <div className="text-xs mt-1 text-[var(--color-text-tertiary)]">{t(`capabilities.safetyMode.${mode}.hint`)}</div>
              </button>
            );
          })}
        </div>
        <div className="text-[11px] mt-2 text-[var(--color-text-muted)]">
          {t("capabilities.safetyModeCurrentRule", { mode: t(`capabilities.safetyMode.${externalSafetyMode}.label`) })}
        </div>
      </div>

      <div className={cardClass()}>
        <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("capabilities.libraryTitle")}</div>
        <div className="text-xs mt-1 text-[var(--color-text-muted)]">{t("capabilities.libraryHint")}</div>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("capabilities.searchPlaceholder")}
          className="glass-input w-full mt-2 rounded-lg px-3 py-2 text-sm min-h-[40px] text-[var(--color-text-primary)]"
        />
        <div className="mt-2 grid grid-cols-1 md:grid-cols-4 gap-2">
          <select value={registryKind} onChange={(e) => setRegistryKind(e.target.value as RegistryKindFilter)} className="glass-input rounded-lg px-2 py-2 text-xs min-h-[40px] text-[var(--color-text-primary)]">
            <option value="all">{t("capabilities.filterKindAll")}</option>
            <option value="pack">{t("capabilities.filterKindPack")}</option>
            <option value="mcp">{t("capabilities.filterKindMcp")}</option>
            <option value="skill">{t("capabilities.filterKindSkill")}</option>
          </select>
          <select value={registryPolicy} onChange={(e) => setRegistryPolicy(e.target.value as RegistryPolicyFilter)} className="glass-input rounded-lg px-2 py-2 text-xs min-h-[40px] text-[var(--color-text-primary)]">
            <option value="all">{t("capabilities.filterPolicyAll")}</option>
            <option value="actionable">{t("capabilities.filterPolicyActionable")}</option>
            <option value="blocked">{t("capabilities.filterPolicyBlocked")}</option>
            <option value="indexed">{t("capabilities.filterPolicyIndexed")}</option>
          </select>
          <select value={registrySource} onChange={(e) => setRegistrySource(e.target.value)} className="glass-input rounded-lg px-2 py-2 text-xs min-h-[40px] text-[var(--color-text-primary)]">
            <option value="all">{t("capabilities.filterSourceAll")}</option>
            {registrySourceOptions.map((sid) => (<option key={sid} value={sid}>{sid}</option>))}
          </select>
          <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 items-center">
            <label className="text-xs text-[var(--color-text-tertiary)]">{t("capabilities.pageSize")}</label>
            <select value={registryPageSize} onChange={(e) => setRegistryPageSize(Number(e.target.value) || 40)} className="glass-input rounded-lg px-2 py-2 text-xs min-h-[40px] text-[var(--color-text-primary)]">
              {REGISTRY_PAGE_SIZE_OPTIONS.map((size) => (<option key={size} value={size}>{size}</option>))}
            </select>
          </div>
        </div>
        <div className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          {t("capabilities.resultsSummary", { count: filteredRegistry.length })} · {t("capabilities.showingRange", { from: registryRange.from, to: registryRange.to })}
        </div>
        <div className="mt-2 max-h-[420px] overflow-auto space-y-2">
          {pagedRegistry.map((row) => {
            const capId = String(row.capability_id || "");
            const blockedNow = Boolean(row.blocked_global);
            const readinessPreview = normalizeReadinessPreview(row.readiness_preview);
            const recommendationMeta = [
              { label: t("capabilities.useWhen"), value: firstRecommendationLine(row.use_when) },
              { label: t("capabilities.verifyWith"), value: String(row.evidence_kind || "").trim() },
              { label: t("capabilities.gotcha"), value: firstRecommendationLine(row.gotchas) },
              { label: t("capabilities.avoidWhen"), value: firstRecommendationLine(row.avoid_when) },
            ].filter((entry) => entry.value);
            return (
              <div key={capId} className="rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate text-[var(--color-text-primary)]">{String(row.name || capId)}</div>
                    <div className="text-[11px] truncate text-[var(--color-text-tertiary)]">{capId}</div>
                    {String(row.description_short || "").trim() ? (
                      <div className="text-[11px] mt-1 text-[var(--color-text-tertiary)]">{String(row.description_short || "")}</div>
                    ) : null}
                    {recommendationMeta.length ? (
                      <div className="mt-1.5 space-y-0.5">
                        {recommendationMeta.map((entry) => (
                          <div key={`${capId}:${entry.label}`} className="text-[10px] leading-4 text-[var(--color-text-muted)]">
                            <span className="font-medium text-[var(--color-text-tertiary)]">{entry.label}: </span>
                            <span>{entry.value}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <div className="flex flex-wrap gap-1 mt-1">
                      {row.kind ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">{row.kind}</span> : null}
                      {row.source_id ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">{row.source_id}</span> : null}
                      {row.policy_level ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">{row.policy_level}</span> : null}
                      {row.recent_success?.success_count ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">{t("capabilities.recentCount", { count: Number(row.recent_success?.success_count || 0) })}</span> : null}
                      {blockedNow ? <span className="px-1.5 py-0.5 rounded text-[10px] bg-rose-500/15 text-rose-600 dark:text-rose-400">{t("capabilities.blocked")}</span> : null}
                    </div>
                    {renderReadinessPreview(readinessPreview)}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      type="button"
                      className={`px-2.5 py-1.5 rounded text-xs min-h-[32px] ${blockedNow ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30" : "bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30"} ${busyKey === `block:${capId}` ? "opacity-60 cursor-not-allowed" : ""}`}
                      disabled={busyKey === `block:${capId}`}
                      onClick={() => void toggleBlock(row, !blockedNow)}
                    >
                      {blockedNow ? t("capabilities.unblock") : t("capabilities.block")}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
          {pagedRegistry.length === 0 ? <div className="text-xs text-[var(--color-text-muted)]">{t("capabilities.noLibraryMatches")}</div> : null}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2">
          <button type="button" className="glass-btn px-3 py-1.5 rounded text-xs min-h-[34px] text-[var(--color-text-secondary)] disabled:opacity-50" disabled={registryPage <= 1} onClick={() => setRegistryPage((p) => Math.max(1, p - 1))}>{t("capabilities.pagePrev")}</button>
          <div className="text-xs text-[var(--color-text-tertiary)]">{t("capabilities.pageLabel", { page: registryPage, total: registryTotalPages })}</div>
          <button type="button" className="glass-btn px-3 py-1.5 rounded text-xs min-h-[34px] text-[var(--color-text-secondary)] disabled:opacity-50" disabled={registryPage >= registryTotalPages} onClick={() => setRegistryPage((p) => Math.min(registryTotalPages, p + 1))}>{t("capabilities.pageNext")}</button>
        </div>
      </div>

      <div className={cardClass()}>
        <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("capabilities.blockedListTitle")}</div>
        <div className="text-xs mt-1 text-[var(--color-text-muted)]">{t("capabilities.blockedListHint")}</div>
        <div className="mt-2 space-y-2">
          {blocked.length === 0 ? (
            <div className="text-xs text-[var(--color-text-muted)]">{t("capabilities.noBlocked")}</div>
          ) : (
            blocked.map((row) => {
              const capId = String(row.capability_id || "");
              return (
                <div key={capId} className="rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-xs">{capId}</code>
                    <button
                      type="button"
                      className="px-2.5 py-1 rounded text-xs min-h-[30px] bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30"
                      disabled={busyKey === `block:${capId}`}
                      onClick={() => void toggleBlock(row, false)}
                    >
                      {t("capabilities.unblock")}
                    </button>
                  </div>
                  {String(row.reason || "").trim() ? <div className="text-[11px] mt-1 text-[var(--color-text-tertiary)]">{String(row.reason || "")}</div> : null}
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
