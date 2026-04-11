// Actor action helpers extracted from ActorTab-related logic.
import { useCallback, useEffect, useRef, useState } from "react";
import { useGroupStore, useUIStore, useModalStore, useInboxStore, useFormStore } from "../stores";
import * as api from "../services/api";
import type { Actor, SupportedRuntime } from "../types";
import { formatCapabilityIdInput } from "../utils/capabilityAutoload";
import { getEffectiveActorRunner } from "../utils/headlessRuntimeSupport";

const ACTOR_START_RECONCILE_DELAYS_MS = [1200, 3500] as const;

export function useActorActions(groupId: string) {
  const {
    refreshActors,
    refreshGroups,
    loadGroup,
    clearStreamingEventsForActor,
    updateActorActivity,
    updateGroupRuntimeState,
  } = useGroupStore();
  const { setBusy, setActiveTab, showError } = useUIStore();
  const { openModal, setEditingActor } = useModalStore();
  const { setInboxActorId, setInboxMessages } = useInboxStore();
  const { setEditActorRuntime, setEditActorRunner, setEditActorCommand, setEditActorTitle, setEditActorCapabilityAutoloadText } =
    useFormStore();

  // Local state: terminal epoch is used to force a terminal re-mount.
  const [termEpochByActor, setTermEpochByActor] = useState<Record<string, number>>({});
  const reconcileTimersRef = useRef<Record<string, number[]>>({});

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

  const clearReconcileTimers = useCallback((actorId: string) => {
    const key = String(actorId || "").trim();
    if (!key) return;
    for (const timerId of reconcileTimersRef.current[key] || []) {
      window.clearTimeout(timerId);
    }
    delete reconcileTimersRef.current[key];
  }, []);

  const scheduleRuntimeReconcile = useCallback((actorId: string) => {
    const key = String(actorId || "").trim();
    if (!groupId || !key) return;
    clearReconcileTimers(key);
    reconcileTimersRef.current[key] = ACTOR_START_RECONCILE_DELAYS_MS.map((delayMs) =>
      window.setTimeout(() => {
        void Promise.allSettled([refreshActors(groupId), refreshGroups()]);
      }, delayMs)
    );
  }, [clearReconcileTimers, groupId, refreshActors, refreshGroups]);

  const optimisticMarkRunning = useCallback((actorId: string, reason: string) => {
    const key = String(actorId || "").trim();
    if (!groupId || !key) return;
    const updatedAt = new Date().toISOString();
    updateActorActivity([{
      id: key,
      running: true,
      idle_seconds: null,
      effective_working_state: "waiting",
      effective_working_reason: reason,
      effective_working_updated_at: updatedAt,
      effective_active_task_id: null,
    }]);
    updateGroupRuntimeState(groupId, {
      lifecycle_state: "active",
      runtime_running: true,
    });
  }, [groupId, updateActorActivity, updateGroupRuntimeState]);

  // Start/stop actor
  const toggleActorEnabled = useCallback(
    async (actor: Actor) => {
      if (!actor || !groupId) return;
      const isRunning = actor.running ?? actor.enabled ?? false;
      const wantsStart = !isRunning;
      setBusy(`actor-${isRunning ? "stop" : "start"}:${actor.id}`);
      try {
        const resp = isRunning
          ? await api.stopActor(groupId, actor.id)
          : await api.startActor(groupId, actor.id);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        await Promise.all([refreshActors(), refreshGroups()]);
        if (wantsStart) {
          optimisticMarkRunning(actor.id, "actor_start_requested");
          scheduleRuntimeReconcile(actor.id);
        } else {
          clearReconcileTimers(actor.id);
        }
      } finally {
        setBusy("");
      }
    },
    [clearReconcileTimers, groupId, optimisticMarkRunning, refreshActors, refreshGroups, scheduleRuntimeReconcile, setBusy, showError]
  );

  // Restart actor
  const relaunchActor = useCallback(
    async (actor: Actor) => {
      if (!groupId || !actor) return;
      setBusy(`actor-relaunch:${actor.id}`);
      try {
        const resp = await api.restartActor(groupId, actor.id);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        await Promise.all([refreshActors(), refreshGroups()]);
        optimisticMarkRunning(actor.id, "actor_restart_requested");
        scheduleRuntimeReconcile(actor.id);
        setTermEpochByActor((prev) => ({
          ...prev,
          [actor.id]: (prev[actor.id] || 0) + 1,
        }));
      } finally {
        setBusy("");
      }
    },
    [groupId, optimisticMarkRunning, refreshActors, refreshGroups, scheduleRuntimeReconcile, setBusy, showError]
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
      if (!actor || !groupId) return;
      if (!window.confirm(`Remove actor "${actor.title || actor.id}"?`)) return;
      setBusy(`actor-remove:${actor.id}`);
      try {
        const resp = await api.removeActor(groupId, actor.id);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        clearStreamingEventsForActor(actor.id, groupId);
        if (currentActiveTab === actor.id) {
          setActiveTab("chat");
        }
        await Promise.all([refreshActors(), refreshGroups()]);
        await loadGroup(groupId);
      } finally {
        setBusy("");
      }
    },
    [groupId, setBusy, showError, refreshActors, refreshGroups, loadGroup, setActiveTab, clearStreamingEventsForActor]
  );

  // Open inbox modal
  const openActorInbox = useCallback(
    async (actor: Actor) => {
      if (!actor || !groupId) return;
      setBusy(`inbox:${actor.id}`);
      try {
        setInboxActorId(actor.id);
        setInboxMessages([]);
        openModal("inbox");
        const resp = await api.fetchInbox(groupId, actor.id);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        setInboxMessages(resp.result.messages || []);
      } finally {
        setBusy("");
      }
    },
    [groupId, setBusy, showError, setInboxActorId, setInboxMessages, openModal]
  );

  // Get actor termEpoch
  const getTermEpoch = useCallback(
    (actorId: string) => termEpochByActor[actorId] || 0,
    [termEpochByActor]
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
