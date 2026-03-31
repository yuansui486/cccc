import { describe, expect, it } from "vitest";
import { getDefaultPetPersonaSeed } from "../../utils/rolePresets";
import {
  alignTaskDraftTaskType,
  emptyTaskDraft,
  getWaitingOnOptions,
  isVisibleContextAgent,
  petPersonaDraftDirty,
  petPersonaDraftMatches,
  resolvePetPersonaDraft,
  taskDraftDirty,
  taskDraftMatches,
  taskToDraft,
  waitingLabel,
} from "./model";

describe("ContextModal pet persona draft baseline", () => {
  it("treats the untouched pre-load state as clean", () => {
    expect(petPersonaDraftDirty("", "", { loaded: false })).toBe(false);
  });

  it("treats the default seed as clean when the saved pet block is empty", () => {
    expect(resolvePetPersonaDraft("")).toBe(getDefaultPetPersonaSeed());
    expect(petPersonaDraftMatches("", getDefaultPetPersonaSeed())).toBe(true);
    expect(petPersonaDraftDirty("", getDefaultPetPersonaSeed(), { loaded: true })).toBe(false);
  });

  it("marks the draft dirty after the user changes the seeded content", () => {
    expect(petPersonaDraftMatches("", `${getDefaultPetPersonaSeed()}\nextra rule`)).toBe(false);
    expect(petPersonaDraftDirty("", `${getDefaultPetPersonaSeed()}\nextra rule`, { loaded: true })).toBe(true);
  });

  it("uses the saved pet note as the clean baseline when one exists", () => {
    expect(petPersonaDraftMatches("Keep it terse.", "Keep it terse.")).toBe(true);
    expect(petPersonaDraftMatches("Keep it terse.", "Keep it terse.\nMore detail")).toBe(false);
  });
});

describe("ContextModal waiting_on labels", () => {
  const tr = (key: string, fallback: string) => `tx:${key}:${fallback}`;

  it("builds waiting_on options from the translator", () => {
    expect(getWaitingOnOptions(tr).map((item) => item.label)).toEqual([
      "tx:context.none:None",
      "tx:context.waitingOnUser:Waiting on user",
      "tx:context.waitingOnActor:Waiting on agent",
      "tx:context.waitingOnExternal:Waiting on external",
    ]);
  });

  it("formats waiting_on labels through the translator", () => {
    expect(waitingLabel("user", tr)).toBe("tx:context.waitingOnUser:Waiting on user");
    expect(waitingLabel("", tr)).toBe("tx:context.none:None");
  });
});

describe("ContextModal task draft task type", () => {
  it("hydrates draft task type from the persisted task field", () => {
    const draft = taskToDraft({
      id: "T010",
      title: "Optimize startup",
      status: "active",
      task_type: "optimization",
      notes: "Custom note only",
      checklist: [],
    });

    expect(draft.taskType).toBe("optimization");
  });

  it("falls back to the structural default when no persisted task type exists", () => {
    const task = {
      id: "T011",
      title: "Optimize startup",
      status: "active",
      notes: "Baseline:\n- 410 ms",
      checklist: [],
    };
    const draft = taskToDraft(task);

    expect(draft.taskType).toBe("standard");
    expect(taskDraftMatches(task, draft)).toBe(true);
  });

  it("marks task-type-only changes as dirty for new drafts", () => {
    const draft = {
      ...emptyTaskDraft("planned"),
      taskType: "free" as const,
    };

    expect(taskDraftDirty(draft)).toBe(true);
  });

  it("only re-aligns the untouched structure-default type when parent shape changes", () => {
    expect(alignTaskDraftTaskType("standard", "T001", "")).toBe("free");
    expect(alignTaskDraftTaskType("free", "", "T001")).toBe("standard");
    expect(alignTaskDraftTaskType("optimization", "T001", "")).toBe("optimization");
    expect(alignTaskDraftTaskType("free", "T001", "")).toBe("free");
    expect(alignTaskDraftTaskType("optimization", "")).toBe("optimization");
  });
});

describe("ContextModal visible agents", () => {
  it("hides pet-peer from the default agents view", () => {
    expect(isVisibleContextAgent({ id: "pet-peer" })).toBe(false);
    expect(isVisibleContextAgent({ id: "foreman-1" })).toBe(true);
  });
});
