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
      await refreshActors();
      await refreshGroups();
    } finally {
      setBusy("");
    }
  }, [selectedGroupId, setBusy, showError, refreshActors, refreshGroups]);

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
      await refreshActors();
      await refreshGroups();
    } finally {
      setBusy("");
    }
  }, [selectedGroupId, setBusy, showError, refreshActors, refreshGroups]);

  // Set group state
  const handleSetGroupState = useCallback(
    async (s: "active" | "idle" | "paused") => {
      if (!selectedGroupId) return;
      setBusy(s === "active" ? "group-activate" : s === "paused" ? "group-pause" : "group-idle");
      try {
        const resp = await api.setGroupState(selectedGroupId, s);
        if (!resp.ok) {
          showError(`${resp.error.code}: ${resp.error.message}`);
        } else {
          setGroupDoc(groupDoc ? { ...groupDoc, state: s } : null);
          await refreshGroups();
        }
      } finally {
        setBusy("");
      }
    },
    [selectedGroupId, groupDoc, setBusy, showError, setGroupDoc, refreshGroups]
  );

  return {
    handleStartGroup,
    handleStopGroup,
    handleSetGroupState,
  };
}
