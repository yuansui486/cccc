import { describe, expect, it } from "vitest";

import { getLatestPetContextRefreshMarker, shouldRefreshPetContextFromEvent } from "../../../src/features/webPet/petContextRefresh";

describe("shouldRefreshPetContextFromEvent", () => {
  it("refreshes on pet decision lifecycle events", () => {
    expect(
      shouldRefreshPetContextFromEvent({
        id: "evt-1",
        kind: "pet.decisions.replace",
        ts: "",
        by: "pet-peer",
        data: {},
      }),
    ).toBe(true);

    expect(
      shouldRefreshPetContextFromEvent({
        id: "evt-2",
        kind: "pet.decisions.clear",
        ts: "",
        by: "pet-peer",
        data: {},
      }),
    ).toBe(true);

    expect(
      shouldRefreshPetContextFromEvent({
        id: "evt-3",
        kind: "pet.decision.outcome",
        ts: "",
        by: "user",
        data: {},
      }),
    ).toBe(true);
  });

  it("ignores unrelated group chatter", () => {
    expect(
      shouldRefreshPetContextFromEvent({
        id: "evt-4",
        kind: "chat.message",
        ts: "",
        by: "peer",
        data: {},
      }),
    ).toBe(false);
  });
});

describe("getLatestPetContextRefreshMarker", () => {
  it("picks the newest pet refresh marker from the tail", () => {
    expect(
      getLatestPetContextRefreshMarker([
        { id: "evt-1", kind: "chat.message", ts: "", by: "user", data: {} },
        { id: "evt-2", kind: "pet.decisions.replace", ts: "", by: "pet-peer", data: {} },
        { id: "evt-3", kind: "system.notify", ts: "", by: "system", data: {} },
        { id: "evt-4", kind: "pet.decision.outcome", ts: "", by: "user", data: {} },
      ]),
    ).toBe("pet.decision.outcome:evt-4");
  });

  it("returns empty marker when nothing pet-relevant happened", () => {
    expect(
      getLatestPetContextRefreshMarker([
        { id: "evt-1", kind: "chat.message", ts: "", by: "user", data: {} },
        { id: "evt-2", kind: "system.notify", ts: "", by: "system", data: {} },
      ]),
    ).toBe("");
  });
});
