import { describe, expect, it } from "vitest";
import { getGroupControlVisual, getLaunchControlMode } from "./groupControls";

describe("groupControls", () => {
  it("treats idle groups as activatable from the launch control", () => {
    expect(getLaunchControlMode("idle")).toBe("activate");
  });

  it("keeps the launch control visually active for running idle groups", () => {
    const visual = getGroupControlVisual("idle", "launch", "");
    expect(visual.active).toBe(true);
    expect(visual.pending).toBe(false);
    expect(visual.className).toContain("bg-emerald-700");
  });

  it("keeps pause active only for paused groups", () => {
    expect(getGroupControlVisual("idle", "pause", "").active).toBe(false);
    expect(getGroupControlVisual("paused", "pause", "").active).toBe(true);
  });
});
