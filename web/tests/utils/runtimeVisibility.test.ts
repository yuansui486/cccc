import { describe, expect, it } from "vitest";
import {
  filterVisibleRuntimeActors,
  isPetRuntimeActor,
  isRuntimeSurfaceActorVisible,
} from "../../src/utils/runtimeVisibility";

describe("runtimeVisibility", () => {
  const foreman = { id: "foreman", role: "foreman" };
  const peer = { id: "peer-1", role: "peer" };
  const pet = { id: "pet-peer", internal_kind: "pet" };

  it("detects pet runtime actors from internal kind or reserved id", () => {
    expect(isPetRuntimeActor(pet)).toBe(true);
    expect(isPetRuntimeActor({ id: "pet-peer" })).toBe(true);
    expect(isPetRuntimeActor(peer)).toBe(false);
  });

  it("applies separate visibility modes for standard and pet runtimes", () => {
    expect(
      isRuntimeSurfaceActorVisible(peer, {
        peerRuntimeVisibility: "visible",
        petRuntimeVisibility: "hidden",
      })
    ).toBe(true);
    expect(
      isRuntimeSurfaceActorVisible(peer, {
        peerRuntimeVisibility: "hidden",
        petRuntimeVisibility: "visible",
      })
    ).toBe(false);
    expect(
      isRuntimeSurfaceActorVisible(pet, {
        peerRuntimeVisibility: "visible",
        petRuntimeVisibility: "hidden",
      })
    ).toBe(false);
    expect(
      isRuntimeSurfaceActorVisible(pet, {
        peerRuntimeVisibility: "hidden",
        petRuntimeVisibility: "visible",
      })
    ).toBe(true);
  });

  it("filters runtime actors without hiding actor identity elsewhere", () => {
    expect(
      filterVisibleRuntimeActors([foreman, peer, pet], {
        peerRuntimeVisibility: "hidden",
        petRuntimeVisibility: "visible",
      }).map((actor) => actor.id)
    ).toEqual(["pet-peer"]);
  });
});
