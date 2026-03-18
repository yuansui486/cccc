import { describe, expect, it } from "vitest";
import {
  createMentionReminder,
  projectPetReminders,
  type ProjectPetRemindersInput,
  type ReminderActorInput,
  type ReminderEventInput,
} from "./reminders";

function makeInput(
  overrides: Partial<ProjectPetRemindersInput> = {},
): ProjectPetRemindersInput {
  return {
    groupId: "g-demo",
    waitingUser: [],
    tasks: [],
    actors: [],
    events: [],
    ...overrides,
  };
}

function makeActor(
  actorId: string,
  overrides: Partial<ReminderActorInput> = {},
): ReminderActorInput {
  return {
    actorId,
    running: false,
    idleSeconds: 0,
    ...overrides,
  };
}

function makeChatEvent(
  eventId: string,
  overrides: Partial<ReminderEventInput> = {},
): ReminderEventInput {
  return {
    eventId,
    kind: "chat.message",
    by: "peer-reviewer",
    text: `message:${eventId}`,
    to: [],
    replyRequired: false,
    acked: false,
    replied: false,
    ...overrides,
  };
}

describe("projectPetReminders", () => {
  it("projects waiting_user from attention entries", () => {
    const reminders = projectPetReminders(
      makeInput({
        waitingUser: [
          {
            taskId: "T249",
            label: "Need user confirmation",
          },
        ],
        tasks: [
          {
            taskId: "T249",
            title: "Need user confirmation",
          },
        ],
      }),
    );

    expect(reminders).toHaveLength(1);
    expect(reminders[0]).toMatchObject({
      kind: "waiting_user",
      priority: 100,
      source: { taskId: "T249" },
      fingerprint: "group:g-demo:waiting_user:T249",
      action: {
        type: "open_task",
        groupId: "g-demo",
        taskId: "T249",
      },
      ephemeral: false,
      agent: "system",
    });
  });

  it("falls back to coordination tasks for waiting_user", () => {
    const reminders = projectPetReminders(
      makeInput({
        tasks: [
          {
            taskId: "T250",
            title: "Wait for approval",
            waitingOn: "user",
            status: "active",
          },
          {
            taskId: "T251",
            title: "Done already",
            waitingOn: "user",
            status: "done",
          },
        ],
      }),
    );

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.source.taskId).toBe("T250");
    expect(reminders[0]?.action).toEqual({
      type: "open_task",
      groupId: "g-demo",
      taskId: "T250",
    });
  });

  it("projects reply_required reminders per stable event id", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-2", {
            replyRequired: true,
          }),
          makeChatEvent("evt-1", {
            replyRequired: true,
          }),
        ],
      }),
    );

    expect(reminders).toHaveLength(2);
    expect(reminders.map((item) => item.fingerprint)).toEqual([
      "group:g-demo:reply_required:evt-2",
      "group:g-demo:reply_required:evt-1",
    ]);
  });

  it("ignores acked or replied reply_required events", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-1", {
            replyRequired: true,
            acked: true,
          }),
          makeChatEvent("evt-2", {
            replyRequired: true,
            replied: true,
          }),
        ],
      }),
    );

    expect(reminders).toHaveLength(0);
  });

  it("projects stalled_peer only for running actors with an active task", () => {
    const reminders = projectPetReminders(
      makeInput({
        actors: [
          makeActor("peer-impl-2", {
            running: true,
            idleSeconds: 601,
            activeTaskId: "T249",
          }),
          makeActor("peer-reviewer", {
            running: true,
            idleSeconds: 599,
            activeTaskId: "T300",
          }),
          makeActor("peer-impl-1", {
            running: false,
            idleSeconds: 1000,
            activeTaskId: "T301",
          }),
        ],
      }),
    );

    expect(reminders).toHaveLength(1);
    expect(reminders[0]).toMatchObject({
      kind: "stalled_peer",
      priority: 70,
      source: { actorId: "peer-impl-2", taskId: "T249" },
      fingerprint: "group:g-demo:stalled_peer:peer-impl-2:T249",
      action: {
        type: "open_panel",
        groupId: "g-demo",
      },
      agent: "peer-impl-2",
    });
  });

  it("does not project mention reminders for agent-to-user messages (visible in chat)", () => {
    const reminders = projectPetReminders(
      makeInput({
        events: [
          makeChatEvent("evt-1", {
            by: "peer-reviewer",
            text: "Need you here",
            to: ["user"],
          }),
          makeChatEvent("evt-2", {
            by: "user",
            text: "self mention",
            to: ["user"],
          }),
        ],
      }),
    );

    expect(reminders).toHaveLength(0);
  });

  it("sorts reminders by priority descending (mention disabled)", () => {
    const reminders = projectPetReminders(
      makeInput({
        waitingUser: [{ taskId: "T249", label: "Need approval" }],
        tasks: [{ taskId: "T249", title: "Need approval" }],
        actors: [
          makeActor("peer-impl-2", {
            running: true,
            idleSeconds: 700,
            activeTaskId: "T249",
          }),
        ],
        events: [
          makeChatEvent("evt-mention", {
            by: "peer-reviewer",
            text: "ping",
            to: ["@user"],
          }),
          makeChatEvent("evt-reply", {
            replyRequired: true,
          }),
        ],
      }),
    );

    expect(reminders.map((item) => item.kind)).toEqual([
      "waiting_user",
      "reply_required",
      "stalled_peer",
    ]);
  });

  it("dedupes reminders by fingerprint", () => {
    const reminders = projectPetReminders(
      makeInput({
        waitingUser: [
          { taskId: "T249", label: "Need approval" },
          { taskId: "T249", label: "Need approval" },
        ],
      }),
    );

    expect(reminders.map((item) => item.fingerprint)).toEqual([
      "group:g-demo:waiting_user:T249",
    ]);
  });

  it("hashes waiting_user fallback strings when task id is absent", () => {
    const reminders = projectPetReminders(
      makeInput({
        waitingUser: [
          { label: "Need final approval" },
          { label: "Need final approval" },
        ],
      }),
    );

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.fingerprint).toMatch(
      /^group:g-demo:waiting_user:hash:[a-z0-9]+$/,
    );
    expect(reminders[0]?.action).toEqual({
      type: "open_panel",
      groupId: "g-demo",
    });
    expect(reminders[0]?.source.taskId).toBeUndefined();
  });

  it("does not create standalone mention reminder for agent messages", () => {
    const reminder = createMentionReminder(
      "g-demo",
      makeChatEvent("evt-1", {
        by: "peer-reviewer",
        text: "Need you now",
        to: ["user"],
      }),
    );

    expect(reminder).toBeNull();
  });
});
