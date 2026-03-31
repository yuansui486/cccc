type SnapshotTranslate = (
  key: string,
  fallback: string,
  vars?: Record<string, unknown>,
) => string;

const TASKS_PATTERN =
  /^Tasks:\s*total=(\d+),\s*active=(\d+),\s*done=(\d+),\s*archived=(\d+)$/i;
const VALUE_LINE_PREFIXES: Array<{ prefix: string; key: string; fallback: string }> = [
  { prefix: "Agent Snapshot: ", key: "snapshot.agentSnapshot", fallback: "Agent Snapshot: {{value}}" },
  { prefix: "Blocked Tasks: ", key: "snapshot.blockedTasks", fallback: "Blocked Tasks: {{value}}" },
  { prefix: "Waiting User Tasks: ", key: "snapshot.waitingUserTasks", fallback: "Waiting User Tasks: {{value}}" },
  { prefix: "Handoff Tasks: ", key: "snapshot.handoffTasks", fallback: "Handoff Tasks: {{value}}" },
  { prefix: "Planned Backlog: ", key: "snapshot.plannedBacklog", fallback: "Planned Backlog: {{value}}" },
  { prefix: "Task Proposals: ", key: "snapshot.taskProposals", fallback: "Task Proposals: {{value}}" },
];

export function formatPetSnapshotLine(
  line: string,
  tr: SnapshotTranslate,
): string {
  const trimmed = String(line || "").trim();
  if (!trimmed) return "";

  if (trimmed.startsWith("Group: ")) {
    return tr("snapshot.group", "Group: {{value}}", {
      value: trimmed.slice("Group: ".length).trim(),
    });
  }

  if (trimmed.startsWith("Group State: ")) {
    return tr("snapshot.groupState", "Group State: {{value}}", {
      value: trimmed.slice("Group State: ".length).trim(),
    });
  }

  const tasksMatch = trimmed.match(TASKS_PATTERN);
  if (tasksMatch) {
    return tr(
      "snapshot.tasks",
      "Tasks: total={{total}}, active={{active}}, done={{done}}, archived={{archived}}",
      {
        total: Number(tasksMatch[1] || 0),
        active: Number(tasksMatch[2] || 0),
        done: Number(tasksMatch[3] || 0),
        archived: Number(tasksMatch[4] || 0),
      },
    );
  }

  for (const entry of VALUE_LINE_PREFIXES) {
    if (trimmed.startsWith(entry.prefix)) {
      return tr(entry.key, entry.fallback, {
        value: trimmed.slice(entry.prefix.length).trim(),
      });
    }
  }

  return trimmed;
}

export function formatPetSnapshot(
  snapshot: string,
  tr: SnapshotTranslate,
): string {
  return String(snapshot || "")
    .split(/\r?\n/u)
    .map((line) => formatPetSnapshotLine(line, tr))
    .filter(Boolean)
    .join("\n");
}

export function getPetSnapshotHeadline(
  snapshot: string,
  tr: SnapshotTranslate,
): string {
  const firstLine = String(snapshot || "")
    .split(/\r?\n/u)
    .find((line) => String(line || "").trim());
  return firstLine ? formatPetSnapshotLine(firstLine, tr) : "";
}
