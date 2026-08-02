import { describe, expect, it } from "vitest";
import {
  buildComposerHistoryEntries,
  canStartComposerHistory,
  moveComposerHistory,
  startComposerHistory,
} from "./chatComposerHistory";

describe("chat composer history", () => {
  it("keeps only committed local user messages", () => {
    expect(buildComposerHistoryEntries([
      { kind: "chat.message", by: "user", data: { text: "first" } },
      { kind: "chat.message", by: "actor", data: { text: "reply" } },
      { kind: "chat.message", by: "user", data: { text: "pending", _optimistic: true } },
      { kind: "chat.message", by: "user", data: { text: "remote", src_group_id: "g2" } },
    ])).toEqual(["first"]);
  });

  it("moves older and newer without leaking across groups", () => {
    const session = startComposerHistory(["one", "two"], "g1", "draft");
    expect(session).not.toBeNull();
    expect(moveComposerHistory(session!, "older").text).toBe("one");
    const newer = moveComposerHistory(moveComposerHistory(session!, "older").session!, "newer");
    expect(newer.text).toBe("two");
    expect(moveComposerHistory(session!, "newer").session).toBeNull();
  });

  it("requires an empty settled composer before history starts", () => {
    expect(canStartComposerHistory({
      composerText: "",
      groupSettled: true,
      groupId: "g1",
      busy: "idle",
      menuOpen: false,
      isComposing: false,
      hasModifier: false,
    })).toBe(true);
    expect(canStartComposerHistory({
      composerText: "draft",
      groupSettled: true,
      groupId: "g1",
      busy: "idle",
      menuOpen: false,
      isComposing: false,
      hasModifier: false,
    })).toBe(false);
  });
});
