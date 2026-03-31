import { describe, expect, it } from "vitest";

import { getPetReminderDraftText, getPetReminderPrimaryText } from "./reminderText";
import type { PetReminder } from "./types";

function makeSendSuggestionReminder(overrides: Partial<PetReminder> = {}): PetReminder {
  return {
    id: "r1",
    kind: "suggestion",
    priority: 80,
    summary: "summary text",
    agent: "pet-peer",
    source: { eventId: "evt-1", suggestionKind: "reply_required" },
    fingerprint: "fp-1",
    action: {
      type: "draft_message",
      groupId: "g-1",
      text: "action text",
      to: ["@foreman"],
      replyTo: "evt-1",
    },
    ...overrides,
  };
}

describe("getPetReminderPrimaryText", () => {
  it("uses summary for reminder display", () => {
    expect(getPetReminderPrimaryText(makeSendSuggestionReminder())).toBe("summary text");
  });

  it("falls back to draft text and other action labels only when summary is missing", () => {
    expect(
      getPetReminderPrimaryText(
        makeSendSuggestionReminder({
          summary: "",
          action: {
            type: "draft_message",
            groupId: "g-1",
            text: "action text",
          },
        }),
      ),
    ).toBe("action text");

    expect(
      getPetReminderPrimaryText(
        makeSendSuggestionReminder({
          action: {
            type: "task_proposal",
            groupId: "g-1",
            operation: "move",
            title: "Move T1 to active",
          },
          summary: "",
        }),
      ),
    ).toBe("Move T1 to active");

    expect(
      getPetReminderPrimaryText(
        makeSendSuggestionReminder({
          action: {
            type: "automation_proposal",
            groupId: "g-1",
            summary: "Apply the temporary rule",
            actions: [{ type: "create_rule" }],
          },
          summary: "",
        }),
      ),
    ).toBe("Apply the temporary rule");
  });
});

describe("getPetReminderDraftText", () => {
  it("only uses action.text for draft_message reminders", () => {
    expect(getPetReminderDraftText(makeSendSuggestionReminder())).toBe("action text");

    expect(
      getPetReminderDraftText(
        makeSendSuggestionReminder({
          action: {
            type: "draft_message",
            groupId: "g-1",
            text: "",
          },
        }),
      ),
    ).toBe("");

    expect(
      getPetReminderDraftText(
        makeSendSuggestionReminder({
          action: {
            type: "task_proposal",
            groupId: "g-1",
            operation: "move",
          },
        }),
      ),
    ).toBe("");
  });
});
