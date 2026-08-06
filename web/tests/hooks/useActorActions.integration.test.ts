import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../src/services/api", () => ({
  fetchActors: vi.fn(),
  fetchGroups: vi.fn(),
  startActor: vi.fn(),
  stopActor: vi.fn(),
  restartActor: vi.fn(),
  removeActor: vi.fn(),
  fetchInbox: vi.fn(),
}));

import * as api from "../../src/services/api";
import type { Actor } from "../../src/types";
import { useActorActions } from "../../src/hooks/useActorActions";
import { useGroupStore, useUIStore } from "../../src/stores";
import { useComposerStore } from "../../src/stores/useComposerStore";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

function captureActions(groupId: string): ReturnType<typeof useActorActions> {
  let actions: ReturnType<typeof useActorActions> | null = null;
  function Harness() {
    actions = useActorActions(groupId);
    return null;
  }
  renderToStaticMarkup(createElement(Harness));
  if (!actions) throw new Error("failed to capture actor actions");
  return actions;
}

describe("useActorActions target isolation", () => {
  beforeAll(() => {
    vi.stubGlobal("window", {
      setTimeout: vi.fn(() => 1),
      clearTimeout: vi.fn(),
      confirm: vi.fn(() => true),
    });
  });

  beforeEach(() => {
    vi.clearAllMocks();
    useUIStore.setState({ busy: "", errorMsg: "" });
    useGroupStore.setState({
      selectedGroupId: "g-a",
      groups: [],
      actors: [{ id: "opencode-1", running: false } as Actor],
      groupDoc: { group_id: "g-a", running: false, state: "stopped" },
    });
    vi.mocked(api.fetchActors).mockResolvedValue({ ok: true, result: { actors: [] } });
    vi.mocked(api.fetchGroups).mockResolvedValue({
      ok: true,
      result: {
        groups: [
          { group_id: "g-a", running: false, state: "stopped" },
          { group_id: "g-b", running: false, state: "stopped" },
        ],
      },
    });
  });

  it("refreshes the original group and does not mark the newly selected actor running", async () => {
    const startRequest = deferred<Awaited<ReturnType<typeof api.startActor>>>();
    vi.mocked(api.startActor).mockReturnValue(startRequest.promise);
    const actions = captureActions("g-a");
    const actor = { id: "opencode-1", running: false } as Actor;

    const pending = actions.toggleActorEnabled(actor);
    useGroupStore.setState({
      selectedGroupId: "g-b",
      actors: [{ id: "opencode-1", running: false } as Actor],
      groupDoc: { group_id: "g-b", running: false, state: "stopped" },
    });
    useComposerStore.setState({ activeGroupId: "g-b" });
    useUIStore.getState().setBusy("group-start");
    startRequest.resolve({ ok: true, result: {} });
    await pending;

    expect(api.startActor).toHaveBeenCalledWith("g-a", "opencode-1");
    expect(api.fetchActors).toHaveBeenCalledWith("g-a", true, undefined, { includeInternal: true });
    expect(useGroupStore.getState().selectedGroupId).toBe("g-b");
    expect(useGroupStore.getState().actors[0]?.running).toBe(false);
    expect(useUIStore.getState().busy).toBe("group-start");
  });
});
