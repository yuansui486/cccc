import { describe, expect, it } from "vitest";

import type { Actor } from "../types";
import { getActorDisplayWorkingState, getTerminalSignalFromChunk } from "./terminalWorkingState";

function makeActor(overrides: Partial<Actor> = {}): Actor {
  return {
    id: "peer-1",
    title: "Peer 1",
    enabled: true,
    running: true,
    runner: "pty",
    runner_effective: "pty",
    effective_working_state: "idle",
    idle_seconds: 0.8,
    ...overrides,
  };
}

describe("getActorDisplayWorkingState", () => {
  it("keeps PTY actors idle without a visible working banner", () => {
    expect(getActorDisplayWorkingState(makeActor(), null)).toBe("idle");
  });

  it("promotes fresh working output to working without waiting for the next daemon tick", () => {
    expect(
      getActorDisplayWorkingState(
        makeActor({ effective_working_state: "waiting" }),
        { kind: "working_output", updatedAt: 10_000 },
        12_000,
      ),
    ).toBe("working");
  });

  it("does not keep stale working output elevated forever", () => {
    expect(
      getActorDisplayWorkingState(
        makeActor({ effective_working_state: "waiting" }),
        { kind: "working_output", updatedAt: 10_000 },
        20_500,
      ),
    ).toBe("waiting");
  });

  it("keeps idle when the visible prompt says the actor is waiting for input", () => {
    expect(
      getActorDisplayWorkingState(
        makeActor(),
        { kind: "idle_prompt", updatedAt: Date.now() },
      ),
    ).toBe("idle");
  });

  it("does not apply PTY prompt overrides to headless actors", () => {
    expect(
      getActorDisplayWorkingState(
        makeActor({ runner: "headless", runner_effective: "headless" }),
        null,
      ),
    ).toBe("idle");
  });
});

describe("getTerminalSignalFromChunk", () => {
  it("can detect a visible codex working banner from a freshly fetched terminal tail", () => {
    const signal = getTerminalSignalFromChunk("", "◦ Working (13s • esc to interrupt)\n", "codex");
    expect(signal.signalKind).toBe("working_output");
  });

  it("prefers the visible prompt over an older codex working banner", () => {
    const signal = getTerminalSignalFromChunk(
      "",
      "◦ Working (13s • esc to interrupt)\nstream disconnected before completion\n› Find and fix a bug in @filename\ngpt-5.4 default · 41% left · ~/Desktop/waterbang/ai/hr-agent\n",
      "codex",
    );
    expect(signal.signalKind).toBe("idle_prompt");
  });
});
