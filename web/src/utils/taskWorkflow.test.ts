import { describe, expect, it } from "vitest";
import {
  TASK_WORKFLOW_SCAFFOLDS,
  evaluateTaskWorkflow,
  getTaskDisplaySummary,
  getTaskDoneTransitionBlockers,
  getTaskWorkflowScaffold,
  recommendTaskWorkflowScaffold,
  resolveTaskWorkflowScaffold,
} from "./taskWorkflow";

describe("taskWorkflow", () => {
  it("ships the compact scaffold roster", () => {
    expect(TASK_WORKFLOW_SCAFFOLDS.map((item) => item.id)).toEqual([
      "lean",
      "root",
      "planner",
      "reviewer",
      "debugger",
      "release",
      "optimization",
    ]);
  });

  it("returns seeded notes and checklist for structured scaffolds", () => {
    const root = getTaskWorkflowScaffold("root");
    expect(root.notes).toContain("Goal:");
    expect(root.notes).toContain("Success Criteria:");
    expect(root.notes).toContain("Closeout Verdict:");
    expect(root.checklist).toContain("Define success criteria");

    const optimization = getTaskWorkflowScaffold("optimization");
    expect(optimization.notes).toContain("Baseline:");
    expect(optimization.notes).toContain("Primary Metric:");
    expect(optimization.notes).toContain("Attempt Decision:");
    expect(optimization.checklist).toContain("Record keep or discard");
  });

  it("recommends root or lean scaffolds from task shape", () => {
    expect(recommendTaskWorkflowScaffold({ parentId: "" })).toBe("root");
    expect(recommendTaskWorkflowScaffold({ parentId: "T001" })).toBe("lean");
    expect(recommendTaskWorkflowScaffold({ notes: "Baseline:\n- 120 ms" })).toBe("optimization");
  });

  it("resolves persisted release presets from their checklist shape", () => {
    expect(resolveTaskWorkflowScaffold({
      notes: [
        "Goal:",
        "- Ship v0.4.7",
        "",
        "Success Criteria:",
        "- Release notes, version surface, and smoke all line up",
        "",
        "Required Evidence:",
        "- Smoke verification and release note",
      ].join("\n"),
      checklist: [
        "[x] Update the release note",
        "[x] Check version and docs drift",
        "[ ] Run smoke verification",
        "[ ] Record the shipping verdict",
      ].join("\n"),
    })).toBe("release");
  });

  it("evaluates root-task contract coverage conservatively", () => {
    const incomplete = evaluateTaskWorkflow({
      assignee: "",
      notes: [
        "Goal:",
        "- Ship the web access panel refresh",
        "",
        "Success Criteria:",
        "- [define the observable finish line]",
      ].join("\n"),
      status: "active",
    });

    expect(incomplete.isRoot).toBe(true);
    expect(incomplete.needsContract).toBe(true);
    expect(incomplete.missingSetup).toEqual(["Required evidence", "Owner"]);

    const complete = evaluateTaskWorkflow({
      assignee: "foreman",
      notes: [
        "Goal:",
        "- Ship the web access panel refresh",
        "",
        "Success Criteria:",
        "- Users can switch local / private / public without ambiguity",
        "",
        "Required Evidence:",
        "- Verified in Linux and Windows smoke checks",
      ].join("\n"),
      status: "active",
    });

    expect(complete.needsContract).toBe(false);
    expect(complete.goalSummary).toContain("Ship the web access panel refresh");
  });

  it("requires explicit closeout on done root tasks", () => {
    const coverage = evaluateTaskWorkflow({
      assignee: "foreman",
      status: "done",
      outcome: "Web access panel refreshed and shipped.",
      notes: [
        "Goal:",
        "- Refresh the web access panel",
        "",
        "Success Criteria:",
        "- Users understand reachability and restart flow",
        "",
        "Required Evidence:",
        "- Manual verification on Linux and Windows",
        "",
        "Closeout Verdict:",
        "- DONE",
      ].join("\n"),
    });

    expect(coverage.needsContract).toBe(false);
    expect(coverage.needsCloseout).toBe(true);
    expect(coverage.missingCloseout).toEqual(["Verification summary"]);
  });

  it("derives a compact release closeout signal from existing fields only", () => {
    const inProgress = evaluateTaskWorkflow({
      assignee: "foreman",
      status: "active",
      outcome: "",
      notes: [
        "Goal:",
        "- Ship v0.4.7",
        "",
        "Success Criteria:",
        "- Release notes, version surface, and smoke all line up",
        "",
        "Required Evidence:",
        "- Smoke verification and release note",
      ].join("\n"),
      checklist: [
        "[x] Update the release note",
        "[ ] Check version and docs drift",
        "[ ] Run smoke verification",
        "[ ] Record the shipping verdict",
      ].join("\n"),
    });
    expect(inProgress.isRelease).toBe(true);
    expect(inProgress.releaseCloseoutState).toBe("in_progress");

    const needsCloseout = evaluateTaskWorkflow({
      assignee: "foreman",
      status: "done",
      outcome: "v0.4.7 shipped.",
      notes: [
        "Goal:",
        "- Ship v0.4.7",
        "",
        "Success Criteria:",
        "- Release notes, version surface, and smoke all line up",
        "",
        "Required Evidence:",
        "- Smoke verification and release note",
        "",
        "Closeout Verdict:",
        "- DONE",
      ].join("\n"),
      checklist: [
        "[x] Update the release note",
        "[x] Check version and docs drift",
        "[x] Run smoke verification",
        "[x] Record the shipping verdict",
      ].join("\n"),
    });
    expect(needsCloseout.releaseCloseoutState).toBe("needs_closeout");

    const ready = evaluateTaskWorkflow({
      assignee: "foreman",
      status: "done",
      outcome: "v0.4.7 shipped.",
      notes: [
        "Goal:",
        "- Ship v0.4.7",
        "",
        "Success Criteria:",
        "- Release notes, version surface, and smoke all line up",
        "",
        "Required Evidence:",
        "- Smoke verification and release note",
        "",
        "Closeout Verdict:",
        "- DONE",
        "",
        "Verification Summary:",
        "- Release note updated, version surface checked, smoke passed",
      ].join("\n"),
      checklist: [
        "[x] Update the release note",
        "[x] Check version and docs drift",
        "[x] Run smoke verification",
        "[x] Record the shipping verdict",
      ].join("\n"),
    });
    expect(ready.releaseCloseoutState).toBe("ready");
  });

  it("treats optimization tasks as metric-sensitive work", () => {
    const coverage = evaluateTaskWorkflow({
      assignee: "peer1",
      status: "done",
      outcome: "Attempt cut cold-start latency from 410 ms to 290 ms.",
      notes: [
        "Goal:",
        "- Reduce cold-start latency for the group context page",
        "",
        "Success Criteria:",
        "- Median latency drops without breaking cache freshness",
        "",
        "Required Evidence:",
        "- Bench output and smoke checks",
        "",
        "Baseline:",
        "- 410 ms median",
        "",
        "Primary Metric:",
        "- Median cold-start latency",
        "",
        "Verifier Boundary:",
        "- Same browser profile and same mock dataset",
        "",
        "Closeout Verdict:",
        "- DONE_WITH_CONCERNS",
        "",
        "Verification Summary:",
        "- Benchmark and manual smoke both passed",
      ].join("\n"),
    });

    expect(coverage.isOptimization).toBe(true);
    expect(coverage.needsContract).toBe(false);
    expect(coverage.needsCloseout).toBe(true);
    expect(coverage.missingCloseout).toEqual(["Attempt decision"]);
  });

  it("uses goal text as the display fallback when outcome is blank", () => {
    expect(getTaskDisplaySummary({
      notes: "Goal:\n- Ship the 0.4.5 release cleanly",
      outcome: "",
    })).toContain("Ship the 0.4.5 release cleanly");

    expect(getTaskDisplaySummary({
      notes: "Goal:\n- Ship the 0.4.5 release cleanly",
      outcome: "Release shipped with smoke verification.",
    })).toBe("Release shipped with smoke verification.");
  });

  it("blocks done transition only when closeout is incomplete for root or optimization work", () => {
    expect(getTaskDoneTransitionBlockers({
      parentId: "T001",
      outcome: "",
      notes: "",
    })).toEqual([]);

    expect(getTaskDoneTransitionBlockers({
      assignee: "foreman",
      outcome: "Shipped the panel refresh.",
      notes: [
        "Goal:",
        "- Refresh the panel",
        "",
        "Success Criteria:",
        "- Users can switch reachability modes clearly",
        "",
        "Required Evidence:",
        "- Manual smoke checks",
        "",
        "Closeout Verdict:",
        "- DONE",
      ].join("\n"),
    })).toEqual(["Verification summary"]);

    expect(getTaskDoneTransitionBlockers({
      assignee: "peer1",
      outcome: "Reduced cold-start time from 410 ms to 290 ms.",
      notes: [
        "Goal:",
        "- Reduce cold-start latency",
        "",
        "Success Criteria:",
        "- Median latency drops without regression",
        "",
        "Required Evidence:",
        "- Bench output and smoke checks",
        "",
        "Baseline:",
        "- 410 ms median",
        "",
        "Primary Metric:",
        "- Median cold-start latency",
        "",
        "Verifier Boundary:",
        "- Same browser profile",
        "",
        "Closeout Verdict:",
        "- DONE",
        "",
        "Verification Summary:",
        "- Bench output captured",
      ].join("\n"),
    })).toEqual(["Attempt decision"]);

    expect(getTaskDoneTransitionBlockers({
      assignee: "peer1",
      outcome: "Reduced cold-start time from 410 ms to 290 ms.",
      notes: [
        "Goal:",
        "- Reduce cold-start latency",
        "",
        "Success Criteria:",
        "- Median latency drops without regression",
        "",
        "Required Evidence:",
        "- Bench output and smoke checks",
        "",
        "Baseline:",
        "- 410 ms median",
        "",
        "Primary Metric:",
        "- Median cold-start latency",
        "",
        "Verifier Boundary:",
        "- Same browser profile",
        "",
        "Closeout Verdict:",
        "- DONE",
        "",
        "Verification Summary:",
        "- Bench output captured",
        "",
        "Attempt Decision:",
        "- keep",
      ].join("\n"),
    })).toEqual([]);
  });
});
