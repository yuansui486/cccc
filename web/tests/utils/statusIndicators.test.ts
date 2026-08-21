import { describe, expect, it } from "vitest";

import { getActorAccentColor } from "../../src/types";
import { getActorAccentDotClass } from "../../src/utils/statusIndicators";

describe("getActorAccentDotClass", () => {
  it.each([true, false])("keeps actor colors stable in %s theme", (isDark) => {
    const accent = getActorAccentColor("actor-1", isDark);
    const runDot = getActorAccentDotClass("actor-1", isDark, "run");
    const workingDot = getActorAccentDotClass("actor-1", isDark, "working");
    const stoppedDot = getActorAccentDotClass("actor-1", isDark, "stop");

    expect(runDot).toBe(accent.dot);
    expect(workingDot).toContain(accent.dot);
    expect(workingDot).toContain("animate-pulse");
    expect(stoppedDot).toContain(accent.mutedDot);
    expect(stoppedDot).toContain("bg-transparent");
  });

  it("maps different actors to their own stable accent dots", () => {
    const first = getActorAccentDotClass("codex-1", true, "run");
    const second = getActorAccentDotClass("codex-2", true, "run");

    expect(first).not.toBe(second);
    expect(getActorAccentDotClass("codex-1", true, "run")).toBe(first);
  });
});
