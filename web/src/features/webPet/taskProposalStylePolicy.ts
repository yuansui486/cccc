import type { PetPeerContext } from "./petPeerContext";
import type { TaskProposalStylePolicy } from "./types";

const DEFAULT_TASK_PROPOSAL_STYLE_POLICY: TaskProposalStylePolicy = {
  tone: "cautious",
  ownershipDriftMode: "reconfirm",
  stalledActiveMode: "reconfirm",
  waitingUserMode: "sync",
};

function normalizeText(value: string): string {
  return String(value || "").trim().toLowerCase();
}

function parseTaggedValue(source: string, key: string): string {
  const pattern = new RegExp(`${key}\\s*:\\s*([a-z_]+)`, "i");
  const match = source.match(pattern);
  return normalizeText(match?.[1] || "");
}

export function getDefaultTaskProposalStylePolicy(): TaskProposalStylePolicy {
  return { ...DEFAULT_TASK_PROPOSAL_STYLE_POLICY };
}

export function deriveTaskProposalStylePolicy(
  petContext?: Partial<Pick<PetPeerContext, "persona" | "help" | "prompt">> | null,
): TaskProposalStylePolicy {
  const combined = [
    String(petContext?.persona || ""),
    String(petContext?.help || ""),
    String(petContext?.prompt || ""),
  ].join("\n");
  const normalized = normalizeText(combined);
  if (!normalized) return getDefaultTaskProposalStylePolicy();

  const policy = getDefaultTaskProposalStylePolicy();

  const toneTag = parseTaggedValue(combined, "task-proposal-tone");
  const ownershipTag = parseTaggedValue(combined, "task-proposal-ownership");
  const stalledTag = parseTaggedValue(combined, "task-proposal-stalled");
  const waitingTag = parseTaggedValue(combined, "task-proposal-waiting-user");

  if (toneTag === "direct" || toneTag === "cautious") {
    policy.tone = toneTag;
  } else if (
    normalized.includes("low-noise")
    || normalized.includes("low noise")
    || normalized.includes("谨慎")
    || normalized.includes("低噪")
    || normalized.includes("保守")
  ) {
    policy.tone = "cautious";
  } else if (
    normalized.includes("direct")
    || normalized.includes("actionable")
    || normalized.includes("直接")
    || normalized.includes("明确指令")
    || normalized.includes("强指令")
  ) {
    policy.tone = "direct";
  }

  if (ownershipTag === "reassign" || ownershipTag === "reconfirm") {
    policy.ownershipDriftMode = ownershipTag;
  } else if (
    normalized.includes("avoid handoff")
    || normalized.includes("avoid direct handoff")
    || normalized.includes("先确认 owner")
    || normalized.includes("重新确认 owner")
    || normalized.includes("避免直接 handoff")
  ) {
    policy.ownershipDriftMode = "reconfirm";
  } else if (
    normalized.includes("reassign stale task")
    || normalized.includes("prefer reassign")
    || normalized.includes("直接改 owner")
    || normalized.includes("直接重新分配")
  ) {
    policy.ownershipDriftMode = "reassign";
  }

  if (stalledTag === "escalate" || stalledTag === "reconfirm") {
    policy.stalledActiveMode = stalledTag;
  } else if (
    normalized.includes("escalate stalled")
    || normalized.includes("升级停滞任务")
    || normalized.includes("escalate stalled active")
  ) {
    policy.stalledActiveMode = "escalate";
  }

  if (waitingTag === "close" || waitingTag === "sync") {
    policy.waitingUserMode = waitingTag;
  } else if (
    normalized.includes("close waiting_on=user")
    || normalized.includes("close waiting user")
    || normalized.includes("收口用户依赖")
    || normalized.includes("明确收口 waiting_on=user")
  ) {
    policy.waitingUserMode = "close";
  }

  return policy;
}
