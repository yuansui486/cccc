import { describe, expect, it } from "vitest";
import { getBackgroundRefreshDelayMs } from "./reviewTiming";

describe("getBackgroundRefreshDelayMs", () => {
  it("uses the base interval for success and backs off exponentially on failures", () => {
    expect(getBackgroundRefreshDelayMs(0)).toBe(30_000);
    expect(getBackgroundRefreshDelayMs(1)).toBe(60_000);
    expect(getBackgroundRefreshDelayMs(2)).toBe(120_000);
  });

  it("caps the retry delay at five minutes", () => {
    expect(getBackgroundRefreshDelayMs(10)).toBe(300_000);
  });
});
