// Group action helpers (start/stop/state).
import { useCallback } from "react";
import { useGroupStore, useUIStore } from "../stores";
import * as api from "../services/api";

export function useGroupActions() {
  const {
    selectedGroupId,
    groupDoc,
    setGroupDoc,
    refreshGroups,
    refreshActors,
  } = useGroupStore();

  const { setBusy, showError } = useUIStore();

  const syncSelectedGroupControlState = useCallback(async () => {
    const gid = String(useGroupStore.getState().selectedGroupId || "").trim();
    if (!gid) return;
    const resp = await api.fetchGroupControlState(gid, { noCache: true });
    if (!resp.ok) return;
    const currentDoc = useGroupStore.getState().groupDoc;
    if (!currentDoc || String(currentDoc.group_id || "").trim() !== gid) return;
    setGroupDoc({
      ...currentDoc,
      control_state: resp.result.control_state,
    });
  }, [setGroupDoc]);

  // Start group
  const handleStartGroup = useCallback(async () => {
    if (!selectedGroupId) return;
    setBusy("group-start");
    try {
      const resp = await api.startGroup(selectedGroupId);
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await syncSelectedGroupControlState();
      await refreshActors();
      await refreshGroups();
    } finally {
      setBusy("");
    }
  }, [selectedGroupId, setBusy, showError, refreshActors, refreshGroups, syncSelectedGroupControlState]);

  // Stop group
  const handleStopGroup = useCallback(async () => {
    if (!selectedGroupId) return;
    setBusy("group-stop");
    try {
      const resp = await api.stopGroup(selectedGroupId);
      if (!resp.ok) {
        showError(`${resp.error.code}: ${resp.error.message}`);
        return;
      }
      await syncSelectedGroupControlState();
      await refreshActors();
      await refreshGroups();
    } finally {
      setBusy("");
    }
  }, [selectedGroupId, setBusy, showError, refreshActors, refreshGroups, syncSelectedGroupControlState]);

  // Set group state
  const handleSetGroupState = useCallback(
    async (s: "active" | "idle" | "paused") => {
      if (!selectedGroupId) return;
      setBusy(s === "active" ? "group-activate" : s === "paused" ? "group-pause" : "group-idle");
      try {
        const resp = await api.setGroupState(selectedGroupId, s);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        await syncSelectedGroupControlState();
        // When resuming to active and no actors are running, also start
        // the group so processes get relaunched (not just the state flag).
        if (s === "active" && groupDoc && !groupDoc.running) {
          const startResp = await api.startGroup(selectedGroupId);
          if (!startResp.ok) {
            showError(`${startResp.error.code}: ${startResp.error.message}`);
          }
          await refreshActors();
          await syncSelectedGroupControlState();
        }
        await refreshGroups();
      } finally {
        setBusy("");
      }
    },
    [selectedGroupId, groupDoc, setBusy, showError, refreshGroups, refreshActors, syncSelectedGroupControlState]
  );

  return {
    handleStartGroup,
    handleStopGroup,
    handleSetGroupState,
  };
}
