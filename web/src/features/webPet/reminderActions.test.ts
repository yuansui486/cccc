import { describe, expect, it } from "vitest";
import { getReminderActionButtons } from "./reminderActions";
import type { PetReminder } from "./types";

describe("getReminderActionButtons", () => {
  it("prefers drafting in chat for reply_required reminders with a draft action", () => {
    const reminder: PetReminder = {
      id: "reply_required:evt-1",
      kind: "suggestion",
      priority: 80,
      summary: "请先回复用户",
      agent: "foreman",
      source: { eventId: "evt-1", suggestionKind: "reply_required" },
      fingerprint: "group:g-1:suggestion:reply_required:evt-1",
      action: {
        type: "draft_message",
        groupId: "g-1",
        text: "收到，我来跟进这条，稍后给你同步进展。",
        to: ["foreman"],
        replyTo: "evt-1",
      },
    };

    expect(getReminderActionButtons(reminder)).toEqual([
      {
        labelKey: "draft",
        fallback: "Draft in chat",
        action: reminder.action,
      },
    ]);
  });

  it("returns draft when action text exists", () => {
    const reminder: PetReminder = {
      id: "reply_required:evt-2",
      kind: "suggestion",
      priority: 80,
      summary: "请直接发送这条回复",
        agent: "foreman",
        source: { eventId: "evt-2", suggestionKind: "reply_required" },
        fingerprint: "group:g-1:suggestion:reply_required:evt-2",
        action: {
          type: "draft_message",
          groupId: "g-1",
          text: "我先处理这条，稍后同步结果。",
          to: ["foreman"],
        replyTo: "evt-2",
      },
    };

    expect(getReminderActionButtons(reminder)).toEqual([
      {
        labelKey: "draft",
        fallback: "Draft in chat",
        action: reminder.action,
      },
    ]);
  });

  it("returns restart button for actor_down reminders", () => {
    const reminder: PetReminder = {
      id: "actor_down:peer-1",
      kind: "actor_down",
      priority: 90,
      summary: "Peer 1 已停止，当前任务 T1 可能卡住，建议重启。",
      agent: "Peer 1",
      source: { actorId: "peer-1", taskId: "T1", actorRole: "peer" },
      fingerprint: "group:g-1:actor_down:peer-1",
      action: {
        type: "restart_actor",
        groupId: "g-1",
        actorId: "peer-1",
      },
    };

    expect(getReminderActionButtons(reminder)).toEqual([
      {
        labelKey: "restartPeer",
        fallback: "Restart peer",
        action: reminder.action,
      },
    ]);
  });

  it("returns foreman restart button for foreman actor_down reminders", () => {
    const reminder: PetReminder = {
      id: "actor_down:foreman-1",
      kind: "actor_down",
      priority: 95,
      summary: "Foreman 已停止，建议重启以恢复调度。",
      agent: "Foreman",
      source: { actorId: "foreman-1", actorRole: "foreman" },
      fingerprint: "group:g-1:actor_down:foreman-1",
      action: {
        type: "restart_actor",
        groupId: "g-1",
        actorId: "foreman-1",
      },
    };

    expect(getReminderActionButtons(reminder)).toEqual([
      {
        labelKey: "restartForeman",
        fallback: "Restart foreman",
        action: reminder.action,
      },
    ]);
  });

  it("returns draft button for task proposals", () => {
    const reminder: PetReminder = {
      id: "task-proposal:T315",
      kind: "suggestion",
      priority: 85,
      summary: "建议让 foreman 把 T315 从 planned 推进到 active。",
      agent: "pet-peer",
      source: { taskId: "T315" },
      fingerprint: "group:g-1:suggestion:task-proposal:T315",
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "move",
        taskId: "T315",
        status: "active",
      },
    };

    expect(getReminderActionButtons(reminder)).toEqual([
      {
        labelKey: "draft",
        fallback: "Draft in chat",
        action: reminder.action,
      },
    ]);
  });
});
