import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useModalA11y } from "../../../hooks/useModalA11y";
import { SettingsIcon } from "../../Icons";
import { BodyPortal } from "../../ui/BodyPortal";
import {
  AutomationPoliciesSection,
  type AutomationPoliciesSectionProps,
} from "./AutomationPoliciesSection";
import {
  primaryButtonClass,
  secondaryButtonClass,
  settingsDialogBodyClass,
  settingsDialogFooterClass,
  settingsDialogHeaderClass,
  settingsDialogPanelClass,
} from "./types";
import type { BuiltinAutomationPolicyValues } from "./useAutomationPolicyDraft";

interface BuiltinAutomationSettingsModalProps extends AutomationPoliciesSectionProps {
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onReset: () => void;
  onSave: () => Promise<boolean>;
}

function readValues(props: BuiltinAutomationSettingsModalProps): BuiltinAutomationPolicyValues {
  return {
    nudge_after_seconds: props.nudgeSeconds,
    reply_required_nudge_after_seconds: props.replyRequiredNudgeSeconds,
    attention_ack_nudge_after_seconds: props.attentionAckNudgeSeconds,
    unread_nudge_after_seconds: props.unreadNudgeSeconds,
    nudge_digest_min_interval_seconds: props.nudgeDigestMinIntervalSeconds,
    nudge_max_repeats_per_obligation: props.nudgeMaxRepeatsPerObligation,
    nudge_escalate_after_repeats: props.nudgeEscalateAfterRepeats,
    actor_idle_timeout_seconds: props.idleSeconds,
    keepalive_delay_seconds: props.keepaliveSeconds,
    keepalive_max_per_actor: props.keepaliveMax,
    silence_timeout_seconds: props.silenceSeconds,
    help_nudge_interval_seconds: props.helpNudgeIntervalSeconds,
    help_nudge_min_messages: props.helpNudgeMinMessages,
  };
}

function restoreValues(
  props: BuiltinAutomationSettingsModalProps,
  values: BuiltinAutomationPolicyValues,
) {
  props.setNudgeSeconds(values.nudge_after_seconds);
  props.setReplyRequiredNudgeSeconds(values.reply_required_nudge_after_seconds);
  props.setAttentionAckNudgeSeconds(values.attention_ack_nudge_after_seconds);
  props.setUnreadNudgeSeconds(values.unread_nudge_after_seconds);
  props.setNudgeDigestMinIntervalSeconds(values.nudge_digest_min_interval_seconds);
  props.setNudgeMaxRepeatsPerObligation(values.nudge_max_repeats_per_obligation);
  props.setNudgeEscalateAfterRepeats(values.nudge_escalate_after_repeats);
  props.setIdleSeconds(values.actor_idle_timeout_seconds);
  props.setKeepaliveSeconds(values.keepalive_delay_seconds);
  props.setKeepaliveMax(values.keepalive_max_per_actor);
  props.setSilenceSeconds(values.silence_timeout_seconds);
  props.setHelpNudgeIntervalSeconds(values.help_nudge_interval_seconds);
  props.setHelpNudgeMinMessages(values.help_nudge_min_messages);
}

export function BuiltinAutomationSettingsModal(props: BuiltinAutomationSettingsModalProps) {
  const { t } = useTranslation(["common", "settings"]);
  const openingValuesRef = useRef<BuiltinAutomationPolicyValues | null>(null);

  useEffect(() => {
    if (props.open) openingValuesRef.current = readValues(props);
    // A new snapshot is intentionally taken only when the modal opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open]);

  const isDirty = () =>
    JSON.stringify(readValues(props)) !== JSON.stringify(openingValuesRef.current);

  const close = () => {
    if (isDirty() && !window.confirm(t("settings:automation.unsavedChangesConfirm"))) return;
    if (openingValuesRef.current) restoreValues(props, openingValuesRef.current);
    props.onClose();
  };

  const { modalRef } = useModalA11y(props.open, close);

  if (!props.open) return null;

  const save = async () => {
    if (!(await props.onSave())) return;
    openingValuesRef.current = readValues(props);
    props.onClose();
  };

  return (
    <BodyPortal>
      <div className="fixed inset-0 z-[1000]" role="dialog" aria-modal="true">
        <div className="absolute inset-0 glass-overlay" onPointerDown={close} />
        <div ref={modalRef} className={settingsDialogPanelClass("xl")}>
          <div className={settingsDialogHeaderClass}>
            <div className="flex min-w-0 items-start gap-3">
              <div className="mt-0.5 rounded-md border border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg)] p-1.5 text-[var(--color-accent-primary)]">
                <SettingsIcon className="h-4 w-4" />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-[var(--color-text-primary)] sm:text-base">
                  {t("settings:policies.title")}
                </h2>
                <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">
                  {t("settings:policies.modalDescription")}
                </p>
              </div>
            </div>
            <button
              type="button"
              className={`${secondaryButtonClass("sm")} ml-auto shrink-0`}
              onClick={close}
            >
              {t("common:close")}
            </button>
          </div>

          <div className={settingsDialogBodyClass}>
            <AutomationPoliciesSection {...props} />
          </div>

          <div className={settingsDialogFooterClass}>
            <button
              type="button"
              className={secondaryButtonClass()}
              disabled={props.busy}
              onClick={props.onReset}
            >
              {t("settings:policies.resetPolicies")}
            </button>
            <button
              type="button"
              className={primaryButtonClass(props.busy)}
              disabled={props.busy}
              onClick={() => void save()}
            >
              {props.busy
                ? t("settings:automation.saving")
                : t("settings:policies.savePolicies")}
            </button>
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}
