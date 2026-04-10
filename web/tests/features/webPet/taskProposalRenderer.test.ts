import { describe, expect, it } from "vitest";

import i18n from "../../../src/i18n";
import { renderTaskProposalDraft, renderTaskProposalSummary } from "../../../src/features/webPet/taskProposalRenderer";

describe("taskProposalRenderer", () => {
  it("deduplicates task id prefixes in move_active summary titles", async () => {
    await i18n.changeLanguage("zh");
    const action = {
      type: "task_proposal" as const,
      groupId: "g-1",
      operation: "move" as const,
      taskId: "T310",
      title: "T310: 根治路线设计 — 前后台隔离 + unread/context 结果化",
      assignee: "claude-1",
      status: "active",
      reason: {
        kind: "move_active" as const,
        actorId: "claude-1",
      },
    };

    expect(renderTaskProposalSummary(action)).toBe(
      "T310 已被 claude-1 挂载，建议把任务板状态同步为 active：根治路线设计 — 前后台隔离 + unread/context 结果化",
    );
  });

  it("renders ownership drift summary and draft from structured reason", async () => {
    await i18n.changeLanguage("zh");
    const action = {
      type: "task_proposal" as const,
      groupId: "g-1",
      operation: "update" as const,
      taskId: "T084",
      title: "治理实施线 1：拆 BossRepliesView.vue 边界",
      assignee: "claude-1",
      reason: {
        kind: "ownership_drift" as const,
        actorId: "claude-1",
        currentActiveTaskId: "T086",
      },
    };

    expect(renderTaskProposalSummary(action)).toBe(
      "T084 仍指派给 claude-1，但执行焦点已经漂移，建议重新确认 owner：治理实施线 1：拆 BossRepliesView.vue 边界",
    );
    expect(renderTaskProposalDraft(action)).toContain("task_id=T084");
    expect(renderTaskProposalDraft(action)).toContain("T086");
    expect(renderTaskProposalDraft(action)).toContain("重新确认 owner 与任务状态");
  });

  it("falls back gracefully when no structured reason is present", async () => {
    await i18n.changeLanguage("en");
    const action = {
      type: "task_proposal" as const,
      groupId: "g-1",
      operation: "move" as const,
      taskId: "T315",
      status: "active",
      title: "推进 active",
    };

    expect(renderTaskProposalSummary(action)).toBe("推进 active");
    expect(renderTaskProposalDraft(action)).toBe(
      "Use cccc_task to move this task (task_id=T315, title=\"推进 active\", status=active).",
    );
  });
});
