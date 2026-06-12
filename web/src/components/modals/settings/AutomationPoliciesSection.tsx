import React from "react";
import { useTranslation } from "react-i18next";

import { BellIcon, NumberInputRow, Section } from "./automationUtils";
import { primaryButtonClass, secondaryButtonClass } from "./types";

interface AutomationPoliciesSectionProps {
  isDark: boolean;
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
  keepaliveSeconds: number;
  setKeepaliveSeconds: (v: number) => void;
  keepaliveMax: number;
  setKeepaliveMax: (v: number) => void;
  helpNudgeIntervalSeconds: number;
  setHelpNudgeIntervalSeconds: (v: number) => void;
  helpNudgeMinMessages: number;
  setHelpNudgeMinMessages: (v: number) => void;
  taskReminderEnabled: boolean;
  setTaskReminderEnabled: (v: boolean) => void;
  taskEmptyCooldownSeconds: number;
  setTaskEmptyCooldownSeconds: (v: number) => void;
  taskActiveOverdueMilestonesSeconds: number[];
  setTaskActiveOverdueMilestonesSeconds: (v: number[]) => void;
  taskPlannedUnassignedMilestonesSeconds: number[];
  setTaskPlannedUnassignedMilestonesSeconds: (v: number[]) => void;
  idleSeconds: number;
  setIdleSeconds: (v: number) => void;
  silenceSeconds: number;
  setSilenceSeconds: (v: number) => void;
  onSavePolicies: () => void;
  onResetPolicies: () => void;
}

function PolicyGroup({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-3.5 space-y-3">
      <div>
        <div className="text-xs font-semibold tracking-[0.02em] text-[var(--color-text-secondary)]">{title}</div>
        {description ? (
          <div className="mt-1 text-[11px] leading-snug text-[var(--color-text-muted)]">{description}</div>
        ) : null}
      </div>
      {children}
    </div>
  );
}

export function AutomationPoliciesSection(props: AutomationPoliciesSectionProps) {
  const { t } = useTranslation("settings");
  const setActiveMilestone = (index: number, value: number) => {
    const next = [...props.taskActiveOverdueMilestonesSeconds];
    next[index] = value;
    props.setTaskActiveOverdueMilestonesSeconds(next);
  };
  const setPlannedMilestone = (index: number, value: number) => {
    const next = [...props.taskPlannedUnassignedMilestonesSeconds];
    next[index] = value;
    props.setTaskPlannedUnassignedMilestonesSeconds(next);
  };
  return (
    <Section
      isDark={props.isDark}
      icon={BellIcon}
      title={t("policies.title")}
      description={t("policies.description")}
    >
      <PolicyGroup title={t("policies.messageFollowups")} description={t("policies.messageFollowupsHelp")}>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.needReplyFollowup")}
            value={props.replyRequiredNudgeSeconds}
            onChange={props.setReplyRequiredNudgeSeconds}
            helperText={t("policies.needReplyFollowupHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.importantFollowup")}
            value={props.attentionAckNudgeSeconds}
            onChange={props.setAttentionAckNudgeSeconds}
            helperText={t("policies.importantFollowupHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.backlogDigest")}
            value={props.unreadNudgeSeconds}
            onChange={props.setUnreadNudgeSeconds}
            helperText={t("policies.backlogDigestHelp")}
          />
        </div>
      </PolicyGroup>

      <PolicyGroup title={t("policies.progressFollowups")} description={t("policies.progressFollowupsHelp")}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.keepaliveDelay")}
            value={props.keepaliveSeconds}
            onChange={props.setKeepaliveSeconds}
            helperText={t("policies.keepaliveDelayHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.keepaliveMaxRetries")}
            value={props.keepaliveMax}
            onChange={props.setKeepaliveMax}
            formatValue={false}
            helperText={
              props.keepaliveMax <= 0
                ? t("policies.keepaliveOff")
                : t("policies.keepaliveRetryUp", { count: props.keepaliveMax })
            }
          />
        </div>
      </PolicyGroup>

      <PolicyGroup title={t("policies.contextRefresh")} description={t("policies.contextRefreshHelp")}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.helpRefreshInterval")}
            value={props.helpNudgeIntervalSeconds}
            onChange={props.setHelpNudgeIntervalSeconds}
            helperText={t("policies.helpRefreshIntervalHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.helpRefreshMinMsgs")}
            value={props.helpNudgeMinMessages}
            onChange={props.setHelpNudgeMinMessages}
            formatValue={false}
            helperText={t("policies.helpRefreshMinMsgsHelp")}
          />
        </div>
      </PolicyGroup>

      <PolicyGroup title={t("policies.taskRuntimeAlerts")} description={t("policies.taskRuntimeAlertsHelp")}>
        <label className="flex items-start gap-3 rounded-lg border border-[var(--glass-border-subtle)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
          <input
            type="checkbox"
            className="mt-0.5 h-4 w-4 rounded border-[var(--glass-border)]"
            checked={props.taskReminderEnabled}
            onChange={(event) => props.setTaskReminderEnabled(event.currentTarget.checked)}
          />
          <span>
            <span className="block font-medium text-[var(--color-text-primary)]">{t("policies.taskRuntimeAlertsEnabled")}</span>
            <span className="mt-0.5 block text-[11px] leading-snug text-[var(--color-text-muted)]">
              {t("policies.taskRuntimeAlertsEnabledHelp")}
            </span>
          </span>
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.taskEmptyCooldown")}
            value={props.taskEmptyCooldownSeconds}
            onChange={props.setTaskEmptyCooldownSeconds}
            helperText={t("policies.taskEmptyCooldownHelp")}
          />
          {props.taskActiveOverdueMilestonesSeconds.map((value, index) => (
            <NumberInputRow
              key={`task-active-${index}`}
              isDark={props.isDark}
              label={t("policies.taskActiveMilestone", { index: index + 1 })}
              value={value}
              onChange={(next) => setActiveMilestone(index, next)}
              helperText={t("policies.taskActiveMilestoneHelp")}
            />
          ))}
        </div>
      </PolicyGroup>

      <PolicyGroup title={t("policies.taskPlannedAlerts")} description={t("policies.taskPlannedAlertsHelp")}>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {props.taskPlannedUnassignedMilestonesSeconds.map((value, index) => (
            <NumberInputRow
              key={`task-planned-${index}`}
              isDark={props.isDark}
              label={t("policies.taskPlannedMilestone", { index: index + 1 })}
              value={value}
              onChange={(next) => setPlannedMilestone(index, next)}
              helperText={t("policies.taskPlannedMilestoneHelp")}
            />
          ))}
        </div>
      </PolicyGroup>

      <PolicyGroup title={t("policies.repeatEscalation")} description={t("policies.repeatEscalationHelp")}>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.digestMinGap")}
            value={props.nudgeDigestMinIntervalSeconds}
            onChange={props.setNudgeDigestMinIntervalSeconds}
            helperText={t("policies.digestMinGapHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.maxRepeats")}
            value={props.nudgeMaxRepeatsPerObligation}
            onChange={props.setNudgeMaxRepeatsPerObligation}
            formatValue={false}
            helperText={t("policies.maxRepeatsHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.escalateAfter")}
            value={props.nudgeEscalateAfterRepeats}
            onChange={props.setNudgeEscalateAfterRepeats}
            formatValue={false}
            helperText={t("policies.escalateAfterHelp")}
          />
        </div>
      </PolicyGroup>

      <PolicyGroup title={t("policies.advancedForemanAlerts")} description={t("policies.advancedForemanAlertsHelp")}>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.fallbackFollowup")}
            value={props.nudgeSeconds}
            onChange={props.setNudgeSeconds}
            helperText={t("policies.fallbackFollowupHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.actorIdleAlert")}
            value={props.idleSeconds}
            onChange={props.setIdleSeconds}
            helperText={t("policies.actorIdleAlertHelp")}
          />
          <NumberInputRow
            isDark={props.isDark}
            label={t("policies.groupSilenceCheck")}
            value={props.silenceSeconds}
            onChange={props.setSilenceSeconds}
            helperText={t("policies.groupSilenceCheckHelp")}
          />
        </div>
      </PolicyGroup>

      <div className="pt-2 flex flex-col sm:flex-row sm:items-center sm:justify-end gap-2">
        <button
          type="button"
          onClick={props.onResetPolicies}
          disabled={props.busy}
          className={`${secondaryButtonClass("md")} w-full sm:w-auto whitespace-nowrap`}
          title={t("policies.resetPoliciesTitle")}
        >
          {t("policies.resetPolicies")}
        </button>
        <button
          type="button"
          onClick={props.onSavePolicies}
          disabled={props.busy}
          className={`${primaryButtonClass(props.busy)} w-full sm:w-auto whitespace-nowrap`}
          title={t("policies.savePoliciesTitle")}
        >
          {props.busy ? t("automation.saving") : t("policies.savePolicies")}
        </button>
      </div>
    </Section>
  );
}
