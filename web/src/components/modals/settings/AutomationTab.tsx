// AutomationTab configures proactive system behaviors (nudges/alerts) and user-defined rules.
import React, { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import * as api from "../../../services/api";
import { getRecipientTokenLabel } from "../../../utils/displayText";
import type { Actor, AutomationRule, AutomationRuleAction, AutomationRuleSet, AutomationRuleStatus } from "../../../types";
import {
  Section,
  SparkIcon,
  actionKind,
  clampInt,
  defaultNotifyAction,
  isValidId,
  nowId,
} from "./automationUtils";
import { AutomationPoliciesSection } from "./AutomationPoliciesSection";
import { AutomationRuleEditorModal } from "./AutomationRuleEditorModal";
import { AutomationRuleList } from "./AutomationRuleList";
import { AutomationSnippetModal } from "./AutomationSnippetModal";
import { cardClass, primaryButtonClass } from "./types";

interface AutomationTabProps {
  isDark: boolean;
  groupId?: string;
  devActors: Actor[];
  busy: boolean;

  nudgeSeconds: number;
  setNudgeSeconds: (v: number) => void;
  replyRequiredNudgeSeconds: number;
  setReplyRequiredNudgeSeconds: (v: number) => void;
  attentionAckNudgeSeconds: number;
  setAttentionAckNudgeSeconds: (v: number) => void;
  unreadNudgeSeconds: number;
  setUnreadNudgeSeconds: (v: number) => void;
  nudgeDigestMinIntervalSeconds: number;
  setNudgeDigestMinIntervalSeconds: (v: number) => void;
  nudgeMaxRepeatsPerObligation: number;
  setNudgeMaxRepeatsPerObligation: (v: number) => void;
  nudgeEscalateAfterRepeats: number;
  setNudgeEscalateAfterRepeats: (v: number) => void;

  idleSeconds: number;
  setIdleSeconds: (v: number) => void;
  keepaliveSeconds: number;
  setKeepaliveSeconds: (v: number) => void;
  keepaliveMax: number;
  setKeepaliveMax: (v: number) => void;
  silenceSeconds: number;
  setSilenceSeconds: (v: number) => void;

  helpNudgeIntervalSeconds: number;
  setHelpNudgeIntervalSeconds: (v: number) => void;
  helpNudgeMinMessages: number;
  setHelpNudgeMinMessages: (v: number) => void;
  onSavePolicies: () => void;
}

export function AutomationTab(props: AutomationTabProps) {
  const { isDark } = props;
  const { t } = useTranslation("settings");

  const [rulesBusy, setRulesBusy] = useState(false);
  const [rulesErr, setRulesErr] = useState("");
  const [ruleset, setRuleset] = useState<AutomationRuleSet | null>(null);
  const [rulesVersion, setRulesVersion] = useState<number | undefined>(undefined);
  const [status, setStatus] = useState<Record<string, AutomationRuleStatus>>({});
  const [configPath, setConfigPath] = useState("");
  const [supportedVars, setSupportedVars] = useState<string[]>([]);
  const [newSnippetId, setNewSnippetId] = useState("");
  const [snippetManagerOpen, setSnippetManagerOpen] = useState(false);
  const [templateErr, setTemplateErr] = useState("");
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [oneShotModeByRule, setOneShotModeByRule] = useState<Record<string, "after" | "exact">>({});
  const [oneShotAfterMinutesByRule, setOneShotAfterMinutesByRule] = useState<Record<string, number>>({});
  const [showCompletedRules, setShowCompletedRules] = useState(false);

  const loadRules = async () => {
    if (!props.groupId) return;
    setRulesBusy(true);
    setRulesErr("");
    try {
      const resp = await api.fetchAutomation(props.groupId);
      if (!resp.ok) {
        setRulesErr(resp.error?.message || t("automation.failedToLoad"));
        return;
      }
      setRuleset(resp.result.ruleset);
      setRulesVersion(typeof resp.result.version === "number" ? resp.result.version : undefined);
      setStatus(resp.result.status || {});
      setConfigPath(String(resp.result.config_path || ""));
      setSupportedVars(Array.isArray(resp.result.supported_vars) ? resp.result.supported_vars.map(String) : []);
    } catch {
      setRulesErr(t("automation.failedToLoad"));
    } finally {
      setRulesBusy(false);
    }
  };

  useEffect(() => {
    if (props.groupId) void loadRules();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload on group switch
  }, [props.groupId]);

  const draft: AutomationRuleSet = ruleset || { rules: [], snippets: {} };
  const snippetIds = useMemo(() => Object.keys(draft.snippets || {}).sort(), [draft.snippets]);

  const actorTargetOptions = useMemo(() => {
    const out: Array<{ value: string; label: string }> = [
      { value: "@foreman", label: getRecipientTokenLabel("@foreman") },
      { value: "@peers", label: getRecipientTokenLabel("@peers") },
      { value: "@all", label: getRecipientTokenLabel("@all") },
    ];
    for (const a of props.devActors || []) {
      if (!a || !a.id || a.id === "user") continue;
      out.push({ value: a.id, label: a.title ? `${a.id} (${a.title})` : a.id });
    }
    return out;
  }, [props.devActors]);

  const completedOneTimeRuleIds = useMemo(() => {
    return draft.rules
      .filter((rule) => {
        const rid = String(rule.id || "").trim();
        const triggerKind = String(rule.trigger?.kind || "");
        const st = status[rid] || {};
        return Boolean(rid) && triggerKind === "at" && Boolean(st.completed);
      })
      .map((rule) => String(rule.id || "").trim());
  }, [draft.rules, status]);

  const visibleRules = useMemo(() => {
    if (showCompletedRules) return draft.rules;
    return draft.rules.filter((rule) => {
      const rid = String(rule.id || "").trim();
      const triggerKind = String(rule.trigger?.kind || "");
      const st = status[rid] || {};
      return !(triggerKind === "at" && Boolean(st.completed));
    });
  }, [draft.rules, status, showCompletedRules]);

  const setDraft = (next: AutomationRuleSet) => setRuleset(next);

  const updateRule = (ruleId: string, patch: Partial<AutomationRule>) => {
    const next = { ...draft, rules: draft.rules.map((r) => (r.id === ruleId ? { ...r, ...patch } : r)) };
    setDraft(next);
  };

  const updateRuleNested = (ruleId: string, patch: Partial<AutomationRule>) => updateRule(ruleId, patch);

  const buildPersistedRuleset = (source: AutomationRuleSet): AutomationRuleSet => {
    const normalizedRules = source.rules.map((rule) => {
      if (!rule.action || rule.action.kind !== "notify") return rule;
      const notifyAction = rule.action as Extract<AutomationRuleAction, { kind: "notify" }>;
      const { kind: _kind, title: _unusedTitle, ...rest } = notifyAction;
      return { ...rule, action: { kind: "notify", ...rest } as AutomationRuleAction };
    });
    return { ...source, rules: normalizedRules };
  };

  const setOneShotAfterMinutes = (ruleId: string, minutes: number) => {
    const m = clampInt(minutes, 1, 7 * 24 * 60);
    setOneShotAfterMinutesByRule((prev) => ({ ...prev, [ruleId]: m }));
    updateRuleNested(ruleId, { trigger: { kind: "at", at: new Date(Date.now() + m * 60 * 1000).toISOString() } });
  };

  const addRule = (seed?: Partial<AutomationRule>) => {
    const id = String(seed?.id || nowId("rule")).trim();
    const nextRule: AutomationRule = {
      id,
      enabled: seed?.enabled ?? true,
      scope: seed?.scope ?? "group",
      owner_actor_id: seed?.owner_actor_id ?? null,
      to: seed?.to ?? ["@foreman"],
      trigger: seed?.trigger ?? { kind: "interval", every_seconds: 900 },
      action: seed?.action ?? defaultNotifyAction(),
    };
    setDraft({ ...draft, rules: [...draft.rules, nextRule] });
    return id;
  };

  const removeRule = (ruleId: string) => {
    setDraft({ ...draft, rules: draft.rules.filter((r) => r.id !== ruleId) });
    if (editingRuleId === ruleId) setEditingRuleId(null);
  };

  const addSnippet = () => {
    const id = newSnippetId.trim();
    if (!id) return;
    if (!isValidId(id)) {
      setTemplateErr(t("automation.snippetInvalid"));
      return;
    }
    if (draft.snippets[id] !== undefined) {
      setTemplateErr(t("automation.snippetExists", { id }));
      return;
    }
    setTemplateErr("");
    setNewSnippetId("");
    setDraft({ ...draft, snippets: { ...draft.snippets, [id]: "" } });
  };

  const updateSnippet = (id: string, content: string) => {
    setDraft({ ...draft, snippets: { ...draft.snippets, [id]: content } });
  };

  const deleteSnippet = (id: string) => {
    const ok = window.confirm(t("automation.deleteSnippetConfirm", { id }));
    if (!ok) return;
    const next = { ...draft.snippets };
    delete next[id];
    setDraft({ ...draft, snippets: next });
  };

  const openSnippetManager = () => {
    setTemplateErr("");
    setSnippetManagerOpen(true);
  };

  const closeSnippetManager = () => {
    setTemplateErr("");
    setSnippetManagerOpen(false);
  };

  const validateBeforeSave = (): string | null => {
    const seen = new Set<string>();
    for (const r of draft.rules) {
      const id = String(r.id || "").trim();
      if (!id) return t("automation.validationRuleNameRequired");
      if (!isValidId(id)) return t("automation.validationRuleNameInvalid", { id });
      if (seen.has(id)) return t("automation.validationRuleNameDuplicate", { id });
      seen.add(id);
      const triggerKind = String(r.trigger?.kind || "interval");
      if (triggerKind === "interval") {
        const every = Number(r.trigger && "every_seconds" in r.trigger ? r.trigger.every_seconds : 0);
        if (!Number.isFinite(every) || every < 1) return t("automation.validationIntervalMin", { id });
      } else if (triggerKind === "cron") {
        const cronExpr = String(r.trigger && "cron" in r.trigger ? r.trigger.cron : "").trim();
        if (!cronExpr) return t("automation.validationScheduleRequired", { id });
      } else if (triggerKind === "at") {
        const atRaw = String(r.trigger && "at" in r.trigger ? r.trigger.at : "").trim();
        if (!atRaw) return t("automation.validationOneTimeRequired", { id });
        const atMillis = Date.parse(atRaw);
        if (!Number.isFinite(atMillis)) return t("automation.validationDateTimeInvalid", { id });
      } else {
        return t("automation.validationTriggerUnsupported", { id, kind: triggerKind });
      }
      const scope = String(r.scope || "group");
      if (scope !== "group" && scope !== "personal") return t("automation.validationScopeInvalid", { id });
      if (scope === "personal" && !String(r.owner_actor_id || "").trim()) {
        return t("automation.validationOwnerRequired", { id });
      }
      const to = Array.isArray(r.to) ? r.to.map((x) => String(x || "").trim()).filter(Boolean) : [];
      const kind = actionKind(r.action);
      if (kind === "notify") {
        if (to.length === 0) return t("automation.validationRecipientRequired", { id });
        const snippetRef = String(r.action && "snippet_ref" in r.action ? r.action.snippet_ref || "" : "").trim();
        const msg = String(r.action && "message" in r.action ? r.action.message || "" : "").trim();
        if (snippetRef && draft.snippets[snippetRef] === undefined) {
          return t("automation.validationSnippetMissing", { id, snippet: snippetRef });
        }
        if (!snippetRef && !msg) return t("automation.validationMessageRequired", { id });
      } else if (kind === "group_state") {
        if (triggerKind !== "at") return t("automation.validationGroupStateOneTimeOnly", { id });
        const targetState = String(r.action && "state" in r.action ? r.action.state || "" : "").trim();
        if (!["active", "idle", "paused", "stopped"].includes(targetState)) {
          return t("automation.validationGroupStateTargetRequired", { id });
        }
      } else if (kind === "actor_control") {
        if (triggerKind !== "at") return t("automation.validationActorControlOneTimeOnly", { id });
        const operation = String(r.action && "operation" in r.action ? r.action.operation || "" : "").trim();
        if (!["start", "stop", "restart"].includes(operation)) {
          return t("automation.validationActorControlOperationRequired", { id });
        }
        const targets = Array.isArray(r.action && "targets" in r.action ? r.action.targets : [])
          ? (r.action as { targets?: string[] }).targets?.map((x) => String(x || "").trim()).filter(Boolean) || []
          : [];
        if (targets.length === 0) return t("automation.validationActorControlTargetRequired", { id });
      }
    }
    for (const k of Object.keys(draft.snippets || {})) {
      const id = String(k || "").trim();
      if (!id) return t("automation.validationSnippetNameRequired");
      if (!isValidId(id)) return t("automation.validationRuleNameInvalid", { id });
    }
    return null;
  };

  const saveRules = async () => {
    if (!props.groupId) return;
    const err = validateBeforeSave();
    if (err) {
      setRulesErr(err);
      return;
    }
    setRulesBusy(true);
    setRulesErr("");
    try {
      const resp = await api.updateAutomation(props.groupId, buildPersistedRuleset(draft), rulesVersion);
      if (!resp.ok) {
        const code = String(resp.error?.code || "").trim();
          if (code === "version_conflict") {
            await loadRules();
            setRulesErr(t("automation.versionConflict"));
            return;
          }
        setRulesErr(resp.error?.message || t("automation.failedToSave"));
        return;
      }
      await loadRules();
    } catch {
      setRulesErr(t("automation.failedToSave"));
    } finally {
      setRulesBusy(false);
    }
  };

  const clearCompletedRules = async () => {
    if (!props.groupId) return;
    if (completedOneTimeRuleIds.length <= 0) {
      setRulesErr(t("automation.noCompletedToClear"));
      return;
    }
    const ok = window.confirm(t("automation.clearCompletedConfirm", { count: completedOneTimeRuleIds.length }));
    if (!ok) return;
    const removing = new Set(completedOneTimeRuleIds);
    const nextRules = draft.rules.filter((rule) => !removing.has(String(rule.id || "").trim()));
    const nextDraft: AutomationRuleSet = { ...draft, rules: nextRules };
    setRulesBusy(true);
    setRulesErr("");
    try {
      const resp = await api.updateAutomation(props.groupId, buildPersistedRuleset(nextDraft), rulesVersion);
      if (!resp.ok) {
        const code = String(resp.error?.code || "").trim();
        if (code === "version_conflict") {
          await loadRules();
          setRulesErr(t("automation.versionConflictShort"));
          return;
        }
        setRulesErr(resp.error?.message || t("automation.failedToClear"));
        return;
      }
      if (editingRuleId && removing.has(editingRuleId)) {
        setEditingRuleId(null);
      }
      await loadRules();
    } catch {
      setRulesErr(t("automation.failedToClear"));
    } finally {
      setRulesBusy(false);
    }
  };

  const resetToBaseline = async () => {
    if (!props.groupId) return;
    const ok = window.confirm(t("automation.resetConfirm"));
    if (!ok) return;
    setRulesBusy(true);
    setRulesErr("");
    try {
      const resp = await api.resetAutomationBaseline(props.groupId, rulesVersion);
      if (!resp.ok) {
        const code = String(resp.error?.code || "").trim();
          if (code === "version_conflict") {
            await loadRules();
            setRulesErr(t("automation.versionConflictShort"));
            return;
          }
        setRulesErr(resp.error?.message || t("automation.failedToReset"));
        return;
      }
      await loadRules();
    } catch {
      setRulesErr(t("automation.failedToReset"));
    } finally {
      setRulesBusy(false);
    }
  };

  useEffect(() => {
    if (!editingRuleId) return;
    const rule = draft.rules.find((r) => String(r.id || "").trim() === editingRuleId);
    if (!rule) return;
    const kind = actionKind(rule.action);
    const triggerKind = String(rule.trigger?.kind || "interval");
    if (kind === "notify" || triggerKind === "at") return;
    const defaultAt = new Date(Date.now() + 30 * 60 * 1000).toISOString();
    setOneShotModeByRule((prev) => ({ ...prev, [editingRuleId]: "after" }));
    setOneShotAfterMinutesByRule((prev) => ({ ...prev, [editingRuleId]: prev[editingRuleId] ?? 30 }));
    updateRuleNested(editingRuleId, { trigger: { kind: "at", at: defaultAt } });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- normalize edited rule only
  }, [editingRuleId, draft.rules]);

  if (!props.groupId) {
    return (
      <div className={cardClass()}>
        <div className="text-sm text-[var(--color-text-secondary)]">{t("automation.openFromGroup")}</div>
      </div>
    );
  }

  const editingRule = editingRuleId ? draft.rules.find((rule) => String(rule.id || "").trim() === editingRuleId) || null : null;
  const editingRuleStatus = editingRule ? status[String(editingRule.id || "").trim()] || {} : {};

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div>
        <h3 className="text-sm font-medium text-[var(--color-text-secondary)]">{t("automation.title")}</h3>
        <p className="text-xs mt-1 text-[var(--color-text-muted)]">
          {t("automation.description")}{" "}
          <span className="font-mono break-all">{configPath || t("automation.configPathFallback")}</span>.
        </p>
      </div>

      <Section
        isDark={isDark}
        icon={SparkIcon}
        title={t("automation.rulesTitle")}
        description={t("automation.rulesDescription")}
      >
        {rulesErr ? <div className="text-xs text-rose-600 dark:text-rose-300">{rulesErr}</div> : null}

        <div className="space-y-2">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
              <button
                type="button"
                className="glass-btn w-full sm:w-auto whitespace-nowrap px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors text-[var(--color-text-primary)] disabled:opacity-50"
                onClick={() => {
                  const rid = addRule();
                  setEditingRuleId(rid);
                  setRulesErr("");
                }}
                disabled={rulesBusy}
                title={t("automation.createRuleTitle")}
              >
                {t("automation.newRule")}
              </button>
              <button
                type="button"
                className="glass-btn w-full sm:w-auto whitespace-nowrap px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors text-[var(--color-text-primary)] disabled:opacity-50"
                onClick={openSnippetManager}
                disabled={rulesBusy}
                title={t("automation.manageSnippetsTitle")}
              >
                {t("automation.snippets")}
              </button>
            </div>

            <div className="grid grid-cols-2 gap-2 sm:flex sm:items-center">
              <button
                type="button"
                className="w-full sm:w-auto whitespace-nowrap px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors bg-rose-500/15 hover:bg-rose-500/25 text-rose-700 dark:text-rose-300 border border-rose-500/30 disabled:opacity-50"
                onClick={resetToBaseline}
                disabled={rulesBusy}
                title={t("automation.resetTitle")}
              >
                {t("automation.resetToDefaults")}
              </button>
              <button
                type="button"
                className={`${primaryButtonClass(rulesBusy)} w-full sm:w-auto whitespace-nowrap`}
                onClick={saveRules}
                disabled={rulesBusy}
                title={t("automation.saveTitle")}
              >
                {rulesBusy ? t("automation.saving") : t("common:save")}
              </button>
            </div>
          </div>
        </div>

        <AutomationRuleList
          isDark={isDark}
          visibleRules={visibleRules}
          status={status}
          rulesBusy={rulesBusy}
          showCompletedRules={showCompletedRules}
          completedOneTimeRuleIds={completedOneTimeRuleIds}
          onToggleShowCompleted={setShowCompletedRules}
          onClearCompleted={clearCompletedRules}
          onToggleRuleEnabled={(ruleId, enabled) => updateRuleNested(ruleId, { enabled })}
          onEditRule={(ruleId) => {
            setEditingRuleId(ruleId);
            setRulesErr("");
          }}
          onDeleteRule={removeRule}
        />

        <div className="mt-2 text-[11px] text-[var(--color-text-muted)]">
          {t("automation.editHint")}
        </div>
      </Section>

      <AutomationRuleEditorModal
        isDark={isDark}
        editingRule={editingRule}
        editingRuleStatus={editingRuleStatus}
        rulesErr={rulesErr}
        snippetIds={snippetIds}
        actorTargetOptions={actorTargetOptions}
        oneShotModeByRule={oneShotModeByRule}
        oneShotAfterMinutesByRule={oneShotAfterMinutesByRule}
        onRulePatch={updateRuleNested}
        onRuleRemove={removeRule}
        onSetEditingRuleId={setEditingRuleId}
        onSetRulesErr={setRulesErr}
        onSetOneShotMode={(ruleId, mode) => setOneShotModeByRule((prev) => ({ ...prev, [ruleId]: mode }))}
        onSetOneShotAfterMinutes={setOneShotAfterMinutes}
      />

      <AutomationPoliciesSection
        isDark={isDark}
        busy={props.busy}
        nudgeSeconds={props.nudgeSeconds}
        setNudgeSeconds={props.setNudgeSeconds}
        replyRequiredNudgeSeconds={props.replyRequiredNudgeSeconds}
        setReplyRequiredNudgeSeconds={props.setReplyRequiredNudgeSeconds}
        attentionAckNudgeSeconds={props.attentionAckNudgeSeconds}
        setAttentionAckNudgeSeconds={props.setAttentionAckNudgeSeconds}
        unreadNudgeSeconds={props.unreadNudgeSeconds}
        setUnreadNudgeSeconds={props.setUnreadNudgeSeconds}
        nudgeDigestMinIntervalSeconds={props.nudgeDigestMinIntervalSeconds}
        setNudgeDigestMinIntervalSeconds={props.setNudgeDigestMinIntervalSeconds}
        nudgeMaxRepeatsPerObligation={props.nudgeMaxRepeatsPerObligation}
        setNudgeMaxRepeatsPerObligation={props.setNudgeMaxRepeatsPerObligation}
        nudgeEscalateAfterRepeats={props.nudgeEscalateAfterRepeats}
        setNudgeEscalateAfterRepeats={props.setNudgeEscalateAfterRepeats}
        keepaliveSeconds={props.keepaliveSeconds}
        setKeepaliveSeconds={props.setKeepaliveSeconds}
        keepaliveMax={props.keepaliveMax}
        setKeepaliveMax={props.setKeepaliveMax}
        helpNudgeIntervalSeconds={props.helpNudgeIntervalSeconds}
        setHelpNudgeIntervalSeconds={props.setHelpNudgeIntervalSeconds}
        helpNudgeMinMessages={props.helpNudgeMinMessages}
        setHelpNudgeMinMessages={props.setHelpNudgeMinMessages}
        idleSeconds={props.idleSeconds}
        setIdleSeconds={props.setIdleSeconds}
        silenceSeconds={props.silenceSeconds}
        setSilenceSeconds={props.setSilenceSeconds}
        onSavePolicies={props.onSavePolicies}
      />

      <AutomationSnippetModal
        open={snippetManagerOpen}
        isDark={isDark}
        templateErr={templateErr}
        newSnippetId={newSnippetId}
        supportedVars={supportedVars}
        snippetIds={snippetIds}
        snippets={draft.snippets || {}}
        onClose={closeSnippetManager}
        onNewSnippetIdChange={setNewSnippetId}
        onAddSnippet={addSnippet}
        onDeleteSnippet={deleteSnippet}
        onUpdateSnippet={updateSnippet}
      />
    </div>
  );
}
