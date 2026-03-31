import { beforeEach, describe, expect, it, vi } from "vitest";

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

const SELECTED_GROUP_ID_KEY = "cccc-selected-group-id";
const ARCHIVED_GROUP_IDS_KEY = "cccc-archived-group-ids";
const localStorageMock = makeStorage();

vi.stubGlobal("localStorage", localStorageMock);

describe("useGroupStore selected group persistence", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorageMock.clear();
    vi.doUnmock("../services/api");
  });

  async function importStore(fetchGroups = vi.fn()) {
    vi.doMock("../services/api", () => ({
      fetchGroups,
    }));
    return await import("./useGroupStore");
  }

  it("initializes selectedGroupId from localStorage", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-2");

    const mod = await importStore();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-2");
  });

  it("persists explicit group selection changes", async () => {
    const mod = await importStore();

    mod.useGroupStore.getState().setSelectedGroupId("g-9");

    expect(localStorageMock.getItem(SELECTED_GROUP_ID_KEY)).toBe("g-9");
    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-9");
  });

  it("refreshGroups prefers the persisted selection when current state is empty", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-2");
    const fetchGroups = vi.fn().mockResolvedValue({
      ok: true,
      result: {
        groups: [
          { group_id: "g-1", title: "One", state: "idle", topic: "" },
          { group_id: "g-2", title: "Two", state: "active", topic: "" },
        ],
      },
    });
    const mod = await importStore(fetchGroups);
    mod.useGroupStore.setState({ selectedGroupId: "" });

    await mod.useGroupStore.getState().refreshGroups();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-2");
  });

  it("refreshGroups falls back to the first group when the persisted one no longer exists", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-missing");
    const fetchGroups = vi.fn().mockResolvedValue({
      ok: true,
      result: {
        groups: [
          { group_id: "g-1", title: "One", state: "idle", topic: "" },
          { group_id: "g-2", title: "Two", state: "active", topic: "" },
        ],
      },
    });
    const mod = await importStore(fetchGroups);

    await mod.useGroupStore.getState().refreshGroups();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("g-1");
    expect(localStorageMock.getItem(SELECTED_GROUP_ID_KEY)).toBe("g-1");
  });

  it("clears the persisted selection when no groups remain", async () => {
    localStorageMock.setItem(SELECTED_GROUP_ID_KEY, "g-2");
    const fetchGroups = vi.fn().mockResolvedValue({
      ok: true,
      result: {
        groups: [],
      },
    });
    const mod = await importStore(fetchGroups);

    await mod.useGroupStore.getState().refreshGroups();

    expect(mod.useGroupStore.getState().selectedGroupId).toBe("");
    expect(localStorageMock.getItem(SELECTED_GROUP_ID_KEY)).toBeNull();
  });
});

describe("useGroupStore archived sidebar groups", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorageMock.clear();
    vi.doUnmock("../services/api");
  });

  async function importStore(fetchGroups = vi.fn()) {
    vi.doMock("../services/api", () => ({
      fetchGroups,
    }));
    return await import("./useGroupStore");
  }

  it("initializes archivedGroupIds from localStorage", async () => {
    localStorageMock.setItem(ARCHIVED_GROUP_IDS_KEY, JSON.stringify(["g-2", "g-3"]));

    const mod = await importStore();

    expect(mod.useGroupStore.getState().archivedGroupIds).toEqual(["g-2", "g-3"]);
  });

  it("cleans stale archived ids when the group list refreshes", async () => {
    localStorageMock.setItem(ARCHIVED_GROUP_IDS_KEY, JSON.stringify(["g-2", "g-missing"]));
    const fetchGroups = vi.fn().mockResolvedValue({
      ok: true,
      result: {
        groups: [
          { group_id: "g-1", title: "One", state: "idle", topic: "" },
          { group_id: "g-2", title: "Two", state: "active", topic: "" },
        ],
      },
    });
    const mod = await importStore(fetchGroups);

    await mod.useGroupStore.getState().refreshGroups();

    expect(mod.useGroupStore.getState().archivedGroupIds).toEqual(["g-2"]);
    expect(localStorageMock.getItem(ARCHIVED_GROUP_IDS_KEY)).toBe(JSON.stringify(["g-2"]));
  });

  it("persists archive and restore actions", async () => {
    const mod = await importStore();
    mod.useGroupStore.setState({
      groups: [
        { group_id: "g-1", title: "One", state: "idle", topic: "" },
        { group_id: "g-2", title: "Two", state: "active", topic: "" },
      ],
      groupOrder: ["g-1", "g-2"],
    });

    mod.useGroupStore.getState().archiveGroup("g-2");
    expect(mod.useGroupStore.getState().archivedGroupIds).toEqual(["g-2"]);
    expect(localStorageMock.getItem(ARCHIVED_GROUP_IDS_KEY)).toBe(JSON.stringify(["g-2"]));

    mod.useGroupStore.getState().restoreGroup("g-2");
    expect(mod.useGroupStore.getState().archivedGroupIds).toEqual([]);
    expect(localStorageMock.getItem(ARCHIVED_GROUP_IDS_KEY)).toBeNull();
  });

  it("reorders only within the requested sidebar section", async () => {
    const mod = await importStore();
    mod.useGroupStore.setState({
      groups: [
        { group_id: "g-1", title: "One", state: "idle", topic: "" },
        { group_id: "g-2", title: "Two", state: "active", topic: "" },
        { group_id: "g-3", title: "Three", state: "paused", topic: "" },
        { group_id: "g-4", title: "Four", state: "idle", topic: "" },
      ],
      groupOrder: ["g-1", "g-2", "g-3", "g-4"],
      archivedGroupIds: ["g-2", "g-4"],
    });

    mod.useGroupStore.getState().reorderGroupsInSection("working", 1, 0);
    expect(mod.useGroupStore.getState().groupOrder).toEqual(["g-3", "g-2", "g-1", "g-4"]);

    mod.useGroupStore.getState().reorderGroupsInSection("archived", 1, 0);
    expect(mod.useGroupStore.getState().groupOrder).toEqual(["g-3", "g-4", "g-1", "g-2"]);
  });
});
