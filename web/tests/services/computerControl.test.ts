import { afterEach, describe, expect, it, vi } from "vitest";
import { computerControlApi } from "../../src/services/api/computerControl";
import {
  computerControlProviderLabel,
  computerControlSessionDetails,
  formatComputerControlTime,
  latestComputerControlObservation,
  newestComputerControlObservation,
  shouldRefreshComputerControlAfterSseTransition,
} from "../../src/pages/computerControl/statusPresentation";

describe("computer control session presentation", () => {
  it("normalizes nested session and observation status", () => {
    const details = computerControlSessionDetails({
      phase: "ready",
      version: "0.9.0",
      session: { started_at: 1_785_200_000, transport_restarts: 2 },
      last_observation: {
        captured_at: 1_785_200_020,
        provider: "uia",
        window_name: "订单管理",
        count: 18,
      },
    });

    expect(details.startedAt).toBe(1_785_200_000);
    expect(details.transportRestarts).toBe(2);
    expect(details.observation).toEqual({
      updatedAt: 1_785_200_020,
      provider: "uia",
      targetWindow: "订单管理",
      elementCount: 18,
    });
    expect(computerControlProviderLabel("uia")).toBe("Windows UI 自动化");
    expect(formatComputerControlTime(details.startedAt)).not.toBe("");
  });

  it("keeps optional observation details hidden when the backend has none", () => {
    const details = computerControlSessionDetails({ phase: "ready", version: "" });
    expect(details.startedAt).toBeNull();
    expect(details.transportRestarts).toBeNull();
    expect(details.observation).toBeNull();
  });

  it("requests a full refresh after SSE reconnects while the page is active", () => {
    expect(shouldRefreshComputerControlAfterSseTransition(true, "connected", true)).toBe(true);
    expect(shouldRefreshComputerControlAfterSseTransition(true, "connecting", true)).toBe(false);
    expect(shouldRefreshComputerControlAfterSseTransition(true, "connected", false)).toBe(false);
    expect(shouldRefreshComputerControlAfterSseTransition(false, "connected", true)).toBe(false);
  });

  it("reads current observation details from run events", () => {
    expect(latestComputerControlObservation([{
      updated_at: 1_785_200_050,
      events: [{
        completed_at: 1_785_200_040,
        observation_context: {
          captured_at: 1_785_200_030,
          provider: "windows_mcp+native_uia",
          target_window: "订单管理",
          target_window_element_count: 27,
        },
      }],
    }])).toEqual({
      updatedAt: 1_785_200_030,
      provider: "windows_mcp+native_uia",
      targetWindow: "订单管理",
      elementCount: 27,
    });
  });

  it("selects the newest observation instead of always preferring a run", () => {
    const setupObservation = { updatedAt: 1_785_200_100, provider: "native_uia", targetWindow: "新窗口", elementCount: 12 };
    const runObservation = { updatedAt: 1_785_200_020, provider: "windows-mcp", targetWindow: "旧窗口", elementCount: 4 };
    expect(newestComputerControlObservation(setupObservation, runObservation)).toBe(setupObservation);
    expect(newestComputerControlObservation(runObservation, setupObservation)).toBe(setupObservation);
  });
});

describe("computer control session API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the dedicated restart-session endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { phase: "ready", version: "latest" } }),
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("window", { location: { search: "" } });
    vi.stubGlobal("sessionStorage", { getItem: () => null, setItem: () => undefined, removeItem: () => undefined });

    const response = await computerControlApi.restartSession();

    expect(response.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/computer-control/setup/restart-session",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
