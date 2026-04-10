import { describe, expect, it } from "vitest";

import { deriveTaskProposalStylePolicy } from "../../../src/features/webPet/taskProposalStylePolicy";
import i18n from "../../../src/i18n";
import { renderTaskProposalDraft } from "../../../src/features/webPet/taskProposalRenderer";

describe("taskProposalStylePolicy", () => {
  it("derives cautious reconfirm policy from low-noise persona hints", () => {
    const policy = deriveTaskProposalStylePolicy({
      persona: "low-noise coordination helper; avoid direct handoff; 先确认 owner",
      help: "",
      prompt: "",
    });

    expect(policy.tone).toBe("cautious");
    expect(policy.ownershipDriftMode).toBe("reconfirm");
  });

  it("derives direct reassign policy from explicit prompt tags", () => {
    const policy = deriveTaskProposalStylePolicy({
      persona: "",
      help: "task-proposal-ownership: reassign",
      prompt: "task-proposal-tone: direct",
    });

    expect(policy.tone).toBe("direct");
    expect(policy.ownershipDriftMode).toBe("reassign");
  });

  it("changes ownership drift draft wording when persona policy prefers reassign", async () => {
    await i18n.changeLanguage("en");
    const action = {
      type: "task_proposal" as const,
      groupId: "g-1",
      operation: "update" as const,
      taskId: "T084",
      assignee: "claude-1",
      style: {
        tone: "direct" as const,
        ownershipDriftMode: "reassign" as const,
        stalledActiveMode: "escalate" as const,
        waitingUserMode: "close" as const,
      },
      reason: {
        kind: "ownership_drift" as const,
        actorId: "claude-1",
        currentActiveTaskId: "T086",
      },
    };

    expect(renderTaskProposalDraft(action)).toContain("Treat T084 as stale unless proven otherwise");
  });
});
