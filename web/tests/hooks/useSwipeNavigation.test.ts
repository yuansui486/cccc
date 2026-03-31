import { describe, expect, it } from "vitest";
import { shouldHandleSwipeNavigationChain } from "../../src/hooks/useSwipeNavigation";

describe("shouldHandleSwipeNavigationChain", () => {
  it("allows swipe from plain static content", () => {
    expect(
      shouldHandleSwipeNavigationChain([
        { interactive: false, scrollable: false },
        { interactive: false, scrollable: false },
      ]),
    ).toBe(true);
  });

  it("blocks swipe when gesture starts from interactive controls", () => {
    expect(
      shouldHandleSwipeNavigationChain([
        { interactive: true, scrollable: false },
      ]),
    ).toBe(false);
  });

  it("blocks swipe when gesture starts inside a scrollable region", () => {
    expect(
      shouldHandleSwipeNavigationChain([
        { interactive: false, scrollable: false },
        { interactive: false, scrollable: true },
      ]),
    ).toBe(false);
  });
});
