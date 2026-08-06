import { describe, expect, it } from "vitest";

import {
  beginActionRequestEpoch,
  isLatestActionRequestEpoch,
} from "../../src/hooks/actionRequestEpoch";
import { beginActorAction, endActorAction } from "../../src/hooks/actorActionInFlight";

describe("actor action in-flight guard", () => {
  it("rejects duplicate lifecycle actions for the same actor until released", () => {
    const inFlight = { current: new Set<string>() };

    expect(beginActorAction(inFlight, "actor-lifecycle:peer1")).toBe(true);
    expect(beginActorAction(inFlight, "actor-lifecycle:peer1")).toBe(false);

    endActorAction(inFlight, "actor-lifecycle:peer1");

    expect(beginActorAction(inFlight, "actor-lifecycle:peer1")).toBe(true);
  });
});

describe("action request epochs", () => {
  it("invalidates older requests for the same target without affecting another target", () => {
    const epochs = { current: new Map<string, number>() };
    const groupAFirst = beginActionRequestEpoch(epochs, "group:g-a");
    const groupBFirst = beginActionRequestEpoch(epochs, "group:g-b");
    const groupASecond = beginActionRequestEpoch(epochs, "group:g-a");

    expect(isLatestActionRequestEpoch(epochs, "group:g-a", groupAFirst)).toBe(false);
    expect(isLatestActionRequestEpoch(epochs, "group:g-a", groupASecond)).toBe(true);
    expect(isLatestActionRequestEpoch(epochs, "group:g-b", groupBFirst)).toBe(true);
  });

  it("keeps identical actor ids isolated by group-qualified action keys", () => {
    const epochs = { current: new Map<string, number>() };
    const actorA = beginActionRequestEpoch(epochs, "actor-lifecycle:g-a:opencode-1");
    const actorB = beginActionRequestEpoch(epochs, "actor-lifecycle:g-b:opencode-1");

    expect(isLatestActionRequestEpoch(epochs, "actor-lifecycle:g-a:opencode-1", actorA)).toBe(true);
    expect(isLatestActionRequestEpoch(epochs, "actor-lifecycle:g-b:opencode-1", actorB)).toBe(true);
  });
});
