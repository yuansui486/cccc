export type TaskWorkflowScaffoldId =
  | "lean"
  | "root"
  | "planner"
  | "reviewer"
  | "debugger"
  | "release"
  | "optimization";

export type TaskWorkflowPresetFamily = "lean" | "contract" | "optimization";

export type TaskReleaseCloseoutState =
  | "missing_setup"
  | "in_progress"
  | "needs_closeout"
  | "ready";

type TaskWorkflowChecklistItemLike = {
  text?: string | null;
  status?: string | null;
};

export type TaskWorkflowFields = {
  parentId?: string | null;
  parent_id?: string | null;
  status?: string | null;
  assignee?: string | null;
  outcome?: string | null;
  notes?: string | null;
  checklist?: string | TaskWorkflowChecklistItemLike[] | null;
};

export type TaskWorkflowScaffold = {
  id: TaskWorkflowScaffoldId;
  family: TaskWorkflowPresetFamily;
  label: string;
  description: string;
  notes: string;
  checklist: string;
};

export type TaskWorkflowCoverage = {
  scaffoldId: TaskWorkflowScaffoldId;
  scaffoldFamily: TaskWorkflowPresetFamily;
  isRoot: boolean;
  isOptimization: boolean;
  isRelease: boolean;
  missingSetup: string[];
  missingCloseout: string[];
  needsContract: boolean;
  needsCloseout: boolean;
  checklistTotal: number;
  checklistDone: number;
  releaseCloseoutState: TaskReleaseCloseoutState | null;
  hasGoal: boolean;
  hasSuccessCriteria: boolean;
  hasRequiredEvidence: boolean;
  hasOwner: boolean;
  hasOutcomeSummary: boolean;
  hasCloseoutVerdict: boolean;
  hasVerificationSummary: boolean;
  hasBaseline: boolean;
  hasPrimaryMetric: boolean;
  hasVerifierBoundary: boolean;
  hasAttemptDecision: boolean;
  goalSummary: string;
};

const ROOT_SECTION_ORDER = [
  "Goal",
  "Success Criteria",
  "Required Evidence",
  "Next Control Move If Unmet",
  "Closeout Verdict",
  "Verification Summary",
  "Open Concerns",
] as const;

const OPTIMIZATION_SECTION_ORDER = [
  "Baseline",
  "Primary Metric",
  "Verifier Boundary",
  "Attempt Decision",
] as const;

const ALL_SECTION_HEADINGS = new Set(
  [...ROOT_SECTION_ORDER, ...OPTIMIZATION_SECTION_ORDER].map((item) => item.toLowerCase())
);

const ROOT_NOTES_TEMPLATE = [
  "Goal:",
  "- [state the intended end result]",
  "",
  "Success Criteria:",
  "- [define the observable finish line]",
  "",
  "Required Evidence:",
  "- [name the proof, checks, or artifacts required]",
  "",
  "Next Control Move If Unmet:",
  "- [continue / request evidence / handoff / blocked / needs context]",
  "",
  "Closeout Verdict:",
  "- [DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT]",
  "",
  "Verification Summary:",
  "- [summarize the proof gathered at closeout]",
  "",
  "Open Concerns:",
  "- [leave blank if none]",
].join("\n");

const OPTIMIZATION_NOTES_TEMPLATE = [
  ROOT_NOTES_TEMPLATE,
  "",
  "Baseline:",
  "- [capture the current measured state]",
  "",
  "Primary Metric:",
  "- [name the one metric that decides keep vs discard]",
  "",
  "Verifier Boundary:",
  "- [state what stays stable while testing]",
  "",
  "Attempt Decision:",
  "- [keep / discard / continue]",
].join("\n");

function checklistLines(items: string[]): string {
  return items.map((item) => `[ ] ${item}`).join("\n");
}

export const TASK_WORKFLOW_SCAFFOLDS: TaskWorkflowScaffold[] = [
  {
    id: "lean",
    family: "lean",
    label: "Lean",
    description: "Keep it minimal. Use this when the task is small enough that extra structure would just add noise.",
    notes: "",
    checklist: "",
  },
  {
    id: "root",
    family: "contract",
    label: "Root",
    description: "Goal, success criteria, required evidence, and closeout in one place. Best default for foreman-owned or user-facing tasks.",
    notes: ROOT_NOTES_TEMPLATE,
    checklist: checklistLines([
      "Confirm the goal and owner",
      "Define success criteria",
      "Name the required evidence",
      "Record the final verdict and verification summary",
    ]),
  },
  {
    id: "planner",
    family: "contract",
    label: "Planner",
    description: "For planning and decomposition work. Bias toward minimum path, existing leverage, and explicit acceptance.",
    notes: ROOT_NOTES_TEMPLATE,
    checklist: checklistLines([
      "Check what existing workflow or code already solves part of this",
      "Choose the minimum path",
      "Capture unresolved decisions or dependencies",
      "Define acceptance before handoff",
    ]),
  },
  {
    id: "reviewer",
    family: "contract",
    label: "Reviewer",
    description: "For review and audit work. Bias toward full reading, severity ordering, and evidence-backed findings.",
    notes: ROOT_NOTES_TEMPLATE,
    checklist: checklistLines([
      "Read the full diff or affected surface",
      "List concrete findings only",
      "Check docs, tests, and adjacent drift",
      "Record the verdict and required fixes",
    ]),
  },
  {
    id: "debugger",
    family: "contract",
    label: "Debugger",
    description: "For bug-fixing work. Bias toward repro, hypothesis, proof, minimal fix, and regression coverage.",
    notes: ROOT_NOTES_TEMPLATE,
    checklist: checklistLines([
      "Capture a stable repro",
      "Name the leading hypothesis",
      "Collect proof before changing behavior",
      "Apply the minimal fix",
      "Add or update regression coverage",
    ]),
  },
  {
    id: "release",
    family: "contract",
    label: "Release",
    description: "For ship or bump work. Bias toward release notes, version-surface checks, and smoke verification.",
    notes: ROOT_NOTES_TEMPLATE,
    checklist: checklistLines([
      "Update the release note",
      "Check version and docs drift",
      "Run smoke verification",
      "Record the shipping verdict",
    ]),
  },
  {
    id: "optimization",
    family: "optimization",
    label: "Optimization",
    description: "For metric-sensitive work. Capture baseline, primary metric, verifier boundary, and keep-or-discard closeout.",
    notes: OPTIMIZATION_NOTES_TEMPLATE,
    checklist: checklistLines([
      "Capture the baseline",
      "Name the primary metric",
      "Freeze the verifier boundary",
      "Run one bounded attempt",
      "Record keep or discard",
    ]),
  },
];

export function getTaskWorkflowScaffold(id: TaskWorkflowScaffoldId): TaskWorkflowScaffold {
  return TASK_WORKFLOW_SCAFFOLDS.find((item) => item.id === id) || TASK_WORKFLOW_SCAFFOLDS[0];
}

function normalizeText(value: string | null | undefined): string {
  return String(value || "").replace(/\r/g, "").trim();
}

function stripPlaceholders(value: string): string {
  return value
    .replace(/\[[^\]]*\]/g, " ")
    .replace(/<[^>]*>/g, " ")
    .replace(/[•*_`]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function hasMeaningfulText(value: string | null | undefined): boolean {
  const normalized = normalizeText(value);
  if (!normalized) return false;
  return stripPlaceholders(normalized).length > 0;
}

function normalizedParentId(fields: TaskWorkflowFields): string {
  return String(fields.parentId ?? fields.parent_id ?? "").trim();
}

function normalizeChecklistItemText(value: string | null | undefined): string {
  return String(value || "")
    .replace(/\[[^\]]*\]/g, " ")
    .replace(/^[-*]\s*/, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function checklistItemsFromString(value: string | null | undefined): Array<{ text: string; status: string }> {
  return normalizeText(value)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(/^\[([ xX~])\]\s*(.+)$/);
      const status = match
        ? match[1].toLowerCase() === "x"
          ? "done"
          : match[1] === "~"
            ? "in_progress"
            : "pending"
        : "pending";
      const text = (match ? match[2] : line).trim();
      return { text, status };
    })
    .filter((item) => normalizeChecklistItemText(item.text).length > 0);
}

function checklistItemsFromFields(
  checklist: string | TaskWorkflowChecklistItemLike[] | null | undefined
): Array<{ text: string; status: string }> {
  if (typeof checklist === "string" || checklist == null) {
    return checklistItemsFromString(checklist);
  }
  if (!Array.isArray(checklist)) {
    return [];
  }
  return checklist
    .map((item) => ({
      text: String(item?.text || "").trim(),
      status: String(item?.status || "pending").trim().toLowerCase() || "pending",
    }))
    .filter((item) => normalizeChecklistItemText(item.text).length > 0);
}

function checklistProgress(fields: TaskWorkflowFields): { total: number; done: number } {
  const items = checklistItemsFromFields(fields.checklist);
  return {
    total: items.length,
    done: items.filter((item) => item.status === "done").length,
  };
}

function normalizedNotes(fields: TaskWorkflowFields): string {
  return normalizeText(fields.notes);
}

function normalizedStatus(fields: TaskWorkflowFields): string {
  return String(fields.status || "planned").trim().toLowerCase();
}

function isSectionHeading(line: string): boolean {
  const trimmed = String(line || "").trim();
  if (!trimmed.endsWith(":")) return false;
  return ALL_SECTION_HEADINGS.has(trimmed.slice(0, -1).trim().toLowerCase());
}

function extractSectionValue(notes: string | null | undefined, heading: string): string {
  const target = heading.trim().toLowerCase();
  const lines = normalizeText(notes).split("\n");
  const out: string[] = [];
  let capturing = false;
  for (const rawLine of lines) {
    const line = String(rawLine || "");
    const trimmed = line.trim();
    if (!capturing) {
      const match = trimmed.match(/^([^:]+):\s*(.*)$/);
      if (!match) continue;
      if (match[1].trim().toLowerCase() !== target) continue;
      capturing = true;
      if (match[2].trim()) out.push(match[2].trim());
      continue;
    }
    if (isSectionHeading(trimmed)) break;
    out.push(line);
  }
  return out.join("\n").trim();
}

export function getTaskGoalSummary(fields: TaskWorkflowFields): string {
  const goal = extractSectionValue(fields.notes, "Goal");
  if (hasMeaningfulText(goal)) return goal;
  return "";
}

export function getTaskDisplaySummary(fields: TaskWorkflowFields): string {
  const outcome = normalizeText(fields.outcome);
  if (hasMeaningfulText(outcome)) return outcome;
  return getTaskGoalSummary(fields);
}

function inferChecklistScaffold(fields: TaskWorkflowFields): TaskWorkflowScaffoldId | null {
  const checklistItems = checklistItemsFromFields(fields.checklist);
  if (!checklistItems.length) return null;
  const checklistSet = new Set(checklistItems.map((item) => normalizeChecklistItemText(item.text)));
  let bestId: TaskWorkflowScaffoldId | null = null;
  let bestScore = 0;
  let bestMatchCount = 0;
  for (const scaffold of TASK_WORKFLOW_SCAFFOLDS) {
    if (scaffold.id === "lean" || scaffold.id === "optimization") continue;
    const seededItems = checklistItemsFromString(scaffold.checklist).map((item) => normalizeChecklistItemText(item.text));
    if (!seededItems.length) continue;
    const matchCount = seededItems.filter((item) => checklistSet.has(item)).length;
    if (!matchCount) continue;
    const score = matchCount / seededItems.length;
    if (
      score > bestScore ||
      (score === bestScore && matchCount > bestMatchCount)
    ) {
      bestId = scaffold.id;
      bestScore = score;
      bestMatchCount = matchCount;
    }
  }
  if (!bestId) return null;
  if (bestId === "root") {
    return bestScore >= 0.5 ? bestId : null;
  }
  return bestScore >= 0.5 ? bestId : null;
}

export function recommendTaskWorkflowScaffold(fields: TaskWorkflowFields): TaskWorkflowScaffoldId {
  if (isOptimizationTask(fields)) return "optimization";
  return normalizedParentId(fields) ? "lean" : "root";
}

export function isOptimizationTask(fields: TaskWorkflowFields): boolean {
  const notes = normalizedNotes(fields);
  return OPTIMIZATION_SECTION_ORDER.some((heading) => notes.toLowerCase().includes(`${heading.toLowerCase()}:`));
}

export function resolveTaskWorkflowScaffold(fields: TaskWorkflowFields): TaskWorkflowScaffoldId {
  if (isOptimizationTask(fields)) return "optimization";
  if (normalizedParentId(fields)) return "lean";
  return inferChecklistScaffold(fields) || "root";
}

export function evaluateTaskWorkflow(fields: TaskWorkflowFields): TaskWorkflowCoverage {
  const scaffoldId = resolveTaskWorkflowScaffold(fields);
  const scaffoldFamily = getTaskWorkflowScaffold(scaffoldId).family;
  const isRoot = !normalizedParentId(fields);
  const isOptimization = isOptimizationTask(fields);
  const isRelease = scaffoldId === "release";
  const status = normalizedStatus(fields);
  const goalSummary = getTaskGoalSummary(fields);
  const hasGoal = hasMeaningfulText(goalSummary);
  const hasSuccessCriteria = hasMeaningfulText(extractSectionValue(fields.notes, "Success Criteria"));
  const hasRequiredEvidence = hasMeaningfulText(extractSectionValue(fields.notes, "Required Evidence"));
  const hasOwner = hasMeaningfulText(fields.assignee);
  const hasOutcomeSummary = hasMeaningfulText(fields.outcome);
  const hasCloseoutVerdict = hasMeaningfulText(extractSectionValue(fields.notes, "Closeout Verdict"));
  const hasVerificationSummary = hasMeaningfulText(extractSectionValue(fields.notes, "Verification Summary"));
  const hasBaseline = hasMeaningfulText(extractSectionValue(fields.notes, "Baseline"));
  const hasPrimaryMetric = hasMeaningfulText(extractSectionValue(fields.notes, "Primary Metric"));
  const hasVerifierBoundary = hasMeaningfulText(extractSectionValue(fields.notes, "Verifier Boundary"));
  const hasAttemptDecision = hasMeaningfulText(extractSectionValue(fields.notes, "Attempt Decision"));
  const checklistState = checklistProgress(fields);

  const missingSetup: string[] = [];
  if (isRoot || isOptimization) {
    if (!hasGoal) missingSetup.push("Goal");
    if (!hasSuccessCriteria) missingSetup.push("Success criteria");
    if (!hasRequiredEvidence) missingSetup.push("Required evidence");
  }
  if (isRoot && !hasOwner) {
    missingSetup.push("Owner");
  }
  if (isOptimization) {
    if (!hasBaseline) missingSetup.push("Baseline");
    if (!hasPrimaryMetric) missingSetup.push("Primary metric");
    if (!hasVerifierBoundary) missingSetup.push("Verifier boundary");
  }

  const missingCloseout: string[] = [];
  const expectsCloseout = status === "done" && (isRoot || isOptimization);
  if (expectsCloseout) {
    if (!hasOutcomeSummary) missingCloseout.push("Outcome summary");
    if (!hasCloseoutVerdict) missingCloseout.push("Closeout verdict");
    if (!hasVerificationSummary) missingCloseout.push("Verification summary");
    if (isOptimization && !hasAttemptDecision) missingCloseout.push("Attempt decision");
  }

  let releaseCloseoutState: TaskReleaseCloseoutState | null = null;
  if (isRelease) {
    if (missingSetup.length > 0) {
      releaseCloseoutState = "missing_setup";
    } else if ((status === "done" || checklistState.done === checklistState.total) && missingCloseout.length > 0) {
      releaseCloseoutState = "needs_closeout";
    } else if (checklistState.total === 0 || checklistState.done < checklistState.total) {
      releaseCloseoutState = "in_progress";
    } else {
      releaseCloseoutState = "ready";
    }
  }

  return {
    scaffoldId,
    scaffoldFamily,
    isRoot,
    isOptimization,
    isRelease,
    missingSetup,
    missingCloseout,
    needsContract: missingSetup.length > 0,
    needsCloseout: missingCloseout.length > 0,
    checklistTotal: checklistState.total,
    checklistDone: checklistState.done,
    releaseCloseoutState,
    hasGoal,
    hasSuccessCriteria,
    hasRequiredEvidence,
    hasOwner,
    hasOutcomeSummary,
    hasCloseoutVerdict,
    hasVerificationSummary,
    hasBaseline,
    hasPrimaryMetric,
    hasVerifierBoundary,
    hasAttemptDecision,
    goalSummary,
  };
}

export function getTaskDoneTransitionBlockers(fields: TaskWorkflowFields): string[] {
  const coverage = evaluateTaskWorkflow({
    ...fields,
    status: "done",
  });
  return coverage.needsCloseout ? coverage.missingCloseout : [];
}
