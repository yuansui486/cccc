import { describe, expect, it } from "vitest";

import { buildLocalTaskProposalReminders } from "../../../src/features/webPet/localTaskAdvisor";
import { buildTaskProposalMessage } from "../../../src/features/webPet/taskProposal";
import { resetTaskAdvisorHistory } from "../../../src/features/webPet/taskAdvisor/history";

describe("buildLocalTaskProposalReminders", () => {
  it("suggests moving a mounted task to active when it is still planned", () => {
    resetTaskAdvisorHistory();
    const reminders = buildLocalTaskProposalReminders("g-1", {
      agent_states: [
        {
          id: "peer-1",
          hot: {
            active_task_id: "T-42",
            focus: "Review launch scope",
          },
        },
      ],
      coordination: {
        tasks: [
          {
            id: "T-42",
            title: "Review launch scope",
            status: "planned",
          },
        ],
      },
    });

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:move-active");
    expect(reminders[0]?.action.type).toBe("task_proposal");
    if (reminders[0]?.action.type === "task_proposal") {
      expect(reminders[0].action.reason?.kind).toBe("move_active");
      expect(reminders[0].action.assignee).toBeUndefined();
      expect(reminders[0].action.status).toBe("active");
    }
  });

  it("suggests syncing waiting_on=user when focus indicates a user dependency", () => {
    resetTaskAdvisorHistory();
    const reminders = buildLocalTaskProposalReminders("g-1", {
      agent_states: [
        {
          id: "peer-1",
          hot: {
            active_task_id: "T-42",
            focus: "waiting_user: clarify launch scope with user",
          },
        },
      ],
      coordination: {
        tasks: [
          {
            id: "T-42",
            title: "Review launch scope",
            status: "active",
          },
        ],
      },
    });

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:waiting-user");
    if (reminders[0]?.action.type === "task_proposal") {
      expect(reminders[0].action.operation).toBe("update");
      expect(buildTaskProposalMessage(reminders[0].action)).toContain("waiting_on=user");
    }
  });

  it("escalates waiting_user when the task stays unsynced across multiple cycles", () => {
    resetTaskAdvisorHistory();
    const originalNow = Date.now;
    const baseContext = {
      agent_states: [
        {
          id: "peer-1",
          hot: {
            active_task_id: "T-42",
            focus: "waiting_user: clarify launch scope with user",
          },
        },
      ],
      coordination: {
        tasks: [
          {
            id: "T-42",
            title: "Review launch scope",
            status: "active",
            updated_at: "2026-03-31T14:30:00.000Z",
          },
        ],
      },
    };
    try {
      Date.now = () => Date.parse("2026-03-31T14:40:00.000Z");
      buildLocalTaskProposalReminders("g-1", baseContext);
      Date.now = () => Date.parse("2026-03-31T14:52:00.000Z");
      const reminders = buildLocalTaskProposalReminders("g-1", baseContext);
      expect(reminders).toHaveLength(1);
      expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:waiting-user-stale");
      if (reminders[0]?.action.type === "task_proposal") {
        expect(reminders[0].action.operation).toBe("move");
        expect(buildTaskProposalMessage(reminders[0].action)).toContain("waiting_on=user");
      }
    } finally {
      Date.now = originalNow;
      resetTaskAdvisorHistory();
    }
  });

  it("suggests syncing blockers when actor reports blockers but task has no blocked metadata", () => {
    resetTaskAdvisorHistory();
    const reminders = buildLocalTaskProposalReminders("g-1", {
      agent_states: [
        {
          id: "peer-1",
          hot: {
            active_task_id: "T-42",
            blockers: ["need API key", "waiting sandbox sample"],
          },
        },
      ],
      coordination: {
        tasks: [
          {
            id: "T-42",
            title: "Review launch scope",
            status: "active",
          },
        ],
      },
    });

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:blocked-sync");
    if (reminders[0]?.action.type === "task_proposal") {
      const message = buildTaskProposalMessage(reminders[0].action);
      expect(message).toContain("blockers");
      expect(message).toContain("need API key");
    }
  });

  it("keeps the highest-priority rule per task", () => {
    resetTaskAdvisorHistory();
    const reminders = buildLocalTaskProposalReminders("g-1", {
      agent_states: [
        {
          id: "peer-1",
          hot: {
            active_task_id: "T-42",
            focus: "waiting_user",
            blockers: ["need API key"],
          },
        },
      ],
      coordination: {
        tasks: [
          {
            id: "T-42",
            title: "Review launch scope",
            status: "active",
          },
        ],
      },
    });

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:waiting-user");
  });

  it("suggests re-triaging a task that stays active too long without progress", () => {
    resetTaskAdvisorHistory();
    const originalNow = Date.now;

    const baseContext = {
      agent_states: [
        {
          id: "peer-1",
          hot: {
            active_task_id: "T-42",
            focus: "Investigate launch regression",
          },
        },
      ],
      coordination: {
        tasks: [
          {
            id: "T-42",
            title: "Investigate launch regression",
            status: "active",
            assignee: "peer-1",
            updated_at: "2026-03-31T14:30:00.000Z",
          },
        ],
      },
    };
    try {
      Date.now = () => Date.parse("2026-03-31T14:38:00.000Z");
      buildLocalTaskProposalReminders("g-1", baseContext);

      buildLocalTaskProposalReminders("g-1", baseContext);
      Date.now = () => Date.parse("2026-03-31T15:00:00.000Z");
      const reminders = buildLocalTaskProposalReminders("g-1", baseContext);
      expect(reminders).toHaveLength(1);
      expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:stalled-active");
      if (reminders[0]?.action.type === "task_proposal") {
        expect(reminders[0].action.operation).toBe("handoff");
        expect(reminders[0].action.reason?.kind).toBe("stalled_active_task");
        expect(buildTaskProposalMessage(reminders[0].action)).toContain("without recent task changes");
      }
    } finally {
      Date.now = originalNow;
      resetTaskAdvisorHistory();
    }
  });

  it("suggests re-confirming owner when task ownership drifts away from the actor's mounted task", () => {
    resetTaskAdvisorHistory();
    const originalNow = Date.now;
    try {
      Date.now = () => Date.parse("2026-03-31T15:00:00.000Z");
      const reminders = buildLocalTaskProposalReminders("g-1", {
        agent_states: [
          {
            id: "peer-1",
            hot: {
              active_task_id: "T-99",
              focus: "Investigate new regression",
            },
          },
        ],
        coordination: {
          tasks: [
            {
              id: "T-42",
              title: "Old active task",
              status: "active",
              assignee: "peer-1",
              updated_at: "2026-03-31T14:30:00.000Z",
            },
            {
              id: "T-99",
              title: "New regression",
              status: "active",
              assignee: "peer-1",
              updated_at: "2026-03-31T14:58:00.000Z",
            },
          ],
        },
      });

      expect(reminders).toHaveLength(1);
      expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:ownership-drift");
      if (reminders[0]?.action.type === "task_proposal") {
        expect(reminders[0].action.operation).toBe("update");
        expect(reminders[0].action.reason?.kind).toBe("ownership_drift");
        const message = buildTaskProposalMessage(reminders[0].action);
        expect(message).toContain("T-99");
        expect(message).toContain("owner and task status");
      }
    } finally {
      Date.now = originalNow;
      resetTaskAdvisorHistory();
    }
  });

  it("suggests assigning an owner when an active mounted task has no assignee", () => {
    resetTaskAdvisorHistory();
    const reminders = buildLocalTaskProposalReminders("g-1", {
      agent_states: [
        {
          id: "peer-1",
          hot: {
            active_task_id: "T-42",
            focus: "Review launch scope",
          },
        },
      ],
      coordination: {
        tasks: [
          {
            id: "T-42",
            title: "Review launch scope",
            status: "active",
          },
        ],
      },
    });

    expect(reminders).toHaveLength(1);
    expect(reminders[0]?.fingerprint).toBe("local-task-proposal:g-1:T-42:assign-owner");
    if (reminders[0]?.action.type === "task_proposal") {
      expect(reminders[0].action.assignee).toBe("peer-1");
      expect(reminders[0].action.reason?.kind).toBe("assign_active_owner");
      expect(buildTaskProposalMessage(reminders[0].action)).toContain("currently has no owner");
    }
  });
});
