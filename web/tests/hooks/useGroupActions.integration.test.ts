import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../src/services/api", () => ({
  fetchActors: vi.fn(),
  fetchGroups: vi.fn(),
  fetchGroupControlState: vi.fn(),
  setGroupState: vi.fn(),
  startGroup: vi.fn(),
  stopGroup: vi.fn(),
}));

import * as api from "../../src/services/api";
import { useGroupActions } from "../../src/hooks/useGroupActions";
import { useGroupStore, useUIStore } from "../../src/stores";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

function captureActions(): ReturnType<typeof useGroupActions> {
  let actions: ReturnType<typeof useGroupActions> | null = null;
  function Harness() {
    actions = useGroupActions();
    return null;
  }
  renderToStaticMarkup(createElement(Harness));
  if (!actions) throw new Error("failed to capture group actions");
  return actions;
}

describe("useGroupActions target isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useUIStore.setState({ busy: "", errorMsg: "" });
    useGroupStore.setState({
      selectedGroupId: "g-a",
      groups: [],
      actors: [],
      groupDoc: {
        group_id: "g-a",
        running: true,
        state: "paused",
      },
    });
    vi.mocked(api.fetchActors).mockResolvedValue({ ok: true, result: { actors: [] } });
    vi.mocked(api.fetchGroups).mockResolvedValue({
      ok: true,
      result: {
        groups: [{ group_id: "g-b", running: false, state: "stopped" }],
      },
    });
  });

  it("uses the captured gid and fresh control state after the selected group changes", async () => {
    const stateMutation = deferred<Awaited<ReturnType<typeof api.setGroupState>>>();
    vi.mocked(api.setGroupState).mockReturnValue(stateMutation.promise);
    vi.mocked(api.fetchGroupControlState).mockResolvedValue({
      ok: true,
      result: {
        group_id: "g-a",
        control_state: {
          status_key: "stop",
          lifecycle_state: "active",
          runtime_running: false,
          primary_action: "start",
          can_start: true,
          can_pause: false,
          can_stop: true,
          actor_count: 1,
          running_actor_count: 0,
          has_running_foreman: false,
        },
      },
    });
    vi.mocked(api.startGroup).mockResolvedValue({ ok: true, result: {} });
    const actions = captureActions();

    const pending = actions.handleSetGroupState("active");
    useGroupStore.setState({
      selectedGroupId: "g-b",
      groupDoc: {
        group_id: "g-b",
        running: false,
        state: "stopped",
      },
    });
    useUIStore.getState().setBusy("actor-start:peer1");
    stateMutation.resolve({ ok: true, result: {} });
    await pending;

    expect(api.setGroupState).toHaveBeenCalledWith("g-a", "active");
    expect(api.fetchGroupControlState).toHaveBeenCalledWith("g-a", { noCache: true });
    expect(api.startGroup).toHaveBeenCalledWith("g-a");
    expect(api.fetchActors).toHaveBeenCalledWith("g-a", true, undefined, { includeInternal: true });
    expect(useGroupStore.getState().groupDoc?.group_id).toBe("g-b");
    expect(useGroupStore.getState().groupDoc?.control_state).toBeUndefined();
    expect(useUIStore.getState().busy).toBe("actor-start:peer1");
  });
});
