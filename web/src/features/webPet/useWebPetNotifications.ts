import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GroupContext, LedgerEvent } from "../../types";
import { recordPetDecisionOutcome } from "../../services/api";
import type { PetReminder } from "./types";
import { buildTaskProposalMessage } from "./taskProposal";
import {
  evaluateLocalTaskProposalReminders,
  type LocalTaskProposalEvaluation,
} from "./localTaskAdvisor";
import { cloneTaskAdvisorHistory, type TaskAdvisorHistoryState } from "./taskAdvisor/history";
import { deriveTaskProposalStylePolicy } from "./taskProposalStylePolicy";
import {
  createMentionReaction,
  createTaskReactionSnapshot,
  diffTaskReaction,
  type PetReaction,
  type TaskReactionSnapshotMap,
} from "./reactions";

const HIDDEN_STORAGE_KEY_PREFIX = "cccc:web-pet:hidden:";
const TASK_PROPOSAL_ECHO_SUPPRESS_MS = 10 * 60 * 1000;
const RECENT_USER_MESSAGE_SCAN_LIMIT = 12;

type ReminderPriorityMap = Record<string, number>;

export interface UseWebPetNotificationsResult {
  reminders: PetReminder[];
  activeReminder: PetReminder | null;
  autoPeekReminder: PetReminder | null;
  unseenReminderCount: number;
  reaction: PetReaction | null;
  dismissReminder: (
    fingerprint: string,
    opts?: { outcome?: "dismissed" | null; cooldownMs?: number }
  ) => void;
  markRemindersSeen: (fingerprints?: string[]) => void;
}

export function shouldProjectReminderForGroupState(
  reminder: PetReminder,
  groupState: string,
): boolean {
  const normalizedState = String(groupState || "").trim().toLowerCase();
  if (!normalizedState || normalizedState === "active") {
    return true;
  }
  if (normalizedState === "idle") {
    return true;
  }
  return false;
}

function normalizeCompareText(value: string): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

function eventTargetsForeman(event: LedgerEvent): boolean {
  const data = event.data as Record<string, unknown> | undefined;
  const to = Array.isArray(data?.to) ? data.to : [];
  return to.some((entry) => {
    const normalized = normalizeCompareText(String(entry || ""));
    return normalized === "@foreman" || normalized === "foreman";
  });
}

function eventTextMentionsTask(reminder: PetReminder, text: string): boolean {
  if (reminder.action.type !== "task_proposal") return false;
  const normalizedText = normalizeCompareText(text);
  if (!normalizedText) return false;
  const explicitText = normalizeCompareText(String(reminder.action.text || ""));
  if (explicitText && normalizedText === explicitText) return true;

  const renderedText = normalizeCompareText(buildTaskProposalMessage(reminder.action));
  if (renderedText && normalizedText === renderedText) return true;

  const taskIdToken = normalizeCompareText(
    reminder.action.taskId ? `task_id=${String(reminder.action.taskId).trim()}` : "",
  );
  if (!taskIdToken || !normalizedText.includes(taskIdToken)) return false;
  if (normalizedText.includes("cccc_task")) return true;

  return [
    reminder.action.title ? `title="${String(reminder.action.title).trim()}"` : "",
    reminder.action.status ? `status=${String(reminder.action.status).trim()}` : "",
    reminder.action.assignee ? `assignee=${String(reminder.action.assignee).trim()}` : "",
  ]
    .map(normalizeCompareText)
    .filter(Boolean)
    .some((token) => normalizedText.includes(token));
}

function getTaskProposalIdentity(reminder: PetReminder): string {
  if (reminder.action.type !== "task_proposal") return "";
  const groupId = String(reminder.action.groupId || "").trim();
  const taskId = String(reminder.action.taskId || "").trim();
  if (!groupId || !taskId) return "";
  return `${groupId}::${taskId}`;
}

export function mergeTaskProposalReminders(
  localReminders: PetReminder[],
  decisionReminders: PetReminder[],
): PetReminder[] {
  const remoteTaskIds = new Set(
    decisionReminders
      .map((reminder) => getTaskProposalIdentity(reminder))
      .filter(Boolean),
  );
  const merged = [
    ...localReminders.filter((reminder) => {
      const identity = getTaskProposalIdentity(reminder);
      return !identity || !remoteTaskIds.has(identity);
    }),
    ...decisionReminders,
  ];
  const seenFingerprints = new Set<string>();
  return merged.filter((reminder) => {
    const fingerprint = String(reminder.fingerprint || "").trim();
    if (!fingerprint) return true;
    if (seenFingerprints.has(fingerprint)) return false;
    seenFingerprints.add(fingerprint);
    return true;
  });
}

export function shouldSuppressTaskProposalEcho(
  reminder: PetReminder,
  events: LedgerEvent[],
  nowMs: number = Date.now(),
): boolean {
  if (reminder.action.type !== "task_proposal") return false;

  let scanned = 0;
  for (let idx = events.length - 1; idx >= 0; idx -= 1) {
    const event = events[idx];
    if (!event || String(event.kind || "").trim() !== "chat.message") continue;
    if (String(event.by || "").trim() !== "user") continue;
    if (!eventTargetsForeman(event)) continue;
    scanned += 1;
    if (scanned > RECENT_USER_MESSAGE_SCAN_LIMIT) break;

    const eventMs = Date.parse(String(event.ts || ""));
    if (Number.isFinite(eventMs) && nowMs - eventMs > TASK_PROPOSAL_ECHO_SUPPRESS_MS) {
      continue;
    }

    const data = event.data as Record<string, unknown> | undefined;
    const text = String(data?.text || "");
    if (eventTextMentionsTask(reminder, text)) {
      return true;
    }
  }
  return false;
}

function getLatestEvent(events: LedgerEvent[]): LedgerEvent | null {
  return events.length > 0 ? events[events.length - 1] ?? null : null;
}

export function sortProjectedReminders(reminders: PetReminder[]): PetReminder[] {
  return [...reminders].sort((left, right) => {
    const priorityDelta = Number(right.priority || 0) - Number(left.priority || 0);
    if (priorityDelta !== 0) return priorityDelta;
    const summaryDelta = String(left.summary || "").localeCompare(String(right.summary || ""));
    if (summaryDelta !== 0) return summaryDelta;
    return String(left.fingerprint || "").localeCompare(String(right.fingerprint || ""));
  });
}

export function getUnseenReminders(
  reminders: PetReminder[],
  seenFingerprints: Record<string, true>,
): PetReminder[] {
  return reminders.filter((reminder) => !seenFingerprints[reminder.fingerprint]);
}

export function selectAutoPeekReminder(
  reminders: PetReminder[],
  seenFingerprints: Record<string, true>,
  blockedFingerprints: ReminderPriorityMap,
): PetReminder | null {
  return getUnseenReminders(reminders, seenFingerprints).find((reminder) => {
    const blockedPriority = Number(blockedFingerprints[reminder.fingerprint] || 0);
    return blockedPriority <= 0 || Number(reminder.priority || 0) > blockedPriority;
  }) ?? null;
}

function getHiddenStorageKey(groupId: string): string {
  return `${HIDDEN_STORAGE_KEY_PREFIX}${String(groupId || "").trim()}`;
}

function loadPersistedHiddenFingerprints(groupId: string): ReminderPriorityMap {
  if (typeof window === "undefined") return {};
  const key = getHiddenStorageKey(groupId);
  if (!key.trim()) return {};
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    const next: ReminderPriorityMap = {};
    if (Array.isArray(parsed)) {
      return {};
    }
    if (!parsed || typeof parsed !== "object") return {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      const fingerprint = String(key || "").trim();
      if (!fingerprint) continue;
      const priority = Number(value || 0);
      next[fingerprint] = Number.isFinite(priority) ? priority : Number.MAX_SAFE_INTEGER;
    }
    return next;
  } catch {
    return {};
  }
}

function persistHiddenFingerprints(groupId: string, hidden: ReminderPriorityMap): void {
  if (typeof window === "undefined") return;
  const key = getHiddenStorageKey(groupId);
  if (!key.trim()) return;
  try {
    if (Object.keys(hidden).length === 0) {
      window.localStorage.removeItem(key);
      return;
    }
    window.localStorage.setItem(key, JSON.stringify(hidden));
  } catch (error) {
    console.warn("failed to persist web pet hidden reminders", error);
  }
}

function filterVisibleReminders(
  reminders: PetReminder[],
  hiddenFingerprints: ReminderPriorityMap,
): PetReminder[] {
  return reminders.filter((reminder) => {
    const hiddenPriority = Number(hiddenFingerprints[reminder.fingerprint] || 0);
    if (hiddenPriority <= 0) return true;
    return Number(reminder.priority || 0) > hiddenPriority;
  });
}

function pruneSeenFingerprints(
  seenFingerprints: Record<string, true>,
  activeFingerprints: string[],
): Record<string, true> {
  const activeSet = new Set(activeFingerprints);
  const next: Record<string, true> = {};
  for (const fingerprint of Object.keys(seenFingerprints)) {
    if (activeSet.has(fingerprint)) {
      next[fingerprint] = true;
    }
  }
  return next;
}

function prunePriorityMap(
  values: ReminderPriorityMap,
  activeFingerprints: string[],
): ReminderPriorityMap {
  const activeSet = new Set(activeFingerprints);
  const next: ReminderPriorityMap = {};
  for (const [fingerprint, priority] of Object.entries(values)) {
    if (activeSet.has(fingerprint)) {
      next[fingerprint] = Number(priority || 0);
    }
  }
  return next;
}

function arePriorityMapsEqual(
  left: ReminderPriorityMap,
  right: ReminderPriorityMap,
): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  for (const key of leftKeys) {
    if (Number(left[key] || 0) !== Number(right[key] || 0)) {
      return false;
    }
  }
  return true;
}

function areSeenMapsEqual(
  left: Record<string, true>,
  right: Record<string, true>,
): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  for (const key of leftKeys) {
    if (!right[key]) {
      return false;
    }
  }
  return true;
}

function getReminderPriorityMap(reminders: PetReminder[]): ReminderPriorityMap {
  const next: ReminderPriorityMap = {};
  for (const reminder of reminders) {
    const fingerprint = String(reminder.fingerprint || "").trim();
    if (!fingerprint) continue;
    next[fingerprint] = Number(reminder.priority || 0);
  }
  return next;
}

function getDismissedReminderPriority(reminder: PetReminder | undefined): number {
  return reminder ? Number(reminder.priority || 0) : Number.MAX_SAFE_INTEGER;
}

function getInitialNotificationState(groupId: string): {
  hiddenFingerprints: ReminderPriorityMap;
} {
  return {
    hiddenFingerprints: loadPersistedHiddenFingerprints(groupId),
  };
}

export function useWebPetNotifications(input: {
  groupId: string;
  groupState?: string;
  groupContext: GroupContext | null;
  events: LedgerEvent[];
  decisions?: PetReminder[];
  petContextStatus?: "idle" | "loading" | "loaded" | "error";
  petContext?: {
    persona?: string;
    help?: string;
    prompt?: string;
  } | null;
}): UseWebPetNotificationsResult {
  const groupId = String(input.groupId || "").trim();
  const groupState = String(input.groupState || "").trim().toLowerCase();
  const groupContext = input.groupContext;
  const events = input.events;
  const petContextStatus = String(input.petContextStatus || "loaded").trim().toLowerCase();
  const allowLocalTaskFallback = petContextStatus === "error";
  const initialState = useMemo(() => getInitialNotificationState(groupId), [groupId]);

  const [reaction, setReaction] = useState<PetReaction | null>(null);
  const [hiddenFingerprintsSnapshot, setHiddenFingerprintsSnapshot] = useState<ReminderPriorityMap>(
    () => initialState.hiddenFingerprints,
  );
  const [seenFingerprintsSnapshot, setSeenFingerprintsSnapshot] = useState<Record<string, true>>({});
  const [blockedAutoPeekSnapshot, setBlockedAutoPeekSnapshot] = useState<ReminderPriorityMap>({});

  const lastProcessedEventIdRef = useRef("");
  const didHydrateTaskSnapshotRef = useRef(false);
  const previousTaskSnapshotRef = useRef<TaskReactionSnapshotMap>({});
  const hiddenFingerprintsRef = useRef<ReminderPriorityMap>(initialState.hiddenFingerprints);
  const seenFingerprintsRef = useRef<Record<string, true>>({});
  const blockedAutoPeekRef = useRef<ReminderPriorityMap>({});
  const advisorHistoryRef = useRef<TaskAdvisorHistoryState>(cloneTaskAdvisorHistory(new Map()));
  const advisorCommittedSignatureRef = useRef("");
  const [localTaskProposalEvaluation, setLocalTaskProposalEvaluation] = useState<{
    groupId: string;
    evaluation: LocalTaskProposalEvaluation;
  }>({
    groupId,
    evaluation: {
      reminders: [],
      nextHistory: cloneTaskAdvisorHistory(new Map()),
      signature: "",
    },
  });

  const projected = useMemo(() => {
    const decisions = Array.isArray(input.decisions) ? input.decisions : [];
    const localReminders = allowLocalTaskFallback && localTaskProposalEvaluation.groupId === groupId
      ? localTaskProposalEvaluation.evaluation.reminders
      : [];
    const mergedReminders = mergeTaskProposalReminders(localReminders, decisions);
    return sortProjectedReminders(
      mergedReminders.filter((reminder) =>
        shouldProjectReminderForGroupState(reminder, groupState) &&
        !shouldSuppressTaskProposalEcho(reminder, events),
      ),
    );
  }, [allowLocalTaskFallback, events, groupId, groupState, input.decisions, localTaskProposalEvaluation]);

  const reminderByFingerprint = useMemo(
    () => new Map(projected.map((reminder) => [reminder.fingerprint, reminder])),
    [projected],
  );

  const reminders = useMemo(
    () => filterVisibleReminders(projected, hiddenFingerprintsSnapshot),
    [hiddenFingerprintsSnapshot, projected],
  );

  const unseenReminders = useMemo(
    () => getUnseenReminders(reminders, seenFingerprintsSnapshot),
    [reminders, seenFingerprintsSnapshot],
  );

  const activeReminder = useMemo(
    () => reminders[0] ?? null,
    [reminders],
  );

  const autoPeekReminder = useMemo(
    () => selectAutoPeekReminder(reminders, seenFingerprintsSnapshot, blockedAutoPeekSnapshot),
    [blockedAutoPeekSnapshot, reminders, seenFingerprintsSnapshot],
  );

  useEffect(() => {
    const nextState = getInitialNotificationState(groupId);
    hiddenFingerprintsRef.current = nextState.hiddenFingerprints;
    seenFingerprintsRef.current = {};
    blockedAutoPeekRef.current = {};
    lastProcessedEventIdRef.current = "";
    didHydrateTaskSnapshotRef.current = false;
    previousTaskSnapshotRef.current = {};
    advisorHistoryRef.current = cloneTaskAdvisorHistory(new Map());
    advisorCommittedSignatureRef.current = "";
    const frame = window.requestAnimationFrame(() => {
      setHiddenFingerprintsSnapshot(nextState.hiddenFingerprints);
      setSeenFingerprintsSnapshot({});
      setBlockedAutoPeekSnapshot({});
      setReaction(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [groupId]);

  useEffect(() => {
    if (!allowLocalTaskFallback) {
      const emptyEvaluation: LocalTaskProposalEvaluation = {
        reminders: [],
        nextHistory: cloneTaskAdvisorHistory(new Map()),
        signature: "",
      };
      advisorHistoryRef.current = cloneTaskAdvisorHistory(new Map());
      advisorCommittedSignatureRef.current = "";
      const frame = window.requestAnimationFrame(() => {
        setLocalTaskProposalEvaluation((current) => {
          if (current.groupId === groupId && current.evaluation.signature === "") {
            return current;
          }
          return {
            groupId,
            evaluation: emptyEvaluation,
          };
        });
      });
      return () => window.cancelAnimationFrame(frame);
    }
    const stylePolicy = deriveTaskProposalStylePolicy(input.petContext);
    const nextEvaluation = evaluateLocalTaskProposalReminders(
      groupId,
      groupContext,
      stylePolicy,
      advisorHistoryRef.current,
    );
    const signature = String(nextEvaluation.signature || "").trim();
    if (signature && advisorCommittedSignatureRef.current !== signature) {
      advisorHistoryRef.current = cloneTaskAdvisorHistory(nextEvaluation.nextHistory);
      advisorCommittedSignatureRef.current = signature;
    }
    const frame = window.requestAnimationFrame(() => {
      setLocalTaskProposalEvaluation((current) => {
        if (
          current.groupId === groupId
          && current.evaluation.signature === nextEvaluation.signature
        ) {
          return current;
        }
        return {
          groupId,
          evaluation: nextEvaluation,
        };
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [allowLocalTaskFallback, groupContext, groupId, input.petContext]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const key = getHiddenStorageKey(groupId);
    if (!key.trim()) return;

    const handleStorage = (event: StorageEvent) => {
      if (event.storageArea !== window.localStorage || event.key !== key) return;
      const next = loadPersistedHiddenFingerprints(groupId);
      hiddenFingerprintsRef.current = next;
      setHiddenFingerprintsSnapshot(next);
    };

    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("storage", handleStorage);
    };
  }, [groupId]);

  useEffect(() => {
    const latestEvent = getLatestEvent(events);
    const eventId = String(latestEvent?.id || "").trim();
    if (!eventId || eventId === lastProcessedEventIdRef.current) {
      return;
    }

    lastProcessedEventIdRef.current = eventId;

    const mentionReaction = createMentionReaction(latestEvent);
    if (!mentionReaction) return;

    const frame = window.requestAnimationFrame(() => {
      setReaction(mentionReaction);
    });

    return () => window.cancelAnimationFrame(frame);
  }, [events, groupId]);

  useEffect(() => {
    const nextSnapshot = createTaskReactionSnapshot(groupContext);

    if (!didHydrateTaskSnapshotRef.current) {
      previousTaskSnapshotRef.current = nextSnapshot;
      didHydrateTaskSnapshotRef.current = true;
      return;
    }

    const taskReaction = diffTaskReaction(
      previousTaskSnapshotRef.current,
      nextSnapshot,
    );
    previousTaskSnapshotRef.current = nextSnapshot;

    if (taskReaction) {
      const frame = window.requestAnimationFrame(() => {
        setReaction(taskReaction);
      });
      return () => window.cancelAnimationFrame(frame);
    }
  }, [groupContext]);

  useEffect(() => {
    if (!reaction) return;

    const timeout = window.setTimeout(() => {
      setReaction((current) =>
        current === reaction ? null : current,
      );
    }, reaction.durationMs);

    return () => window.clearTimeout(timeout);
  }, [reaction]);

  useEffect(() => {
    const activeFingerprints = reminders.map((reminder) => reminder.fingerprint);

    const nextSeen = pruneSeenFingerprints(seenFingerprintsRef.current, activeFingerprints);
    if (!areSeenMapsEqual(seenFingerprintsRef.current, nextSeen)) {
      seenFingerprintsRef.current = nextSeen;
      setSeenFingerprintsSnapshot(nextSeen);
    }

    const nextBlocked = prunePriorityMap(blockedAutoPeekRef.current, activeFingerprints);
    if (!arePriorityMapsEqual(blockedAutoPeekRef.current, nextBlocked)) {
      blockedAutoPeekRef.current = nextBlocked;
      setBlockedAutoPeekSnapshot(nextBlocked);
    }
  }, [reminders]);

  const markRemindersSeen = useCallback((fingerprints?: string[]) => {
    const nextFingerprints = Array.isArray(fingerprints) && fingerprints.length > 0
      ? fingerprints
      : reminders.map((reminder) => reminder.fingerprint);
    if (nextFingerprints.length === 0) return;

    const nextSeen: Record<string, true> = { ...seenFingerprintsRef.current };
    let changed = false;
    for (const value of nextFingerprints) {
      const fingerprint = String(value || "").trim();
      if (!fingerprint || nextSeen[fingerprint]) continue;
      nextSeen[fingerprint] = true;
      changed = true;
    }
    if (!changed) return;
    seenFingerprintsRef.current = nextSeen;
    setSeenFingerprintsSnapshot(nextSeen);
  }, [reminders]);

  const dismissReminder = useCallback((fingerprint: string, opts?: { outcome?: "dismissed" | null; cooldownMs?: number }) => {
    const normalized = String(fingerprint || "").trim();
    if (!normalized) return;

    const reminder = reminderByFingerprint.get(normalized);
    const nextHidden: ReminderPriorityMap = {
      ...hiddenFingerprintsRef.current,
      [normalized]: getDismissedReminderPriority(reminder),
    };
    hiddenFingerprintsRef.current = nextHidden;
    persistHiddenFingerprints(groupId, nextHidden);
    setHiddenFingerprintsSnapshot(nextHidden);

    const nextBlocked: ReminderPriorityMap = {
      ...blockedAutoPeekRef.current,
      ...getReminderPriorityMap(reminders),
    };
    blockedAutoPeekRef.current = nextBlocked;
    setBlockedAutoPeekSnapshot(nextBlocked);

    markRemindersSeen([normalized]);

    const outcome = opts?.outcome === undefined ? "dismissed" : opts.outcome;
    if (outcome && reminder) {
      void recordPetDecisionOutcome(groupId, {
        fingerprint: normalized,
        outcome,
        decisionId: reminder.id,
        actionType: reminder.action.type,
        cooldownMs: Math.max(0, Number(opts?.cooldownMs || 0)),
        sourceEventId: reminder.source.eventId,
      });
    }
  }, [groupId, markRemindersSeen, reminderByFingerprint, reminders]);

  return {
    reminders,
    activeReminder,
    autoPeekReminder,
    unseenReminderCount: unseenReminders.length,
    reaction,
    dismissReminder,
    markRemindersSeen,
  };
}
