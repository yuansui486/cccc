import { describe, expect, it } from "vitest";
import { getBackgroundRefreshDelayMs } from "../../../src/features/webPet/reviewTiming";
import { isManualReviewReminderReady } from "../../../src/features/webPet/manualReviewReminder";
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

describe("getBackgroundRefreshDelayMs", () => {
  it("uses the base interval for success and backs off exponentially on failures", () => {
    expect(getBackgroundRefreshDelayMs(0)).toBe(30_000);
    expect(getBackgroundRefreshDelayMs(1)).toBe(60_000);
    expect(getBackgroundRefreshDelayMs(2)).toBe(120_000);
  });

  it("caps the retry delay at five minutes", () => {
    expect(getBackgroundRefreshDelayMs(10)).toBe(300_000);
  });
});

describe("isManualReviewReminderReady", () => {
  it("keeps actionable reminders when group is active", () => {
    expect(isManualReviewReminderReady(makeReminder(), "active")).toBe(true);
  });

  it("suppresses draft reminders when group is idle", () => {
    expect(isManualReviewReminderReady(makeReminder(), "idle")).toBe(false);
  });

  it("still treats restart reminders as ready while idle", () => {
    expect(
      isManualReviewReminderReady(
        makeReminder({
          kind: "actor_down",
          action: {
            type: "restart_actor",
            groupId: "g-1",
            actorId: "peer-1",
          },
        }),
        "idle",
      ),
    ).toBe(true);
  });
});
