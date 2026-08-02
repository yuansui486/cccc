import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import type { GroupBridgeRemoteReceipt, GroupBridgeRemoteTarget } from "../services/api/groupBridge";
import {
  GROUP_BRIDGE_REMOTE_POLL_LIMIT,
  GroupBridgeRemoteLifecycle,
  captureGroupBridgeRemoteComposerIntent,
  decideGroupBridgeRemoteSend,
  isGroupBridgeRemoteComposerIntentCurrent,
  isGroupBridgeRemoteViewCurrent,
  pollGroupBridgeRemoteReceipt,
  projectGroupBridgeRemoteReceipt,
  type GroupBridgeRemoteComposerState,
} from "./groupBridgeRemoteOrchestration";

const target: GroupBridgeRemoteTarget = {
  registration_id: "registration-a",
  group_id: "group-a",
  remote_group_id: "remote-a",
  remote_peer_id: "peer-a",
};

const baseDecision = {
  groupId: "group-a",
  remoteTargetId: target.registration_id,
  remoteTarget: target,
  attachmentCount: 0,
  hasReply: false,
  hasQuote: false,
  collaborationRequired: false,
  computerControlEnabled: false,
  selectedSkillCommand: "",
  text: "hello",
};

describe("GroupBridgeRemoteLifecycle", () => {
  it("keeps an aborted render pure, then aborts every old operation on commit", () => {
    const lifecycle = new GroupBridgeRemoteLifecycle();
    lifecycle.selectGroup("group-a");
    lifecycle.selectRoute("group-a", "registration-a");
    const loadA = lifecycle.begin("load", "group-a")!;
    const sendA = lifecycle.begin("send", "group-a", "registration-a")!;
    const pollA = lifecycle.begin("poll", "group-a", "registration-a", sendA.messageGeneration)!;

    expect(isGroupBridgeRemoteViewCurrent("group-b", "group-a")).toBe(false);
    expect(lifecycle.isCurrent(loadA)).toBe(true);
    expect(lifecycle.isCurrent(sendA)).toBe(true);
    expect(lifecycle.isCurrent(pollA)).toBe(true);

    lifecycle.selectGroup("group-b");
    expect(loadA.signal.aborted).toBe(true);
    expect(sendA.signal.aborted).toBe(true);
    expect(pollA.signal.aborted).toBe(true);
    expect(lifecycle.isCurrent(loadA)).toBe(false);
  });

  it("rejects stale load and send effects without disturbing the next send", () => {
    const lifecycle = new GroupBridgeRemoteLifecycle();
    const effects: string[] = [];
    lifecycle.selectGroup("group-a");
    lifecycle.selectRoute("group-a", "registration-a");
    const loadA = lifecycle.begin("load", "group-a")!;
    const sendA = lifecycle.begin("send", "group-a", "registration-a")!;

    lifecycle.selectGroup("group-b");
    lifecycle.selectRoute("group-b", "registration-b");
    const sendB = lifecycle.begin("send", "group-b", "registration-b")!;
    expect(lifecycle.commit(loadA, () => effects.push("targets-a"))).toBe(false);
    expect(lifecycle.commit(sendA, () => effects.push(
      "receipt-a",
      "request-a",
      "status-a",
      "clear-composer-a",
      "clear-draft-a",
      "dest-a",
      "message-sent-a",
    ))).toBe(false);
    expect(lifecycle.finish(sendA)).toBe(false);
    expect(lifecycle.isCurrent(sendB)).toBe(true);
    expect(effects).toEqual([]);
  });

  it("invalidates load, send, and poll ownership when the committed view unmounts", () => {
    const lifecycle = new GroupBridgeRemoteLifecycle();
    lifecycle.selectGroup("group-a");
    lifecycle.selectRoute("group-a", "registration-a");
    const load = lifecycle.begin("load", "group-a")!;
    const send = lifecycle.begin("send", "group-a", "registration-a")!;
    const poll = lifecycle.begin("poll", "group-a", "registration-a", send.messageGeneration)!;
    const effects: string[] = [];

    lifecycle.selectGroup("");

    for (const token of [load, send, poll]) {
      expect(token.signal.aborted).toBe(true);
      expect(lifecycle.commit(token, () => effects.push(token.kind))).toBe(false);
      expect(lifecycle.finish(token)).toBe(false);
    }
    expect(effects).toEqual([]);
  });

  it("binds send and poll to the exact committed route while load remains group-scoped", () => {
    const lifecycle = new GroupBridgeRemoteLifecycle();
    lifecycle.selectGroup("group-a");
    lifecycle.selectRoute("group-a", "registration-1");
    const load = lifecycle.begin("load", "group-a")!;
    const sendR1 = lifecycle.begin("send", "group-a", "registration-1")!;
    const pollR1 = lifecycle.begin("poll", "group-a", "registration-1", sendR1.messageGeneration)!;

    expect(lifecycle.selectRoute("group-a", "registration-2")).toBe(true);
    expect(sendR1.signal.aborted).toBe(true);
    expect(pollR1.signal.aborted).toBe(true);
    expect(load.signal.aborted).toBe(false);
    expect(lifecycle.commit(sendR1, () => {})).toBe(false);
    expect(lifecycle.finish(pollR1)).toBe(false);
    expect(lifecycle.begin("poll", "group-a", "registration-1", sendR1.messageGeneration)).toBeNull();

    const sendR2 = lifecycle.begin("send", "group-a", "registration-2")!;
    const pollR2 = lifecycle.begin("poll", "group-a", "registration-2", sendR2.messageGeneration)!;
    expect(lifecycle.begin("send", "group-a", "registration-1")).toBeNull();
    expect(lifecycle.finish(sendR1)).toBe(false);
    expect(lifecycle.isCurrent(sendR2)).toBe(true);
    expect(sendR2.signal.aborted).toBe(false);
    expect(lifecycle.isCurrent(pollR2)).toBe(true);

    expect(lifecycle.selectRoute("group-a", "")).toBe(true);
    expect(sendR2.signal.aborted).toBe(true);
    expect(pollR2.signal.aborted).toBe(true);
    expect(lifecycle.commit(sendR2, () => {})).toBe(false);
    expect(lifecycle.finish(pollR2)).toBe(false);
  });

  it("gives each same-route send exclusive ownership of its receipt and poll", () => {
    const lifecycle = new GroupBridgeRemoteLifecycle();
    const effects: string[] = [];
    lifecycle.selectGroup("group-a");
    lifecycle.selectRoute("group-a", "registration-a");

    const sendS1 = lifecycle.begin("send", "group-a", "registration-a")!;
    expect(lifecycle.finish(sendS1)).toBe(true);
    const pollP1 = lifecycle.begin("poll", "group-a", "registration-a", sendS1.messageGeneration)!;

    const sendS2 = lifecycle.begin("send", "group-a", "registration-a")!;
    expect(sendS2.messageGeneration).toBeGreaterThan(sendS1.messageGeneration);
    expect(pollP1.signal.aborted).toBe(true);
    expect(lifecycle.commit(pollP1, () => effects.push("late-p1-receipt"))).toBe(false);
    expect(lifecycle.finish(pollP1)).toBe(false);
    expect(lifecycle.isCurrent(sendS2)).toBe(true);
    expect(lifecycle.begin("poll", "group-a", "registration-a", sendS1.messageGeneration)).toBeNull();

    expect(lifecycle.finish(sendS2)).toBe(true);
    const pollP2 = lifecycle.begin("poll", "group-a", "registration-a", sendS2.messageGeneration)!;
    expect(pollP2.messageGeneration).toBe(sendS2.messageGeneration);
    lifecycle.abort(pollP1);
    expect(lifecycle.finish(pollP1)).toBe(false);
    expect(lifecycle.isCurrent(pollP2)).toBe(true);
    expect(effects).toEqual([]);
  });

  it("defensively revokes a pending same-route send when a newer send token is issued", () => {
    const lifecycle = new GroupBridgeRemoteLifecycle();
    lifecycle.selectGroup("group-a");
    lifecycle.selectRoute("group-a", "registration-a");
    const sendS1 = lifecycle.begin("send", "group-a", "registration-a")!;
    const sendS2 = lifecycle.begin("send", "group-a", "registration-a")!;

    expect(sendS1.signal.aborted).toBe(true);
    expect(lifecycle.commit(sendS1, () => {})).toBe(false);
    expect(lifecycle.finish(sendS1)).toBe(false);
    expect(lifecycle.isCurrent(sendS2)).toBe(true);
  });

  it("consumes rejected loads while keeping catch and busy cleanup generation-scoped", async () => {
    const settleRejectedLoad = async (
      lifecycle: GroupBridgeRemoteLifecycle,
      token: NonNullable<ReturnType<GroupBridgeRemoteLifecycle["begin"]>>,
      rejection: Promise<void>,
      state: { targets: string[]; busy: boolean },
    ) => {
      try {
        await rejection;
      } catch {
        lifecycle.commit(token, () => { state.targets = []; });
      } finally {
        if (lifecycle.finish(token)) state.busy = false;
      }
    };

    const currentLifecycle = new GroupBridgeRemoteLifecycle();
    currentLifecycle.selectGroup("group-a");
    const currentLoad = currentLifecycle.begin("load", "group-a")!;
    const currentState = { targets: ["old"], busy: true };
    await settleRejectedLoad(currentLifecycle, currentLoad, Promise.reject(new Error("body read failed")), currentState);
    expect(currentState).toEqual({ targets: [], busy: false });

    const staleLifecycle = new GroupBridgeRemoteLifecycle();
    staleLifecycle.selectGroup("group-a");
    const staleLoad = staleLifecycle.begin("load", "group-a")!;
    let rejectStale!: (error: Error) => void;
    const staleResponse = new Promise<void>((_resolve, reject) => { rejectStale = reject; });
    const staleState = { targets: ["group-b"], busy: true };
    const staleCompletion = settleRejectedLoad(staleLifecycle, staleLoad, staleResponse, staleState);
    staleLifecycle.selectGroup("group-b");
    const currentLoadB = staleLifecycle.begin("load", "group-b")!;
    rejectStale(new Error("late body read failed"));
    await staleCompletion;

    expect(staleState).toEqual({ targets: ["group-b"], busy: true });
    expect(staleLifecycle.isCurrent(currentLoadB)).toBe(true);
  });
});

function createComposerState(): GroupBridgeRemoteComposerState {
  const files = [
    { name: "first.txt" } as File,
    { name: "second.txt" } as File,
  ];
  return {
    activeGroupId: "group-a",
    composerText: "hello",
    composerFiles: files,
    selectedSkillCommand: "",
    toText: "@foreman",
    replyTarget: null,
    quotedPresentationRef: null,
    priority: "normal",
    replyRequired: false,
    collaborationRequired: false,
    computerControlEnabled: false,
    computerControlWorkflowId: "",
    computerControlActorId: "foreman",
    computerControlPermissions: { publish: true, trust: true, unattendedTriggers: true },
    destGroupId: "group-a",
    drafts: {
      "group-a": {
        composerText: "saved",
        composerFiles: [files[0]],
        selectedSkillCommand: "",
        toText: "@foreman",
        replyTarget: null,
        quotedPresentationRef: null,
        priority: "normal",
        replyRequired: false,
        collaborationRequired: false,
        computerControlEnabled: false,
        computerControlWorkflowId: "",
        computerControlActorId: "foreman",
        computerControlPermissions: { publish: true, trust: true, unattendedTriggers: true },
      },
    },
    normalToTextByGroup: { "group-a": "@foreman" },
  };
}

describe("Group Bridge remote composer intent", () => {
  it("consumes the original intent only while every composer fact is unchanged", () => {
    const state = createComposerState();
    const intent = captureGroupBridgeRemoteComposerIntent("group-a", state);
    const effects: string[] = [];

    if (isGroupBridgeRemoteComposerIntentCurrent(intent, state)) effects.push("clear-original-intent");

    expect(effects).toEqual(["clear-original-intent"]);
  });

  it("lets a late success consume only the unchanged intent", async () => {
    const unchanged = createComposerState();
    const unchangedIntent = captureGroupBridgeRemoteComposerIntent("group-a", unchanged);
    const unchangedEffects: string[] = [];
    await Promise.resolve().then(() => {
      if (isGroupBridgeRemoteComposerIntentCurrent(unchangedIntent, unchanged)) {
        unchangedEffects.push("clear-composer", "clear-draft", "clear-file-input", "set-destination");
      }
    });
    expect(unchangedEffects).toEqual(["clear-composer", "clear-draft", "clear-file-input", "set-destination"]);

    const edited = createComposerState();
    const editedIntent = captureGroupBridgeRemoteComposerIntent("group-a", edited);
    const editedEffects: string[] = [];
    const completion = Promise.resolve().then(() => {
      if (isGroupBridgeRemoteComposerIntentCurrent(editedIntent, edited)) {
        editedEffects.push("clear-composer", "clear-draft", "clear-file-input", "set-destination");
      }
    });
    edited.composerText = "new text typed while sending";
    await completion;
    expect(editedEffects).toEqual([]);

    const rerouted = createComposerState();
    const reroutedIntent = captureGroupBridgeRemoteComposerIntent("group-a", rerouted);
    const reroutedEffects: string[] = [];
    const reroutedCompletion = Promise.resolve().then(() => {
      if (isGroupBridgeRemoteComposerIntentCurrent(reroutedIntent, rerouted)) {
        reroutedEffects.push("clear-composer", "clear-draft", "clear-file-input", "set-destination");
      }
    });
    rerouted.destGroupId = "group-b";
    await reroutedCompletion;
    expect(reroutedEffects).toEqual([]);
  });

  it.each<[string, (state: GroupBridgeRemoteComposerState) => void]>([
    ["active group", (state) => { state.activeGroupId = "group-b"; }],
    ["raw text", (state) => { state.composerText = "hello "; }],
    ["file order", (state) => { state.composerFiles = [...state.composerFiles].reverse(); }],
    ["file identity", (state) => { state.composerFiles = [{ name: "first.txt" } as File, state.composerFiles[1]]; }],
    ["skill", (state) => { state.selectedSkillCommand = "skill:example"; }],
    ["recipient", (state) => { state.toText = "@all"; }],
    ["reply", (state) => { state.replyTarget = { event_id: "event-1" }; }],
    ["quote", (state) => { state.quotedPresentationRef = { event_id: "event-1" }; }],
    ["priority", (state) => { state.priority = "attention"; }],
    ["reply required", (state) => { state.replyRequired = true; }],
    ["collaboration", (state) => { state.collaborationRequired = true; }],
    ["computer enabled", (state) => { state.computerControlEnabled = true; }],
    ["computer workflow", (state) => { state.computerControlWorkflowId = "workflow-1"; }],
    ["computer actor", (state) => { state.computerControlActorId = "actor-1"; }],
    ["computer publish permission", (state) => { state.computerControlPermissions.publish = false; }],
    ["computer trust permission", (state) => { state.computerControlPermissions.trust = false; }],
    ["computer trigger permission", (state) => { state.computerControlPermissions.unattendedTriggers = false; }],
    ["destination", (state) => { state.destGroupId = "group-b"; }],
    ["group draft", (state) => { state.drafts["group-a"] = { ...state.drafts["group-a"]!, composerText: "new draft" }; }],
    ["normal recipient", (state) => { state.normalToTextByGroup["group-a"] = "@all"; }],
  ])("preserves new intent after %s changes", (_name, mutate) => {
    const state = createComposerState();
    const intent = captureGroupBridgeRemoteComposerIntent("group-a", state);
    mutate(state);

    expect(isGroupBridgeRemoteComposerIntentCurrent(intent, state)).toBe(false);
  });
});

describe("decideGroupBridgeRemoteSend", () => {
  it("selects exactly one local or remote route and rejects stale targets", () => {
    expect(decideGroupBridgeRemoteSend({ ...baseDecision, remoteTargetId: "", remoteTarget: null })).toEqual({ kind: "local" });
    expect(decideGroupBridgeRemoteSend(baseDecision)).toEqual({ kind: "remote", target });
    expect(decideGroupBridgeRemoteSend({ ...baseDecision, remoteTarget: null })).toEqual({
      kind: "reject",
      reason: "target_unavailable",
    });
    expect(decideGroupBridgeRemoteSend({
      ...baseDecision,
      remoteTarget: { ...target, registration_id: "registration-b" },
    })).toEqual({ kind: "reject", reason: "target_unavailable" });
    expect(decideGroupBridgeRemoteSend({ ...baseDecision, groupId: "group-b" })).toEqual({
      kind: "reject",
      reason: "target_unavailable",
    });
  });

  it.each([
    ["reply", { hasReply: true }],
    ["quote", { hasQuote: true }],
    ["attachment", { attachmentCount: 1 }],
    ["skill", { selectedSkillCommand: "skill:example" }],
    ["computer", { computerControlEnabled: true }],
    ["collaboration", { collaborationRequired: true }],
    ["slash", { text: "/status" }],
  ])("rejects unsupported remote %s composition", (_name, override) => {
    expect(decideGroupBridgeRemoteSend({ ...baseDecision, ...override })).toEqual({
      kind: "reject",
      reason: "unsupported_composition",
    });
  });

  it("keeps the production remote branch outside local optimistic effects", () => {
    const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "useChatTab.ts"), "utf8");
    const loadStart = source.indexOf("const token = remoteLifecycle.begin(\"load\"");
    const loadEnd = source.indexOf("const remoteViewCurrent", loadStart);
    const loadBlock = source.slice(loadStart, loadEnd);
    const routeSetterStart = source.indexOf("const setRemoteTargetId = useCallback");
    const routeSetterEnd = source.indexOf("useEffect(() =>", routeSetterStart);
    const routeSetter = source.slice(routeSetterStart, routeSetterEnd);
    const remoteStart = source.indexOf('if (remoteDecision.kind === "remote")');
    const localStart = source.indexOf("if (!skillDispatchText && await tryExecuteSlashCommand", remoteStart);
    const remoteBranch = source.slice(remoteStart, localStart);

    expect(remoteStart).toBeGreaterThan(-1);
    expect(localStart).toBeGreaterThan(remoteStart);
    expect(remoteBranch).not.toContain("enqueueOutbox");
    expect(remoteBranch).not.toContain("upsertStreamingEvent");
    expect(source).toContain("remoteTargetId: visibleRemoteTargetId");
    expect(source).toMatch(/remoteLifecycle\.begin\(\n\s+"poll",\n\s+request\.groupId,\n\s+request\.registrationId,\n\s+request\.messageGeneration/);
    expect(source).toContain("messageGeneration: token.messageGeneration");
    expect(loadBlock).toContain("} catch {");
    expect(loadBlock).toContain("remoteLifecycle.commit(token");
    expect(loadBlock).toContain("if (remoteLifecycle.finish(token)) setRemoteTargetsBusy(false)");
    expect(loadBlock.indexOf("remoteLifecycle.selectRoute(groupId, nextRoute)")).toBeLessThan(
      loadBlock.indexOf("setRemoteTargets(targets)"),
    );
    expect(routeSetter.indexOf("remoteLifecycle.selectRoute(selectedGroupId, next)")).toBeLessThan(
      routeSetter.indexOf("setRemoteTargetIdState(next)"),
    );
    expect(source.indexOf("if (sendInFlightRef.current) return;")).toBeLessThan(remoteStart);
    expect(remoteBranch.indexOf("setRemoteReceipt(null)")).toBeLessThan(
      remoteBranch.indexOf("groupBridgeApi.remoteSend"),
    );
    expect(remoteBranch.indexOf("setRemoteRequest(null)")).toBeLessThan(
      remoteBranch.indexOf("groupBridgeApi.remoteSend"),
    );
    expect(remoteBranch).toContain("isGroupBridgeRemoteComposerIntentCurrent");
    expect(remoteBranch).toMatch(/if \(consumeComposerIntent\) \{[\s\S]*clearComposer\(\);[\s\S]*clearDraft\(selectedGroupId\);[\s\S]*fileInputRef[\s\S]*setDestGroupId\(selectedGroupId\);[\s\S]*\}\n\s{10}onMessageSent/);
    expect(remoteBranch).toContain("remoteSendOwnerRef.current === token");
    expect(remoteBranch).toMatch(/\n\s{6}return;\n\s{4}}\n\s{4}$/);
    expect(source).toMatch(/return \(\) => \{\n\s{6}remoteLifecycle\.selectGroup\(""\);\n\s{4}};/);
  });
});

describe("pollGroupBridgeRemoteReceipt", () => {
  const immediateWait = async () => {};

  it("stops after the bounded number of status requests", async () => {
    const controller = new AbortController();
    const getStatus = vi.fn(async () => ({ ok: true as const, receipt: null }));
    const result = await pollGroupBridgeRemoteReceipt({
      initialReceipt: { status: "queued" },
      signal: controller.signal,
      getStatus,
      onReceipt: vi.fn(),
      wait: immediateWait,
    });

    expect(result.kind).toBe("exhausted");
    expect(getStatus).toHaveBeenCalledTimes(GROUP_BRIDGE_REMOTE_POLL_LIMIT);
  });

  it("retains the last receipt when status becomes unavailable", async () => {
    const receipt: GroupBridgeRemoteReceipt = { status: "retrying", attempt: 2 };
    const onReceipt = vi.fn();
    const result = await pollGroupBridgeRemoteReceipt({
      initialReceipt: receipt,
      signal: new AbortController().signal,
      getStatus: async () => ({ ok: false }),
      onReceipt,
      wait: immediateWait,
    });

    expect(result).toEqual({ kind: "unavailable", receipt });
    expect(onReceipt).not.toHaveBeenCalled();
  });

  it("projects a terminal signed receipt and stops polling", async () => {
    const receipt: GroupBridgeRemoteReceipt = { status: "sent", remote_event_id: "remote-event-1" };
    const onReceipt = vi.fn();
    const getStatus = vi.fn(async () => ({ ok: true as const, receipt }));
    const result = await pollGroupBridgeRemoteReceipt({
      initialReceipt: { status: "queued" },
      signal: new AbortController().signal,
      getStatus,
      onReceipt,
      wait: immediateWait,
    });

    expect(result).toEqual({ kind: "terminal", receipt });
    expect(getStatus).toHaveBeenCalledTimes(1);
    expect(onReceipt).toHaveBeenCalledWith(receipt);
    expect(projectGroupBridgeRemoteReceipt(receipt)).toEqual({
      label: "Remote accepted / signed receipt verified",
      eventId: "remote-event-1",
      status: "sent",
      statusUnavailable: false,
    });
  });

  it("cancels polling when its generation is replaced", async () => {
    const lifecycle = new GroupBridgeRemoteLifecycle();
    lifecycle.selectGroup("group-a");
    lifecycle.selectRoute("group-a", "registration-a");
    const sendA = lifecycle.begin("send", "group-a", "registration-a")!;
    expect(lifecycle.finish(sendA)).toBe(true);
    const pollA = lifecycle.begin("poll", "group-a", "registration-a", sendA.messageGeneration)!;
    const completion = pollGroupBridgeRemoteReceipt({
      initialReceipt: { status: "queued" },
      signal: pollA.signal,
      getStatus: vi.fn(),
      onReceipt: vi.fn(),
      wait: (_delay, signal) => new Promise((resolve) => {
        signal.addEventListener("abort", () => resolve(), { once: true });
      }),
    });

    lifecycle.selectGroup("group-b");
    await expect(completion).resolves.toEqual({ kind: "cancelled", receipt: { status: "queued" } });
  });

  it("marks status unavailable without discarding the visible receipt projection", () => {
    expect(projectGroupBridgeRemoteReceipt(
      { status: "retrying", remote_event_id: "last-event" },
      true,
    )).toEqual({
      label: "Remote status unavailable",
      eventId: "last-event",
      status: "retrying",
      statusUnavailable: true,
    });
  });
});
