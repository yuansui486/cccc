import { describe, expect, it } from "vitest";

import { getActorTabIndicatorState } from "./TabBar";
import { QUIET_RUN_INDICATOR_DOT_CLASS } from "../utils/statusIndicators";

describe("getActorTabIndicatorState", () => {
  it("uses theme-aware accessible text colors for working and stuck tabs", () => {
    expect(getActorTabIndicatorState({ isRunning: true, workingState: "working" }).labelClass)
      .toBe("text-emerald-700 dark:text-emerald-300");
    expect(getActorTabIndicatorState({ isRunning: true, workingState: "stuck" }).labelClass)
      .toBe("text-amber-700 dark:text-amber-300");
  });

  it("does not override label color for non-accent states", () => {
    expect(getActorTabIndicatorState({ isRunning: true, workingState: "waiting" }).labelClass).toBe("");
    expect(getActorTabIndicatorState({ isRunning: true, workingState: "idle" }).labelClass).toBe("");
    expect(getActorTabIndicatorState({ isRunning: false, workingState: "working" }).labelClass).toBe("");
  });

  it("renders quiet running as a hollow ring instead of a weak solid dot", () => {
    expect(getActorTabIndicatorState({ isRunning: true, workingState: "" }).dotClass)
      .toBe(QUIET_RUN_INDICATOR_DOT_CLASS);
    expect(getActorTabIndicatorState({ isRunning: true, workingState: "" }).dotClass)
      .toContain("bg-transparent");
  });
});
