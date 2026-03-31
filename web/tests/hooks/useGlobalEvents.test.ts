import { describe, expect, it } from "vitest";
import {
  getGlobalEventGroupId,
  shouldRefreshActorsAfterGlobalEvent,
  shouldRefreshGroupsAfterGlobalEventsOpen,
} from "../../src/hooks/useGlobalEvents";

describe("useGlobalEvents open refresh policy", () => {
  it("requires catch-up refresh on the first successful open", () => {
    expect(shouldRefreshGroupsAfterGlobalEventsOpen(false)).toBe(true);
  });

  it("requires catch-up refresh on reconnects too", () => {
    expect(shouldRefreshGroupsAfterGlobalEventsOpen(true)).toBe(true);
  });

  it("extracts group id from top-level global events", () => {
    expect(getGlobalEventGroupId({ kind: "actor.stop", group_id: "g-demo" })).toBe("g-demo");
  });

  it("extracts group id from nested event data as a fallback", () => {
    expect(getGlobalEventGroupId({ kind: "group.state_changed", data: { group_id: "g-demo" } })).toBe("g-demo");
  });

  it("refreshes selected actors for matching lifecycle events", () => {
    expect(
      shouldRefreshActorsAfterGlobalEvent(
        { kind: "actor.stop", group_id: "g-demo", data: { actor_id: "peer-1" } },
        "g-demo",
      ),
    ).toBe(true);
  });

  it("ignores lifecycle events for other groups", () => {
    expect(
      shouldRefreshActorsAfterGlobalEvent(
        { kind: "actor.stop", group_id: "g-other", data: { actor_id: "peer-1" } },
        "g-demo",
      ),
    ).toBe(false);
  });

  it("ignores non-lifecycle global events for actor refresh", () => {
    expect(
      shouldRefreshActorsAfterGlobalEvent(
        { kind: "group.updated", group_id: "g-demo" },
        "g-demo",
      ),
    ).toBe(false);
  });
});
