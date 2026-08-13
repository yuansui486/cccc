import { beforeEach, describe, expect, it, vi } from "vitest";

const { updateObservability } = vi.hoisted(() => ({
  updateObservability: vi.fn(),
}));

vi.mock("../../src/services/api/webAccess", () => ({
  updateObservability,
}));

import {
  resetTerminalThemeSyncForTests,
  resolveTerminalColorScheme,
  syncTerminalColorScheme,
} from "../../src/utils/terminalThemeSync";

describe("terminalThemeSync", () => {
  beforeEach(() => {
    updateObservability.mockReset();
    resetTerminalThemeSyncForTests();
  });

  it("resolves explicit and system themes", () => {
    expect(resolveTerminalColorScheme("light", "dark")).toBe("light");
    expect(resolveTerminalColorScheme("dark", "light")).toBe("dark");
    expect(resolveTerminalColorScheme("system", "light")).toBe("light");
  });

  it("deduplicates repeated synchronized schemes", async () => {
    updateObservability.mockResolvedValue({ ok: true, result: { observability: {} } });

    await expect(syncTerminalColorScheme("light")).resolves.toBe(true);
    await expect(syncTerminalColorScheme("light")).resolves.toBe(true);

    expect(updateObservability).toHaveBeenCalledTimes(1);
    expect(updateObservability).toHaveBeenCalledWith({ terminalUiColorScheme: "light" });
  });

  it("forces launch-time synchronization when another client may have overwritten the setting", async () => {
    updateObservability.mockResolvedValue({ ok: true, result: { observability: {} } });

    await expect(syncTerminalColorScheme("dark")).resolves.toBe(true);
    await expect(syncTerminalColorScheme("dark", { force: true })).resolves.toBe(true);

    expect(updateObservability).toHaveBeenCalledTimes(2);
    expect(updateObservability).toHaveBeenNthCalledWith(2, { terminalUiColorScheme: "dark" });
  });

  it("coalesces changes while a sync is in flight", async () => {
    let finishFirst: ((value: unknown) => void) | undefined;
    updateObservability
      .mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }))
      .mockResolvedValueOnce({ ok: true, result: { observability: {} } });

    const first = syncTerminalColorScheme("dark");
    const second = syncTerminalColorScheme("light");
    finishFirst?.({ ok: true, result: { observability: {} } });

    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
    expect(updateObservability).toHaveBeenNthCalledWith(1, { terminalUiColorScheme: "dark" });
    expect(updateObservability).toHaveBeenNthCalledWith(2, { terminalUiColorScheme: "light" });
  });

  it("reconfirms a forced launch sync requested during an in-flight update", async () => {
    let finishFirst: ((value: unknown) => void) | undefined;
    updateObservability
      .mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }))
      .mockResolvedValueOnce({ ok: true, result: { observability: {} } });

    const backgroundSync = syncTerminalColorScheme("dark");
    const launchSync = syncTerminalColorScheme("dark", { force: true });
    finishFirst?.({ ok: true, result: { observability: {} } });

    await expect(Promise.all([backgroundSync, launchSync])).resolves.toEqual([true, true]);
    expect(updateObservability).toHaveBeenCalledTimes(2);
    expect(updateObservability).toHaveBeenNthCalledWith(2, { terminalUiColorScheme: "dark" });
  });

  it("allows retry after a failed non-blocking sync", async () => {
    updateObservability
      .mockResolvedValueOnce({ ok: false, error: { code: "network_error" } })
      .mockResolvedValueOnce({ ok: true, result: { observability: {} } });

    await expect(syncTerminalColorScheme("light")).resolves.toBe(false);
    await expect(syncTerminalColorScheme("light")).resolves.toBe(true);
    expect(updateObservability).toHaveBeenCalledTimes(2);
  });
});
