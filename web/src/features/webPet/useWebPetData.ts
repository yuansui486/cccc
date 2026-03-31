import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useUIStore } from "../../stores";
import type { GroupContext, GroupDoc, LedgerEvent } from "../../types";
import { aggregateWebPetState } from "./aggregateWebPetState";
import { useWebPetNotifications } from "./useWebPetNotifications";
import type { PetPeerContext } from "./petPeerContext";
import { getPetReminderDraftText } from "./reminderText";
import type { PanelData, PetReminder } from "./types";

export function shouldSurfaceReminder(
  reminder: PetReminder,
): boolean {
  if (reminder.action.type === "restart_actor") {
    return !!reminder.action.groupId && !!reminder.action.actorId;
  }
  if (reminder.action.type === "task_proposal") {
    return !!reminder.action.groupId &&
      (!!reminder.action.text?.trim() || !!reminder.summary.trim());
  }
  if (reminder.action.type === "automation_proposal") {
    return !!reminder.action.groupId &&
      reminder.action.actions.length > 0 &&
      (!!reminder.action.summary?.trim() || !!reminder.action.title?.trim() || !!reminder.summary.trim());
  }
  return reminder.action.type === "draft_message" &&
    !!getPetReminderDraftText(reminder);
}

function localizeConnectionMessage(
  sseStatus: "connected" | "connecting" | "disconnected",
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): string {
  if (sseStatus === "connected") {
    return tr("connection.connected", "Connected");
  }
  if (sseStatus === "connecting") {
    return tr("connection.connecting", "Connecting…");
  }
  return tr("connection.disconnected", "Disconnected");
}

function localizeReminder(
  reminder: PetReminder,
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): PetReminder {
  const agentLabel =
    reminder.agent === "system"
      ? tr("systemAgent", "System")
      : reminder.agent;

  let summary = reminder.summary;
  if (!summary.trim()) {
    if (reminder.kind === "suggestion" && reminder.source.suggestionKind === "reply_required") {
      summary = tr(
        "reminderSummary.replyRequired",
        "{{actor}} prepared a reply draft you can review in chat.",
        { actor: agentLabel },
      );
    } else if (reminder.kind === "suggestion") {
      summary = tr(
        "reminderSummary.mention",
        "{{actor}} prepared a draft you can review in chat.",
        { actor: agentLabel },
      );
    }
  }

  return {
    ...reminder,
    summary,
  };
}

function localizePanelData(
  panelData: PanelData,
  sseStatus: "connected" | "connecting" | "disconnected",
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): PanelData {
  return {
    ...panelData,
    teamName:
      panelData.teamName.trim() ||
      tr("teamFallback", "Team"),
    connection: {
      ...panelData.connection,
      message: localizeConnectionMessage(sseStatus, tr),
    },
  };
}

export function useWebPetData(input: {
  groupId: string;
  groupDoc: GroupDoc | null;
  groupContext: GroupContext | null;
  events: LedgerEvent[];
  petContext: PetPeerContext;
}) {
  const { t } = useTranslation("webPet");
  const groupId = String(input.groupId || "").trim();
  const groupContext = input.groupContext;
  const groupDocTitle = input.groupDoc?.title ?? "";
  const groupState = input.groupDoc?.state ?? "";
  const events = input.events;
  const petContext = input.petContext;
  const sseStatus = useUIStore((state) => state.sseStatus);
  const {
    reminders,
    activeReminder,
    autoPeekReminder,
    unseenReminderCount,
    reaction,
    dismissReminder,
    markRemindersSeen,
  } =
    useWebPetNotifications({
      groupId,
      groupState,
      groupContext,
      events,
      decisions: petContext.decisions,
    });
  const tr = useCallback(
    (key: string, fallback: string, vars?: Record<string, unknown>) =>
      String(t(key, { defaultValue: fallback, ...(vars || {}) })),
    [t]
  );

  return useMemo(() => {
    const { catState, panelData: rawPanelData } = aggregateWebPetState({
      groupContext,
      events,
      sseStatus,
      groupState,
      teamName: groupDocTitle || groupId || "",
      groupId: groupId || "",
    });
    const localizedPanelData = localizePanelData(rawPanelData, sseStatus, tr);
    const localizedReminders = reminders.map((reminder) =>
      localizeReminder(reminder, tr),
    );
    const filteredReminders = localizedReminders.filter((reminder) =>
      shouldSurfaceReminder(reminder),
    );
    const localizedActiveReminder = activeReminder
      ? localizeReminder(activeReminder, tr)
      : null;
    const localizedAutoPeekReminder = autoPeekReminder
      ? localizeReminder(autoPeekReminder, tr)
      : null;
    const filteredActiveReminder =
      localizedActiveReminder && shouldSurfaceReminder(localizedActiveReminder)
        ? localizedActiveReminder
        : filteredReminders[0] || null;
    const filteredAutoPeekReminder =
      localizedAutoPeekReminder && shouldSurfaceReminder(localizedAutoPeekReminder)
        ? localizedAutoPeekReminder
        : null;

    // Smart hint: prioritize connection > active reminder > task progress > agent focus > team name
    let hint: string;
    if (!localizedPanelData.connection.connected) {
      hint = localizedPanelData.connection.message;
    } else if (filteredActiveReminder?.summary) {
      hint = filteredActiveReminder.summary;
    } else if (petContext.status === "loading") {
      hint = tr("hintPetContextLoading", "Syncing pet context…");
    } else if (petContext.status === "error") {
      hint = tr("hintPetContextUnavailable", "Pet context unavailable");
    } else if (
      localizedPanelData.taskProgress &&
      localizedPanelData.taskProgress.total > 0
    ) {
      const { done, total } = localizedPanelData.taskProgress;
      hint = tr(
        "hintTaskProgress",
        "{{done}}/{{total}} tasks done",
        { done, total },
      );
    } else {
      hint =
        localizedPanelData.agents.find((agent) => agent.focus.trim())?.focus ||
        petContext.snapshot.split("\n")[0]?.trim() ||
        localizedPanelData.teamName;
    }

    return {
      catState,
      panelData: localizedPanelData,
      petContext,
      hint,
      reminders: filteredReminders,
      activeReminder: filteredActiveReminder,
      autoPeekReminder: filteredAutoPeekReminder,
      unseenReminderCount,
      reaction,
      dismissReminder,
      markRemindersSeen,
    };
  }, [
    activeReminder,
    autoPeekReminder,
    dismissReminder,
    events,
    groupContext,
    groupDocTitle,
    groupState,
    reminders,
    reaction,
    groupId,
    sseStatus,
    tr,
    petContext,
    unseenReminderCount,
    markRemindersSeen,
  ]);
}
