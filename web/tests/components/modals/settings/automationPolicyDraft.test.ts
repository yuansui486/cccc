import { describe, expect, it } from "vitest";

import {
  buildBuiltinAutomationSettingsPatch,
  buildTaskReminderSettingsPatch,
  type BuiltinAutomationPolicyValues,
  type TaskReminderPolicyValues,
} from "../../../../src/components/modals/settings/useAutomationPolicyDraft";

describe("automation policy settings patches", () => {
  it("keeps task reminder settings isolated from built-in automation", () => {
    const values: TaskReminderPolicyValues = {
      task_reminder_enabled: false,
      task_empty_cooldown_seconds: 1200,
      task_active_overdue_milestones_seconds: [600, 1200],
      task_planned_unassigned_milestones_seconds: [1800, 3600],
    };

    const patch = buildTaskReminderSettingsPatch(values);

    expect(Object.keys(patch).sort()).toEqual([
      "task_active_overdue_milestones_seconds",
      "task_empty_cooldown_seconds",
      "task_planned_unassigned_milestones_seconds",
      "task_reminder_enabled",
    ]);
    expect(patch.task_active_overdue_milestones_seconds).not.toBe(
      values.task_active_overdue_milestones_seconds,
    );
    expect(patch.task_planned_unassigned_milestones_seconds).not.toBe(
      values.task_planned_unassigned_milestones_seconds,
    );
  });

  it("keeps built-in automation settings isolated from task reminders", () => {
    const values: BuiltinAutomationPolicyValues = {
      nudge_after_seconds: 300,
      reply_required_nudge_after_seconds: 310,
      attention_ack_nudge_after_seconds: 620,
      unread_nudge_after_seconds: 930,
      nudge_digest_min_interval_seconds: 125,
      nudge_max_repeats_per_obligation: 4,
      nudge_escalate_after_repeats: 3,
      actor_idle_timeout_seconds: 1800,
      keepalive_delay_seconds: 130,
      keepalive_max_per_actor: 5,
      silence_timeout_seconds: 3600,
      help_nudge_interval_seconds: 700,
      help_nudge_min_messages: 12,
    };

    const patch = buildBuiltinAutomationSettingsPatch(values);

    expect(patch).toEqual(values);
    expect(Object.keys(patch).some((key) => key.startsWith("task_"))).toBe(false);
  });
});
