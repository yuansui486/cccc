import { describe, expect, it } from "vitest";
import { buildComposerMentionSuggestions, filterComposerMentionSuggestions } from "./chatMentionSuggestions";

describe("chat mention suggestions", () => {
  const suggestions = buildComposerMentionSuggestions(
    [{ id: "alice", title: "Alice" }],
    { g1: "One", g2: "Research" },
  );

  it("keeps actor and group suggestions distinct", () => {
    expect(filterComposerMentionSuggestions(suggestions, "actor", "ali").map((item) => item.token)).toEqual(["alice"]);
    expect(filterComposerMentionSuggestions(suggestions, "group", "res").map((item) => item.token)).toEqual(["g2"]);
  });

  it("includes the existing all-recipient contract", () => {
    expect(filterComposerMentionSuggestions(suggestions, "actor", "all")[0]?.token).toBe("@all");
  });
});
