import { describe, expect, it } from "vitest";

import {
  getPetReminderActionPreviewText,
  getPetReminderDraftText,
  getPetReminderPrimaryText,
  getPetReminderRouteInfo,
} from "../../../src/features/webPet/reminderText";
import type { PetReminder } from "../../../src/features/webPet/types";

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

describe("getPetReminderActionPreviewText", () => {
  it("returns the prepared outbound text for draft and task proposal actions", () => {
    expect(getPetReminderActionPreviewText(makeSendSuggestionReminder())).toBe("action text");

    expect(
      getPetReminderActionPreviewText(
        makeSendSuggestionReminder({
          summary: "summary text",
          action: {
            type: "task_proposal",
            groupId: "g-1",
            operation: "move",
            taskId: "T315",
            status: "active",
          },
        }),
      ),
    ).toBe("Use cccc_task to move this task (task_id=T315, status=active).");
  });
});

describe("getPetReminderRouteInfo", () => {
  it("derives routing metadata for draft_message and task_proposal reminders", () => {
    expect(getPetReminderRouteInfo(makeSendSuggestionReminder())).toEqual({
      toText: "@foreman",
      replyInThread: true,
    });

    expect(
      getPetReminderRouteInfo(
        makeSendSuggestionReminder({
          action: {
            type: "task_proposal",
            groupId: "g-1",
            operation: "move",
            taskId: "T315",
            status: "active",
          },
        }),
      ),
    ).toEqual({
      toText: "@foreman",
      replyInThread: false,
    });

  });
});
