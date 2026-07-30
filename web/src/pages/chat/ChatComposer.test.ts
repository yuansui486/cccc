import { describe, expect, it } from "vitest";

import { getComposerActionVisibility } from "./chatComposerActions";

describe("ChatComposer action visibility", () => {
  it("hides the message mode selector on small screens", () => {
    expect(getComposerActionVisibility(true)).toEqual({
      showMessageModeSelector: false,
    });
  });

  it("keeps the message mode selector on larger screens", () => {
    expect(getComposerActionVisibility(false)).toEqual({
      showMessageModeSelector: true,
    });
  });
});
