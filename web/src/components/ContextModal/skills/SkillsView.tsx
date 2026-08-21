import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Plus, RefreshCw, Search, Sparkles } from "lucide-react";
import * as api from "../../../services/api";
import type { AgentState, CapabilityOverviewItem, CapabilityStateResult } from "../../../types";
import { classNames } from "../../../utils/classNames";
import type { ContextTranslator } from "../model";
import type { ContextModalUi } from "../ui";
import {
  filterMarketplaceSkills,
  marketplaceSkills,
  skillCategoryKey,
  UNCATEGORIZED_KEY,
} from "./skillsModel";

interface SkillsViewProps {
  groupId: string;
  agents: AgentState[];
  tr: ContextTranslator;
  ui: ContextModalUi;
  hideHeaderText?: boolean;
}

function cleanId(value: unknown): string {
  return String(value || "").trim();
}

function enabledSetFromState(state: CapabilityStateResult | null): Set<string> {
  const out = new Set<string>();
  for (const capabilityId of state?.enabled_capabilities || []) {
    const id = cleanId(capabilityId);
    if (id) out.add(id);
  }
  for (const row of state?.active_capsule_skills || []) {
    const id = cleanId(row.capability_id);
    if (id) out.add(id);
  }
  return out;
}

function groupDefaultSetFromState(state: CapabilityStateResult | null): Set<string> {
  return new Set(
    (state?.enabled || [])
      .filter((row) => cleanId(row.scope).toLowerCase() === "group")
      .map((row) => cleanId(row.capability_id))
      .filter(Boolean),
  );
}

export function SkillsView({ groupId, agents, tr, ui, hideHeaderText = false }: SkillsViewProps) {
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState("");
  const [activeView, setActiveView] = useState<"market" | "added">("market");
  const [items, setItems] = useState<CapabilityOverviewItem[]>([]);
  const [groupState, setGroupState] = useState<CapabilityStateResult | null>(null);
  const [actorStates, setActorStates] = useState<Record<string, CapabilityStateResult | null>>({});

  const actorIds = useMemo(
    () => agents.map((agent) => cleanId(agent.id)).filter((id) => id && id !== "user"),
    [agents],
  );

  const load = useCallback(async () => {
    if (!groupId) return;
    setLoading(true);
    setError("");
    try {
      const [overviewResp, groupStateResp, actorStateRows] = await Promise.all([
        api.fetchCapabilityOverview({
          includeIndexed: true,
          includeSourceInstances: false,
          kind: "skill",
          sourceId: "onecolleague_skill_library",
          limit: 1200,
        }),
        api.fetchGroupCapabilityState(groupId, "user"),
        Promise.all(actorIds.map(async (actorId) => {
          const resp = await api.fetchGroupCapabilityState(groupId, actorId);
          return [actorId, resp.ok ? resp.result || null : null] as const;
        })),
      ]);
      if (!overviewResp.ok || !groupStateResp.ok) {
        setError(
          overviewResp.error?.message
          || groupStateResp.error?.message
          || tr("context.failedToLoadSkills", "加载技能失败。"),
        );
        return;
      }
      setItems(overviewResp.result?.items || []);
      setGroupState(groupStateResp.result || null);
      setActorStates(Object.fromEntries(actorStateRows));
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("context.failedToLoadSkills", "加载技能失败。"));
    } finally {
      setLoading(false);
    }
  }, [actorIds, groupId, tr]);

  useEffect(() => { void load(); }, [load]);

  const enabled = useMemo(() => groupDefaultSetFromState(groupState), [groupState]);
  const actorEnabled = useMemo(() => Object.fromEntries(
    Object.entries(actorStates).map(([actorId, state]) => [actorId, enabledSetFromState(state)]),
  ) as Record<string, Set<string>>, [actorStates]);
  const allSkills = useMemo(() => marketplaceSkills(items), [items]);
  const categories = useMemo(() => {
    const byKey = new Map<string, { key: string; name: string; order: number }>();
    for (const row of allSkills) {
      const key = skillCategoryKey(row);
      if (byKey.has(key)) continue;
      byKey.set(key, {
        key,
        name: cleanId(row.category_name) || tr("context.skillsUncategorized", "未分组"),
        order: Number(row.category_order) || 0,
      });
    }
    return [...byKey.values()].sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
  }, [allSkills, tr]);
  const visibleSkills = useMemo(() => filterMarketplaceSkills(
    items,
    query,
    activeCategory,
    activeView === "added",
    enabled,
  ), [activeCategory, activeView, enabled, items, query]);

  const toggle = async (capabilityId: string, nextEnabled: boolean) => {
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
        reason: "context_modal_skill_market",
      });
      if (!resp.ok) {
        setError(resp.error?.message || tr("context.failedToUpdateSkill", "更新技能失败。"));
        return;
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : tr("context.failedToUpdateSkill", "更新技能失败。"));
    } finally {
      setBusyKey("");
    }
  };

  return (
    <section className="min-w-0 px-1 py-2">
      {!hideHeaderText ? (
        <div className="mb-5">
          <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">
            {tr("context.skillsTitle", "技能")}
          </h2>
          <p className={classNames("mt-1 text-sm", ui.subtleTextClass)}>
            {tr("context.skillsHint", "为工作组选择需要的技能。")}
          </p>
        </div>
      ) : null}

      <div className="flex flex-col gap-3 border-b border-[var(--glass-border-subtle)] pb-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="inline-flex w-fit rounded-lg bg-[var(--glass-tab-bg)] p-1">
            {(["market", "added"] as const).map((view) => (
              <button
                key={view}
                type="button"
                onClick={() => setActiveView(view)}
                className={classNames(
                  "min-h-9 rounded-md px-4 text-sm font-medium transition-colors",
                  activeView === view
                    ? "bg-[var(--glass-panel-bg)] text-[var(--color-text-primary)] shadow-sm"
                    : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
                )}
              >
                {view === "market"
                  ? tr("context.skillsMarket", "技能市场")
                  : tr("context.skillsAdded", "已添加")}
              </button>
            ))}
          </div>
          <div className="flex min-w-0 items-center gap-2">
            <label className="relative min-w-0 flex-1 md:w-72 md:flex-none">
              <Search aria-hidden="true" className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--color-text-muted)]" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={tr("context.skillsSearchPlaceholder", "搜索技能")}
                className={classNames(ui.inputClass, "pl-9")}
              />
            </label>
            <button
              type="button"
              onClick={() => void load()}
              disabled={loading}
              className="grid size-11 shrink-0 place-items-center rounded-lg border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg)] disabled:opacity-50"
              title={tr("context.refresh", "刷新")}
              aria-label={tr("context.refresh", "刷新")}
            >
              <RefreshCw className={classNames("size-4", loading && "animate-spin")} />
            </button>
          </div>
        </div>

        <div className="flex gap-2 overflow-x-auto pb-1">
          <button
            type="button"
            onClick={() => setActiveCategory("")}
            className={classNames(ui.chipBaseClass, "shrink-0", !activeCategory && "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-300")}
          >
            {tr("context.all", "全部")}
          </button>
          {categories.map((category) => (
            <button
              key={category.key}
              type="button"
              onClick={() => setActiveCategory(category.key)}
              className={classNames(ui.chipBaseClass, "shrink-0", activeCategory === category.key && "border-blue-500/40 bg-blue-500/10 text-blue-600 dark:text-blue-300")}
            >
              {category.name}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <div className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-300">{error}</div>
      ) : null}

      <div className="mt-5 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {visibleSkills.map((row) => {
          const capId = cleanId(row.capability_id);
          const isEnabled = enabled.has(capId);
          const blocked = !isEnabled && (row.blocked_global || row.enable_supported === false);
          const syncedActors = actorIds.filter((actorId) => actorEnabled[actorId]?.has(capId)).length;
          return (
            <article key={capId} className="flex min-h-52 flex-col rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-4 shadow-sm transition-colors hover:border-[var(--glass-accent-border)]">
              <div className="flex min-w-0 items-start gap-3">
                <div className="relative grid size-11 shrink-0 place-items-center overflow-hidden rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)]">
                  <Sparkles className="size-5 text-[var(--color-accent-primary)]" aria-hidden="true" />
                  {row.icon_url ? (
                    <img
                      src={row.icon_url}
                      alt=""
                      className="absolute inset-0 size-full object-cover"
                      loading="lazy"
                      onError={(event) => { event.currentTarget.hidden = true; }}
                    />
                  ) : null}
                </div>
                <div className="min-w-0 flex-1">
                  <h3 className="line-clamp-2 text-sm font-semibold text-[var(--color-text-primary)]">
                    {cleanId(row.name) || capId}
                  </h3>
                  <p className="mt-0.5 truncate text-xs text-[var(--color-text-muted)]">
                    {cleanId(row.category_name) || (skillCategoryKey(row) === UNCATEGORIZED_KEY ? tr("context.skillsUncategorized", "未分组") : skillCategoryKey(row))}
                  </p>
                </div>
              </div>
              <p className="mt-3 line-clamp-3 min-h-[3.75rem] text-sm leading-5 text-[var(--color-text-secondary)]">
                {cleanId(row.description_short) || tr("context.skillsNoDescription", "暂无技能说明")}
              </p>
              <div className="mt-auto flex items-end justify-between gap-2 pt-4">
                <span className="truncate text-xs text-[var(--color-text-muted)]">
                  {actorIds.length > 0
                    ? tr("context.skillSyncedActors", "{{synced}}/{{total}} 个智能体", { synced: syncedActors, total: actorIds.length })
                    : capId}
                </span>
                <button
                  type="button"
                  disabled={loading || busyKey === capId || blocked}
                  onClick={() => void toggle(capId, !isEnabled)}
                  className={classNames(
                    "inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                    isEnabled
                      ? "border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)] hover:bg-rose-500/10 hover:text-rose-600"
                      : "bg-[var(--color-accent-primary)] text-white hover:brightness-110",
                  )}
                >
                  {isEnabled ? <Check className="size-3.5" /> : <Plus className="size-3.5" />}
                  {isEnabled ? tr("context.skillsAdded", "已添加") : tr("context.skillsAdd", "添加")}
                </button>
              </div>
            </article>
          );
        })}
      </div>

      {visibleSkills.length === 0 ? (
        <div className="mt-5 rounded-lg border border-dashed border-[var(--glass-border-subtle)] px-4 py-10 text-center text-sm text-[var(--color-text-muted)]">
          {loading ? tr("context.loadingSkills", "正在加载技能…") : tr("context.noSkills", "暂无可用技能。")}
        </div>
      ) : null}
    </section>
  );
}
