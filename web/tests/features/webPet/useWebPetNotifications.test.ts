import { describe, expect, it } from "vitest";
import type { LedgerEvent } from "../../../src/types";
import {
  getUnseenReminders,
  mergeTaskProposalReminders,
  selectAutoPeekReminder,
  shouldProjectReminderForGroupState,
  shouldSuppressTaskProposalEcho,
  sortProjectedReminders,
} from "../../../src/features/webPet/useWebPetNotifications";
import type { PetReminder } from "../../../src/features/webPet/types";

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

  it("keeps draft_message when group is idle", () => {
    expect(shouldProjectReminderForGroupState(makeReminder(), "idle")).toBe(true);
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

  it("hides reminders when group is paused", () => {
    expect(
      shouldProjectReminderForGroupState(
        makeReminder({
          action: { type: "restart_actor", groupId: "g-1", actorId: "peer-1" },
        }),
        "paused",
      ),
    ).toBe(false);
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

  it("suppresses task proposal when explicit custom text was already sent to foreman", () => {
    const reminder = makeReminder({
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        text: "请用 cccc_task 把 T192 标记成 waiting_on=user。",
      },
    });
    const events: LedgerEvent[] = [
      {
        id: "evt-1",
        ts: "2026-03-27T16:00:00.000Z",
        kind: "chat.message",
        by: "user",
        data: {
          text: "请用 cccc_task 把 T192 标记成 waiting_on=user。",
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
});

describe("mergeTaskProposalReminders", () => {
  it("prefers remote pet task proposals over local proposals for the same task", () => {
    const localReminder = makeReminder({
      id: "local-task-proposal:T192",
      fingerprint: "local-task-proposal:g-1:T192:move-active",
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "move",
        taskId: "T192",
        status: "active",
      },
    });
    const remoteReminder = makeReminder({
      id: "pet-decision:T192",
      fingerprint: "pet-decision:g-1:T192",
      summary: "pet 认为应该先同步 owner。",
      action: {
        type: "task_proposal",
        groupId: "g-1",
        operation: "update",
        taskId: "T192",
        assignee: "peer-1",
      },
    });

    expect(mergeTaskProposalReminders([localReminder], [remoteReminder])).toEqual([remoteReminder]);
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
