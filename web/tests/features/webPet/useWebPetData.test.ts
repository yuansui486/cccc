import { describe, expect, it } from "vitest";
import { getAgentTaskSummaries, getPreferredAgentTaskHint, shouldSurfaceReminder } from "../../../src/features/webPet/useWebPetData";
import type { AgentSummary, PetReminder } from "../../../src/features/webPet/types";

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

  it("keeps task proposal reminders when the action can synthesize the prepared message", () => {
    expect(
      shouldSurfaceReminder(
        makeReminder({
          summary: "",
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
});

describe("getPreferredAgentTaskHint", () => {
  it("prefers active task id paired with focus", () => {
    const agents: AgentSummary[] = [
      { id: "peer-1", state: "working", activeTaskId: "T-42", focus: "Review launch scope" },
    ];

    expect(getPreferredAgentTaskHint(agents)).toBe("T-42 | Review launch scope");
  });

  it("falls back to focus when no active task id exists", () => {
    const agents: AgentSummary[] = [
      { id: "peer-1", state: "waiting", focus: "Need user input" },
    ];

    expect(getPreferredAgentTaskHint(agents)).toBe("Need user input");
  });
});

describe("getAgentTaskSummaries", () => {
  it("collects task-oriented summaries from agents in order", () => {
    const agents: AgentSummary[] = [
      { id: "peer-1", state: "working", activeTaskId: "T-42", focus: "Review launch scope" },
      { id: "peer-2", state: "waiting", focus: "Need user input" },
      { id: "peer-3", state: "idle", activeTaskId: "T-77", focus: "" },
    ];

    expect(getAgentTaskSummaries(agents)).toEqual([
      "T-42 | Review launch scope",
      "Need user input",
      "T-77",
    ]);
  });

  it("respects the item limit", () => {
    const agents: AgentSummary[] = [
      { id: "peer-1", state: "working", activeTaskId: "T-42", focus: "Review launch scope" },
      { id: "peer-2", state: "waiting", focus: "Need user input" },
    ];

    expect(getAgentTaskSummaries(agents, 1)).toEqual(["T-42 | Review launch scope"]);
  });
});
