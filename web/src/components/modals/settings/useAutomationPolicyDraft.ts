import { useEffect, useState } from "react";

import type { GroupSettings } from "../../../types";

type UpdateSettings = (settings: Partial<GroupSettings>) => Promise<boolean | void>;

export function useAutomationPolicyDraft({
  active,
  settings,
  onUpdateSettings,
}: {
  active: boolean;
  settings: GroupSettings | null;
  onUpdateSettings: UpdateSettings;
}) {
  const [nudgeSeconds, setNudgeSeconds] = useState(300);
  const [replyRequiredNudgeSeconds, setReplyRequiredNudgeSeconds] = useState(300);
  const [attentionAckNudgeSeconds, setAttentionAckNudgeSeconds] = useState(600);
  const [unreadNudgeSeconds, setUnreadNudgeSeconds] = useState(900);
  const [nudgeDigestMinIntervalSeconds, setNudgeDigestMinIntervalSeconds] = useState(120);
  const [nudgeMaxRepeatsPerObligation, setNudgeMaxRepeatsPerObligation] = useState(3);
  const [nudgeEscalateAfterRepeats, setNudgeEscalateAfterRepeats] = useState(2);
  const [idleSeconds, setIdleSeconds] = useState(0);
  const [keepaliveSeconds, setKeepaliveSeconds] = useState(120);
  const [keepaliveMax, setKeepaliveMax] = useState(3);
  const [silenceSeconds, setSilenceSeconds] = useState(0);
  const [helpNudgeIntervalSeconds, setHelpNudgeIntervalSeconds] = useState(600);
  const [helpNudgeMinMessages, setHelpNudgeMinMessages] = useState(10);
  const [taskReminderEnabled, setTaskReminderEnabled] = useState(true);
  const [taskEmptyCooldownSeconds, setTaskEmptyCooldownSeconds] = useState(900);
  const [taskActiveOverdueMilestonesSeconds, setTaskActiveOverdueMilestonesSeconds] = useState([1800, 3000, 3600, 5400]);
  const [taskPlannedUnassignedMilestonesSeconds, setTaskPlannedUnassignedMilestonesSeconds] = useState([
    900,
    1800,
    3600,
    7200,
    10800,
    21600,
  ]);

  useEffect(() => {
    if (!active || !settings) return;
    setNudgeSeconds(settings.nudge_after_seconds);
    setReplyRequiredNudgeSeconds(settings.reply_required_nudge_after_seconds ?? 300);
    setAttentionAckNudgeSeconds(settings.attention_ack_nudge_after_seconds ?? 600);
    setUnreadNudgeSeconds(settings.unread_nudge_after_seconds ?? 900);
    setNudgeDigestMinIntervalSeconds(settings.nudge_digest_min_interval_seconds ?? 120);
    setNudgeMaxRepeatsPerObligation(settings.nudge_max_repeats_per_obligation ?? 3);
    setNudgeEscalateAfterRepeats(settings.nudge_escalate_after_repeats ?? 2);
    setIdleSeconds(settings.actor_idle_timeout_seconds);
    setKeepaliveSeconds(settings.keepalive_delay_seconds);
    setKeepaliveMax(settings.keepalive_max_per_actor ?? 3);
    setSilenceSeconds(settings.silence_timeout_seconds);
    setHelpNudgeIntervalSeconds(settings.help_nudge_interval_seconds ?? 600);
    setHelpNudgeMinMessages(settings.help_nudge_min_messages ?? 10);
    setTaskReminderEnabled(settings.task_reminder_enabled ?? true);
    setTaskEmptyCooldownSeconds(settings.task_empty_cooldown_seconds ?? 900);
    setTaskActiveOverdueMilestonesSeconds(settings.task_active_overdue_milestones_seconds ?? [1800, 3000, 3600, 5400]);
    setTaskPlannedUnassignedMilestonesSeconds(
      settings.task_planned_unassigned_milestones_seconds ?? [900, 1800, 3600, 7200, 10800, 21600],
    );
  }, [active, settings]);

  const savePolicies = async () => {
    await onUpdateSettings({
      nudge_after_seconds: nudgeSeconds,
      reply_required_nudge_after_seconds: replyRequiredNudgeSeconds,
      attention_ack_nudge_after_seconds: attentionAckNudgeSeconds,
      unread_nudge_after_seconds: unreadNudgeSeconds,
      nudge_digest_min_interval_seconds: nudgeDigestMinIntervalSeconds,
      nudge_max_repeats_per_obligation: nudgeMaxRepeatsPerObligation,
      nudge_escalate_after_repeats: nudgeEscalateAfterRepeats,
      actor_idle_timeout_seconds: idleSeconds,
      keepalive_delay_seconds: keepaliveSeconds,
      keepalive_max_per_actor: keepaliveMax,
      silence_timeout_seconds: silenceSeconds,
      help_nudge_interval_seconds: helpNudgeIntervalSeconds,
      help_nudge_min_messages: helpNudgeMinMessages,
      task_reminder_enabled: taskReminderEnabled,
      task_empty_cooldown_seconds: taskEmptyCooldownSeconds,
      task_active_overdue_milestones_seconds: taskActiveOverdueMilestonesSeconds,
      task_planned_unassigned_milestones_seconds: taskPlannedUnassignedMilestonesSeconds,
    });
  };

  const resetPoliciesDraft = () => {
    setNudgeSeconds(300);
    setReplyRequiredNudgeSeconds(300);
    setAttentionAckNudgeSeconds(600);
    setUnreadNudgeSeconds(900);
    setNudgeDigestMinIntervalSeconds(120);
    setNudgeMaxRepeatsPerObligation(3);
    setNudgeEscalateAfterRepeats(2);
    setIdleSeconds(0);
    setKeepaliveSeconds(120);
    setKeepaliveMax(3);
    setSilenceSeconds(0);
    setHelpNudgeIntervalSeconds(600);
    setHelpNudgeMinMessages(10);
    setTaskReminderEnabled(true);
    setTaskEmptyCooldownSeconds(900);
    setTaskActiveOverdueMilestonesSeconds([1800, 3000, 3600, 5400]);
    setTaskPlannedUnassignedMilestonesSeconds([900, 1800, 3600, 7200, 10800, 21600]);
  };

  return {
    nudgeSeconds,
    setNudgeSeconds,
    replyRequiredNudgeSeconds,
    setReplyRequiredNudgeSeconds,
    attentionAckNudgeSeconds,
    setAttentionAckNudgeSeconds,
    unreadNudgeSeconds,
    setUnreadNudgeSeconds,
    nudgeDigestMinIntervalSeconds,
    setNudgeDigestMinIntervalSeconds,
    nudgeMaxRepeatsPerObligation,
    setNudgeMaxRepeatsPerObligation,
    nudgeEscalateAfterRepeats,
    setNudgeEscalateAfterRepeats,
    idleSeconds,
    setIdleSeconds,
    keepaliveSeconds,
    setKeepaliveSeconds,
    keepaliveMax,
    setKeepaliveMax,
    silenceSeconds,
    setSilenceSeconds,
    helpNudgeIntervalSeconds,
    setHelpNudgeIntervalSeconds,
    helpNudgeMinMessages,
    setHelpNudgeMinMessages,
    taskReminderEnabled,
    setTaskReminderEnabled,
    taskEmptyCooldownSeconds,
    setTaskEmptyCooldownSeconds,
    taskActiveOverdueMilestonesSeconds,
    setTaskActiveOverdueMilestonesSeconds,
    taskPlannedUnassignedMilestonesSeconds,
    setTaskPlannedUnassignedMilestonesSeconds,
    savePolicies,
    resetPoliciesDraft,
  };
}
