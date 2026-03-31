import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../src/services/api", () => ({
  fetchActors: vi.fn(),
  fetchGroup: vi.fn(),
  fetchLedgerTail: vi.fn(),
  fetchOlderMessages: vi.fn(),
  fetchMessageWindow: vi.fn(),
  fetchContext: vi.fn(),
  fetchSettings: vi.fn(),
  fetchPresentation: vi.fn(),
  fetchGroups: vi.fn(),
}));

function makeStorage() {
  const data = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, String(value));
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key);
    }),
    clear: vi.fn(() => {
      data.clear();
    }),
  };
}

const localStorageMock = makeStorage();
vi.stubGlobal("localStorage", localStorageMock);

let useGroupStore: typeof import("../../src/stores/useGroupStore").useGroupStore;
let api: typeof import("../../src/services/api");

async function flushDeferredUnreadRefresh() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeAll(async () => {
  api = await import("../../src/services/api");
  ({ useGroupStore } = await import("../../src/stores/useGroupStore"));
});

describe("useGroupStore actors fetch policy", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorageMock.clear();
    useGroupStore.setState({
      groups: [{ group_id: "g-demo", title: "Demo", topic: "", state: "active" }],
      groupOrder: ["g-demo"],
      archivedGroupIds: [],
      selectedGroupId: "g-demo",
      chatByGroup: {},
      groupDoc: null,
      events: [],
      chatWindow: null,
      actors: [],
      groupContext: null,
      groupSettings: null,
      groupPresentation: null,
      runtimes: [],
      hasMoreHistory: true,
      isLoadingHistory: false,
      isChatWindowLoading: false,
    });

    vi.mocked(api.fetchActors).mockResolvedValue({ ok: true, result: { actors: [] } });
    vi.mocked(api.fetchGroup).mockImplementation(
      async (groupId: string) =>
        ({
          ok: true,
          result: {
            group: {
              group_id: groupId,
              title: String(groupId || ""),
              topic: "",
              state: "active",
            },
          },
        }) as Awaited<ReturnType<typeof api.fetchGroup>>,
    );
    vi.mocked(api.fetchLedgerTail).mockResolvedValue({
      ok: true,
      result: { events: [], has_more: false },
    } as Awaited<ReturnType<typeof api.fetchLedgerTail>>);
    vi.mocked(api.fetchOlderMessages).mockResolvedValue({
      ok: true,
      result: { events: [], has_more: false, count: 0 },
    } as Awaited<ReturnType<typeof api.fetchOlderMessages>>);
    vi.mocked(api.fetchMessageWindow).mockResolvedValue({
      ok: true,
      result: {
        center_id: "msg-1",
        center_index: 0,
        events: [],
        has_more_before: false,
        has_more_after: false,
      },
    } as Awaited<ReturnType<typeof api.fetchMessageWindow>>);
    vi.mocked(api.fetchContext).mockResolvedValue({
      ok: true,
      result: null,
    } as Awaited<ReturnType<typeof api.fetchContext>>);
    vi.mocked(api.fetchSettings).mockResolvedValue({
      ok: true,
      result: { settings: null },
    } as Awaited<ReturnType<typeof api.fetchSettings>>);
    vi.mocked(api.fetchPresentation).mockResolvedValue({
      ok: true,
      result: {
        group_id: "g-demo",
        presentation: { v: 1, updated_at: "", highlight_slot_id: "", slots: [] },
      },
    } as Awaited<ReturnType<typeof api.fetchPresentation>>);
    vi.mocked(api.fetchGroups).mockResolvedValue({
      ok: true,
      result: { groups: [] },
    } as Awaited<ReturnType<typeof api.fetchGroups>>);
  });

  it("refreshActors explicitly requests unread counts by default", async () => {
    await useGroupStore.getState().refreshActors("g-demo");
    expect(api.fetchActors).toHaveBeenCalledWith("g-demo", true);
  });

  it("refreshActors can do pure-read refresh without wiping existing unread counts", async () => {
    useGroupStore.setState({
      actors: [{ id: "peer-1", unread_count: 4, running: true }],
    });
    vi.mocked(api.fetchActors).mockResolvedValue({
      ok: true,
      result: { actors: [{ id: "peer-1", running: false }] },
    } as Awaited<ReturnType<typeof api.fetchActors>>);

    await useGroupStore.getState().refreshActors("g-demo", { includeUnread: false });

    expect(api.fetchActors).toHaveBeenCalledWith("g-demo", false);
    expect(useGroupStore.getState().actors).toEqual([{ id: "peer-1", running: false, unread_count: 4 }]);
  });

  it("updateActorActivity merges effective working state fields", () => {
    useGroupStore.setState({
      actors: [{ id: "peer-1", unread_count: 4, running: true, idle_seconds: 8 }],
    });

    useGroupStore.getState().updateActorActivity([
      {
        id: "peer-1",
        running: true,
        idle_seconds: 2,
        effective_working_state: "working",
        effective_working_reason: "agent_active_task",
        effective_active_task_id: "T1",
      },
    ]);

    expect(useGroupStore.getState().actors).toEqual([
      {
        id: "peer-1",
        unread_count: 4,
        running: true,
        idle_seconds: 2,
        effective_working_state: "working",
        effective_working_reason: "agent_active_task",
        effective_working_updated_at: null,
        effective_active_task_id: "T1",
      },
    ]);
  });

  it("loadGroup keeps unread counts on the selected group path", async () => {
    await useGroupStore.getState().loadGroup("g-demo");
    await vi.waitFor(() => {
      expect(api.fetchActors).toHaveBeenNthCalledWith(1, "g-demo", false);
      expect(api.fetchActors).toHaveBeenNthCalledWith(2, "g-demo", true);
    });
  });

  it("loadGroup waits for pure-read actors before scheduling unread refresh", async () => {
    let resolvePureRead: ((value: Awaited<ReturnType<typeof api.fetchActors>>) => void) | null = null;
    vi.mocked(api.fetchActors).mockImplementation((groupId: string, includeUnread = false) => {
      if (includeUnread) {
        return Promise.resolve({ ok: true, result: { actors: [{ id: "peer-1", unread_count: 3 }] } }) as ReturnType<typeof api.fetchActors>;
      }
      return new Promise((resolve) => {
        resolvePureRead = resolve as (value: Awaited<ReturnType<typeof api.fetchActors>>) => void;
      }) as ReturnType<typeof api.fetchActors>;
    });

    await useGroupStore.getState().loadGroup("g-demo");
    await flushDeferredUnreadRefresh();
    expect(api.fetchActors).toHaveBeenCalledTimes(1);
    expect(api.fetchActors).toHaveBeenCalledWith("g-demo", false);

    resolvePureRead?.({ ok: true, result: { actors: [{ id: "peer-1" }] } } as Awaited<ReturnType<typeof api.fetchActors>>);
    await vi.waitFor(() => {
      expect(api.fetchActors).toHaveBeenCalledTimes(2);
      expect(api.fetchActors).toHaveBeenNthCalledWith(2, "g-demo", true);
    });
  });

  it("queued unread refresh is not downgraded after a readonly request finishes", async () => {
    let resolvePureRead: ((value: Awaited<ReturnType<typeof api.fetchActors>>) => void) | null = null;
    vi.mocked(api.fetchActors).mockImplementation((groupId: string, includeUnread = false) => {
      if (includeUnread) {
        return Promise.resolve({
          ok: true,
          result: { actors: [{ id: "peer-1", unread_count: 5 }] },
        }) as ReturnType<typeof api.fetchActors>;
      }
      return new Promise((resolve) => {
        resolvePureRead = resolve as (value: Awaited<ReturnType<typeof api.fetchActors>>) => void;
      }) as ReturnType<typeof api.fetchActors>;
    });

    const readonlyRefresh = useGroupStore.getState().refreshActors("g-demo", { includeUnread: false });
    const queuedUnreadRefresh = useGroupStore.getState().refreshActors("g-demo", { includeUnread: true });

    expect(api.fetchActors).toHaveBeenCalledTimes(1);
    expect(api.fetchActors).toHaveBeenNthCalledWith(1, "g-demo", false);

    resolvePureRead?.({ ok: true, result: { actors: [{ id: "peer-1" }] } } as Awaited<ReturnType<typeof api.fetchActors>>);
    await readonlyRefresh;
    await queuedUnreadRefresh;

    await vi.waitFor(() => {
      expect(api.fetchActors).toHaveBeenCalledTimes(2);
      expect(api.fetchActors).toHaveBeenNthCalledWith(2, "g-demo", true);
    });
  });

  it("warmGroup preserves unread counts in the prefetched actor snapshot", async () => {
    const warmGroupId = "g-warm";
    useGroupStore.setState({
      selectedGroupId: "g-current",
      groups: [
        { group_id: "g-current", title: "Current", topic: "", state: "active" },
        { group_id: warmGroupId, title: "Warm", topic: "", state: "active" },
      ],
      groupOrder: ["g-current", warmGroupId],
    });

    await useGroupStore.getState().warmGroup(warmGroupId);
    expect(api.fetchActors).toHaveBeenCalledWith(warmGroupId, false);
  });

  it("refreshPresentation updates the selected group snapshot", async () => {
    vi.mocked(api.fetchPresentation).mockResolvedValue({
      ok: true,
      result: {
        group_id: "g-demo",
        presentation: {
          v: 1,
          updated_at: "2026-03-21T00:00:00Z",
          highlight_slot_id: "slot-2",
          slots: [
            { slot_id: "slot-1", index: 1, card: null },
            { slot_id: "slot-2", index: 2, card: { slot_id: "slot-2", title: "Deck", card_type: "markdown", published_by: "peer-1", published_at: "2026-03-21T00:00:00Z", content: { mode: "inline", markdown: "# deck" } } },
          ],
        },
      },
    } as Awaited<ReturnType<typeof api.fetchPresentation>>);

    await useGroupStore.getState().refreshPresentation("g-demo");

    expect(api.fetchPresentation).toHaveBeenCalledWith("g-demo");
    expect(useGroupStore.getState().groupPresentation?.highlight_slot_id).toBe("slot-2");
    expect(useGroupStore.getState().groupPresentation?.slots[1]?.card?.title).toBe("Deck");
  });

  it("refreshPresentation ignores stale responses that resolve after a newer local update", async () => {
    let resolveRefresh: ((value: Awaited<ReturnType<typeof api.fetchPresentation>>) => void) | null = null;
    vi.mocked(api.fetchPresentation).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve as (value: Awaited<ReturnType<typeof api.fetchPresentation>>) => void;
        }) as ReturnType<typeof api.fetchPresentation>
    );

    const refreshPromise = useGroupStore.getState().refreshPresentation("g-demo");
    useGroupStore.getState().setGroupPresentation({
      v: 1,
      updated_at: "2026-03-25T14:00:00Z",
      highlight_slot_id: "slot-3",
      slots: [],
    });

    resolveRefresh?.({
      ok: true,
      result: {
        group_id: "g-demo",
        presentation: {
          v: 1,
          updated_at: "2026-03-25T13:59:00Z",
          highlight_slot_id: "slot-1",
          slots: [],
        },
      },
    } as Awaited<ReturnType<typeof api.fetchPresentation>>);

    await refreshPromise;

    expect(useGroupStore.getState().groupPresentation?.highlight_slot_id).toBe("slot-3");
  });

  it("refreshSettings ignores stale responses that resolve after a newer local update", async () => {
    let resolveRefresh: ((value: Awaited<ReturnType<typeof api.fetchSettings>>) => void) | null = null;
    vi.mocked(api.fetchSettings).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve as (value: Awaited<ReturnType<typeof api.fetchSettings>>) => void;
        }) as ReturnType<typeof api.fetchSettings>
    );

    const refreshPromise = useGroupStore.getState().refreshSettings("g-demo");
    useGroupStore.getState().setGroupSettings({
      auto_accept: true,
    } as never);

    resolveRefresh?.({
      ok: true,
      result: {
        settings: {
          auto_accept: false,
        },
      },
    } as Awaited<ReturnType<typeof api.fetchSettings>>);

    await refreshPromise;

    expect((useGroupStore.getState().groupSettings as { auto_accept?: boolean } | null)?.auto_accept).toBe(true);
  });

  it("loadGroup falls back to full fresh context when summary snapshot is missing and no cached context exists", async () => {
    vi.mocked(api.fetchContext)
      .mockResolvedValueOnce({
        ok: true,
        result: {
          version: "ctxv:1",
          coordination: { tasks: [] },
          agent_states: [],
          meta: { summary_snapshot: { state: "missing" } },
        },
      } as Awaited<ReturnType<typeof api.fetchContext>>)
      .mockResolvedValueOnce({
        ok: true,
        result: {
          version: "ctxv:2",
          coordination: { tasks: [{ id: "t-1", title: "Real", outcome: "x" }] },
          agent_states: [{ id: "peer-1" }],
          meta: {},
        },
      } as Awaited<ReturnType<typeof api.fetchContext>>);

    await useGroupStore.getState().loadGroup("g-demo");

    await vi.waitFor(() => {
      expect(api.fetchContext).toHaveBeenNthCalledWith(1, "g-demo", { detail: "summary" });
      expect(api.fetchContext).toHaveBeenNthCalledWith(2, "g-demo", { detail: "full", fresh: true });
      expect(useGroupStore.getState().groupContext?.version).toBe("ctxv:2");
      expect(useGroupStore.getState().groupContext?.agent_states?.[0]?.id).toBe("peer-1");
    });
  });

  it("loadGroup preserves existing context when summary snapshot is stale", async () => {
    useGroupStore.setState({
      groupDoc: {
        group_id: "g-demo",
        title: "Demo",
        topic: "",
        state: "active",
      },
      groupContext: {
        version: "ctxv:cached",
        coordination: { tasks: [{ id: "t-cached", title: "Cached", outcome: "y" }] },
        agent_states: [{ id: "peer-cached" }],
      },
    });
    vi.mocked(api.fetchContext).mockResolvedValue({
      ok: true,
      result: {
        version: "ctxv:stale",
        coordination: { tasks: [] },
        agent_states: [],
        meta: { summary_snapshot: { state: "stale" } },
      },
    } as Awaited<ReturnType<typeof api.fetchContext>>);

    await useGroupStore.getState().loadGroup("g-demo");

    await vi.waitFor(() => {
      expect(api.fetchContext).toHaveBeenCalledTimes(1);
      expect(api.fetchContext).toHaveBeenNthCalledWith(1, "g-demo", { detail: "summary" });
      expect(useGroupStore.getState().groupContext?.version).toBe("ctxv:cached");
      expect(useGroupStore.getState().groupContext?.agent_states?.[0]?.id).toBe("peer-cached");
    });
  });

  it("openChatWindow ignores stale window responses from an older request", async () => {
    let resolveFirstWindow: ((value: Awaited<ReturnType<typeof api.fetchMessageWindow>>) => void) | null = null;
    let resolveSecondWindow: ((value: Awaited<ReturnType<typeof api.fetchMessageWindow>>) => void) | null = null;

    vi.mocked(api.fetchMessageWindow)
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirstWindow = resolve as (value: Awaited<ReturnType<typeof api.fetchMessageWindow>>) => void;
          }) as ReturnType<typeof api.fetchMessageWindow>
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveSecondWindow = resolve as (value: Awaited<ReturnType<typeof api.fetchMessageWindow>>) => void;
          }) as ReturnType<typeof api.fetchMessageWindow>
      );

    const firstPromise = useGroupStore.getState().openChatWindow("g-demo", "msg-old");
    const secondPromise = useGroupStore.getState().openChatWindow("g-demo", "msg-new");

    resolveSecondWindow?.({
      ok: true,
      result: {
        center_id: "msg-new",
        center_index: 0,
        events: [
          {
            id: "msg-new",
            kind: "chat.message",
            ts: "2026-03-25T09:02:00Z",
            by: "peer-1",
            data: { text: "new" },
          },
        ],
        has_more_before: true,
        has_more_after: false,
      },
    } as Awaited<ReturnType<typeof api.fetchMessageWindow>>);

    resolveFirstWindow?.({
      ok: true,
      result: {
        center_id: "msg-old",
        center_index: 0,
        events: [
          {
            id: "msg-old",
            kind: "chat.message",
            ts: "2026-03-25T09:00:00Z",
            by: "peer-1",
            data: { text: "old" },
          },
        ],
        has_more_before: false,
        has_more_after: true,
      },
    } as Awaited<ReturnType<typeof api.fetchMessageWindow>>);

    await Promise.all([firstPromise, secondPromise]);

    const bucket = useGroupStore.getState().chatByGroup["g-demo"];
    expect(bucket?.chatWindow?.centerEventId).toBe("msg-new");
    expect(bucket?.chatWindow?.events.map((event) => event.id)).toEqual(["msg-new"]);
    expect(bucket?.isChatWindowLoading).toBe(false);
  });

  it("loadGroup merges tail data with messages appended before tail settles", async () => {
    let resolveTail: ((value: Awaited<ReturnType<typeof api.fetchLedgerTail>>) => void) | null = null;
    vi.mocked(api.fetchLedgerTail).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveTail = resolve as (value: Awaited<ReturnType<typeof api.fetchLedgerTail>>) => void;
        }) as ReturnType<typeof api.fetchLedgerTail>
    );

    const loadPromise = useGroupStore.getState().loadGroup("g-demo");
    useGroupStore.getState().appendEvent(
      {
        id: "msg-live",
        kind: "chat.message",
        ts: "2026-03-25T09:01:00Z",
        by: "peer-1",
        data: { text: "live" },
      },
      "g-demo"
    );

    resolveTail?.({
      ok: true,
      result: {
        events: [
          {
            id: "msg-old",
            kind: "chat.message",
            ts: "2026-03-25T09:00:00Z",
            by: "peer-1",
            data: { text: "old" },
          },
        ],
        has_more: false,
        count: 1,
      },
    } as Awaited<ReturnType<typeof api.fetchLedgerTail>>);

    await loadPromise;

    await vi.waitFor(() => {
      const bucket = useGroupStore.getState().chatByGroup["g-demo"];
      expect(bucket?.events.map((event) => event.id)).toEqual(["msg-old", "msg-live"]);
      expect(bucket?.hasLoadedTail).toBe(true);
      expect(bucket?.hasMoreHistory).toBe(false);
    });
  });

  it("restores inactive-group messages from cache after the bucket is rebuilt", async () => {
    useGroupStore.setState({
      groups: [
        { group_id: "g-demo", title: "Demo", topic: "", state: "active" },
        { group_id: "g-other", title: "Other", topic: "", state: "active" },
      ],
      groupOrder: ["g-demo", "g-other"],
      selectedGroupId: "g-demo",
      chatByGroup: {
        "g-demo": {
          events: [
            {
              id: "msg-old",
              kind: "chat.message",
              ts: "2026-03-25T09:00:00Z",
              by: "peer-1",
              data: { text: "old" },
            },
          ],
          chatWindow: null,
          hasMoreHistory: false,
          hasLoadedTail: true,
          isLoadingHistory: false,
          isChatWindowLoading: false,
        },
      },
    });

    useGroupStore.getState().setSelectedGroupId("g-other");
    useGroupStore.getState().appendEvent(
      {
        id: "msg-late",
        kind: "chat.message",
        ts: "2026-03-25T09:01:00Z",
        by: "user",
        group_id: "g-demo",
        data: { text: "late" },
      },
      "g-demo"
    );

    useGroupStore.setState((state) => ({
      chatByGroup: {
        "g-other": state.chatByGroup["g-other"],
      },
    }));

    useGroupStore.getState().setSelectedGroupId("g-demo");

    const restoredBucket = useGroupStore.getState().chatByGroup["g-demo"];
    expect(restoredBucket?.events.map((event) => event.id)).toEqual(["msg-old", "msg-late"]);
  });

  it("loadMoreHistory closes history pagination when the older page is empty", async () => {
    useGroupStore.setState({
      selectedGroupId: "g-demo",
      chatByGroup: {
        "g-demo": {
          events: [
            {
              id: "msg-2",
              kind: "chat.message",
              ts: "2026-03-25T09:00:00Z",
              by: "peer-1",
              data: { text: "latest" },
            },
          ],
          chatWindow: null,
          hasMoreHistory: true,
          hasLoadedTail: true,
          isLoadingHistory: false,
          isChatWindowLoading: false,
        },
      },
    });
    vi.mocked(api.fetchOlderMessages).mockResolvedValueOnce({
      ok: true,
      result: { events: [], has_more: true, count: 0 },
    } as Awaited<ReturnType<typeof api.fetchOlderMessages>>);

    await useGroupStore.getState().loadMoreHistory("g-demo");

    const bucket = useGroupStore.getState().chatByGroup["g-demo"];
    expect(api.fetchOlderMessages).toHaveBeenCalledWith("g-demo", "msg-2", 50);
    expect(bucket?.isLoadingHistory).toBe(false);
    expect(bucket?.hasMoreHistory).toBe(false);
  });

  it("loadMoreHistory closes history pagination when the older page only contains duplicates", async () => {
    useGroupStore.setState({
      selectedGroupId: "g-demo",
      chatByGroup: {
        "g-demo": {
          events: [
            {
              id: "msg-1",
              kind: "chat.message",
              ts: "2026-03-25T08:59:00Z",
              by: "peer-1",
              data: { text: "old" },
            },
            {
              id: "msg-2",
              kind: "chat.message",
              ts: "2026-03-25T09:00:00Z",
              by: "peer-1",
              data: { text: "latest" },
            },
          ],
          chatWindow: null,
          hasMoreHistory: true,
          hasLoadedTail: true,
          isLoadingHistory: false,
          isChatWindowLoading: false,
        },
      },
    });
    vi.mocked(api.fetchOlderMessages).mockResolvedValueOnce({
      ok: true,
      result: {
        events: [
          {
            id: "msg-1",
            kind: "chat.message",
            ts: "2026-03-25T08:59:00Z",
            by: "peer-1",
            data: { text: "old" },
          },
        ],
        has_more: true,
        count: 1,
      },
    } as Awaited<ReturnType<typeof api.fetchOlderMessages>>);

    await useGroupStore.getState().loadMoreHistory("g-demo");

    const bucket = useGroupStore.getState().chatByGroup["g-demo"];
    expect(bucket?.events.map((event) => event.id)).toEqual(["msg-1", "msg-2"]);
    expect(bucket?.isLoadingHistory).toBe(false);
    expect(bucket?.hasMoreHistory).toBe(false);
  });
});
