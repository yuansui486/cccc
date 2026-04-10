import { describe, expect, it } from "vitest";

import { buildRuntimeDockItems } from "../../../src/pages/chat/runtimeDockItems";
import type { Actor } from "../../../src/types";

describe("runtimeDockItems", () => {
  it("preserves runtime actor order while attaching live work to headless actors", () => {
    const actors: Actor[] = [
      { id: "shell", title: "Shell", runtime: "codex", runner: "pty", unread_count: 2 },
      { id: "coder", title: "Coder", runtime: "codex", runner: "headless" },
      { id: "reviewer", title: "Reviewer", runtime: "claude", runner_effective: "headless" },
    ];

    const items = buildRuntimeDockItems({
      actors,
      liveWorkCards: [
        {
          actorId: "reviewer",
          actorLabel: "Reviewer",
          runtime: "claude",
          phase: "completed",
          streamPhase: "final_answer",
          text: "Review complete",
          transcriptBlocks: [],
          activities: [],
          updatedAt: "2025-01-02T12:00:00Z",
          streamId: "stream-reviewer",
          pendingEventId: "evt-reviewer",
        },
      ],
    });

    expect(items.map((item) => item.actorId)).toEqual(["shell", "coder", "reviewer"]);
    expect(items[0]).toMatchObject({ runner: "pty", unreadCount: 2, liveWorkCard: null });
    expect(items[1]).toMatchObject({ runner: "headless", liveWorkCard: null });
    expect(items[2]?.liveWorkCard?.text).toBe("Review complete");
  });

  it("uses runner_effective when deciding whether an actor is headless", () => {
    const actors: Actor[] = [
      { id: "shell", title: "Shell", runtime: "codex", runner: "pty" },
      { id: "coder", title: "Coder", runtime: "codex", runner: "pty", runner_effective: "headless" },
    ];

    const items = buildRuntimeDockItems({
      actors,
      liveWorkCards: [
        {
          actorId: "coder",
          actorLabel: "Coder",
          runtime: "codex",
          phase: "streaming",
          streamPhase: "commentary",
          text: "Investigating",
          transcriptBlocks: [],
          activities: [],
          updatedAt: "2025-01-02T12:00:01Z",
          streamId: "stream-coder",
          pendingEventId: "evt-coder",
        },
      ],
    });

    expect(items).toHaveLength(2);
    expect(items[1]).toMatchObject({ runner: "headless" });
    expect(items[1]?.liveWorkCard?.streamPhase).toBe("commentary");
  });
});