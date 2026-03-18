import { describe, it, expect } from "vitest";
import { aggregateWebPetState } from "./aggregateWebPetState";
import type { AggregateInput } from "./aggregateWebPetState";
import type { GroupContext, AgentState, LedgerEvent } from "../../types";

function makeInput(overrides: Partial<AggregateInput> = {}): AggregateInput {
  return {
    groupContext: null,
    events: [],
    sseStatus: "connected",
    teamName: "cccc",
    groupId: "g_test",
    ...overrides,
  };
}

function makeAgentState(
  id: string,
  activeTaskId: string,
  focus = ""
): AgentState {
  return {
    id,
    hot: { active_task_id: activeTaskId || null, focus },
  };
}

describe("aggregateWebPetState", () => {
  it("returns napping when no activity", () => {
    const result = aggregateWebPetState(makeInput());
    expect(result.catState).toBe("napping");
    expect(result.panelData.actionItems).toHaveLength(0);
  });

  it("returns working when single agent is active", () => {
    const context: GroupContext = {
      agent_states: [makeAgentState("peer-impl-1", "T1", "implementing")],
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context })
    );
    expect(result.catState).toBe("working");
    expect(result.panelData.agents[0].state).toBe("working");
  });

  it("returns busy when multiple agents are active", () => {
    const context: GroupContext = {
      agent_states: [
        makeAgentState("peer-impl-1", "T1"),
        makeAgentState("peer-impl-2", "T2"),
      ],
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context })
    );
    expect(result.catState).toBe("busy");
    expect(result.panelData.agents.every((a) => a.state === "busy")).toBe(
      true
    );
  });

  it("returns needs_you when waiting_user tasks exist", () => {
    const context: GroupContext = {
      attention: {
        waiting_user: [
          {
            id: "T100",
            title: "Need approval",
            assignee: "peer-impl-1",
            waiting_on: "user",
            status: "active",
          },
        ],
      },
      agent_states: [],
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context })
    );
    expect(result.catState).toBe("needs_you");
    expect(result.panelData.actionItems.length).toBeGreaterThan(0);
    expect(result.panelData.actionItems[0].id).toBe(
      "T100_waiting_on_user"
    );
  });

  it("returns needs_you when reply_required events exist", () => {
    const events: LedgerEvent[] = [
      {
        id: "evt1",
        kind: "chat.message",
        by: "peer-reviewer",
        data: { text: "Please confirm this patch" },
        _obligation_status: {
          user: {
            read: false,
            acked: false,
            replied: false,
            reply_required: true,
          },
        },
      },
    ];
    const result = aggregateWebPetState(makeInput({ events }));
    expect(result.catState).toBe("needs_you");
    expect(result.panelData.actionItems[0].agent).toBe("peer-reviewer");
  });

  it("returns napping when group is paused (overrides everything)", () => {
    const context: GroupContext = {
      attention: {
        waiting_user: [
          {
            id: "T100",
            title: "Need approval",
            assignee: "peer-impl-1",
          },
        ],
      },
      agent_states: [makeAgentState("peer-impl-1", "T1")],
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context, groupState: "paused" })
    );
    expect(result.catState).toBe("napping");
  });

  it("does not count acked reply_required as pending", () => {
    const events: LedgerEvent[] = [
      {
        id: "evt1",
        kind: "chat.message",
        by: "peer-reviewer",
        data: { text: "Please confirm" },
        _obligation_status: {
          user: {
            read: true,
            acked: true,
            replied: false,
            reply_required: true,
          },
        },
      },
    ];
    const result = aggregateWebPetState(makeInput({ events }));
    expect(result.catState).toBe("napping");
    expect(result.panelData.actionItems).toHaveLength(0);
  });

  it("focus-only agents are not considered active", () => {
    const context: GroupContext = {
      agent_states: [
        makeAgentState("peer-impl-1", "", "stale focus text"),
        makeAgentState("peer-reviewer", "", "also stale"),
      ],
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context })
    );
    expect(result.catState).toBe("napping");
  });

  it("connection status reflects sseStatus", () => {
    const disconnected = aggregateWebPetState(
      makeInput({ sseStatus: "disconnected" })
    );
    expect(disconnected.panelData.connection.connected).toBe(false);
    expect(disconnected.panelData.connection.message).toBe("connectionDisconnected");

    const connected = aggregateWebPetState(
      makeInput({ sseStatus: "connected" })
    );
    expect(connected.panelData.connection.connected).toBe(true);
  });

  it("limits action items to top 3", () => {
    const context: GroupContext = {
      coordination: {
        tasks: Array.from({ length: 5 }, (_, i) => ({
          id: `T${i}`,
          title: `Task ${i}`,
          assignee: "someone",
          waiting_on: "user",
          status: "active",
        })),
      },
      agent_states: [],
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context })
    );
    expect(result.panelData.actionItems).toHaveLength(3);
  });

  it("populates action on waiting_user action items", () => {
    const context: GroupContext = {
      attention: {
        waiting_user: [
          { id: "T1", title: "Review PR", assignee: "peer-impl-1" },
        ],
      },
      agent_states: [],
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context, groupId: "g_abc" })
    );
    expect(result.panelData.actionItems[0].action).toEqual({
      type: "open_task",
      groupId: "g_abc",
      taskId: "T1",
    });
  });

  it("populates action on reply_required action items", () => {
    const events: LedgerEvent[] = [
      {
        id: "evt1",
        kind: "chat.message",
        by: "peer-reviewer",
        data: { text: "Please confirm" },
        _obligation_status: {
          user: { read: false, acked: false, replied: false, reply_required: true },
        },
      },
    ];
    const result = aggregateWebPetState(
      makeInput({ events, groupId: "g_xyz" })
    );
    expect(result.panelData.actionItems[0].action).toEqual({
      type: "open_chat",
      groupId: "g_xyz",
      eventId: "evt1",
    });
  });

  it("populates taskProgress from tasks_summary excluding archived", () => {
    const context: GroupContext = {
      agent_states: [],
      tasks_summary: { total: 75, done: 3, active: 21, planned: 41, archived: 10 },
    };
    const result = aggregateWebPetState(
      makeInput({ groupContext: context })
    );
    expect(result.panelData.taskProgress).toEqual({
      total: 65,
      done: 3,
      active: 21,
    });
  });

  it("taskProgress is undefined when no tasks_summary", () => {
    const result = aggregateWebPetState(makeInput());
    expect(result.panelData.taskProgress).toBeUndefined();
  });
});
