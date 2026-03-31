import { describe, expect, it } from "vitest";
import type { LedgerEvent } from "../../types";
import {
  getUnseenReminders,
  selectAutoPeekReminder,
  shouldProjectReminderForGroupState,
  shouldSuppressTaskProposalEcho,
  sortProjectedReminders,
} from "./useWebPetNotifications";
import type { PetReminder } from "./types";

function makeReminder(overrides: Partial<PetReminder> = {}): PetReminder {
  return {
    id: "r1",
    kind: "suggestion",
    priority: 70,
    summary: "summary",
    agent: "pet-peer",
    source: {},
    fingerprint: "fp-1",
    action: {
      type: "draft_message",
      groupId: "g-1",
      text: "hello",
    },
    ...overrides,
  };
}

describe("shouldProjectReminderForGroupState", () => {
  it("keeps all reminders when group is active", () => {
    expect(shouldProjectReminderForGroupState(makeReminder(), "active")).toBe(true);
  });

  it("hides draft_message when group is idle", () => {
    expect(shouldProjectReminderForGroupState(makeReminder(), "idle")).toBe(false);
  });

  it("keeps restart_actor when group is idle", () => {
    expect(
      shouldProjectReminderForGroupState(
        makeReminder({
          kind: "actor_down",
          action: { type: "restart_actor", groupId: "g-1", actorId: "peer-1" },
        }),
        "idle",
      ),
    ).toBe(true);
  });

  it("keeps automation_proposal when group is paused", () => {
    expect(
      shouldProjectReminderForGroupState(
        makeReminder({
          action: {
            type: "automation_proposal",
            groupId: "g-1",
            title: "Apply follow-up",
            actions: [{ type: "create_rule" }],
          },
        }),
        "paused",
      ),
    ).toBe(true);
  });
});

describe("shouldSuppressTaskProposalEcho", () => {
  it("suppresses task proposal when user just sent the same task request to foreman", () => {
    const reminder = makeReminder({
      id: "task-proposal:T192",
      summary: "先把 T192 的解阻路径定下来。",
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T16:00:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: 'Pet task proposal: please use cccc_task to update this task (task_id=T192, title="M1-2: Bridge 出站链路 stream_emit daemon op").',
          to: ["@foreman"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(true);
  });

  it("keeps task proposal when user is only discussing the same task with foreman", () => {
    const reminder = makeReminder({
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T16:00:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: "先协调 T192 的解阻路径：确认 M1-2: Bridge 出站链路 stream_emit daemon op 还缺什么运行态样本。",
          to: ["@foreman"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(false);
  });

  it("keeps task proposal when recent user message targets someone else", () => {
    const reminder = makeReminder({
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T16:00:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: "先协调 T192 的解阻路径。",
          to: ["peer-reviewer"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(false);
  });

  it("keeps task proposal when matching user message is too old", () => {
    const reminder = makeReminder({
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        title: "M1-2: Bridge 出站链路 stream_emit daemon op",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T15:40:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: "先协调 T192 的解阻路径。",
          to: ["@foreman"],
        },
      },
    ];

    expect(
      shouldSuppressTaskProposalEcho(
        reminder,
        events,
        Date.parse("2026-03-27T16:03:00.000Z"),
      ),
    ).toBe(false);
  });
});

describe("sortProjectedReminders", () => {
  it("orders reminders by descending priority", () => {
    const sorted = sortProjectedReminders([
      makeReminder({ id: "low", fingerprint: "fp-3", priority: 40, summary: "low" }),
      makeReminder({ id: "high", fingerprint: "fp-1", priority: 95, summary: "high" }),
      makeReminder({ id: "mid", fingerprint: "fp-2", priority: 70, summary: "mid" }),
    ]);

    expect(sorted.map((item) => item.id)).toEqual(["high", "mid", "low"]);
  });
});

describe("selectAutoPeekReminder", () => {
  it("returns the highest-priority unseen reminder when auto-peek is allowed", () => {
    const reminders = sortProjectedReminders([
      makeReminder({ id: "r1", fingerprint: "fp-1", priority: 60, summary: "first" }),
      makeReminder({ id: "r2", fingerprint: "fp-2", priority: 95, summary: "second" }),
    ]);

    expect(selectAutoPeekReminder(reminders, {}, {})?.id).toBe("r2");
  });

  it("skips reminders that are already seen", () => {
    const reminders = sortProjectedReminders([
      makeReminder({ id: "r1", fingerprint: "fp-1", priority: 60, summary: "first" }),
      makeReminder({ id: "r2", fingerprint: "fp-2", priority: 95, summary: "second" }),
    ]);

    const unseen = getUnseenReminders(reminders, { "fp-2": true });
    expect(unseen.map((item) => item.id)).toEqual(["r1"]);
    expect(selectAutoPeekReminder(reminders, { "fp-2": true }, {})?.id).toBe("r1");
  });

  it("returns null when current reminders are blocked at the same priority", () => {
    const reminders = sortProjectedReminders([
      makeReminder({ id: "r1", fingerprint: "fp-1", priority: 95, summary: "first" }),
    ]);

    expect(selectAutoPeekReminder(reminders, {}, { "fp-1": 95 })).toBeNull();
  });

  it("allows the same fingerprint to re-peek when its priority increases", () => {
    const reminders = sortProjectedReminders([
      makeReminder({ id: "r1", fingerprint: "fp-1", priority: 96, summary: "first" }),
    ]);

    expect(selectAutoPeekReminder(reminders, {}, { "fp-1": 80 })?.id).toBe("r1");
  });
});
