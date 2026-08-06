// Actor action helpers extracted from ActorTab-related logic.
import { useCallback, useEffect, useRef, useState } from "react";
import { useGroupStore, useUIStore, useModalStore, useInboxStore, useFormStore } from "../stores";
import * as api from "../services/api";
import type { Actor, SupportedRuntime } from "../types";
import { formatCapabilityIdInput } from "../utils/capabilityAutoload";
import { getEffectiveActorRunner } from "../utils/headlessRuntimeSupport";
import { beginActorAction, endActorAction } from "./actorActionInFlight";
import {
  beginActionRequestEpoch,
  isLatestActionRequestEpoch,
} from "./actionRequestEpoch";

const ACTOR_START_RECONCILE_DELAYS_MS = [1200, 3500] as const;

function latestActorHasResumeFailure(groupId: string, actorId: string): boolean {
  const gid = String(groupId || "").trim();
  const aid = String(actorId || "").trim();
  const state = useGroupStore.getState();
  if (!gid || !aid || String(state.selectedGroupId || "").trim() !== gid) return false;
  const latest = state.actors.find((item) => String(item.id || "").trim() === aid);
  return String(latest?.runtime_session_status || "").trim().toLowerCase() === "resume_failed";
}

export function useActorActions(groupId: string) {
  const {
    refreshActors,
    refreshGroups,
    loadGroup,
    clearStreamingEventsForActor,
  } = useGroupStore();
  const { setBusy, setActiveTab, showError } = useUIStore();
  const { openModal, setEditingActor } = useModalStore();
  const { setInboxActorId, setInboxMessages } = useInboxStore();
  const { setEditActorRuntime, setEditActorRunner, setEditActorCommand, setEditActorTitle, setEditActorCapabilityAutoloadText } =
    useFormStore();

  // Local state: terminal epoch is used to force a terminal re-mount.
  const [termEpochByActor, setTermEpochByActor] = useState<Record<string, number>>({});
  const reconcileTimersRef = useRef<Record<string, number[]>>({});
  const actorActionInFlightRef = useRef<Set<string>>(new Set());
  const actionEpochsRef = useRef(new Map<string, number>());
  const busyEpochRef = useRef(0);

  const beginBusy = useCallback((label: string) => {
    busyEpochRef.current += 1;
    const epoch = busyEpochRef.current;
    setBusy(label);
    return epoch;
  }, [setBusy]);

  const clearBusy = useCallback((label: string, epoch: number) => {
    if (busyEpochRef.current !== epoch) return;
    if (useUIStore.getState().busy !== label) return;
    setBusy("");
  }, [setBusy]);

  useEffect(() => {
    return () => {
      const timersByActor = reconcileTimersRef.current;
      for (const actorId of Object.keys(timersByActor)) {
        for (const timerId of timersByActor[actorId] || []) {
          window.clearTimeout(timerId);
        }
      }
      reconcileTimersRef.current = {};
    };
  }, []);

  const clearReconcileTimers = useCallback((targetGid: string, actorId: string) => {
    const gid = String(targetGid || "").trim();
    const aid = String(actorId || "").trim();
    const key = gid && aid ? `${gid}:${aid}` : "";
    if (!key) return;
    for (const timerId of reconcileTimersRef.current[key] || []) {
      window.clearTimeout(timerId);
    }
    delete reconcileTimersRef.current[key];
  }, []);

  const scheduleRuntimeReconcile = useCallback((
    targetGid: string,
    actorId: string,
    actionKey: string,
    actionEpoch: number,
  ) => {
    const gid = String(targetGid || "").trim();
    const aid = String(actorId || "").trim();
    const timerKey = gid && aid ? `${gid}:${aid}` : "";
    if (!timerKey) return;
    clearReconcileTimers(gid, aid);
    reconcileTimersRef.current[timerKey] = ACTOR_START_RECONCILE_DELAYS_MS.map((delayMs) =>
      window.setTimeout(() => {
        if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
        void Promise.allSettled([refreshActors(gid), refreshGroups()]);
      }, delayMs)
    );
  }, [clearReconcileTimers, refreshActors, refreshGroups]);

  // Start/stop actor
  const toggleActorEnabled = useCallback(
    async (actor: Actor) => {
      const targetGid = String(groupId || "").trim();
      if (!actor || !targetGid) return;
      const isRunning = actor.running ?? actor.enabled ?? false;
      const wantsStart = !isRunning;
      const actionKey = `actor-lifecycle:${targetGid}:${actor.id}`;
      if (!beginActorAction(actorActionInFlightRef, actionKey)) return;
      const actionEpoch = beginActionRequestEpoch(actionEpochsRef, actionKey);
      clearReconcileTimers(targetGid, actor.id);
      const busyLabel = `actor-${isRunning ? "stop" : "start"}:${actor.id}`;
      const busyEpoch = beginBusy(busyLabel);
      try {
        const resp = isRunning
          ? await api.stopActor(targetGid, actor.id)
          : await api.startActor(targetGid, actor.id);
        if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
        if (!resp.ok) {
          await Promise.all([refreshActors(targetGid), refreshGroups()]);
          if (isRunning || !latestActorHasResumeFailure(targetGid, actor.id)) {
            showError(`${resp.error.code}: ${resp.error.message}`);
          }
          return;
        }
        clearStreamingEventsForActor(actor.id, targetGid);
        await Promise.all([refreshActors(targetGid), refreshGroups()]);
        if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
        if (wantsStart) {
          scheduleRuntimeReconcile(targetGid, actor.id, actionKey, actionEpoch);
        }
      } finally {
        endActorAction(actorActionInFlightRef, actionKey);
        clearBusy(busyLabel, busyEpoch);
      }
    },
    [beginBusy, clearBusy, clearReconcileTimers, clearStreamingEventsForActor, groupId, refreshActors, refreshGroups, scheduleRuntimeReconcile, showError]
  );

  // Restart actor
  const relaunchActor = useCallback(
    async (actor: Actor) => {
      const targetGid = String(groupId || "").trim();
      if (!targetGid || !actor) return;
      const actionKey = `actor-lifecycle:${targetGid}:${actor.id}`;
      if (!beginActorAction(actorActionInFlightRef, actionKey)) return;
      const actionEpoch = beginActionRequestEpoch(actionEpochsRef, actionKey);
      clearReconcileTimers(targetGid, actor.id);
      const busyLabel = `actor-relaunch:${actor.id}`;
      const busyEpoch = beginBusy(busyLabel);
      try {
        const resp = await api.restartActor(targetGid, actor.id);
        if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
        if (!resp.ok) {
          await Promise.all([refreshActors(targetGid), refreshGroups()]);
          if (!latestActorHasResumeFailure(targetGid, actor.id)) {
            showError(`${resp.error.code}: ${resp.error.message}`);
          }
          return;
        } else {
          await Promise.all([refreshActors(targetGid), refreshGroups()]);
          if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
          scheduleRuntimeReconcile(targetGid, actor.id, actionKey, actionEpoch);
          setTermEpochByActor((prev) => ({
            ...prev,
            [`${targetGid}:${actor.id}`]: (prev[`${targetGid}:${actor.id}`] || 0) + 1,
          }));
        }
      } finally {
        endActorAction(actorActionInFlightRef, actionKey);
        clearBusy(busyLabel, busyEpoch);
      }
    },
    [beginBusy, clearBusy, clearReconcileTimers, groupId, refreshActors, refreshGroups, scheduleRuntimeReconcile, showError]
  );

  // Edit actor (initialize form state and open modal).
  const editActor = useCallback(
    (actor: Actor) => {
      if (!actor) return;
      // Initialize form state with actor's current values
      const runtime = String(actor.runtime || "").trim();
      setEditActorRuntime((runtime || "codex") as SupportedRuntime);
      setEditActorRunner(getEffectiveActorRunner(actor));
      setEditActorCommand(Array.isArray(actor.command) ? actor.command.join(" ") : "");
      setEditActorTitle(actor.title || "");
      setEditActorCapabilityAutoloadText(formatCapabilityIdInput(actor.capability_autoload));
      setEditingActor(actor);
    },
    [setEditingActor, setEditActorRuntime, setEditActorRunner, setEditActorCommand, setEditActorTitle, setEditActorCapabilityAutoloadText]
  );

  // Remove actor
  const removeActor = useCallback(
    async (actor: Actor, currentActiveTab: string) => {
      const targetGid = String(groupId || "").trim();
      if (!actor || !targetGid) return;
      if (!window.confirm(`Remove actor "${actor.title || actor.id}"?`)) return;
      const busyLabel = `actor-remove:${actor.id}`;
      const busyEpoch = beginBusy(busyLabel);
      try {
        const resp = await api.removeActor(targetGid, actor.id);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        clearStreamingEventsForActor(actor.id, targetGid);
        if (
          currentActiveTab === actor.id
          && String(useGroupStore.getState().selectedGroupId || "").trim() === targetGid
        ) {
          setActiveTab("chat");
        }
        await Promise.all([refreshActors(targetGid), refreshGroups()]);
        await loadGroup(targetGid);
      } finally {
        clearBusy(busyLabel, busyEpoch);
      }
    },
    [beginBusy, clearBusy, groupId, showError, refreshActors, refreshGroups, loadGroup, setActiveTab, clearStreamingEventsForActor]
  );

  // Open inbox modal
  const openActorInbox = useCallback(
    async (actor: Actor) => {
      const targetGid = String(groupId || "").trim();
      if (!actor || !targetGid) return;
      const busyLabel = `inbox:${actor.id}`;
      const busyEpoch = beginBusy(busyLabel);
      try {
        setInboxActorId(actor.id);
        setInboxMessages([]);
        openModal("inbox");
        const resp = await api.fetchInbox(targetGid, actor.id);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        if (String(useGroupStore.getState().selectedGroupId || "").trim() !== targetGid) return;
        setInboxMessages(resp.result.messages || []);
      } finally {
        clearBusy(busyLabel, busyEpoch);
      }
    },
    [beginBusy, clearBusy, groupId, showError, setInboxActorId, setInboxMessages, openModal]
  );

  // Get actor termEpoch
  const getTermEpoch = useCallback(
    (actorId: string) => termEpochByActor[`${groupId}:${actorId}`] || 0,
    [groupId, termEpochByActor]
  );

  return {
    termEpochByActor,
    getTermEpoch,
    toggleActorEnabled,
    relaunchActor,
    editActor,
    removeActor,
    openActorInbox,
  };
}
