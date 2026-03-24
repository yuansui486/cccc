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

const localStorageMock = makeStorage();
vi.stubGlobal("localStorage", localStorageMock);

describe("useUIStore sidebar width", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorageMock.clear();
  });

  it("clamps persisted sidebar width through the public setter", async () => {
    const mod = await import("./useUIStore");
    mod.useUIStore.setState({ sidebarWidth: mod.SIDEBAR_DEFAULT_WIDTH });

    mod.useUIStore.getState().setSidebarWidth(999);
    expect(mod.useUIStore.getState().sidebarWidth).toBe(mod.SIDEBAR_MAX_WIDTH);

    mod.useUIStore.getState().setSidebarWidth(120);
    expect(mod.useUIStore.getState().sidebarWidth).toBe(mod.SIDEBAR_MIN_WIDTH);
  });

  it("exports a stable clamp helper for desktop resize math", async () => {
    const mod = await import("./useUIStore");
    expect(mod.clampSidebarWidth(NaN)).toBe(mod.SIDEBAR_DEFAULT_WIDTH);
    expect(mod.clampSidebarWidth(281.7)).toBe(282);
  });

  it("tracks presentation dock open state per group", async () => {
    const mod = await import("./useUIStore");
    mod.useUIStore.getState().setChatPresentationDockOpen("g-demo", true);
    expect(mod.getChatSession("g-demo", mod.useUIStore.getState().chatSessions).presentationDockOpen).toBe(true);

    mod.useUIStore.getState().setChatPresentationDockOpen("g-demo", false);
    expect(mod.getChatSession("g-demo", mod.useUIStore.getState().chatSessions).presentationDockOpen).toBe(false);
  });
});
