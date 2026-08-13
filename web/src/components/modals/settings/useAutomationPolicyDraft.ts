import { useEffect, useState } from "react";

import type { GroupSettings } from "../../../types";

type UpdateSettings = (settings: Partial<GroupSettings>) => Promise<boolean | void>;

export type TaskReminderPolicyValues = Pick<
  GroupSettings,
  | "task_reminder_enabled"
  | "task_empty_cooldown_seconds"
  | "task_active_overdue_milestones_seconds"
  | "task_planned_unassigned_milestones_seconds"
>;

export type BuiltinAutomationPolicyValues = Pick<
  GroupSettings,
  | "nudge_after_seconds"
  | "reply_required_nudge_after_seconds"
  | "attention_ack_nudge_after_seconds"
  | "unread_nudge_after_seconds"
  | "nudge_digest_min_interval_seconds"
  | "nudge_max_repeats_per_obligation"
  | "nudge_escalate_after_repeats"
  | "actor_idle_timeout_seconds"
  | "keepalive_delay_seconds"
  | "keepalive_max_per_actor"
  | "silence_timeout_seconds"
  | "help_nudge_interval_seconds"
  | "help_nudge_min_messages"
>;

export const DEFAULT_TASK_REMINDER_POLICY: TaskReminderPolicyValues = {
  task_reminder_enabled: true,
  task_empty_cooldown_seconds: 900,
  task_active_overdue_milestones_seconds: [1800, 3000, 3600, 5400],
  task_planned_unassigned_milestones_seconds: [900, 1800, 3600, 7200, 10800, 21600],
};

export const DEFAULT_BUILTIN_AUTOMATION_POLICY: BuiltinAutomationPolicyValues = {
  nudge_after_seconds: 300,
  reply_required_nudge_after_seconds: 300,
  attention_ack_nudge_after_seconds: 600,
  unread_nudge_after_seconds: 900,
  nudge_digest_min_interval_seconds: 120,
  nudge_max_repeats_per_obligation: 3,
  nudge_escalate_after_repeats: 2,
  actor_idle_timeout_seconds: 0,
  keepalive_delay_seconds: 120,
  keepalive_max_per_actor: 3,
  silence_timeout_seconds: 0,
  help_nudge_interval_seconds: 600,
  help_nudge_min_messages: 10,
};

export function buildTaskReminderSettingsPatch(
  values: TaskReminderPolicyValues,
): Partial<GroupSettings> {
  return {
    task_reminder_enabled: values.task_reminder_enabled,
    task_empty_cooldown_seconds: values.task_empty_cooldown_seconds,
    task_active_overdue_milestones_seconds: [...values.task_active_overdue_milestones_seconds],
    task_planned_unassigned_milestones_seconds: [
      ...values.task_planned_unassigned_milestones_seconds,
    ],
  };
}

export function buildBuiltinAutomationSettingsPatch(
  values: BuiltinAutomationPolicyValues,
): Partial<GroupSettings> {
  return { ...values };
}

export function useAutomationPolicyDraft({
  active,
  settings,
  onUpdateSettings,
}: {
  active: boolean;
  settings: GroupSettings | null;
  onUpdateSettings: UpdateSettings;
}) {
  const [nudgeSeconds, setNudgeSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_after_seconds,
  );
  const [replyRequiredNudgeSeconds, setReplyRequiredNudgeSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.reply_required_nudge_after_seconds,
  );
  const [attentionAckNudgeSeconds, setAttentionAckNudgeSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.attention_ack_nudge_after_seconds,
  );
  const [unreadNudgeSeconds, setUnreadNudgeSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.unread_nudge_after_seconds,
  );
  const [nudgeDigestMinIntervalSeconds, setNudgeDigestMinIntervalSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_digest_min_interval_seconds,
  );
  const [nudgeMaxRepeatsPerObligation, setNudgeMaxRepeatsPerObligation] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_max_repeats_per_obligation,
  );
  const [nudgeEscalateAfterRepeats, setNudgeEscalateAfterRepeats] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_escalate_after_repeats,
  );
  const [idleSeconds, setIdleSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.actor_idle_timeout_seconds,
  );
  const [keepaliveSeconds, setKeepaliveSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.keepalive_delay_seconds,
  );
  const [keepaliveMax, setKeepaliveMax] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.keepalive_max_per_actor,
  );
  const [silenceSeconds, setSilenceSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.silence_timeout_seconds,
  );
  const [helpNudgeIntervalSeconds, setHelpNudgeIntervalSeconds] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.help_nudge_interval_seconds,
  );
  const [helpNudgeMinMessages, setHelpNudgeMinMessages] = useState(
    DEFAULT_BUILTIN_AUTOMATION_POLICY.help_nudge_min_messages,
  );
  const [taskReminderEnabled, setTaskReminderEnabled] = useState(
    DEFAULT_TASK_REMINDER_POLICY.task_reminder_enabled,
  );
  const [taskEmptyCooldownSeconds, setTaskEmptyCooldownSeconds] = useState(
    DEFAULT_TASK_REMINDER_POLICY.task_empty_cooldown_seconds,
  );
  const [taskActiveOverdueMilestonesSeconds, setTaskActiveOverdueMilestonesSeconds] = useState([
    ...DEFAULT_TASK_REMINDER_POLICY.task_active_overdue_milestones_seconds,
  ]);
  const [taskPlannedUnassignedMilestonesSeconds, setTaskPlannedUnassignedMilestonesSeconds] =
    useState([...DEFAULT_TASK_REMINDER_POLICY.task_planned_unassigned_milestones_seconds]);

  useEffect(() => {
    if (!active || !settings) return;
    // Refresh the editable draft when the persisted policy changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNudgeSeconds(settings.nudge_after_seconds);
    setReplyRequiredNudgeSeconds(
      settings.reply_required_nudge_after_seconds ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.reply_required_nudge_after_seconds,
    );
    setAttentionAckNudgeSeconds(
      settings.attention_ack_nudge_after_seconds ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.attention_ack_nudge_after_seconds,
    );
    setUnreadNudgeSeconds(
      settings.unread_nudge_after_seconds ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.unread_nudge_after_seconds,
    );
    setNudgeDigestMinIntervalSeconds(
      settings.nudge_digest_min_interval_seconds ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_digest_min_interval_seconds,
    );
    setNudgeMaxRepeatsPerObligation(
      settings.nudge_max_repeats_per_obligation ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_max_repeats_per_obligation,
    );
    setNudgeEscalateAfterRepeats(
      settings.nudge_escalate_after_repeats ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_escalate_after_repeats,
    );
    setIdleSeconds(settings.actor_idle_timeout_seconds);
    setKeepaliveSeconds(settings.keepalive_delay_seconds);
    setKeepaliveMax(
      settings.keepalive_max_per_actor ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.keepalive_max_per_actor,
    );
    setSilenceSeconds(settings.silence_timeout_seconds);
    setHelpNudgeIntervalSeconds(
      settings.help_nudge_interval_seconds ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.help_nudge_interval_seconds,
    );
    setHelpNudgeMinMessages(
      settings.help_nudge_min_messages ??
        DEFAULT_BUILTIN_AUTOMATION_POLICY.help_nudge_min_messages,
    );
    setTaskReminderEnabled(
      settings.task_reminder_enabled ?? DEFAULT_TASK_REMINDER_POLICY.task_reminder_enabled,
    );
    setTaskEmptyCooldownSeconds(
      settings.task_empty_cooldown_seconds ??
        DEFAULT_TASK_REMINDER_POLICY.task_empty_cooldown_seconds,
    );
    setTaskActiveOverdueMilestonesSeconds(
      settings.task_active_overdue_milestones_seconds ?? [
        ...DEFAULT_TASK_REMINDER_POLICY.task_active_overdue_milestones_seconds,
      ],
    );
    setTaskPlannedUnassignedMilestonesSeconds(
      settings.task_planned_unassigned_milestones_seconds ?? [
        ...DEFAULT_TASK_REMINDER_POLICY.task_planned_unassigned_milestones_seconds,
      ],
    );
  }, [active, settings]);

  const currentBuiltinPolicy = (): BuiltinAutomationPolicyValues => ({
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
  });

  const currentTaskReminderPolicy = (): TaskReminderPolicyValues => ({
    task_reminder_enabled: taskReminderEnabled,
    task_empty_cooldown_seconds: taskEmptyCooldownSeconds,
    task_active_overdue_milestones_seconds: taskActiveOverdueMilestonesSeconds,
    task_planned_unassigned_milestones_seconds: taskPlannedUnassignedMilestonesSeconds,
  });

  const saveBuiltinPolicies = async (): Promise<boolean> => {
    try {
      const result = await onUpdateSettings(
        buildBuiltinAutomationSettingsPatch(currentBuiltinPolicy()),
      );
      return result !== false;
    } catch {
      return false;
    }
  };

  const saveTaskReminderSettings = async (): Promise<boolean> => {
    try {
      const result = await onUpdateSettings(
        buildTaskReminderSettingsPatch(currentTaskReminderPolicy()),
      );
      return result !== false;
    } catch {
      return false;
    }
  };

  const saveTaskReminderEnabled = async (enabled: boolean): Promise<boolean> => {
    const previous = taskReminderEnabled;
    setTaskReminderEnabled(enabled);
    try {
      const result = await onUpdateSettings({ task_reminder_enabled: enabled });
      if (result !== false) return true;
    } catch {
      // Restore the previous switch state below.
    }
    if (enabled !== previous) {
      setTaskReminderEnabled(previous);
    }
    return false;
  };

  const resetBuiltinPoliciesDraft = () => {
    setNudgeSeconds(DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_after_seconds);
    setReplyRequiredNudgeSeconds(
      DEFAULT_BUILTIN_AUTOMATION_POLICY.reply_required_nudge_after_seconds,
    );
    setAttentionAckNudgeSeconds(
      DEFAULT_BUILTIN_AUTOMATION_POLICY.attention_ack_nudge_after_seconds,
    );
    setUnreadNudgeSeconds(DEFAULT_BUILTIN_AUTOMATION_POLICY.unread_nudge_after_seconds);
    setNudgeDigestMinIntervalSeconds(
      DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_digest_min_interval_seconds,
    );
    setNudgeMaxRepeatsPerObligation(
      DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_max_repeats_per_obligation,
    );
    setNudgeEscalateAfterRepeats(
      DEFAULT_BUILTIN_AUTOMATION_POLICY.nudge_escalate_after_repeats,
    );
    setIdleSeconds(DEFAULT_BUILTIN_AUTOMATION_POLICY.actor_idle_timeout_seconds);
    setKeepaliveSeconds(DEFAULT_BUILTIN_AUTOMATION_POLICY.keepalive_delay_seconds);
    setKeepaliveMax(DEFAULT_BUILTIN_AUTOMATION_POLICY.keepalive_max_per_actor);
    setSilenceSeconds(DEFAULT_BUILTIN_AUTOMATION_POLICY.silence_timeout_seconds);
    setHelpNudgeIntervalSeconds(
      DEFAULT_BUILTIN_AUTOMATION_POLICY.help_nudge_interval_seconds,
    );
    setHelpNudgeMinMessages(DEFAULT_BUILTIN_AUTOMATION_POLICY.help_nudge_min_messages);
  };

  const resetTaskReminderDraft = () => {
    setTaskEmptyCooldownSeconds(DEFAULT_TASK_REMINDER_POLICY.task_empty_cooldown_seconds);
    setTaskActiveOverdueMilestonesSeconds([
      ...DEFAULT_TASK_REMINDER_POLICY.task_active_overdue_milestones_seconds,
    ]);
    setTaskPlannedUnassignedMilestonesSeconds([
      ...DEFAULT_TASK_REMINDER_POLICY.task_planned_unassigned_milestones_seconds,
    ]);
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
    saveBuiltinPolicies,
    saveTaskReminderSettings,
    saveTaskReminderEnabled,
    resetBuiltinPoliciesDraft,
    resetTaskReminderDraft,
  };
}
