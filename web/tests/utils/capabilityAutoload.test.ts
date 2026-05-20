import { describe, expect, it } from "vitest";

import { mergeInheritedCapabilityAutoload } from "../../src/utils/capabilityAutoload";

describe("capability autoload helpers", () => {
  it("shows group enabled and inherited autoload capabilities without duplicating actor-owned autoload", () => {
    expect(
      mergeInheritedCapabilityAutoload({
        own: ["skill:team:actor-only", "skill:team:already-owned"],
        actorStateAutoload: ["skill:team:actor-only", "skill:team:already-owned"],
        effectiveAutoload: ["skill:team:group-default", "skill:team:already-owned", "skill:team:profile-default"],
        groupAutoload: ["skill:team:group-default"],
        profileAutoload: ["skill:team:profile-default"],
        enabled: [
          { capability_id: "skill:team:enabled-for-group", scope: "group" },
          { capability_id: "skill:team:actor-enabled", scope: "actor" },
        ],
      }),
    ).toEqual(["skill:team:enabled-for-group", "skill:team:group-default", "skill:team:profile-default"]);
  });

  it("falls back to explicit group/profile autoload fields when effective autoload is unavailable", () => {
    expect(
      mergeInheritedCapabilityAutoload({
        own: ["skill:team:actor-only"],
        actorStateAutoload: ["skill:team:actor-only"],
        groupAutoload: ["skill:team:group-default"],
        profileAutoload: ["skill:team:profile-default"],
      }),
    ).toEqual(["skill:team:group-default", "skill:team:profile-default"]);
  });
});
