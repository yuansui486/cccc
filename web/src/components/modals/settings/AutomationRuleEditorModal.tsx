import React from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import type { AutomationRule, AutomationRuleStatus } from "../../../types";
import { getRecipientDisplayLabel } from "../../../utils/displayText";
import {
  Chip,
  buildCronFromPreset,
  clampInt,
  defaultActorControlAction,
  defaultGroupStateAction,
  defaultNotifyAction,
  formatDuration,
  formatTimeInput,
  getActorOperationCopy,
  getGroupStateCopy,
  getWeekdayOptions,
  isoToLocalDatetimeInput,
  localDatetimeInputToIso,
  localTimeZone,
  parseCronToPreset,
  parseTimeInput,
} from "./automationUtils";
import type { SchedulePreset } from "./automationUtils";
import {
  inputClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
  settingsDialogBodyClass,
  settingsDialogFooterClass,
  settingsDialogHeaderClass,
  settingsDialogPanelClass,
} from "./types";

interface AutomationRuleEditorModalProps {
  isDark: boolean;
  ruleDraft: AutomationRule | null;
  ruleStatus: AutomationRuleStatus;
  isNewRule: boolean;
  errorMessage: string;
  saveBusy: boolean;
  snippetIds: string[];
  actorTargetOptions: Array<{ value: string; label: string }>;
  oneShotMode: "after" | "exact";
  oneShotAfterMinutes: number;
  onRuleChange: (next: AutomationRule | null) => void;
  onClose: () => void;
  onSetRulesErr: (message: string) => void;
  onSetOneShotMode: (mode: "after" | "exact") => void;
  onSetOneShotAfterMinutes: (minutes: number) => void;
  onSave: () => void | Promise<void>;
}

export function AutomationRuleEditorModal(props: AutomationRuleEditorModalProps) {
  const {
    isDark,
    ruleDraft,
    ruleStatus,
    isNewRule,
    errorMessage,
    saveBusy,
    snippetIds,
    actorTargetOptions,
    oneShotMode,
    oneShotAfterMinutes,
    onRuleChange,
    onClose,
    onSetRulesErr,
    onSetOneShotMode,
    onSetOneShotAfterMinutes,
    onSave,
  } = props;

  const { t } = useTranslation("settings");
  const actorOperationCopy = getActorOperationCopy(t);
  const groupStateCopy = getGroupStateCopy(t);
  const weekdayOptions = getWeekdayOptions(t);

  if (!ruleDraft) return null;

  const patchRule = (patch: Partial<AutomationRule>) => {
    onRuleChange({ ...ruleDraft, ...patch });
  };

  const ruleId = String(ruleDraft.id || "").trim();
  const status = ruleStatus || {};
  const recipients = Array.isArray(ruleDraft.to) ? ruleDraft.to.map((item) => String(item || "").trim()).filter(Boolean) : [];
  const triggerKind = String(ruleDraft.trigger?.kind || "interval");
  const everySeconds = clampInt(
    Number(triggerKind === "interval" && ruleDraft.trigger && "every_seconds" in ruleDraft.trigger ? ruleDraft.trigger.every_seconds : 0),
    1,
    365 * 24 * 3600
  );
  const cronExpr = String(triggerKind === "cron" && ruleDraft.trigger && "cron" in ruleDraft.trigger ? ruleDraft.trigger.cron : "").trim();
  const atRaw = String(triggerKind === "at" && ruleDraft.trigger && "at" in ruleDraft.trigger ? ruleDraft.trigger.at : "").trim();
  const kind = String(ruleDraft.action?.kind || "notify").trim() as "notify" | "group_state" | "actor_control";
  const scheduleLockedToOneTime = kind !== "notify";
  const scheduleSelectValue = scheduleLockedToOneTime ? "at" : triggerKind;
  const activeTriggerKind = scheduleLockedToOneTime ? "at" : triggerKind;
  const operationalActionsEnabled = activeTriggerKind === "at";
  const snippetRef = String(kind === "notify" && ruleDraft.action && "snippet_ref" in ruleDraft.action ? ruleDraft.action.snippet_ref || "" : "").trim();
  const message = String(kind === "notify" && ruleDraft.action && "message" in ruleDraft.action ? ruleDraft.action.message || "" : "");
  const contentMode: "snippet" | "custom" = snippetRef ? "snippet" : "custom";
  const groupStateValue = String(
    kind === "group_state" && ruleDraft.action && "state" in ruleDraft.action ? ruleDraft.action.state || "paused" : "paused"
  );
  const actorOperation = String(
    kind === "actor_control" && ruleDraft.action && "operation" in ruleDraft.action ? ruleDraft.action.operation || "restart" : "restart"
  );
  const actorTargets = Array.isArray(kind === "actor_control" && ruleDraft.action && "targets" in ruleDraft.action ? ruleDraft.action.targets : [])
    ? (ruleDraft.action as { targets?: string[] }).targets?.map((item) => String(item || "").trim()).filter(Boolean) || []
    : [];
  const notifyAction =
    kind === "notify" && ruleDraft.action && ruleDraft.action.kind === "notify" ? ruleDraft.action : defaultNotifyAction();
  const enabled = ruleDraft.enabled !== false;
  const scope = String(ruleDraft.scope || "group") === "personal" ? "personal" : "group";
  const ownerActorId = String(ruleDraft.owner_actor_id || "").trim();
  const localTz = localTimeZone();
  const schedule = parseCronToPreset(cronExpr);
  const scheduleTime = formatTimeInput(schedule.hour, schedule.minute);
  const atInput = isoToLocalDatetimeInput(atRaw);

  const title = isNewRule ? t("automation.newRule") : t("ruleEditor.editRule");

  const content = (
    <div className="fixed inset-0 z-[1000]" role="dialog" aria-modal="true">
      <div className="absolute inset-0 glass-overlay" onPointerDown={onClose} />
      <div className={settingsDialogPanelClass("xl")}>
        <div className={settingsDialogHeaderClass}>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">
              {title} <span className="font-mono">{ruleId || t("ruleEditor.unnamed")}</span>
            </div>
            {!isNewRule ? (
              <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
                {t("ruleEditor.last")} {status.last_fired_at || "—"} • {t("ruleEditor.next")} {status.next_fire_at || "—"}{" "}
                {status.completed ? `• ${t("ruleEditor.completed")} ${status.completed_at || status.last_fired_at || "—"}` : ""}{" "}
                {status.last_error ? `• ${t("ruleEditor.error")} ${status.last_error_at || "—"}` : ""}
              </div>
            ) : null}
          </div>

          <div className="ml-auto flex items-center gap-2 flex-wrap justify-end">
            <label className="text-xs text-[var(--color-text-tertiary)] flex items-center gap-2">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => patchRule({ enabled: e.target.checked })}
              />
              {t("ruleList.on")}
            </label>
            <button type="button" className={secondaryButtonClass("sm")} onClick={onClose}>
              {t("common:close")}
            </button>
          </div>
        </div>

        {errorMessage ? <div className="px-4 pt-3 text-xs text-rose-600 dark:text-rose-300">{errorMessage}</div> : null}
        {status.last_error && !isNewRule ? <div className="px-4 pt-1 text-xs text-rose-600 dark:text-rose-300">{status.last_error}</div> : null}

        <div className={settingsDialogBodyClass}>
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.ruleName")}</label>
                <input
                  value={ruleId}
                  onChange={(e) => patchRule({ id: e.target.value })}
                  className={`${inputClass(isDark)} font-mono`}
                  placeholder="daily_checkin"
                  spellCheck={false}
                />
                <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                  {t("ruleEditor.ruleNameHint")}
                </div>
              </div>
              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.scheduleType")}</label>
                <select
                  value={scheduleSelectValue}
                  disabled={scheduleLockedToOneTime}
                  onChange={(e) => {
                    const nextKind = String(e.target.value || "interval");
                    if (nextKind === "cron") {
                      const nextCron = buildCronFromPreset({
                        preset: schedule.preset,
                        hour: schedule.hour,
                        minute: schedule.minute,
                        weekday: schedule.weekday,
                        dayOfMonth: schedule.dayOfMonth,
                      });
                      patchRule({
                        trigger: {
                          kind: "cron",
                          cron: cronExpr || nextCron,
                          timezone: localTz,
                        },
                      });
                      return;
                    }
                    if (nextKind === "at") {
                      onSetOneShotMode("after");
                      patchRule({ trigger: { kind: "at", at: atRaw || new Date(Date.now() + 30 * 60 * 1000).toISOString() } });
                      return;
                    }
                    patchRule({ trigger: { kind: "interval", every_seconds: everySeconds } });
                  }}
                  className={inputClass(isDark)}
                >
                  {kind === "notify" ? <option value="interval">{t("ruleEditor.intervalSchedule")}</option> : null}
                  {kind === "notify" ? <option value="cron">{t("ruleEditor.recurringSchedule")}</option> : null}
                  <option value="at">{t("ruleEditor.oneTimeSchedule")}</option>
                </select>
                <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                  {scheduleLockedToOneTime
                    ? t("ruleEditor.oneTimeOnly")
                    : activeTriggerKind === "interval"
                      ? t("ruleEditor.intervalHint")
                      : activeTriggerKind === "cron"
                        ? t("ruleEditor.recurringHint")
                        : t("ruleEditor.oneTimeHint")}
                </div>
              </div>
            </div>

          {scope === "personal" ? (
            <div className="text-[11px] text-amber-700 dark:text-amber-300">
              {t("ruleEditor.personalRule", { owner: ownerActorId || "unknown" })}
            </div>
          ) : null}

          {activeTriggerKind === "interval" ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.repeatEvery")}</label>
                <input
                  type="number"
                  min={1}
                  value={Math.max(1, Math.round(everySeconds / 60))}
                  onChange={(e) =>
                    patchRule({
                      trigger: {
                        kind: "interval",
                        every_seconds: Math.max(1, Number(e.target.value || 1)) * 60,
                      },
                    })
                  }
                  className={inputClass(isDark)}
                />
              </div>
              <div className="self-end text-[11px] text-[var(--color-text-muted)]">
                {t("ruleEditor.currentCadence", { duration: formatDuration(everySeconds, t) })}
              </div>
            </div>
          ) : null}

          {activeTriggerKind === "cron" ? (
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className={labelClass(isDark)}>{t("ruleEditor.pattern")}</label>
                  <select
                    value={schedule.preset}
                    onChange={(e) => {
                      const preset = String(e.target.value || "daily") as SchedulePreset;
                      patchRule({
                        trigger: {
                          kind: "cron",
                          cron: buildCronFromPreset({
                            preset,
                            hour: schedule.hour,
                            minute: schedule.minute,
                            weekday: schedule.weekday,
                            dayOfMonth: schedule.dayOfMonth,
                          }),
                          timezone: localTz,
                        },
                      });
                    }}
                    className={inputClass(isDark)}
                  >
                    <option value="daily">{t("ruleEditor.daily")}</option>
                    <option value="weekly">{t("ruleEditor.weekly")}</option>
                    <option value="monthly">{t("ruleEditor.monthly")}</option>
                  </select>
                </div>
                <div>
                  <label className={labelClass(isDark)}>{t("ruleEditor.time")}</label>
                  <input
                    type="time"
                    value={scheduleTime}
                    onChange={(e) => {
                      const parsed = parseTimeInput(e.target.value);
                      patchRule({
                        trigger: {
                          kind: "cron",
                          cron: buildCronFromPreset({
                            preset: schedule.preset,
                            hour: parsed.hour,
                            minute: parsed.minute,
                            weekday: schedule.weekday,
                            dayOfMonth: schedule.dayOfMonth,
                          }),
                          timezone: localTz,
                        },
                      });
                    }}
                    className={inputClass(isDark)}
                  />
                </div>
              </div>

              {schedule.preset === "weekly" ? (
                <div>
                  <label className={labelClass(isDark)}>{t("ruleEditor.weekday")}</label>
                  <select
                    value={String(schedule.weekday)}
                    onChange={(e) =>
                      patchRule({
                        trigger: {
                          kind: "cron",
                          cron: buildCronFromPreset({
                            preset: "weekly",
                            hour: schedule.hour,
                            minute: schedule.minute,
                            weekday: Number(e.target.value || 1),
                            dayOfMonth: schedule.dayOfMonth,
                          }),
                          timezone: localTz,
                        },
                      })
                    }
                    className={inputClass(isDark)}
                  >
                    {weekdayOptions.map((day) => (
                      <option key={day.value} value={String(day.value)}>
                        {day.label}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}

              {schedule.preset === "monthly" ? (
                <div>
                  <label className={labelClass(isDark)}>{t("ruleEditor.dayOfMonth")}</label>
                  <input
                    type="number"
                    min={1}
                    max={31}
                    value={schedule.dayOfMonth}
                    onChange={(e) =>
                      patchRule({
                        trigger: {
                          kind: "cron",
                          cron: buildCronFromPreset({
                            preset: "monthly",
                            hour: schedule.hour,
                            minute: schedule.minute,
                            weekday: schedule.weekday,
                            dayOfMonth: Number(e.target.value || 1),
                          }),
                          timezone: localTz,
                        },
                      })
                    }
                    className={inputClass(isDark)}
                  />
                </div>
              ) : null}
            </div>
          ) : null}

          {activeTriggerKind === "at" ? (
            <div className="space-y-3">
              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.oneTimeMode")}</label>
                <select
                  value={oneShotMode}
                  onChange={(e) => onSetOneShotMode(String(e.target.value || "after") as "after" | "exact")}
                  className={inputClass(isDark)}
                >
                  <option value="after">{t("ruleEditor.afterCountdown")}</option>
                  <option value="exact">{t("ruleEditor.exactTime")}</option>
                </select>
              </div>

              {oneShotMode === "after" ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap gap-2">
                    {[5, 10, 30, 60, 120].map((minutes) => (
                      <button
                        key={minutes}
                        type="button"
                        className={secondaryButtonClass("sm")}
                        onClick={() => onSetOneShotAfterMinutes(minutes)}
                      >
                        {minutes >= 60 ? `${Math.round(minutes / 60)}h` : `${minutes}m`}
                      </button>
                    ))}
                  </div>
                  <div>
                    <label className={labelClass(isDark)}>{t("ruleEditor.remindMeIn")}</label>
                    <input
                      type="number"
                      min={1}
                      max={10080}
                      value={oneShotAfterMinutes}
                      onChange={(e) => onSetOneShotAfterMinutes(clampInt(Number(e.target.value || 1), 1, 7 * 24 * 60))}
                      className={inputClass(isDark)}
                    />
                  </div>
                </div>
              ) : (
                <div>
                  <label className={labelClass(isDark)}>{t("ruleEditor.dateTime")}</label>
                  <input
                    type="datetime-local"
                    value={atInput}
                    onChange={(e) => patchRule({ trigger: { kind: "at", at: localDatetimeInputToIso(e.target.value) } })}
                    className={inputClass(isDark)}
                  />
                </div>
              )}

              <div className="text-[11px] text-[var(--color-text-muted)]">
                {t("automation.savedSendTime")} <span className="font-mono break-all">{atRaw || "—"}</span>
              </div>
            </div>
          ) : null}

          <div>
            <label className={labelClass(isDark)}>{t("ruleEditor.action")}</label>
            <select
              value={kind}
              onChange={(e) => {
                const next = String(e.target.value || "notify");
                if (next !== "notify" && !operationalActionsEnabled) {
                  onSetRulesErr(t("automation.operationalActionsHint"));
                  return;
                }
                onSetRulesErr("");
                if (next === "group_state") {
                  onSetOneShotMode("after");
                  patchRule({
                    action: defaultGroupStateAction(),
                    trigger: { kind: "at", at: atRaw || new Date(Date.now() + 30 * 60 * 1000).toISOString() },
                  });
                  return;
                }
                if (next === "actor_control") {
                  onSetOneShotMode("after");
                  patchRule({
                    action: defaultActorControlAction(),
                    trigger: { kind: "at", at: atRaw || new Date(Date.now() + 30 * 60 * 1000).toISOString() },
                  });
                  return;
                }
                patchRule({
                  action: defaultNotifyAction(),
                  to: recipients.length > 0 ? recipients : ["@foreman"],
                });
              }}
              className={inputClass(isDark)}
            >
              <option value="notify">{t("ruleEditor.sendReminder")}</option>
              <option value="group_state" disabled={!operationalActionsEnabled}>
                {t("ruleEditor.setGroupStatus")}
                {operationalActionsEnabled ? "" : t("automation.oneTimeOnlySuffix")}
              </option>
              <option value="actor_control" disabled={!operationalActionsEnabled}>
                {t("ruleEditor.controlActorRuntimes")}
                {operationalActionsEnabled ? "" : t("automation.oneTimeOnlySuffix")}
              </option>
            </select>
            {!operationalActionsEnabled ? (
              <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">{t("automation.operationalActionsOnly")}</div>
            ) : null}
          </div>

          {kind === "notify" ? (
            <>
              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.sendTo")}</label>
                <div className="flex flex-wrap gap-2">
                  {recipients.map((token) => (
                    <Chip
                      key={token}
                      label={getRecipientDisplayLabel(token)}
                      isDark={isDark}
                      onRemove={() => patchRule({ to: recipients.filter((item) => item !== token) })}
                    />
                  ))}
                  <select
                    value=""
                    onChange={(e) => {
                      const value = String(e.target.value || "").trim();
                      if (!value || recipients.includes(value)) return;
                      patchRule({ to: [...recipients, value] });
                    }}
                    className="glass-input px-3 py-2 rounded-lg text-sm min-h-[44px] text-[var(--color-text-primary)]"
                  >
                    <option value="">{t("automation.addRecipient")}</option>
                    {actorTargetOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.notificationSource")}</label>
                <select
                  value={contentMode}
                  onChange={(e) => {
                    const nextMode = String(e.target.value || "custom");
                    if (nextMode === "snippet") {
                      if (snippetIds.length === 0) {
                        onSetRulesErr(t("automation.createSnippetFirst"));
                        return;
                      }
                      onSetRulesErr("");
                      patchRule({ action: { ...notifyAction, snippet_ref: snippetRef || snippetIds[0] || null } });
                      return;
                    }
                    onSetRulesErr("");
                    patchRule({ action: { ...notifyAction, snippet_ref: null } });
                  }}
                  className={inputClass(isDark)}
                >
                  <option value="snippet">{t("ruleEditor.messageSnippet")}</option>
                  <option value="custom">{t("ruleEditor.typeText")}</option>
                </select>
              </div>

              {contentMode === "snippet" ? (
                <div>
                  <label className={labelClass(isDark)}>{t("ruleEditor.selectSnippet")}</label>
                  <select
                    value={snippetRef}
                    onChange={(e) => patchRule({ action: { ...notifyAction, snippet_ref: e.target.value || null } })}
                    className={`${inputClass(isDark)} font-mono`}
                  >
                    <option value="">(select snippet)</option>
                    {snippetIds.map((snippetId) => (
                      <option key={snippetId} value={snippetId}>
                        {snippetId}
                      </option>
                    ))}
                  </select>
                  {snippetIds.length === 0 ? (
                    <div className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">{t("automation.noSnippetsYet")}</div>
                  ) : null}
                </div>
              ) : (
                <div>
                  <label className={labelClass(isDark)}>{t("ruleEditor.messageLabel")}</label>
                  <textarea
                    value={message}
                    onChange={(e) => patchRule({ action: { ...notifyAction, message: e.target.value } })}
                    className={`${inputClass(isDark)} font-mono text-[12px]`}
                    style={{ minHeight: 140 }}
                    placeholder={t("automation.messagePlaceholder")}
                    spellCheck={false}
                  />
                </div>
              )}
            </>
          ) : null}

          {kind === "group_state" ? (
            <div>
              <label className={labelClass(isDark)}>{t("ruleEditor.groupStatusTarget")}</label>
              <select
                value={groupStateValue}
                onChange={(e) =>
                  patchRule({
                    action: {
                      kind: "group_state",
                      state: String(e.target.value || "paused") as "active" | "idle" | "paused" | "stopped",
                    },
                  })
                }
                className={inputClass(isDark)}
              >
                <option value="active">{groupStateCopy.active.label}</option>
                <option value="idle">{groupStateCopy.idle.label}</option>
                <option value="paused">{groupStateCopy.paused.label}</option>
                <option value="stopped">{groupStateCopy.stopped.label}</option>
              </select>
              <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                {groupStateCopy[(groupStateValue as "active" | "idle" | "paused" | "stopped") || "paused"].hint}
              </div>
            </div>
          ) : null}

          {kind === "actor_control" ? (
            <div className="space-y-3">
              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.runtimeOperation")}</label>
                <select
                  value={actorOperation}
                  onChange={(e) =>
                    patchRule({
                      action: {
                        kind: "actor_control",
                        operation: String(e.target.value || "restart") as "start" | "stop" | "restart",
                        targets: actorTargets.length > 0 ? actorTargets : ["@all"],
                      },
                    })
                  }
                  className={inputClass(isDark)}
                >
                  <option value="start">{actorOperationCopy.start.label}</option>
                  <option value="stop">{actorOperationCopy.stop.label}</option>
                  <option value="restart">{actorOperationCopy.restart.label}</option>
                </select>
                <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                  {actorOperationCopy[(actorOperation as "start" | "stop" | "restart") || "restart"].hint}
                </div>
              </div>

              <div>
                <label className={labelClass(isDark)}>{t("ruleEditor.targetActors")}</label>
                <div className="flex flex-wrap gap-2">
                  {actorTargets.map((token) => (
                    <Chip
                      key={token}
                      label={getRecipientDisplayLabel(token)}
                      isDark={isDark}
                      onRemove={() =>
                        patchRule({
                          action: {
                            kind: "actor_control",
                            operation: actorOperation as "start" | "stop" | "restart",
                            targets: actorTargets.filter((item) => item !== token),
                          },
                        })
                      }
                    />
                  ))}
                  <select
                    value=""
                    onChange={(e) => {
                      const value = String(e.target.value || "").trim();
                      if (!value || actorTargets.includes(value)) return;
                      patchRule({
                        action: {
                          kind: "actor_control",
                          operation: actorOperation as "start" | "stop" | "restart",
                          targets: [...actorTargets, value],
                        },
                      });
                    }}
                    className="glass-input px-3 py-2 rounded-lg text-sm min-h-[44px] text-[var(--color-text-primary)]"
                  >
                    <option value="">{t("automation.addTarget")}</option>
                    {actorTargetOptions.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
            ) : null}
          </div>
        </div>
        <div className={settingsDialogFooterClass}>
          <button type="button" className={secondaryButtonClass()} onClick={onClose} disabled={saveBusy}>
            {t("common:cancel")}
          </button>
          <button type="button" className={primaryButtonClass(saveBusy)} onClick={() => void onSave()} disabled={saveBusy}>
            {saveBusy ? t("common:saving") : t("common:save")}
          </button>
        </div>
      </div>
    </div>
  );

  return typeof document !== "undefined" ? createPortal(content, document.body) : content;
}
