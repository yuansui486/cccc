import { describe, expect, it } from "vitest";
import { shouldRefreshGroupsAfterGlobalEventsOpen } from "../../src/hooks/useGlobalEvents";

describe("useGlobalEvents open refresh policy", () => {
  it("requires catch-up refresh on the first successful open", () => {
    expect(shouldRefreshGroupsAfterGlobalEventsOpen(false)).toBe(true);
  });

  it("requires catch-up refresh on reconnects too", () => {
    expect(shouldRefreshGroupsAfterGlobalEventsOpen(true)).toBe(true);
  });
});
