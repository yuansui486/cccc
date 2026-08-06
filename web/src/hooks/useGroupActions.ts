// Group action helpers (start/stop/state).
import { useCallback, useRef } from "react";
import { useGroupStore, useUIStore } from "../stores";
import * as api from "../services/api";
import {
  beginActionRequestEpoch,
  isLatestActionRequestEpoch,
} from "./actionRequestEpoch";
import { shouldStartGroupAfterActivation } from "./groupActionDecision";

export function useGroupActions() {
  const {
    setGroupDoc,
    refreshGroups,
    refreshActors,
  } = useGroupStore();

  const { setBusy, showError } = useUIStore();
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

  const syncGroupControlState = useCallback(async (groupId: string, actionKey: string, actionEpoch: number) => {
    const gid = String(groupId || "").trim();
    if (!gid || !isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return null;
    const resp = await api.fetchGroupControlState(gid, { noCache: true });
    if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return null;
    if (!resp.ok) return null;
    const currentDoc = useGroupStore.getState().groupDoc;
    if (currentDoc && String(currentDoc.group_id || "").trim() === gid) {
      setGroupDoc({
        ...currentDoc,
        control_state: resp.result.control_state,
      });
    }
    return resp.result.control_state;
  }, [setGroupDoc]);

  // Start group
  const handleStartGroup = useCallback(async () => {
    const targetGid = String(useGroupStore.getState().selectedGroupId || "").trim();
    if (!targetGid) return;
    const actionKey = `group:${targetGid}`;
    const actionEpoch = beginActionRequestEpoch(actionEpochsRef, actionKey);
    const busyLabel = "group-start";
    const busyEpoch = beginBusy(busyLabel);
    try {
      const resp = await api.startGroup(targetGid);
      if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await syncGroupControlState(targetGid, actionKey, actionEpoch);
      if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
      await refreshActors(targetGid);
      await refreshGroups();
    } finally {
      clearBusy(busyLabel, busyEpoch);
    }
  }, [beginBusy, clearBusy, refreshActors, refreshGroups, showError, syncGroupControlState]);

  // Stop group
  const handleStopGroup = useCallback(async () => {
    const targetGid = String(useGroupStore.getState().selectedGroupId || "").trim();
    if (!targetGid) return;
    const actionKey = `group:${targetGid}`;
    const actionEpoch = beginActionRequestEpoch(actionEpochsRef, actionKey);
    const busyLabel = "group-stop";
    const busyEpoch = beginBusy(busyLabel);
    try {
      const resp = await api.stopGroup(targetGid);
      if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await syncGroupControlState(targetGid, actionKey, actionEpoch);
      if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
      await refreshActors(targetGid);
      await refreshGroups();
    } finally {
      clearBusy(busyLabel, busyEpoch);
    }
  }, [beginBusy, clearBusy, refreshActors, refreshGroups, showError, syncGroupControlState]);

  // Set group state
  const handleSetGroupState = useCallback(
    async (s: "active" | "idle" | "paused") => {
      const targetGid = String(useGroupStore.getState().selectedGroupId || "").trim();
      if (!targetGid) return;
      const actionKey = `group:${targetGid}`;
      const actionEpoch = beginActionRequestEpoch(actionEpochsRef, actionKey);
      const busyLabel = s === "active" ? "group-activate" : s === "paused" ? "group-pause" : "group-idle";
      const busyEpoch = beginBusy(busyLabel);
      try {
        const resp = await api.setGroupState(targetGid, s);
        if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        const controlState = await syncGroupControlState(targetGid, actionKey, actionEpoch);
        if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
        // When resuming to active and no actors are running, also start
        // the group so processes get relaunched (not just the state flag).
        if (s === "active" && shouldStartGroupAfterActivation(controlState)) {
          const startResp = await api.startGroup(targetGid);
          if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
          if (!startResp.ok) {
            showError(`${startResp.error.code}: ${startResp.error.message}`);
          }
          await refreshActors(targetGid);
          await syncGroupControlState(targetGid, actionKey, actionEpoch);
        }
        if (!isLatestActionRequestEpoch(actionEpochsRef, actionKey, actionEpoch)) return;
        await refreshGroups();
      } finally {
        clearBusy(busyLabel, busyEpoch);
      }
    },
    [beginBusy, clearBusy, refreshActors, refreshGroups, showError, syncGroupControlState]
  );

  return {
    handleStartGroup,
    handleStopGroup,
    handleSetGroupState,
  };
}
