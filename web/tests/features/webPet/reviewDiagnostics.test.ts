import { describe, expect, it } from "vitest";

import { classifyPetManualReviewResponse, classifyPetRuntimeTail } from "../../../src/features/webPet/reviewDiagnostics";

describe("classifyPetRuntimeTail", () => {
  it("detects auth-expired pet runtimes from common CLI login markers", () => {
    expect(
      classifyPetRuntimeTail("Please run /login\nAPI Error: 401\nOAuth token has expired"),
    ).toBe("auth_expired");
  });

  it("ignores ordinary terminal output", () => {
    expect(classifyPetRuntimeTail("Working (esc to interrupt)\n>")).toBeNull();
  });
});

describe("classifyPetManualReviewResponse", () => {
  it("maps actor_not_found to runtime_unavailable", () => {
    expect(
      classifyPetManualReviewResponse({
        ok: false,
        error: { code: "actor_not_found", message: "actor not found: pet-peer" },
      }),
    ).toEqual({ kind: "runtime_unavailable" });
  });

  it("maps actor_not_running to runtime_not_running", () => {
    expect(
      classifyPetManualReviewResponse({
        ok: false,
        error: { code: "actor_not_running", message: "actor is not running" },
      }),
    ).toEqual({ kind: "runtime_not_running" });
  });

  it("treats readable non-auth tail as no_reminders", () => {
    expect(
      classifyPetManualReviewResponse({
        ok: true,
        result: { text: "OpenAI Codex\n›", hint: "" },
      }),
    ).toEqual({ kind: "no_reminders" });
  });
});
