// Group state store (groups, actors, events, context, settings).
import { create } from "zustand";
import type {
  GroupMeta,
  GroupDoc,
  LedgerEvent,
  Actor,
  RuntimeInfo,
  GroupContext,
  GroupSettings,
  GroupPresentation,
} from "../types";
import * as api from "../services/api";
import { mergeLedgerEvents } from "../utils/mergeLedgerEvents";

type ChatWindowState = {
  groupId: string;
  centerEventId: string;
  centerIndex: number;
  events: LedgerEvent[];
  hasMoreBefore: boolean;
  hasMoreAfter: boolean;
};

type GroupChatBucket = {
  events: LedgerEvent[];
  chatWindow: ChatWindowState | null;
  hasMoreHistory: boolean;
  hasLoadedTail: boolean;
  isLoadingHistory: boolean;
  isChatWindowLoading: boolean;
};

const EMPTY_CHAT_BUCKET: GroupChatBucket = {
  events: [],
  chatWindow: null,
  hasMoreHistory: false,
  hasLoadedTail: false,
  isLoadingHistory: false,
  isChatWindowLoading: false,
};

interface GroupState {
  // Data
  groups: GroupMeta[];
  groupOrder: string[]; // Group IDs in user-defined order
  archivedGroupIds: string[]; // Local-only sidebar archive bucket
  selectedGroupId: string;
  chatByGroup: Record<string, GroupChatBucket>;
  groupDoc: GroupDoc | null;
  events: LedgerEvent[];
  chatWindow: ChatWindowState | null;
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  groupPresentation: GroupPresentation | null;
  runtimes: RuntimeInfo[];
  hasMoreHistory: boolean;
  isLoadingHistory: boolean;
  isChatWindowLoading: boolean;

  // Actions
  setGroups: (groups: GroupMeta[]) => void;
  setGroupOrder: (order: string[]) => void;
  reorderGroupsInSection: (section: "working" | "archived", fromIndex: number, toIndex: number) => void;
  archiveGroup: (groupId: string) => void;
  restoreGroup: (groupId: string) => void;
  getOrderedGroups: () => GroupMeta[];
  setSelectedGroupId: (id: string) => void;
  setGroupDoc: (doc: GroupDoc | null) => void;
  setEvents: (events: LedgerEvent[], groupId?: string) => void;
  appendEvent: (event: LedgerEvent, groupId?: string) => void;
  prependEvents: (events: LedgerEvent[], groupId?: string) => void;
  setChatWindow: (w: GroupState["chatWindow"], groupId?: string) => void;
  setActors: (actors: Actor[]) => void;
  incrementActorUnread: (actorIds: string[]) => void;
  updateActorActivity: (updates: Array<{
    id: string;
    idle_seconds?: number | null;
    running: boolean;
    effective_working_state?: string;
    effective_working_reason?: string;
    effective_working_updated_at?: string | null;
    effective_active_task_id?: string | null;
  }>) => void;
  setGroupContext: (ctx: GroupContext | null) => void;
  setGroupSettings: (settings: GroupSettings | null) => void;
  setGroupPresentation: (presentation: GroupPresentation | null) => void;
  setRuntimes: (runtimes: RuntimeInfo[]) => void;
  updateReadStatus: (eventId: string, actorId: string, groupId?: string) => void;
  updateAckStatus: (eventId: string, actorId: string, groupId?: string) => void;
  updateReplyStatus: (eventId: string, actorId: string, groupId?: string) => void;
  setHasMoreHistory: (v: boolean, groupId?: string) => void;
  setIsLoadingHistory: (v: boolean, groupId?: string) => void;
  setIsChatWindowLoading: (v: boolean, groupId?: string) => void;

  // Async actions
  refreshGroups: () => Promise<void>;
  refreshActors: (groupId?: string, opts?: { includeUnread?: boolean }) => Promise<void>;
  refreshSettings: (groupId?: string) => Promise<void>;
  refreshPresentation: (groupId?: string) => Promise<void>;
  loadGroup: (groupId: string) => Promise<void>;
  warmGroup: (groupId: string) => Promise<void>;
  loadMoreHistory: (groupId?: string) => Promise<void>;
  openChatWindow: (groupId: string, centerEventId: string) => Promise<void>;
  closeChatWindow: (groupId?: string) => void;
}

const MAX_UI_EVENTS = 800;
const GROUP_VIEW_CACHE_TTL_MS = 300_000;

// localStorage key for group order
const GROUP_ORDER_KEY = "cccc-group-order";
const ARCHIVED_GROUP_IDS_KEY = "cccc-archived-group-ids";
const SELECTED_GROUP_ID_KEY = "cccc-selected-group-id";

function normalizeGroupIdList(ids: string[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of ids) {
    const id = String(raw || "").trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function loadGroupOrder(): string[] {
  try {
    const stored = localStorage.getItem(GROUP_ORDER_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) return normalizeGroupIdList(parsed);
    }
  } catch (e) {
    console.warn("Failed to read group order from localStorage:", e);
  }
  return [];
}

function loadArchivedGroupIds(): string[] {
  try {
    const stored = localStorage.getItem(ARCHIVED_GROUP_IDS_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) return normalizeGroupIdList(parsed);
    }
  } catch (e) {
    console.warn("Failed to read archived groups from localStorage:", e);
  }
  return [];
}

function saveGroupOrder(order: string[]): void {
  try {
    localStorage.setItem(GROUP_ORDER_KEY, JSON.stringify(normalizeGroupIdList(order)));
  } catch (e) {
    console.warn("Failed to persist group order to localStorage:", e);
  }
}

function saveArchivedGroupIds(groupIds: string[]): void {
  try {
    const next = normalizeGroupIdList(groupIds);
    if (next.length === 0) {
      localStorage.removeItem(ARCHIVED_GROUP_IDS_KEY);
      return;
    }
    localStorage.setItem(ARCHIVED_GROUP_IDS_KEY, JSON.stringify(next));
  } catch (e) {
    console.warn("Failed to persist archived groups to localStorage:", e);
  }
}

function loadSelectedGroupId(): string {
  try {
    return String(localStorage.getItem(SELECTED_GROUP_ID_KEY) || "").trim();
  } catch (e) {
    console.warn("Failed to read selected group from localStorage:", e);
  }
  return "";
}

function saveSelectedGroupId(groupId: string): void {
  try {
    const gid = String(groupId || "").trim();
    if (!gid) {
      localStorage.removeItem(SELECTED_GROUP_ID_KEY);
      return;
    }
    localStorage.setItem(SELECTED_GROUP_ID_KEY, gid);
  } catch (e) {
    console.warn("Failed to persist selected group to localStorage:", e);
  }
}

// Merge stored order with current groups: preserve order for existing groups, append new ones at end
function mergeGroupOrder(storedOrder: string[], groups: GroupMeta[]): string[] {
  const currentIds = new Set(groups.map((g) => String(g.group_id || "")));
  // Keep only IDs that still exist in the current group list
  const validOrder = storedOrder.filter((id) => currentIds.has(id));
  // Find new groups not in stored order
  const orderedSet = new Set(validOrder);
  const newIds = groups
    .map((g) => String(g.group_id || ""))
    .filter((id) => id && !orderedSet.has(id));
  return [...validOrder, ...newIds];
}

function mergeArchivedGroupIds(storedIds: string[], groups: GroupMeta[]): string[] {
  const currentIds = new Set(groups.map((g) => String(g.group_id || "").trim()).filter(Boolean));
  return normalizeGroupIdList(storedIds).filter((id) => currentIds.has(id));
}

function reorderGroupSubset(globalOrder: string[], subsetIds: string[], fromIndex: number, toIndex: number): string[] {
  if (fromIndex === toIndex) return globalOrder;
  if (fromIndex < 0 || toIndex < 0) return globalOrder;
  if (fromIndex >= subsetIds.length || toIndex >= subsetIds.length) return globalOrder;

  const nextSubset = subsetIds.slice();
  const [moved] = nextSubset.splice(fromIndex, 1);
  if (!moved) return globalOrder;
  nextSubset.splice(toIndex, 0, moved);

  const subsetSet = new Set(subsetIds);
  let cursor = 0;
  return globalOrder.map((id) => {
    if (!subsetSet.has(id)) return id;
    const replacement = nextSubset[cursor];
    cursor += 1;
    return replacement || id;
  });
}

// In-flight guards
let refreshGroupsInFlight = false;
let refreshGroupsQueued = false;
const refreshActorsInFlight = new Set<string>();
const refreshActorsQueued = new Map<string, boolean>();
const warmGroupInFlight = new Set<string>();
const loadGroupInFlight = new Map<string, Promise<void>>();
let loadGroupToken = 0;
const deferredUnreadRefreshTimers = new Map<string, number>();
const settingsRequestEpochByGroup = new Map<string, number>();
const presentationRequestEpochByGroup = new Map<string, number>();
const chatWindowRequestEpochByGroup = new Map<string, number>();

type GroupViewSnapshot = {
  groupDoc: GroupDoc | null;
  events: LedgerEvent[];
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  groupPresentation: GroupPresentation | null;
  hasMoreHistory: boolean;
  cachedAt: number;
};

const groupViewCache = new Map<string, GroupViewSnapshot>();
const contextRequestEpochByGroup = new Map<string, number>();

function beginGroupRequestEpoch(map: Map<string, number>, groupId: string): number {
  const gid = String(groupId || "").trim();
  if (!gid) return 0;
  const next = Number(map.get(gid) || 0) + 1;
  map.set(gid, next);
  return next;
}

function isLatestGroupRequestEpoch(map: Map<string, number>, groupId: string, epoch: number): boolean {
  const gid = String(groupId || "").trim();
  if (!gid || epoch <= 0) return false;
  return Number(map.get(gid) || 0) === epoch;
}

export function beginContextRequest(groupId: string): number {
  const gid = String(groupId || "").trim();
  if (!gid) return 0;
  const next = Number(contextRequestEpochByGroup.get(gid) || 0) + 1;
  contextRequestEpochByGroup.set(gid, next);
  return next;
}

export function isLatestContextRequest(groupId: string, epoch: number): boolean {
  const gid = String(groupId || "").trim();
  if (!gid || epoch <= 0) return false;
  return Number(contextRequestEpochByGroup.get(gid) || 0) === epoch;
}

function cloneGroupDoc(doc: GroupDoc | null | undefined): GroupDoc | null {
  return doc ? { ...doc } : null;
}

function cloneEvents(events: LedgerEvent[] | undefined): LedgerEvent[] {
  return Array.isArray(events) ? [...events] : [];
}

function cloneActors(actors: Actor[] | undefined): Actor[] {
  return Array.isArray(actors) ? [...actors] : [];
}

function cloneGroupContext(ctx: GroupContext | null | undefined): GroupContext | null {
  return ctx ? { ...ctx } : null;
}

function cloneGroupSettings(settings: GroupSettings | null | undefined): GroupSettings | null {
  return settings ? { ...settings } : null;
}

function cloneGroupPresentation(presentation: GroupPresentation | null | undefined): GroupPresentation | null {
  if (!presentation) return null;
  return {
    ...presentation,
    slots: Array.isArray(presentation.slots)
      ? presentation.slots.map((slot) => ({
          ...slot,
          card: slot.card
            ? {
                ...slot.card,
                content: {
                  ...(slot.card.content || {}),
                  table: slot.card.content?.table
                    ? {
                        ...slot.card.content.table,
                        columns: [...(slot.card.content.table.columns || [])],
                        rows: Array.isArray(slot.card.content.table.rows)
                          ? slot.card.content.table.rows.map((row) => [...row])
                          : [],
                      }
                    : null,
                },
              }
            : null,
        }))
      : [],
  };
}

function getCachedGroupView(groupId: string): GroupViewSnapshot | null {
  const gid = String(groupId || "").trim();
  if (!gid) return null;
  const cached = groupViewCache.get(gid);
  if (!cached) return null;
  if (Date.now() - cached.cachedAt > GROUP_VIEW_CACHE_TTL_MS) return null;
  return {
    groupDoc: cloneGroupDoc(cached.groupDoc),
    events: cloneEvents(cached.events),
    actors: cloneActors(cached.actors),
    groupContext: cloneGroupContext(cached.groupContext),
    groupSettings: cloneGroupSettings(cached.groupSettings),
    groupPresentation: cloneGroupPresentation(cached.groupPresentation),
    hasMoreHistory: !!cached.hasMoreHistory,
    cachedAt: cached.cachedAt,
  };
}

function saveGroupView(groupId: string, patch: Partial<Omit<GroupViewSnapshot, "cachedAt">>): void {
  const gid = String(groupId || "").trim();
  if (!gid) return;

  const prev = groupViewCache.get(gid);
  groupViewCache.set(gid, {
    groupDoc: patch.groupDoc !== undefined ? cloneGroupDoc(patch.groupDoc) : cloneGroupDoc(prev?.groupDoc),
    events: patch.events !== undefined ? cloneEvents(patch.events) : cloneEvents(prev?.events),
    actors: patch.actors !== undefined ? cloneActors(patch.actors) : cloneActors(prev?.actors),
    groupContext: patch.groupContext !== undefined ? cloneGroupContext(patch.groupContext) : cloneGroupContext(prev?.groupContext),
    groupSettings: patch.groupSettings !== undefined ? cloneGroupSettings(patch.groupSettings) : cloneGroupSettings(prev?.groupSettings),
    groupPresentation: patch.groupPresentation !== undefined ? cloneGroupPresentation(patch.groupPresentation) : cloneGroupPresentation(prev?.groupPresentation),
    hasMoreHistory: patch.hasMoreHistory !== undefined ? !!patch.hasMoreHistory : !!prev?.hasMoreHistory,
    cachedAt: Date.now(),
  });
}

function clearDeferredUnreadRefresh(groupId: string): void {
  const gid = String(groupId || "").trim();
  const timer = deferredUnreadRefreshTimers.get(gid);
  if (timer !== undefined) {
    globalThis.clearTimeout(timer);
    deferredUnreadRefreshTimers.delete(gid);
  }
}

function scheduleDeferredUnreadRefresh(groupId: string, task: () => void): void {
  const gid = String(groupId || "").trim();
  if (!gid) return;
  clearDeferredUnreadRefresh(gid);
  const timer = globalThis.setTimeout(() => {
    deferredUnreadRefreshTimers.delete(gid);
    task();
  }, 0);
  deferredUnreadRefreshTimers.set(gid, timer);
}

function mergeActorUnreadCounts(nextActors: Actor[], previousActors: Actor[]): Actor[] {
  if (!nextActors.length || !previousActors.length) return nextActors;
  const unreadByActorId = new Map(
    previousActors
      .filter((actor) => typeof actor?.unread_count === "number")
      .map((actor) => [String(actor.id || ""), Number(actor.unread_count || 0)] as const)
  );

  return nextActors.map((actor) => {
    if (typeof actor?.unread_count === "number") return actor;
    const actorId = String(actor.id || "");
    if (!actorId || !unreadByActorId.has(actorId)) return actor;
    return { ...actor, unread_count: unreadByActorId.get(actorId) ?? 0 };
  });
}

function saveCurrentViewSnapshot(groupId: string, state: GroupState): void {
  const gid = String(groupId || "").trim();
  if (!gid) return;

  const bucket = state.chatByGroup[gid] || EMPTY_CHAT_BUCKET;
  const shellDoc = buildShellGroupDoc(gid, state.groups, null);
  saveGroupView(gid, {
    groupDoc: state.groupDoc && String(state.groupDoc.group_id || "") === gid ? state.groupDoc : shellDoc,
    events: bucket.events,
    actors: state.actors,
    groupContext: state.groupContext,
    groupSettings: state.groupSettings,
    groupPresentation: state.groupPresentation,
    hasMoreHistory: bucket.hasMoreHistory,
  });
}

function getGroupChatBucket(chatByGroup: Record<string, GroupChatBucket>, groupId: string): GroupChatBucket {
  const gid = String(groupId || "").trim();
  if (!gid) return EMPTY_CHAT_BUCKET;
  const stored = chatByGroup[gid];
  if (stored) return stored;
  const cached = getCachedGroupView(gid);
  return {
    events: cached?.events ? [...cached.events] : [],
    chatWindow: null,
    hasMoreHistory: cached?.hasMoreHistory ?? true,
    hasLoadedTail: false,
    isLoadingHistory: false,
    isChatWindowLoading: false,
  };
}

function ensureGroupChatBucket(chatByGroup: Record<string, GroupChatBucket>, groupId: string): Record<string, GroupChatBucket> {
  const gid = String(groupId || "").trim();
  if (!gid || chatByGroup[gid]) return chatByGroup;
  return {
    ...chatByGroup,
    [gid]: getGroupChatBucket(chatByGroup, gid),
  };
}

export function selectChatBucketState(state: GroupState, groupId: string): GroupChatBucket {
  const gid = String(groupId || "").trim();
  if (!gid) return EMPTY_CHAT_BUCKET;
  return state.chatByGroup[gid] || EMPTY_CHAT_BUCKET;
}

function buildChatBucketPatch(
  state: GroupState,
  groupId: string,
  patch: Partial<GroupChatBucket>
): Partial<GroupState> | null {
  const gid = String(groupId || "").trim();
  if (!gid) return null;

  const prev = state.chatByGroup[gid] || EMPTY_CHAT_BUCKET;
  const nextEvents = patch.events !== undefined ? patch.events : prev.events;
  const nextHasMoreHistory = patch.hasMoreHistory !== undefined ? patch.hasMoreHistory : prev.hasMoreHistory;
  if (patch.events !== undefined || patch.hasMoreHistory !== undefined) {
    saveGroupView(gid, {
      events: nextEvents,
      hasMoreHistory: nextHasMoreHistory,
    });
  }
  return {
    chatByGroup: {
      ...state.chatByGroup,
      [gid]: {
        events: nextEvents,
        chatWindow: patch.chatWindow !== undefined ? patch.chatWindow : prev.chatWindow,
        hasMoreHistory: nextHasMoreHistory,
        hasLoadedTail: patch.hasLoadedTail !== undefined ? patch.hasLoadedTail : prev.hasLoadedTail,
        isLoadingHistory: patch.isLoadingHistory !== undefined ? patch.isLoadingHistory : prev.isLoadingHistory,
        isChatWindowLoading: patch.isChatWindowLoading !== undefined ? patch.isChatWindowLoading : prev.isChatWindowLoading,
      },
    },
  };
}

function resolveChatGroupId(state: GroupState, groupId?: string): string {
  return String(groupId || state.selectedGroupId || "").trim();
}

function updateReadThroughIndex(messages: LedgerEvent[], endIndex: number, actorId: string) {
  const next = messages.slice();
  let changed = false;
  for (let i = 0; i <= endIndex; i++) {
    const message = next[i];
    if (!message || message.kind !== "chat.message") continue;
    const readStatus: Record<string, boolean> | null =
      message._read_status && typeof message._read_status === "object" ? { ...message._read_status } : null;
    const obligationStatus =
      message._obligation_status && typeof message._obligation_status === "object"
        ? { ...message._obligation_status }
        : null;
    if (!readStatus || !Object.prototype.hasOwnProperty.call(readStatus, actorId)) continue;
    if (readStatus[actorId] === true) continue;
    readStatus[actorId] = true;
    if (
      obligationStatus &&
      Object.prototype.hasOwnProperty.call(obligationStatus, actorId) &&
      typeof obligationStatus[actorId] === "object"
    ) {
      obligationStatus[actorId] = { ...obligationStatus[actorId], read: true };
      next[i] = { ...message, _read_status: readStatus, _obligation_status: obligationStatus };
    } else {
      next[i] = { ...message, _read_status: readStatus };
    }
    changed = true;
  }
  return { next, changed };
}

function updateAckAtIndex(messages: LedgerEvent[], index: number, actorId: string) {
  const next = messages.slice();
  const message = next[index];
  if (!message || message.kind !== "chat.message") return { next, changed: false };

  const ackStatus: Record<string, boolean> | null =
    message._ack_status && typeof message._ack_status === "object" ? { ...message._ack_status } : null;
  const obligationStatus =
    message._obligation_status && typeof message._obligation_status === "object"
      ? { ...message._obligation_status }
      : null;
  if (!ackStatus || !Object.prototype.hasOwnProperty.call(ackStatus, actorId) || ackStatus[actorId] === true) {
    return { next, changed: false };
  }

  ackStatus[actorId] = true;
  if (
    obligationStatus &&
    Object.prototype.hasOwnProperty.call(obligationStatus, actorId) &&
    typeof obligationStatus[actorId] === "object"
  ) {
    obligationStatus[actorId] = { ...obligationStatus[actorId], acked: true };
    next[index] = { ...message, _ack_status: ackStatus, _obligation_status: obligationStatus };
  } else {
    next[index] = { ...message, _ack_status: ackStatus };
  }
  return { next, changed: true };
}

function updateReplyAtIndex(messages: LedgerEvent[], index: number, actorId: string) {
  const next = messages.slice();
  const message = next[index];
  if (!message || message.kind !== "chat.message") return { next, changed: false };

  const ackStatus: Record<string, boolean> | null =
    message._ack_status && typeof message._ack_status === "object" ? { ...message._ack_status } : null;
  const obligationStatus =
    message._obligation_status && typeof message._obligation_status === "object"
      ? { ...message._obligation_status }
      : null;
  if (
    !obligationStatus ||
    !Object.prototype.hasOwnProperty.call(obligationStatus, actorId) ||
    typeof obligationStatus[actorId] !== "object"
  ) {
    return { next, changed: false };
  }

  const previous = obligationStatus[actorId];
  if (previous.replied && previous.acked) {
    return { next, changed: false };
  }

  obligationStatus[actorId] = { ...previous, replied: true, acked: true };
  if (ackStatus && Object.prototype.hasOwnProperty.call(ackStatus, actorId)) {
    ackStatus[actorId] = true;
    next[index] = { ...message, _ack_status: ackStatus, _obligation_status: obligationStatus };
  } else {
    next[index] = { ...message, _obligation_status: obligationStatus };
  }
  return { next, changed: true };
}

function buildShellGroupDoc(groupId: string, groups: GroupMeta[], cached: GroupViewSnapshot | null): GroupDoc | null {
  const gid = String(groupId || "").trim();
  if (!gid) return null;
  if (cached?.groupDoc && String(cached.groupDoc.group_id || "") === gid) {
    return cloneGroupDoc(cached.groupDoc);
  }

  const meta = groups.find((group) => String(group.group_id || "") === gid) || null;
  if (!meta) return null;
  return {
    group_id: gid,
    title: meta.title,
    topic: meta.topic,
    state: meta.state,
  };
}

function buildPrimedGroupState(groupId: string, groups: GroupMeta[]) {
  const gid = String(groupId || "").trim();
  if (!gid) {
    return {
      groupDoc: null,
      actors: [],
      groupContext: null,
      groupSettings: null,
      groupPresentation: null,
    };
  }

  const cached = getCachedGroupView(gid);
  return {
    groupDoc: buildShellGroupDoc(gid, groups, cached),
    actors: cached?.actors || [],
    groupContext: cached?.groupContext || null,
    groupSettings: cached?.groupSettings || null,
    groupPresentation: cached?.groupPresentation || null,
  };
}

function filterUiEvents(events: LedgerEvent[] | undefined): LedgerEvent[] {
  return Array.isArray(events) ? events.filter((ev) => ev && ev.kind !== "context.sync") : [];
}

export const useGroupStore = create<GroupState>((set, get) => ({
  // Initial state
  groups: [],
  groupOrder: loadGroupOrder(),
  archivedGroupIds: loadArchivedGroupIds(),
  selectedGroupId: loadSelectedGroupId(),
  chatByGroup: {},
  groupDoc: null,
  events: [],
  chatWindow: null,
  actors: [],
  groupContext: null,
  groupSettings: null,
  groupPresentation: null,
  runtimes: [],
  hasMoreHistory: true,
  isLoadingHistory: false,
  isChatWindowLoading: false,

  // Sync actions
  setGroups: (groups) => {
    const storedOrder = get().groupOrder;
    const storedArchived = get().archivedGroupIds;
    const mergedOrder = mergeGroupOrder(storedOrder, groups);
    const mergedArchived = mergeArchivedGroupIds(storedArchived, groups);
    saveGroupOrder(mergedOrder);
    saveArchivedGroupIds(mergedArchived);
    set({ groups, groupOrder: mergedOrder, archivedGroupIds: mergedArchived });
  },
  setGroupOrder: (order) => {
    saveGroupOrder(order);
    set({ groupOrder: order });
  },
  reorderGroupsInSection: (section, fromIndex, toIndex) => {
    const { groupOrder, archivedGroupIds } = get();
    const archivedSet = new Set(archivedGroupIds);
    const subsetIds = groupOrder.filter((id) =>
      section === "archived" ? archivedSet.has(id) : !archivedSet.has(id)
    );
    const nextOrder = reorderGroupSubset(groupOrder, subsetIds, fromIndex, toIndex);
    if (nextOrder === groupOrder) return;
    saveGroupOrder(nextOrder);
    set({ groupOrder: nextOrder });
  },
  archiveGroup: (groupId) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    const next = normalizeGroupIdList([...get().archivedGroupIds, gid]);
    saveArchivedGroupIds(next);
    set({ archivedGroupIds: next });
  },
  restoreGroup: (groupId) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    const next = get().archivedGroupIds.filter((id) => id !== gid);
    saveArchivedGroupIds(next);
    set({ archivedGroupIds: next });
  },
  getOrderedGroups: () => {
    const { groups, groupOrder } = get();
    const groupMap = new Map(groups.map((g) => [String(g.group_id || ""), g]));
    const ordered: GroupMeta[] = [];
    for (const id of groupOrder) {
      const g = groupMap.get(id);
      if (g) ordered.push(g);
    }
    // Include any groups not in order (shouldn't happen normally, but be safe)
    for (const g of groups) {
      const id = String(g.group_id || "");
      if (!groupOrder.includes(id)) ordered.push(g);
    }
    return ordered;
  },
  setSelectedGroupId: (id) => {
    const gid = String(id || "").trim();
    saveSelectedGroupId(gid);
    set((state) => {
      const prevGid = String(state.selectedGroupId || "").trim();

      // 切组前先把当前视图快照落到缓存，回切时才能做到秒开。
      if (prevGid && prevGid !== gid) {
        saveCurrentViewSnapshot(prevGid, state);
      }

      const nextChatByGroup = ensureGroupChatBucket(state.chatByGroup, gid);
      return {
        selectedGroupId: gid,
        chatByGroup: nextChatByGroup,
        ...buildPrimedGroupState(gid, state.groups),
      };
    });
  },
  setGroupDoc: (doc) => set({ groupDoc: doc }),
  setEvents: (events, groupId) =>
    set((state) => buildChatBucketPatch(state, resolveChatGroupId(state, groupId), { events }) ?? state),
  setChatWindow: (chatWindow, groupId) =>
    set((state) => buildChatBucketPatch(state, resolveChatGroupId(state, groupId), { chatWindow }) ?? state),
  appendEvent: (event, groupId) =>
    set((state) => {
      const gid = resolveChatGroupId(state, groupId);
      if (!gid) return state;
      const bucket = getGroupChatBucket(state.chatByGroup, gid);
      if (event.id && bucket.events.some((e) => e.id === event.id)) return state;
      const nextEvents = bucket.events.concat([event]);
      return buildChatBucketPatch(state, gid, {
        events: nextEvents.length > MAX_UI_EVENTS ? nextEvents.slice(nextEvents.length - MAX_UI_EVENTS) : nextEvents,
      }) ?? state;
    }),
  prependEvents: (newEvents, groupId) =>
    set((state) => {
      const gid = resolveChatGroupId(state, groupId);
      if (!gid) return state;
      const bucket = getGroupChatBucket(state.chatByGroup, gid);
      const existingIds = new Set(bucket.events.map((event) => event.id).filter(Boolean));
      const uniqueNew = newEvents.filter((event) => event.id && !existingIds.has(event.id));
      const merged = [...uniqueNew, ...bucket.events];
      return buildChatBucketPatch(state, gid, {
        events: merged.length > MAX_UI_EVENTS ? merged.slice(0, MAX_UI_EVENTS) : merged,
      }) ?? state;
    }),
  setActors: (actors) => set({ actors }),
  incrementActorUnread: (actorIds) =>
    set((state) => {
      if (!state.actors.length || !actorIds.length) return state;
      const targets = new Set(actorIds.map((id) => String(id || "").trim()).filter(Boolean));
      if (targets.size === 0) return state;

      let changed = false;
      const next = state.actors.map((actor) => {
        const actorId = String(actor.id || "").trim();
        if (!targets.has(actorId)) return actor;
        changed = true;
        return {
          ...actor,
          unread_count: Math.max(0, Number(actor.unread_count || 0)) + 1,
        };
      });

      return changed ? { actors: next } : state;
    }),
  updateActorActivity: (updates) =>
    set((state) => {
      if (!state.actors.length || !updates.length) return state;
      const map = new Map(updates.map((u) => [u.id, u]));
      let changed = false;
      const next = state.actors.map((a) => {
        const u = map.get(a.id);
        if (
          u && (
            a.idle_seconds !== (u.idle_seconds ?? null)
            || a.running !== u.running
            || a.effective_working_state !== u.effective_working_state
            || a.effective_working_reason !== u.effective_working_reason
            || a.effective_working_updated_at !== (u.effective_working_updated_at ?? null)
            || a.effective_active_task_id !== (u.effective_active_task_id ?? null)
          )
        ) {
          changed = true;
          return {
            ...a,
            idle_seconds: u.idle_seconds ?? null,
            running: u.running,
            effective_working_state: u.effective_working_state,
            effective_working_reason: u.effective_working_reason,
            effective_working_updated_at: u.effective_working_updated_at ?? null,
            effective_active_task_id: u.effective_active_task_id ?? null,
          };
        }
        return a;
      });
      return changed ? { actors: next } : state;
    }),
  setGroupContext: (ctx) => set({ groupContext: ctx }),
  setGroupSettings: (settings) =>
    set((state) => {
      const gid = String(state.selectedGroupId || "").trim();
      if (gid) {
        beginGroupRequestEpoch(settingsRequestEpochByGroup, gid);
        saveGroupView(gid, { groupSettings: settings });
      }
      return { groupSettings: settings };
    }),
  setGroupPresentation: (presentation) =>
    set((state) => {
      const gid = String(state.selectedGroupId || "").trim();
      if (gid) {
        beginGroupRequestEpoch(presentationRequestEpochByGroup, gid);
        saveGroupView(gid, { groupPresentation: presentation });
      }
      return { groupPresentation: presentation };
    }),
  setRuntimes: (runtimes) => set({ runtimes }),

  updateReadStatus: (eventId, actorId, groupId) =>
    set((state) => {
      const gid = resolveChatGroupId(state, groupId);
      if (!gid) return state;
      const bucket = getGroupChatBucket(state.chatByGroup, gid);
      const eventIndex = bucket.events.findIndex(
        (event) => event.kind === "chat.message" && String(event.id || "") === eventId
      );
      if (eventIndex < 0 && !bucket.chatWindow) return state;

      const liveResult = eventIndex >= 0
        ? updateReadThroughIndex(bucket.events, eventIndex, actorId)
        : { next: bucket.events, changed: false };
      let nextWindow = bucket.chatWindow;
      let didChange = liveResult.changed;

      if (bucket.chatWindow) {
        const windowIndex = bucket.chatWindow.events.findIndex(
          (event) => event.kind === "chat.message" && String(event.id || "") === eventId
        );
        if (windowIndex >= 0) {
          const windowResult = updateReadThroughIndex(bucket.chatWindow.events, windowIndex, actorId);
          if (windowResult.changed) {
            nextWindow = { ...bucket.chatWindow, events: windowResult.next };
            didChange = true;
          }
        }
      }

      if (!didChange) return state;
      return buildChatBucketPatch(state, gid, {
        events: liveResult.next,
        chatWindow: nextWindow,
      }) ?? state;
    }),

  updateAckStatus: (eventId, actorId, groupId) =>
    set((state) => {
      const gid = resolveChatGroupId(state, groupId);
      if (!gid) return state;
      const bucket = getGroupChatBucket(state.chatByGroup, gid);
      const eventIndex = bucket.events.findIndex(
        (event) => event.kind === "chat.message" && String(event.id || "") === eventId
      );
      if (eventIndex < 0 && !bucket.chatWindow) return state;

      const liveResult = eventIndex >= 0
        ? updateAckAtIndex(bucket.events, eventIndex, actorId)
        : { next: bucket.events, changed: false };
      let nextWindow = bucket.chatWindow;
      let didChange = liveResult.changed;

      if (bucket.chatWindow) {
        const windowIndex = bucket.chatWindow.events.findIndex(
          (event) => event.kind === "chat.message" && String(event.id || "") === eventId
        );
        if (windowIndex >= 0) {
          const windowResult = updateAckAtIndex(bucket.chatWindow.events, windowIndex, actorId);
          if (windowResult.changed) {
            nextWindow = { ...bucket.chatWindow, events: windowResult.next };
            didChange = true;
          }
        }
      }

      if (!didChange) return state;
      return buildChatBucketPatch(state, gid, {
        events: liveResult.next,
        chatWindow: nextWindow,
      }) ?? state;
    }),

  updateReplyStatus: (eventId, actorId, groupId) =>
    set((state) => {
      const gid = resolveChatGroupId(state, groupId);
      if (!gid) return state;
      const bucket = getGroupChatBucket(state.chatByGroup, gid);
      const eventIndex = bucket.events.findIndex(
        (event) => event.kind === "chat.message" && String(event.id || "") === eventId
      );
      if (eventIndex < 0 && !bucket.chatWindow) return state;

      const liveResult = eventIndex >= 0
        ? updateReplyAtIndex(bucket.events, eventIndex, actorId)
        : { next: bucket.events, changed: false };
      let nextWindow = bucket.chatWindow;
      let didChange = liveResult.changed;

      if (bucket.chatWindow) {
        const windowIndex = bucket.chatWindow.events.findIndex(
          (event) => event.kind === "chat.message" && String(event.id || "") === eventId
        );
        if (windowIndex >= 0) {
          const windowResult = updateReplyAtIndex(bucket.chatWindow.events, windowIndex, actorId);
          if (windowResult.changed) {
            nextWindow = { ...bucket.chatWindow, events: windowResult.next };
            didChange = true;
          }
        }
      }

      if (!didChange) return state;
      return buildChatBucketPatch(state, gid, {
        events: liveResult.next,
        chatWindow: nextWindow,
      }) ?? state;
    }),
  setHasMoreHistory: (value, groupId) =>
    set((state) => buildChatBucketPatch(state, resolveChatGroupId(state, groupId), { hasMoreHistory: value }) ?? state),
  setIsLoadingHistory: (value, groupId) =>
    set((state) => buildChatBucketPatch(state, resolveChatGroupId(state, groupId), { isLoadingHistory: value }) ?? state),
  setIsChatWindowLoading: (value, groupId) =>
    set((state) => buildChatBucketPatch(state, resolveChatGroupId(state, groupId), { isChatWindowLoading: value }) ?? state),

  // Async actions
  refreshGroups: async () => {
    if (refreshGroupsInFlight) {
      refreshGroupsQueued = true;
      return;
    }
    refreshGroupsInFlight = true;
    try {
      const resp = await api.fetchGroups();
      if (resp.ok) {
        const next = resp.result.groups || [];
        // Use setGroups to ensure groupOrder is updated
        get().setGroups(next);

        const cur = String(get().selectedGroupId || "").trim();
        const curExists = !!cur && next.some((g) => String(g.group_id || "") === cur);
        if (!curExists) {
          if (next.length > 0) {
            // Selected group disappeared (or none selected): restore the persisted
            // selection when it still exists, otherwise fall back to the first group.
            // Also clear stale per-group caches so UI does not render old data while switching.
            const persisted = loadSelectedGroupId();
            const persistedExists = !!persisted && next.some((g) => String(g.group_id || "") === persisted);
            const nextGroupId = persistedExists ? persisted : String(next[0].group_id || "");
            saveSelectedGroupId(nextGroupId);
            const nextChatByGroup = ensureGroupChatBucket(get().chatByGroup, nextGroupId);
            set({
              selectedGroupId: nextGroupId,
              chatByGroup: nextChatByGroup,
              ...buildPrimedGroupState(nextGroupId, next),
            });
          } else {
            // No groups remain: clear selection + per-group caches.
            groupViewCache.clear();
            saveSelectedGroupId("");
            set({
              selectedGroupId: "",
              chatByGroup: {},
              groupDoc: null,
              events: [],
              actors: [],
              groupContext: null,
              groupSettings: null,
              groupPresentation: null,
              chatWindow: null,
              hasMoreHistory: false,
              isLoadingHistory: false,
              isChatWindowLoading: false,
            });
          }
          return;
        }

        saveSelectedGroupId(cur);

        // Keep groupDoc's basic fields in sync with the group list. This matters when
        // group state/title/topic is changed externally (CLI/MCP/etc.), since groupDoc
        // is otherwise only updated by web actions or explicit loadGroup().
        const selectedId = get().selectedGroupId;
        const doc = get().groupDoc;
        if (doc && selectedId && String(doc.group_id || "") === String(selectedId || "")) {
          const meta = next.find((g) => String(g.group_id || "") === String(selectedId || "")) || null;
          if (meta) {
            const patch: Partial<GroupDoc> = {};
            if (typeof meta.state === "string" && meta.state !== doc.state) patch.state = meta.state;
            if (typeof meta.title === "string" && meta.title !== doc.title) patch.title = meta.title;
            if (typeof meta.topic === "string" && meta.topic !== doc.topic) patch.topic = meta.topic;
            if (Object.keys(patch).length > 0) {
              const nextDoc = { ...doc, ...patch };
              set({ groupDoc: nextDoc });
              saveGroupView(selectedId, { groupDoc: nextDoc });
            }
          }
        }
      }
    } catch (e) {
      console.error("Failed to refresh groups:", e);
    } finally {
      refreshGroupsInFlight = false;
      if (refreshGroupsQueued) {
        refreshGroupsQueued = false;
        void get().refreshGroups();
      }
    }
  },

  refreshActors: async (groupId?: string, opts?: { includeUnread?: boolean }) => {
    const gid = String(groupId || get().selectedGroupId || "").trim();
    if (!gid) return;
    const includeUnread = opts?.includeUnread !== false;
    if (refreshActorsInFlight.has(gid)) {
      const queuedIncludeUnread = refreshActorsQueued.get(gid) ?? false;
      refreshActorsQueued.set(gid, queuedIncludeUnread || includeUnread);
      return;
    }
    refreshActorsInFlight.add(gid);
    try {
      const resp = await api.fetchActors(gid, includeUnread);
      if (resp.ok) {
        const prevActors = get().selectedGroupId === gid ? get().actors : getCachedGroupView(gid)?.actors || [];
        const nextActors = includeUnread
          ? (resp.result.actors || [])
          : mergeActorUnreadCounts(resp.result.actors || [], prevActors);
        saveGroupView(gid, { actors: nextActors });
        if (get().selectedGroupId === gid) {
          set({ actors: nextActors });
        }
      }
    } catch (e) {
      console.error(`Failed to refresh actors for group=${gid}:`, e);
    } finally {
      refreshActorsInFlight.delete(gid);
      const queuedIncludeUnread = refreshActorsQueued.get(gid);
      if (queuedIncludeUnread !== undefined) {
        refreshActorsQueued.delete(gid);
        void get().refreshActors(gid, { includeUnread: queuedIncludeUnread });
      }
    }
  },

  refreshSettings: async (groupId?: string) => {
    const gid = String(groupId || get().selectedGroupId || "").trim();
    if (!gid) return;
    const epoch = beginGroupRequestEpoch(settingsRequestEpochByGroup, gid);
    try {
      const resp = await api.fetchSettings(gid);
      if (!resp.ok) return;
      if (!isLatestGroupRequestEpoch(settingsRequestEpochByGroup, gid, epoch)) return;
      const nextSettings = resp.result.settings || null;
      saveGroupView(gid, { groupSettings: nextSettings });
      if (String(get().selectedGroupId || "").trim() === gid) {
        set({ groupSettings: nextSettings });
      }
    } catch (error) {
      console.error(`Failed to refresh settings for group=${gid}:`, error);
    }
  },

  refreshPresentation: async (groupId?: string) => {
    const gid = String(groupId || get().selectedGroupId || "").trim();
    if (!gid) return;
    const epoch = beginGroupRequestEpoch(presentationRequestEpochByGroup, gid);
    try {
      const resp = await api.fetchPresentation(gid);
      if (!resp.ok) return;
      if (!isLatestGroupRequestEpoch(presentationRequestEpochByGroup, gid, epoch)) return;
      const nextPresentation = resp.result.presentation || null;
      saveGroupView(gid, { groupPresentation: nextPresentation });
      if (String(get().selectedGroupId || "").trim() === gid) {
        set({ groupPresentation: nextPresentation });
      }
    } catch (error) {
      console.error(`Failed to refresh presentation for group=${gid}:`, error);
    }
  },

  loadGroup: async (groupId: string) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    const inFlight = loadGroupInFlight.get(gid);
    if (inFlight) {
      return;
    }

    const token = ++loadGroupToken;
    const isLatestSelection = () => get().selectedGroupId === gid && loadGroupToken === token;
    const commitViewPatch = (patch: Partial<Pick<GroupState, "groupDoc" | "actors" | "groupContext" | "groupSettings" | "groupPresentation">>) => {
      saveGroupView(gid, patch);
      if (isLatestSelection()) {
        set(patch);
      }
    };
    const commitChatPatch = (patch: Partial<GroupChatBucket>) => {
      const cachePatch: Partial<Omit<GroupViewSnapshot, "cachedAt">> = {};
      if (patch.events !== undefined) cachePatch.events = patch.events;
      if (patch.hasMoreHistory !== undefined) cachePatch.hasMoreHistory = patch.hasMoreHistory;
      if (Object.keys(cachePatch).length > 0) {
        saveGroupView(gid, cachePatch);
      }
      if (isLatestSelection()) {
        set((state) => buildChatBucketPatch(state, gid, patch) ?? state);
      }
    };
    const state = get();
    const currentDocGroupId = String(state.groupDoc?.group_id || "").trim();
    if (currentDocGroupId !== gid) {
      const nextChatByGroup = ensureGroupChatBucket(state.chatByGroup, gid);
      const chatBucket = nextChatByGroup[gid] || EMPTY_CHAT_BUCKET;
      const primedState = buildPrimedGroupState(gid, state.groups);
      saveGroupView(gid, {
        groupDoc: primedState.groupDoc,
        events: chatBucket.events,
        actors: primedState.actors,
        groupContext: primedState.groupContext,
        groupSettings: primedState.groupSettings,
        groupPresentation: primedState.groupPresentation,
        hasMoreHistory: chatBucket.hasMoreHistory,
      });
      if (isLatestSelection()) {
        set({ chatByGroup: nextChatByGroup, ...primedState });
      }
    }

    const showPromise = api.fetchGroup(gid);
    const tailPromise = api.fetchLedgerTail(gid);
    const actorsPromise = api.fetchActors(gid, false);

    void showPromise.then((show) => {
      if (show.ok) {
        commitViewPatch({ groupDoc: show.result.group });
        return;
      }

      const code = String(show.error?.code || "").trim();
      if (code === "group_not_found") {
        groupViewCache.delete(gid);
        set((state) => ({
          ...(buildChatBucketPatch(state, gid, {
            events: [],
            chatWindow: null,
            hasMoreHistory: false,
            hasLoadedTail: true,
            isLoadingHistory: false,
            isChatWindowLoading: false,
          }) || {}),
          ...(isLatestSelection()
            ? {
                groupDoc: null,
                actors: [],
                groupContext: null,
                groupSettings: null,
                groupPresentation: null,
              }
            : {}),
        }));
      }
    }).catch((error) => {
      console.error(`Failed to load group metadata for group=${gid}:`, error);
    });

    void tailPromise.then((tail) => {
      if (!tail.ok) {
        commitChatPatch({ hasLoadedTail: true });
        return;
      }
      const mergedEvents = mergeLedgerEvents(
        getGroupChatBucket(get().chatByGroup, gid).events,
        filterUiEvents(tail.result.events || []),
        MAX_UI_EVENTS
      );
      commitChatPatch({
        events: mergedEvents,
        hasMoreHistory: !!tail.result.has_more,
        hasLoadedTail: true,
      });
    }).catch((error) => {
      console.error(`Failed to load ledger tail for group=${gid}:`, error);
      commitChatPatch({ hasLoadedTail: true });
    });

    void actorsPromise.then((actorsResp) => {
      if (!actorsResp.ok) return;
      commitViewPatch({ actors: actorsResp.result.actors || [] });
    }).catch((error) => {
      console.error(`Failed to load actors for group=${gid}:`, error);
    }).finally(() => {
      scheduleDeferredUnreadRefresh(gid, () => {
        if (isLatestSelection()) {
          void get().refreshActors(gid, { includeUnread: true });
        }
      });
    });

    const initialLoad = Promise.allSettled([showPromise, tailPromise, actorsPromise]);
    const initialLoadDone = initialLoad.then(() => undefined);
    loadGroupInFlight.set(gid, initialLoadDone);
    void initialLoad.then(() => {
      const contextEpoch = beginContextRequest(gid);
      void api.fetchContext(gid, { detail: "summary" }).then((ctx) => {
        if (!ctx.ok) return;
        if (!isLatestContextRequest(gid, contextEpoch)) return;
        const summary = ctx.result as GroupContext;
        const meta = summary && typeof summary === "object" ? (summary as { meta?: unknown }).meta : null;
        const summarySnapshot = meta && typeof meta === "object"
          ? ((meta as { summary_snapshot?: unknown }).summary_snapshot ?? null)
          : null;
        const snapshotState = summarySnapshot && typeof summarySnapshot === "object"
          ? String((summarySnapshot as { state?: unknown }).state || "").trim().toLowerCase()
          : "";
        const currentState = get();
        const hasCachedContext =
          String(currentState.groupDoc?.group_id || "").trim() === gid &&
          currentState.groupContext !== null;

        if (snapshotState === "stale" && hasCachedContext) {
          return;
        }
        if (snapshotState === "missing" || (snapshotState === "stale" && !hasCachedContext)) {
          void api.fetchContext(gid, { detail: "full", fresh: true }).then((fullCtx) => {
            if (!fullCtx.ok) return;
            if (!isLatestContextRequest(gid, contextEpoch)) return;
            commitViewPatch({ groupContext: fullCtx.result as GroupContext });
          }).catch((error) => {
            console.error(`Failed to load fresh context for group=${gid}:`, error);
          });
          return;
        }

        commitViewPatch({ groupContext: summary });
      }).catch((error) => {
        console.error(`Failed to load context for group=${gid}:`, error);
      });

      const settingsEpoch = beginGroupRequestEpoch(settingsRequestEpochByGroup, gid);
      void api.fetchSettings(gid).then((settings) => {
        if (!settings.ok) return;
        if (!isLatestGroupRequestEpoch(settingsRequestEpochByGroup, gid, settingsEpoch)) return;
        commitViewPatch({ groupSettings: settings.result.settings || null });
      }).catch((error) => {
        console.error(`Failed to load settings for group=${gid}:`, error);
      });

      void api.fetchPresentation(gid).then((presentationResp) => {
        if (!presentationResp.ok) return;
        commitViewPatch({ groupPresentation: presentationResp.result.presentation || null });
      }).catch((error) => {
        console.error(`Failed to load presentation for group=${gid}:`, error);
      });
    }).finally(() => {
      if (loadGroupInFlight.get(gid) === initialLoadDone) {
        loadGroupInFlight.delete(gid);
      }
    });
  },

  warmGroup: async (groupId: string) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    if (gid === String(get().selectedGroupId || "").trim()) return;
    if (warmGroupInFlight.has(gid)) return;
    if (getCachedGroupView(gid)) return;

    warmGroupInFlight.add(gid);
    try {
      const [show, tail, actorsResp, presentationResp] = await Promise.all([
        api.fetchGroup(gid),
        api.fetchLedgerTail(gid),
        api.fetchActors(gid, false),
        api.fetchPresentation(gid),
      ]);

      const patch: Partial<Omit<GroupViewSnapshot, "cachedAt">> = {};
      if (show.ok) {
        patch.groupDoc = show.result.group;
      } else {
        patch.groupDoc = buildShellGroupDoc(gid, get().groups, null);
      }
      if (tail.ok) {
        patch.events = filterUiEvents(tail.result.events || []);
        patch.hasMoreHistory = !!tail.result.has_more;
      }
      if (actorsResp.ok) {
        patch.actors = actorsResp.result.actors || [];
      }
      if (presentationResp.ok) {
        patch.groupPresentation = presentationResp.result.presentation || null;
      }

      saveGroupView(gid, patch);
    } catch (error) {
      console.error(`Failed to warm group=${gid}:`, error);
    } finally {
      warmGroupInFlight.delete(gid);
    }
  },

  loadMoreHistory: async (groupId?: string) => {
    const gid = String(groupId || get().selectedGroupId || "").trim();
    if (!gid) return;

    const bucket = getGroupChatBucket(get().chatByGroup, gid);
    if (bucket.isLoadingHistory || !bucket.hasMoreHistory) return;

    const chatMessages = bucket.events.filter((event) => event.kind === "chat.message");
    const firstEvent = chatMessages[0];
    if (!firstEvent?.id) return;

    set((state) => buildChatBucketPatch(state, gid, { isLoadingHistory: true }) ?? state);
    try {
      const resp = await api.fetchOlderMessages(gid, String(firstEvent.id), 50);
      if (resp.ok) {
        const olderChatEvents = (resp.result.events || []).filter(
          (event) => event && event.kind === "chat.message"
        );
        const currentBucket = getGroupChatBucket(get().chatByGroup, gid);
        const existingIds = new Set(currentBucket.events.map((event) => event.id).filter(Boolean));
        const uniqueNew = olderChatEvents.filter((event) => event.id && !existingIds.has(event.id));
        const exhaustedHistory = olderChatEvents.length === 0 || uniqueNew.length === 0;
        const merged = [...uniqueNew, ...currentBucket.events];
        set((state) => buildChatBucketPatch(state, gid, {
          events: merged.length > MAX_UI_EVENTS ? merged.slice(0, MAX_UI_EVENTS) : merged,
          hasMoreHistory: exhaustedHistory ? false : !!resp.result.has_more,
        }) ?? state);
      }
    } finally {
      set((state) => buildChatBucketPatch(state, gid, { isLoadingHistory: false }) ?? state);
    }
  },

  openChatWindow: async (groupId: string, centerEventId: string) => {
    const gid = String(groupId || "").trim();
    const eid = String(centerEventId || "").trim();
    if (!gid || !eid) return;
    const epoch = beginGroupRequestEpoch(chatWindowRequestEpochByGroup, gid);

    set((state) => buildChatBucketPatch(state, gid, {
      isChatWindowLoading: true,
      chatWindow: {
        groupId: gid,
        centerEventId: eid,
        centerIndex: 0,
        events: [],
        hasMoreBefore: false,
        hasMoreAfter: false,
      },
    }) ?? state);
    try {
      const resp = await api.fetchMessageWindow(gid, eid, { before: 30, after: 30 });
      if (!isLatestGroupRequestEpoch(chatWindowRequestEpochByGroup, gid, epoch)) return;
      if (!resp.ok) {
        set((state) => buildChatBucketPatch(state, gid, { chatWindow: null }) ?? state);
        return;
      }

      const events = (resp.result.events || []).filter((event) => event && event.kind === "chat.message");
      set((state) => buildChatBucketPatch(state, gid, {
        chatWindow: {
          groupId: gid,
          centerEventId: resp.result.center_id,
          centerIndex: resp.result.center_index,
          events,
          hasMoreBefore: !!resp.result.has_more_before,
          hasMoreAfter: !!resp.result.has_more_after,
        },
      }) ?? state);
    } finally {
      if (isLatestGroupRequestEpoch(chatWindowRequestEpochByGroup, gid, epoch)) {
        set((state) => buildChatBucketPatch(state, gid, { isChatWindowLoading: false }) ?? state);
      }
    }
  },

  closeChatWindow: (groupId?: string) =>
    set((state) => {
      const gid = resolveChatGroupId(state, groupId);
      beginGroupRequestEpoch(chatWindowRequestEpochByGroup, gid);
      return buildChatBucketPatch(state, gid, {
        chatWindow: null,
        isChatWindowLoading: false,
      }) ?? state;
    }),
}));
