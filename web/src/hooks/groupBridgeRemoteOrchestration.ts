import type { GroupBridgeRemoteReceipt, GroupBridgeRemoteTarget } from "../services/api/groupBridge";

export const GROUP_BRIDGE_REMOTE_POLL_LIMIT = 20;
export const GROUP_BRIDGE_REMOTE_POLL_DELAY_MS = 1000;

export type GroupBridgeRemoteOperationKind = "load" | "send" | "poll";

export interface GroupBridgeRemoteOperationToken {
  readonly groupId: string;
  readonly generation: number;
  readonly registrationId: string;
  readonly routeGeneration: number;
  readonly messageGeneration: number;
  readonly kind: GroupBridgeRemoteOperationKind;
  readonly operation: number;
  readonly signal: AbortSignal;
}

type ActiveOperation = {
  token: GroupBridgeRemoteOperationToken;
  controller: AbortController;
};

export function normalizeGroupBridgeRemoteGroupId(groupId?: string): string {
  return String(groupId || "").trim();
}

export function isGroupBridgeRemoteViewCurrent(selectedGroupId?: string, viewGroupId?: string): boolean {
  const selected = normalizeGroupBridgeRemoteGroupId(selectedGroupId);
  return Boolean(selected) && normalizeGroupBridgeRemoteGroupId(viewGroupId) === selected;
}

export class GroupBridgeRemoteLifecycle {
  private groupId = "";
  private generation = 0;
  private registrationId = "";
  private routeGeneration = 0;
  private messageGeneration = 0;
  private operation = 0;
  private readonly active = new Map<GroupBridgeRemoteOperationKind, ActiveOperation>();

  selectGroup(groupId?: string): void {
    const nextGroupId = normalizeGroupBridgeRemoteGroupId(groupId);
    if (nextGroupId === this.groupId) return;
    this.abortAll();
    this.groupId = nextGroupId;
    this.generation += 1;
    this.registrationId = "";
    this.routeGeneration += 1;
    this.messageGeneration += 1;
  }

  selectRoute(groupId: string, registrationId?: string): boolean {
    const normalizedGroupId = normalizeGroupBridgeRemoteGroupId(groupId);
    if (!normalizedGroupId || normalizedGroupId !== this.groupId) return false;
    const nextRegistrationId = String(registrationId || "").trim();
    if (nextRegistrationId === this.registrationId) return true;
    this.abort("send");
    this.abort("poll");
    this.registrationId = nextRegistrationId;
    this.routeGeneration += 1;
    this.messageGeneration += 1;
    return true;
  }

  currentRoute(groupId?: string): string {
    return normalizeGroupBridgeRemoteGroupId(groupId) === this.groupId ? this.registrationId : "";
  }

  begin(
    kind: GroupBridgeRemoteOperationKind,
    groupId?: string,
    registrationId?: string,
    messageGeneration?: number,
  ): GroupBridgeRemoteOperationToken | null {
    const normalizedGroupId = normalizeGroupBridgeRemoteGroupId(groupId);
    if (!normalizedGroupId || normalizedGroupId !== this.groupId) return null;
    const normalizedRegistrationId = String(registrationId || "").trim();
    if (kind !== "load" && (!normalizedRegistrationId || normalizedRegistrationId !== this.registrationId)) {
      return null;
    }
    if (kind === "poll" && messageGeneration !== this.messageGeneration) return null;
    if (kind === "send") {
      this.abort("send");
      this.abort("poll");
      this.messageGeneration += 1;
    } else {
      this.abort(kind);
    }
    const controller = new AbortController();
    const token: GroupBridgeRemoteOperationToken = {
      groupId: this.groupId,
      generation: this.generation,
      registrationId: kind === "load" ? "" : this.registrationId,
      routeGeneration: kind === "load" ? 0 : this.routeGeneration,
      messageGeneration: kind === "load" ? 0 : this.messageGeneration,
      kind,
      operation: ++this.operation,
      signal: controller.signal,
    };
    this.active.set(kind, { token, controller });
    return token;
  }

  isCurrent(token: GroupBridgeRemoteOperationToken): boolean {
    const entry = this.active.get(token.kind);
    return Boolean(
      entry
      && entry.token === token
      && token.groupId === this.groupId
      && token.generation === this.generation
      && (token.kind === "load" || (
        token.registrationId === this.registrationId
        && token.routeGeneration === this.routeGeneration
        && token.messageGeneration === this.messageGeneration
      ))
      && !token.signal.aborted,
    );
  }

  commit(token: GroupBridgeRemoteOperationToken, apply: () => void): boolean {
    if (!this.isCurrent(token)) return false;
    apply();
    return true;
  }

  finish(token: GroupBridgeRemoteOperationToken): boolean {
    if (!this.isCurrent(token)) return false;
    this.active.delete(token.kind);
    return true;
  }

  abort(tokenOrKind: GroupBridgeRemoteOperationToken | GroupBridgeRemoteOperationKind): void {
    const kind = typeof tokenOrKind === "string" ? tokenOrKind : tokenOrKind.kind;
    const entry = this.active.get(kind);
    if (!entry || (typeof tokenOrKind !== "string" && entry.token !== tokenOrKind)) return;
    this.active.delete(kind);
    entry.controller.abort();
  }

  private abortAll(): void {
    for (const entry of this.active.values()) entry.controller.abort();
    this.active.clear();
  }
}

type GroupBridgeRemoteComputerPermissions = {
  publish: boolean;
  trust: boolean;
  unattendedTriggers: boolean;
};

export type GroupBridgeRemoteComposerDraft = {
  composerText: string;
  composerFiles: readonly File[];
  selectedSkillCommand: string;
  toText: string;
  replyTarget: unknown;
  quotedPresentationRef: unknown;
  priority: "normal" | "attention";
  replyRequired: boolean;
  collaborationRequired: boolean;
  computerControlEnabled?: boolean;
  computerControlWorkflowId?: string;
  computerControlActorId?: string;
  computerControlPermissions?: GroupBridgeRemoteComputerPermissions;
};

export type GroupBridgeRemoteComposerState = {
  activeGroupId: string;
  composerText: string;
  composerFiles: readonly File[];
  selectedSkillCommand: string;
  toText: string;
  replyTarget: unknown;
  quotedPresentationRef: unknown;
  priority: "normal" | "attention";
  replyRequired: boolean;
  collaborationRequired: boolean;
  computerControlEnabled: boolean;
  computerControlWorkflowId: string;
  computerControlActorId: string;
  computerControlPermissions: GroupBridgeRemoteComputerPermissions;
  destGroupId: string;
  drafts: Record<string, GroupBridgeRemoteComposerDraft | undefined>;
  normalToTextByGroup: Record<string, string | undefined>;
};

export type GroupBridgeRemoteComposerIntent = Omit<
  GroupBridgeRemoteComposerState,
  "drafts" | "normalToTextByGroup"
> & {
  groupId: string;
  composerFiles: readonly File[];
  draft: GroupBridgeRemoteComposerDraft | null;
  normalToText: string | undefined;
};

function copyPermissions(
  permissions: GroupBridgeRemoteComputerPermissions | undefined,
): GroupBridgeRemoteComputerPermissions | undefined {
  return permissions ? { ...permissions } : undefined;
}

function copyDraft(draft: GroupBridgeRemoteComposerDraft | undefined): GroupBridgeRemoteComposerDraft | null {
  return draft ? {
    ...draft,
    composerFiles: [...draft.composerFiles],
    computerControlPermissions: copyPermissions(draft.computerControlPermissions),
  } : null;
}

export function captureGroupBridgeRemoteComposerIntent(
  groupId: string,
  state: GroupBridgeRemoteComposerState,
): GroupBridgeRemoteComposerIntent {
  const normalizedGroupId = normalizeGroupBridgeRemoteGroupId(groupId);
  return {
    groupId: normalizedGroupId,
    activeGroupId: state.activeGroupId,
    composerText: state.composerText,
    composerFiles: [...state.composerFiles],
    selectedSkillCommand: state.selectedSkillCommand,
    toText: state.toText,
    replyTarget: state.replyTarget,
    quotedPresentationRef: state.quotedPresentationRef,
    priority: state.priority,
    replyRequired: state.replyRequired,
    collaborationRequired: state.collaborationRequired,
    computerControlEnabled: state.computerControlEnabled,
    computerControlWorkflowId: state.computerControlWorkflowId,
    computerControlActorId: state.computerControlActorId,
    computerControlPermissions: { ...state.computerControlPermissions },
    destGroupId: state.destGroupId,
    draft: copyDraft(state.drafts[normalizedGroupId]),
    normalToText: state.normalToTextByGroup[normalizedGroupId],
  };
}

function sameFiles(left: readonly File[], right: readonly File[]): boolean {
  return left.length === right.length && left.every((file, index) => file === right[index]);
}

function samePermissions(
  left: GroupBridgeRemoteComputerPermissions | undefined,
  right: GroupBridgeRemoteComputerPermissions | undefined,
): boolean {
  return left === right || Boolean(
    left
    && right
    && left.publish === right.publish
    && left.trust === right.trust
    && left.unattendedTriggers === right.unattendedTriggers,
  );
}

function sameDraft(
  left: GroupBridgeRemoteComposerDraft | null,
  right: GroupBridgeRemoteComposerDraft | null,
): boolean {
  if (!left || !right) return left === right;
  return left.composerText === right.composerText
    && sameFiles(left.composerFiles, right.composerFiles)
    && left.selectedSkillCommand === right.selectedSkillCommand
    && left.toText === right.toText
    && left.replyTarget === right.replyTarget
    && left.quotedPresentationRef === right.quotedPresentationRef
    && left.priority === right.priority
    && left.replyRequired === right.replyRequired
    && left.collaborationRequired === right.collaborationRequired
    && left.computerControlEnabled === right.computerControlEnabled
    && left.computerControlWorkflowId === right.computerControlWorkflowId
    && left.computerControlActorId === right.computerControlActorId
    && samePermissions(left.computerControlPermissions, right.computerControlPermissions);
}

export function isGroupBridgeRemoteComposerIntentCurrent(
  intent: GroupBridgeRemoteComposerIntent,
  state: GroupBridgeRemoteComposerState,
): boolean {
  const current = captureGroupBridgeRemoteComposerIntent(intent.groupId, state);
  return intent.activeGroupId === current.activeGroupId
    && intent.composerText === current.composerText
    && sameFiles(intent.composerFiles, current.composerFiles)
    && intent.selectedSkillCommand === current.selectedSkillCommand
    && intent.toText === current.toText
    && intent.replyTarget === current.replyTarget
    && intent.quotedPresentationRef === current.quotedPresentationRef
    && intent.priority === current.priority
    && intent.replyRequired === current.replyRequired
    && intent.collaborationRequired === current.collaborationRequired
    && intent.computerControlEnabled === current.computerControlEnabled
    && intent.computerControlWorkflowId === current.computerControlWorkflowId
    && intent.computerControlActorId === current.computerControlActorId
    && samePermissions(intent.computerControlPermissions, current.computerControlPermissions)
    && intent.destGroupId === current.destGroupId
    && sameDraft(intent.draft, current.draft)
    && intent.normalToText === current.normalToText;
}

export type GroupBridgeRemoteSendDecision =
  | { kind: "local" }
  | { kind: "reject"; reason: "target_unavailable" | "unsupported_composition" }
  | { kind: "remote"; target: GroupBridgeRemoteTarget };

export function decideGroupBridgeRemoteSend(args: {
  groupId: string;
  remoteTargetId: string;
  remoteTarget: GroupBridgeRemoteTarget | null;
  attachmentCount: number;
  hasReply: boolean;
  hasQuote: boolean;
  collaborationRequired: boolean;
  computerControlEnabled: boolean;
  selectedSkillCommand: string;
  text: string;
}): GroupBridgeRemoteSendDecision {
  const remoteTargetId = String(args.remoteTargetId || "").trim();
  if (!remoteTargetId) return { kind: "local" };
  if (
    !args.remoteTarget
    || args.remoteTarget.registration_id !== remoteTargetId
    || normalizeGroupBridgeRemoteGroupId(args.remoteTarget.group_id) !== normalizeGroupBridgeRemoteGroupId(args.groupId)
  ) {
    return { kind: "reject", reason: "target_unavailable" };
  }
  if (
    args.attachmentCount > 0
    || args.hasReply
    || args.hasQuote
    || args.collaborationRequired
    || args.computerControlEnabled
    || Boolean(String(args.selectedSkillCommand || "").trim())
    || String(args.text || "").trim().startsWith("/")
  ) {
    return { kind: "reject", reason: "unsupported_composition" };
  }
  return { kind: "remote", target: args.remoteTarget };
}

export type GroupBridgeRemotePollResult = {
  kind: "terminal" | "exhausted" | "unavailable" | "cancelled";
  receipt: GroupBridgeRemoteReceipt;
};

type GroupBridgeRemoteStatusResult =
  | { ok: true; receipt: GroupBridgeRemoteReceipt | null }
  | { ok: false };

function isTerminalReceipt(receipt: GroupBridgeRemoteReceipt): boolean {
  const status = String(receipt.status || "").trim();
  return status === "sent" || status === "failed";
}

export function waitForGroupBridgeRemotePoll(delayMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const onAbort = () => {
      globalThis.clearTimeout(timer);
      resolve();
    };
    const timer = globalThis.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function pollGroupBridgeRemoteReceipt(args: {
  initialReceipt: GroupBridgeRemoteReceipt;
  signal: AbortSignal;
  getStatus: (signal: AbortSignal) => Promise<GroupBridgeRemoteStatusResult>;
  onReceipt: (receipt: GroupBridgeRemoteReceipt) => void;
  limit?: number;
  delayMs?: number;
  wait?: (delayMs: number, signal: AbortSignal) => Promise<void>;
}): Promise<GroupBridgeRemotePollResult> {
  let lastReceipt = args.initialReceipt;
  if (isTerminalReceipt(lastReceipt)) return { kind: "terminal", receipt: lastReceipt };
  const limit = Math.max(0, args.limit ?? GROUP_BRIDGE_REMOTE_POLL_LIMIT);
  const delayMs = Math.max(0, args.delayMs ?? GROUP_BRIDGE_REMOTE_POLL_DELAY_MS);
  const wait = args.wait || waitForGroupBridgeRemotePoll;

  for (let attempt = 0; attempt < limit; attempt += 1) {
    await wait(delayMs, args.signal);
    if (args.signal.aborted) return { kind: "cancelled", receipt: lastReceipt };
    try {
      const response = await args.getStatus(args.signal);
      if (args.signal.aborted) return { kind: "cancelled", receipt: lastReceipt };
      if (!response.ok) return { kind: "unavailable", receipt: lastReceipt };
      if (!response.receipt) continue;
      lastReceipt = response.receipt;
      args.onReceipt(lastReceipt);
      if (isTerminalReceipt(lastReceipt)) return { kind: "terminal", receipt: lastReceipt };
    } catch {
      return args.signal.aborted
        ? { kind: "cancelled", receipt: lastReceipt }
        : { kind: "unavailable", receipt: lastReceipt };
    }
  }
  return { kind: "exhausted", receipt: lastReceipt };
}

export type GroupBridgeRemoteReceiptProjection = {
  label: string;
  eventId: string;
  status: string;
  statusUnavailable: boolean;
};

export function projectGroupBridgeRemoteReceipt(
  receipt: GroupBridgeRemoteReceipt | null,
  statusUnavailable = false,
): GroupBridgeRemoteReceiptProjection {
  const status = String(receipt?.status || "").trim();
  const eventId = String(receipt?.remote_event_id || "").trim();
  const receiptLabel = status === "sent" && eventId
    ? "Remote accepted / signed receipt verified"
    : status === "sent"
      ? "Remote receipt pending"
      : status === "failed"
        ? "Remote delivery failed"
        : status === "sending"
          ? "Remote sending"
          : status === "retrying"
            ? "Remote retrying"
            : status === "queued"
              ? "Remote queued"
              : "";
  return {
    label: statusUnavailable ? "Remote status unavailable" : receiptLabel,
    eventId,
    status,
    statusUnavailable,
  };
}
