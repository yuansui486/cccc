import { describe, expect, it } from "vitest";
import {
  createTrigger,
  normalizeTrigger,
  normalizeTriggerRuntime,
  serializeTrigger,
} from "../src/pages/computerControl/triggerTypes";

describe("computer control trigger drafts", () => {
  it("uses practical interval and element polling defaults", () => {
    expect(createTrigger("interval").intervalSeconds).toBe(300);
    expect(createTrigger("element").pollIntervalSeconds).toBe(2);
  });

  it("clamps interval serialization to one second", () => {
    const trigger = createTrigger("interval");
    trigger.intervalSeconds = 0;
    expect(serializeTrigger(trigger)).toMatchObject({
      type: "interval",
      config: { seconds: 1 },
    });
  });

  it("persists schedule and at as their native trigger types", () => {
    const schedule = createTrigger("schedule");
    schedule.scheduleMode = "weekly";
    schedule.time = "08:35";
    schedule.weekdays = [0, 4];
    expect(serializeTrigger(schedule)).toMatchObject({
      type: "schedule",
      config: { time: "08:35", weekdays: [0, 4] },
    });

    const at = createTrigger("at");
    at.runAt = "2030-03-04T05:06";
    const persisted = serializeTrigger(at) as { type: string; config: Record<string, unknown> };
    expect(persisted.type).toBe("at");
    expect(persisted.config.at).toBe(new Date("2030-03-04T05:06").toISOString());
    expect(persisted.config).not.toHaveProperty("run_at");
  });

  it("prefers persisted name and accepts legacy title", () => {
    expect(normalizeTrigger({ id: "named", name: "新名称", title: "旧标题" }, 0).title).toBe("新名称");
    expect(normalizeTrigger({ id: "legacy", title: "兼容标题" }, 0).title).toBe("兼容标题");
  });

  it("clamps element polling and only preserves appearance-edge triggering", () => {
    const normalized = normalizeTrigger({
      id: "element",
      type: "element",
      config: { poll_seconds: 0.1, condition: "focused", locator: { strategy: "uia", name: "提交" } },
    }, 0);
    expect(normalized.pollIntervalSeconds).toBe(0.5);
    expect(normalized.elementCondition).toBe("next_appear");
    expect(serializeTrigger(normalized)).toMatchObject({
      type: "element",
      config: { poll_seconds: 0.5, condition: "next_appear" },
    });
  });
});

describe("computer control trigger runtime status", () => {
  it("normalizes scheduler timestamps, pending state, run id, and errors", () => {
    expect(normalizeTriggerRuntime({
      pending_changes: true,
      pending_reason: "workflow_not_trusted",
      scheduler: {
        state: "pending",
        next_check_at: 100,
        next_fire_at: 200,
        last_checked_at: 50,
        last_fired_at: 75,
        last_run_id: "run_123",
        last_error: "lease unavailable",
      },
    })).toEqual({
      state: "pending",
      nextCheckAt: 100,
      nextFireAt: 200,
      lastCheckedAt: 50,
      lastFiredAt: 75,
      pending: true,
      pendingReason: "workflow_not_trusted",
      lastRunId: "run_123",
      error: "lease unavailable",
    });
  });

  it("maps persisted scheduler due and poll timestamps", () => {
    expect(normalizeTriggerRuntime({
      scheduler: {
        next_poll_at: 300,
        next_due_at: 400,
      },
    })).toMatchObject({
      nextCheckAt: 300,
      nextFireAt: 400,
    });
  });
});
