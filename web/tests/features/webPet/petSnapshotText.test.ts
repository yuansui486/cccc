import { describe, expect, it } from "vitest";
import { formatPetSnapshot, getPetSnapshotHeadline } from "../../../src/features/webPet/petSnapshotText";

function makeTranslator(prefix = "ja") {
  return (key: string, fallback: string, vars?: Record<string, unknown>) => {
    switch (key) {
      case "snapshot.group":
        return `${prefix}:group:${String(vars?.value || "")}`;
      case "snapshot.groupState":
        return `${prefix}:state:${String(vars?.value || "")}`;
      case "snapshot.tasks":
        return `${prefix}:tasks:${vars?.total}/${vars?.active}/${vars?.done}/${vars?.archived}`;
      case "snapshot.agentSnapshot":
        return `${prefix}:agents:${String(vars?.value || "")}`;
      case "snapshot.blockedTasks":
        return `${prefix}:blocked:${String(vars?.value || "")}`;
      case "snapshot.waitingUserTasks":
        return `${prefix}:waiting-user:${String(vars?.value || "")}`;
      case "snapshot.handoffTasks":
        return `${prefix}:handoff:${String(vars?.value || "")}`;
      case "snapshot.plannedBacklog":
        return `${prefix}:planned:${String(vars?.value || "")}`;
      case "snapshot.taskProposals":
        return `${prefix}:proposals:${String(vars?.value || "")}`;
      default:
        return fallback;
    }
  };
}

describe("petSnapshotText", () => {
  it("formats known snapshot lines through i18n", () => {
    const tr = makeTranslator();
    const formatted = formatPetSnapshot(
      [
        "Group: Demo Team",
        "Group State: active",
        "Tasks: total=12, active=3, done=5, archived=4",
        "Agent Snapshot: foreman: T1 | close loop",
        "Blocked Tasks: T101:fix send latency @peer-debugger",
        "Waiting User Tasks: T102:clarify launch scope",
        "Handoff Tasks: T103:review pet reminders @foreman",
        "Planned Backlog: T104:trim stale tasks",
        "Task Proposals: T102:clarify launch scope is waiting on user ; T103:review pet reminders should be picked up by foreman",
      ].join("\n"),
      tr,
    );

    expect(formatted).toBe(
      [
        "ja:group:Demo Team",
        "ja:state:active",
        "ja:tasks:12/3/5/4",
        "ja:agents:foreman: T1 | close loop",
        "ja:blocked:T101:fix send latency @peer-debugger",
        "ja:waiting-user:T102:clarify launch scope",
        "ja:handoff:T103:review pet reminders @foreman",
        "ja:planned:T104:trim stale tasks",
        "ja:proposals:T102:clarify launch scope is waiting on user ; T103:review pet reminders should be picked up by foreman",
      ].join("\n"),
    );
  });

  it("keeps unknown lines as-is and exposes a localized headline", () => {
    const tr = makeTranslator("zh");
    expect(getPetSnapshotHeadline("Group: Demo Team\ncustom line", tr)).toBe(
      "zh:group:Demo Team",
    );
    expect(formatPetSnapshot("custom line", tr)).toBe("custom line");
  });
});
