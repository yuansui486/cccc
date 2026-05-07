import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../../../services/api";
import type { AgentState, CapabilityOverviewItem, CapabilityStateResult } from "../../../types";
import { classNames } from "../../../utils/classNames";
import type { ContextTranslator } from "../model";
import type { ContextModalUi } from "../ui";

interface SkillsViewProps {
  groupId: string;
  agents: AgentState[];
  tr: ContextTranslator;
  ui: ContextModalUi;
}

function cleanId(value: unknown): string {
  return String(value || "").trim();
}

function capabilityLabel(row: CapabilityOverviewItem): string {
  return cleanId(row.name) || cleanId(row.capability_id);
}

function enabledSetFromState(state: CapabilityStateResult | null): Set<string> {
  const out = new Set<string>();
  const enabled = Array.isArray(state?.enabled_capabilities) ? state?.enabled_capabilities : [];
  for (const capabilityId of enabled || []) {
    const id = cleanId(capabilityId);
    if (id) out.add(id);
  }
  const activeSkills = Array.isArray(state?.active_capsule_skills) ? state?.active_capsule_skills : [];
  for (const row of activeSkills || []) {
    const id = cleanId(row.capability_id);
    if (id) out.add(id);
  }
  return out;
}

function groupDefaultSetFromState(state: CapabilityStateResult | null): Set<string> {
  const out = new Set<string>();
  const entries = Array.isArray(state?.enabled) ? state?.enabled : [];
  for (const row of entries) {
    if (cleanId(row.scope).toLowerCase() !== "group") continue;
    const id = cleanId(row.capability_id);
    if (id) out.add(id);
  }
  return out;
}

export function SkillsView({ groupId, agents, tr, ui }: SkillsViewProps) {
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CapabilityOverviewItem[]>([]);
  const [groupState, setGroupState] = useState<CapabilityStateResult | null>(null);
  const [actorStates, setActorStates] = useState<Record<string, CapabilityStateResult | null>>({});

  const actorIds = useMemo(() => {
    return agents.map((agent) => cleanId(agent.id)).filter((id) => id && id !== "user");
  }, [agents]);

  const load = useCallback(async () => {
    if (!groupId) return;
    setLoading(true);
    setError("");
    try {
      const [overviewResp, groupStateResp, actorStateRows] = await Promise.all([
        api.fetchCapabilityOverview({ includeIndexed: true, limit: 1200 }),
        api.fetchGroupCapabilityState(groupId, "user"),
        Promise.all(
          actorIds.map(async (actorId) => {
            const resp = await api.fetchGroupCapabilityState(groupId, actorId);
            return [actorId, resp.ok ? resp.result || null : null] as const;
          }),
        ),
      ]);

      if (!overviewResp.ok) {
        setItems([]);
        setError(overviewResp.error?.message || tr("context.failedToLoadSkills", "Failed to load skills."));
        return;
      }
      if (!groupStateResp.ok) {
        setGroupState(null);
        setError(groupStateResp.error?.message || tr("context.failedToLoadSkillState", "Failed to load group skill state."));
        return;
      }

      setItems(Array.isArray(overviewResp.result?.items) ? overviewResp.result.items : []);
      setGroupState(groupStateResp.result || null);
      setActorStates(Object.fromEntries(actorStateRows));
    } catch (err) {
      setItems([]);
      setGroupState(null);
      setActorStates({});
      setError(err instanceof Error ? err.message : tr("context.failedToLoadSkills", "Failed to load skills."));
    } finally {
      setLoading(false);
    }
  }, [actorIds, groupId, tr]);

  useEffect(() => {
    void load();
  }, [load]);

  const groupDefaultSet = useMemo(() => groupDefaultSetFromState(groupState), [groupState]);
  const actorEnabledSets = useMemo(() => {
    return Object.fromEntries(
      Object.entries(actorStates).map(([actorId, state]) => [actorId, enabledSetFromState(state)])
    ) as Record<string, Set<string>>;
  }, [actorStates]);

  const skillRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items
      .filter((row) => cleanId(row.kind).toLowerCase() === "skill")
      .filter((row) => {
        if (!q) return true;
        const text = [
          row.capability_id,
          row.name,
          row.description_short,
          row.source_id,
          row.policy_level,
          ...(Array.isArray(row.tags) ? row.tags : []),
        ].map((value) => cleanId(value).toLowerCase()).join(" ");
        return text.includes(q);
      })
      .sort((a, b) => {
        const aEnabled = groupDefaultSet.has(cleanId(a.capability_id)) ? 0 : 1;
        const bEnabled = groupDefaultSet.has(cleanId(b.capability_id)) ? 0 : 1;
        if (aEnabled !== bEnabled) return aEnabled - bEnabled;
        return capabilityLabel(a).localeCompare(capabilityLabel(b));
      });
  }, [groupDefaultSet, items, query]);

  const enabledSkillCount = useMemo(
    () => skillRows.filter((row) => groupDefaultSet.has(cleanId(row.capability_id))).length,
    [groupDefaultSet, skillRows],
  );

  const toggleGroupDefault = async (capabilityId: string, nextEnabled: boolean) => {
    const capId = cleanId(capabilityId);
    if (!capId || !groupId) return;
    setBusyKey(capId);
    setError("");
    try {
      const resp = await api.enableGroupCapability(groupId, capId, {
        enabled: nextEnabled,
        scope: "group",
        actorId: "user",
        ttlSeconds: 0,
        reason: "context_modal_group_skill_default",
      });
      if (!resp.ok) {
        setError(resp.error?.message || tr("context.failedToUpdateSkill", "Failed to update skill."));
        return;
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("context.failedToUpdateSkill", "Failed to update skill."));
    } finally {
      setBusyKey("");
    }
  };

  return (
    <section className={classNames(ui.surfaceClass, "p-4")}>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className={classNames("text-lg font-semibold", "text-[var(--color-text-primary)]")}>
            {tr("context.skillsTitle", "Skills")}
          </div>
          <div className={classNames("mt-1 text-sm", ui.subtleTextClass)}>
            {tr("context.skillsHint", "Set the workgroup skill defaults. Group-enabled skills are effective for current agents and inherited by later agents.")}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-[var(--glass-panel-bg)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)]">
            {tr("context.skillsAvailableCount", "{{count}} available", { count: skillRows.length })}
          </span>
          <span className="rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs text-emerald-600 dark:text-emerald-400">
            {tr("context.skillsEnabledCount", "{{count}} enabled", { count: enabledSkillCount })}
          </span>
          <button type="button" onClick={() => void load()} disabled={loading} className={ui.buttonSecondaryClass}>
            {loading ? tr("context.loading", "Loading...") : tr("context.refresh", "Refresh")}
          </button>
        </div>
      </div>

      <div className="mt-4">
        <input
          className={ui.inputClass}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={tr("context.skillsSearchPlaceholder", "Search skills")}
        />
      </div>

      {error ? (
        <div className={classNames("mt-3 rounded-xl border px-3 py-2 text-sm", "border-rose-500/30 bg-rose-500/15 text-rose-600 dark:text-rose-400")}>
          {error}
        </div>
      ) : null}

      <div className="mt-4 grid gap-3">
        {skillRows.length === 0 ? (
          <div className="rounded-xl border border-dashed border-[var(--glass-border-subtle)] px-3 py-4 text-sm text-[var(--color-text-muted)]">
            {loading ? tr("context.loadingSkills", "Loading skills...") : tr("context.noSkills", "No skills available.")}
          </div>
        ) : (
          skillRows.map((row) => {
            const capId = cleanId(row.capability_id);
            const enabled = groupDefaultSet.has(capId);
            const syncedActors = actorIds.filter((actorId) => actorEnabledSets[actorId]?.has(capId)).length;
            const enableBlocked = !enabled && (row.blocked_global || row.enable_supported === false);
            return (
              <div key={capId} className="rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] px-3 py-3">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-[var(--color-text-primary)]">{capabilityLabel(row)}</div>
                    <div className="truncate text-[11px] text-[var(--color-text-tertiary)]">{capId}</div>
                    {cleanId(row.description_short) ? (
                      <div className="mt-1 text-xs text-[var(--color-text-secondary)]">{cleanId(row.description_short)}</div>
                    ) : null}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {enabled ? (
                        <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
                          {tr("context.skillGroupDefault", "Group default")}
                        </span>
                      ) : null}
                      {actorIds.length > 0 ? (
                        <span className="rounded bg-blue-500/15 px-1.5 py-0.5 text-[10px] text-blue-600 dark:text-blue-300">
                          {tr("context.skillSyncedActors", "{{synced}}/{{total}} agents", { synced: syncedActors, total: actorIds.length })}
                        </span>
                      ) : null}
                      {cleanId(row.source_id) ? (
                        <span className="rounded bg-[var(--glass-tab-bg)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-secondary)]">
                          {cleanId(row.source_id)}
                        </span>
                      ) : null}
                      {cleanId(row.policy_level) ? (
                        <span className="rounded bg-[var(--glass-tab-bg)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-secondary)]">
                          {cleanId(row.policy_level)}
                        </span>
                      ) : null}
                      {row.blocked_global ? (
                        <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] text-rose-600 dark:text-rose-300">
                          {tr("context.skillBlocked", "Blocked")}
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={enabled}
                    aria-label={enabled ? tr("context.disableSkill", "Disable skill") : tr("context.enableSkill", "Enable skill")}
                    title={enabled ? tr("context.disableSkill", "Disable skill") : tr("context.enableSkill", "Enable skill")}
                    disabled={loading || busyKey === capId || enableBlocked}
                    onClick={() => void toggleGroupDefault(capId, !enabled)}
                    className={ui.switchTrackClass(enabled)}
                  >
                    <span className={ui.switchThumbClass(enabled)} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </section>
  );
}
