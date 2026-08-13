import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { NumberInputRow } from "./automationUtils";

export interface AutomationPoliciesSectionProps {
  isDark: boolean;
  nudgeSeconds: number;
  setNudgeSeconds: (value: number) => void;
  replyRequiredNudgeSeconds: number;
  setReplyRequiredNudgeSeconds: (value: number) => void;
  attentionAckNudgeSeconds: number;
  setAttentionAckNudgeSeconds: (value: number) => void;
  unreadNudgeSeconds: number;
  setUnreadNudgeSeconds: (value: number) => void;
  nudgeDigestMinIntervalSeconds: number;
  setNudgeDigestMinIntervalSeconds: (value: number) => void;
  nudgeMaxRepeatsPerObligation: number;
  setNudgeMaxRepeatsPerObligation: (value: number) => void;
  nudgeEscalateAfterRepeats: number;
  setNudgeEscalateAfterRepeats: (value: number) => void;
  keepaliveSeconds: number;
  setKeepaliveSeconds: (value: number) => void;
  keepaliveMax: number;
  setKeepaliveMax: (value: number) => void;
  helpNudgeIntervalSeconds: number;
  setHelpNudgeIntervalSeconds: (value: number) => void;
  helpNudgeMinMessages: number;
  setHelpNudgeMinMessages: (value: number) => void;
  idleSeconds: number;
  setIdleSeconds: (value: number) => void;
  silenceSeconds: number;
  setSilenceSeconds: (value: number) => void;
}

export function PolicyGroup({
  title,
  description,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`space-y-3 rounded-lg border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] p-3.5 ${className}`}
    >
      <div>
        <h3 className="text-xs font-semibold text-[var(--color-text-secondary)]">{title}</h3>
        {description ? (
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--color-text-muted)]">
            {description}
          </p>
        ) : null}
      </div>
      {children}
    </section>
  );
}

export function AutomationPoliciesSection(props: AutomationPoliciesSectionProps) {
  const { t } = useTranslation("settings");

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <PolicyGroup
        title={t("policies.messageFollowups")}
        description={t("policies.messageFollowupsHelp")}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
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

      <PolicyGroup
        title={t("policies.progressFollowups")}
        description={t("policies.progressFollowupsHelp")}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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

      <PolicyGroup
        title={t("policies.contextRefresh")}
        description={t("policies.contextRefreshHelp")}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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

      <PolicyGroup
        title={t("policies.repeatEscalation")}
        description={t("policies.repeatEscalationHelp")}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
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

      <PolicyGroup
        title={t("policies.advancedForemanAlerts")}
        description={t("policies.advancedForemanAlertsHelp")}
        className="lg:col-span-2"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
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
    </div>
  );
}
