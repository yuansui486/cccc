import { describe, expect, it } from "vitest";

import { aggregateWebPetState } from "../../../src/features/webPet/aggregateWebPetState";
import type { AggregateInput } from "../../../src/features/webPet/aggregateWebPetState";
import type { AgentState, GroupContext, LedgerEvent } from "../../../src/types";

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

function makeAgentState(id: string, activeTaskId: string, focus = ""): AgentState {
  return {
    id,
    hot: { active_task_id: activeTaskId || null, focus },
  };
}

describe("aggregateWebPetState", () => {
  it("returns napping when no activity", () => {
    const result = aggregateWebPetState(makeInput());
    expect(result.catState).toBe("napping");
  });

  it("returns working when single agent is active", () => {
    const context: GroupContext = {
      agent_states: [makeAgentState("peer-impl-1", "T1", "implementing")],
    };
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
    expect(result.catState).toBe("working");
    expect(result.panelData.agents[0].state).toBe("working");
  });

  it("prefers actors_runtime effective working state over active task heuristics", () => {
    const context: GroupContext = {
      agent_states: [makeAgentState("peer-impl-1", "", "implementing")],
      actors_runtime: [
        {
          id: "peer-impl-1",
          effective_working_state: "working",
        },
      ],
    };
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
    expect(result.catState).toBe("working");
    expect(result.panelData.agents[0].state).toBe("working");
  });

  it("includes runtime-only actors in panel state", () => {
    const context: GroupContext = {
      actors_runtime: [
        {
          id: "peer-impl-1",
          effective_working_state: "working",
        },
      ],
    };
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
    expect(result.catState).toBe("working");
    expect(result.panelData.agents).toHaveLength(1);
    expect(result.panelData.agents[0].id).toBe("peer-impl-1");
  });

  it("returns busy when multiple agents are active", () => {
    const context: GroupContext = {
      agent_states: [
        makeAgentState("peer-impl-1", "T1"),
        makeAgentState("peer-impl-2", "T2"),
      ],
    };
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
    expect(result.catState).toBe("busy");
    expect(result.panelData.agents.every((a) => a.state === "busy")).toBe(true);
  });

  it("does not surface waiting_user tasks as pet action items", () => {
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
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
    expect(result.catState).toBe("napping");
  });

  it("does not promote reply_required events into pet state", () => {
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
    expect(result.catState).toBe("napping");
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
    const result = aggregateWebPetState(makeInput({ groupContext: context, groupState: "paused" }));
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
  });

  it("focus-only agents are not considered active", () => {
    const context: GroupContext = {
      agent_states: [
        makeAgentState("peer-impl-1", "", "stale focus text"),
        makeAgentState("peer-reviewer", "", "also stale"),
      ],
    };
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
    expect(result.catState).toBe("napping");
  });

  it("connection status reflects sseStatus", () => {
    const disconnected = aggregateWebPetState(makeInput({ sseStatus: "disconnected" }));
    expect(disconnected.panelData.connection.connected).toBe(false);
    expect(disconnected.panelData.connection.message).toBe("connectionDisconnected");

    const connected = aggregateWebPetState(makeInput({ sseStatus: "connected" }));
    expect(connected.panelData.connection.connected).toBe(true);
  });

  it("does not project waiting_user action items into the panel", () => {
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
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
    expect(result.panelData.agents).toHaveLength(0);
  });

  it("does not populate waiting_user actions in the panel", () => {
    const context: GroupContext = {
      attention: {
        waiting_user: [{ id: "T1", title: "Review PR", assignee: "peer-impl-1" }],
      },
      agent_states: [],
    };
    const result = aggregateWebPetState(makeInput({ groupContext: context, groupId: "g_abc" }));
    expect(result.panelData.agents).toHaveLength(0);
  });

  it("ignores reply_required events for panel content", () => {
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
    const result = aggregateWebPetState(makeInput({ events, groupId: "g_xyz" }));
    expect(result.panelData.agents).toHaveLength(0);
  });

  it("populates taskProgress from tasks_summary excluding archived", () => {
    const context: GroupContext = {
      agent_states: [],
      tasks_summary: { total: 75, done: 3, active: 21, planned: 41, archived: 10 },
    };
    const result = aggregateWebPetState(makeInput({ groupContext: context }));
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
