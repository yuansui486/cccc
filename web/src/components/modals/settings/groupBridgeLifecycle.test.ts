import { describe, expect, it } from "vitest";

import {
  GroupBridgeLifecycle,
  isGroupBridgeViewCurrent,
  normalizeGroupBridgeGroupId,
} from "./groupBridgeLifecycle";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("GroupBridgeLifecycle", () => {
  it("does not revoke the committed generation during an aborted render", () => {
    const lifecycle = new GroupBridgeLifecycle();
    lifecycle.selectGroup("group-a");
    const actionA = lifecycle.beginAction("group-a");
    expect(actionA).not.toBeNull();

    const renderedGroupId = normalizeGroupBridgeGroupId("group-b");
    expect(isGroupBridgeViewCurrent(renderedGroupId, "group-a")).toBe(false);
    expect(lifecycle.isCurrent(actionA!)).toBe(true);

    lifecycle.selectGroup(renderedGroupId);
    expect(lifecycle.isCurrent(actionA!)).toBe(false);
  });

  it("hides the old view and rejects its late load after the next group resolves", async () => {
    const lifecycle = new GroupBridgeLifecycle();
    lifecycle.selectGroup("group-a");
    const loadA = lifecycle.beginLoad("group-a");
    expect(loadA).not.toBeNull();
    const state = { groupId: "group-a", identity: "identity-a" };
    const responseA = deferred<string>();
    const completionA = responseA.promise.then((identity) => lifecycle.commit(loadA!, () => {
      state.groupId = "group-a";
      state.identity = identity;
    }));

    lifecycle.selectGroup("group-b");
    expect(lifecycle.isViewCurrent(state.groupId)).toBe(false);
    const loadB = lifecycle.beginLoad("group-b");
    expect(loadB).not.toBeNull();
    const responseB = deferred<string>();
    const completionB = responseB.promise.then((identity) => lifecycle.commit(loadB!, () => {
      state.groupId = "group-b";
      state.identity = identity;
    }));

    responseB.resolve("identity-b");
    expect(await completionB).toBe(true);
    expect(lifecycle.finish(loadB!)).toBe(true);
    responseA.resolve("late-identity-a");
    expect(await completionA).toBe(false);
    expect(state).toEqual({ groupId: "group-b", identity: "identity-b" });
  });

  it("rejects stale actions and blocks mutations during the current load or action", async () => {
    const lifecycle = new GroupBridgeLifecycle();
    lifecycle.selectGroup("group-a");

    const actionA = lifecycle.beginAction("group-a");
    expect(actionA).not.toBeNull();
    const actionResponse = deferred<string>();
    const notices: string[] = [];
    const actionCompletion = actionResponse.promise.then((notice) => lifecycle.commit(actionA!, () => notices.push(notice)));
    expect(lifecycle.canMutate("group-a")).toBe(false);
    expect(lifecycle.beginAction("group-a")).toBeNull();

    lifecycle.selectGroup("group-b");
    expect(lifecycle.beginAction("group-a")).toBeNull();

    const loadB = lifecycle.beginLoad("group-b");
    expect(loadB).not.toBeNull();
    expect(lifecycle.canMutate("group-b")).toBe(false);
    expect(lifecycle.beginAction("group-b")).toBeNull();
    expect(lifecycle.finish(loadB!)).toBe(true);
    expect(lifecycle.canMutate("group-b")).toBe(true);
    actionResponse.resolve("late-action-a");
    expect(await actionCompletion).toBe(false);
    expect(notices).toEqual([]);
  });

  it("lets only the latest same-generation refresh commit", async () => {
    const lifecycle = new GroupBridgeLifecycle();
    lifecycle.selectGroup("group-a");
    const initialLoad = lifecycle.beginLoad("group-a");
    const manualRefresh = lifecycle.beginLoad("group-a");
    const values: string[] = [];
    const initialResponse = deferred<string>();
    const refreshResponse = deferred<string>();
    const initialCompletion = initialResponse.promise.then((value) => lifecycle.commit(initialLoad!, () => values.push(value)));
    const refreshCompletion = refreshResponse.promise.then((value) => lifecycle.commit(manualRefresh!, () => values.push(value)));

    refreshResponse.resolve("refresh");
    expect(await refreshCompletion).toBe(true);
    expect(lifecycle.finish(manualRefresh!)).toBe(true);
    initialResponse.resolve("initial");
    expect(await initialCompletion).toBe(false);
    expect(values).toEqual(["refresh"]);
  });
});
