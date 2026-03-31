import { describe, expect, it } from "vitest";
import { shouldSurfaceReminder } from "./useWebPetData";
import type { PetReminder } from "./types";

function makeReminder(overrides: Partial<PetReminder> = {}): PetReminder {
  return {
    id: "mention:evt-1",
    kind: "suggestion",
    priority: 70,
    summary: "peer 给了一个可直接发送的建议。",
    agent: "peer",
    source: { eventId: "evt-1", suggestionKind: "mention" },
    fingerprint: "group:g-1:suggestion:mention:evt-1",
    action: {
      type: "draft_message",
      groupId: "g-1",
      text: "我来处理这条。",
      to: ["peer"],
      replyTo: "evt-1",
    },
    ...overrides,
  };
}

describe("shouldSurfaceReminder", () => {
  it("keeps actionable draft_message reminders", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          action: {
            type: "draft_message",
            groupId: "g-1",
            text: "我来处理这条。",
            to: ["peer"],
            replyTo: "evt-1",
          },
        }),
      ),
    ).toBe(true);
  });

  it("keeps restart_actor reminders for low-noise persona", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          id: "actor_down:peer-1",
          kind: "actor_down",
          action: {
            type: "restart_actor",
            groupId: "g-1",
            actorId: "peer-1",
          },
        }),
      ),
    ).toBe(true);
  });

  it("hides malformed draft_message reminders", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          action: {
            type: "draft_message",
            groupId: "g-1",
            text: "",
          },
        }),
      ),
    ).toBe(false);
  });

  it("keeps task proposal reminders when they can be forwarded to foreman", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          summary: "建议把 T315 推进到 active。",
          action: {
            type: "task_proposal",
            groupId: "g-1",
            operation: "move",
            taskId: "T315",
            status: "active",
          },
        }),
      ),
    ).toBe(true);
  });

  it("keeps automation proposal reminders when they have executable actions", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          summary: "建议创建一条一次性提醒规则。",
          action: {
            type: "automation_proposal",
            groupId: "g-1",
            title: "One-shot follow-up",
            summary: "稍后检查 waiting_user 是否已推进。",
            actions: [
              {
                type: "create_rule",
                rule: {
                  id: "pet-user-dependency-followup-once",
                },
              },
            ],
          },
        }),
      ),
    ).toBe(true);
  });
});
