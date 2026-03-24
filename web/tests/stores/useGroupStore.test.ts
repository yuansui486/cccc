import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../src/services/api", () => ({
  fetchActors: vi.fn(),
  fetchGroup: vi.fn(),
  fetchLedgerTail: vi.fn(),
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

async function flushDeferredUnreadRefresh() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

let useGroupStore: typeof import("../../src/stores/useGroupStore").useGroupStore;
let api: typeof import("../../src/services/api");

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

  it("refreshActors explicitly requests unread counts", async () => {
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
    expect(api.fetchActors).toHaveBeenNthCalledWith(1, "g-demo", false);

    resolvePureRead?.({ ok: true, result: { actors: [{ id: "peer-1" }] } } as Awaited<ReturnType<typeof api.fetchActors>>);
    await vi.waitFor(() => {
      expect(api.fetchActors).toHaveBeenCalledTimes(2);
      expect(api.fetchActors).toHaveBeenNthCalledWith(2, "g-demo", true);
    });
  });

  it("scheduleActorUnreadRefresh delays the unread fetch", async () => {
    vi.useFakeTimers();
    try {
      useGroupStore.getState().scheduleActorUnreadRefresh("g-demo", 250);
      expect(api.fetchActors).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(249);
      expect(api.fetchActors).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(1);
      await vi.waitFor(() => {
        expect(api.fetchActors).toHaveBeenCalledWith("g-demo", true);
      });
    } finally {
      vi.useRealTimers();
    }
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
});
