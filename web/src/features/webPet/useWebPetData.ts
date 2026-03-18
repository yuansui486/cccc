import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { selectChatBucketState, useGroupStore, useUIStore } from "../../stores";
import { aggregateWebPetState } from "./aggregateWebPetState";
import { useWebPetNotifications } from "./useWebPetNotifications";
import type { PanelData, PetReminder } from "./types";

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
  if (reminder.kind === "stalled_peer") {
    summary = tr(
      "reminderSummary.stalledPeer",
      "{{actor}} has been idle for a while on {{taskId}}.",
      {
        actor: reminder.source.actorId || agentLabel,
        taskId: reminder.source.taskId || tr("taskFallback", "this task"),
      },
    );
  } else if (!summary.trim()) {
    if (reminder.kind === "mention") {
      summary = tr(
        "reminderSummary.mention",
        "{{actor}} mentioned you.",
        { actor: agentLabel },
      );
    } else if (reminder.kind === "reply_required") {
      summary = tr(
        "reminderSummary.replyRequired",
        "{{actor}} is waiting for your reply.",
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
    actionItems: panelData.actionItems.map((item) => ({
      ...item,
      agent:
        item.agent === "system"
          ? tr("systemAgent", "System")
          : item.agent,
    })),
    connection: {
      ...panelData.connection,
      message: localizeConnectionMessage(sseStatus, tr),
    },
  };
}

export function useWebPetData() {
  const { t } = useTranslation("webPet");
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const groupContext = useGroupStore((state) => state.groupContext);
  const groupDocTitle = useGroupStore((state) => state.groupDoc?.title ?? "");
  const groupState = useGroupStore((state) => state.groupDoc?.state ?? "");
  const events = useGroupStore((state) => selectChatBucketState(state, state.selectedGroupId).events);
  const sseStatus = useUIStore((state) => state.sseStatus);
  const { reminders, activeReminder, reaction, dismissReminder } =
    useWebPetNotifications();
  const tr = (key: string, fallback: string, vars?: Record<string, unknown>) =>
    String(t(key, { defaultValue: fallback, ...(vars || {}) }));

  return useMemo(() => {
    const { catState, panelData: rawPanelData } = aggregateWebPetState({
      groupContext,
      events,
      sseStatus,
      groupState,
      teamName: groupDocTitle || selectedGroupId || "",
      groupId: selectedGroupId || "",
    });
    const localizedPanelData = localizePanelData(rawPanelData, sseStatus, tr);
    const localizedReminders = reminders.map((reminder) =>
      localizeReminder(reminder, tr),
    );
    const localizedActiveReminder = activeReminder
      ? localizeReminder(activeReminder, tr)
      : null;

    // Smart hint: prioritize connection > active reminder > task progress > agent focus > team name
    let hint: string;
    if (!localizedPanelData.connection.connected) {
      hint = localizedPanelData.connection.message;
    } else if (localizedActiveReminder?.summary) {
      hint = localizedActiveReminder.summary;
    } else if (
      localizedPanelData.taskProgress &&
      localizedPanelData.taskProgress.total > 0
    ) {
      const { done, total } = localizedPanelData.taskProgress;
      const needsYouCount = localizedPanelData.actionItems.length;
      hint = needsYouCount > 0
        ? tr(
            "hintTaskWithAction",
            "{{done}}/{{total}} done, {{count}} need you",
            { done, total, count: needsYouCount },
          )
        : tr(
            "hintTaskProgress",
            "{{done}}/{{total}} tasks done",
            { done, total },
          );
    } else {
      hint =
        localizedPanelData.actionItems[0]?.summary ||
        localizedPanelData.agents.find((agent) => agent.focus.trim())?.focus ||
        localizedPanelData.teamName;
    }

    return {
      catState,
      panelData: localizedPanelData,
      hint,
      reminders: localizedReminders,
      activeReminder: localizedActiveReminder,
      reaction,
      dismissReminder,
    };
  }, [
    t,
    activeReminder,
    dismissReminder,
    events,
    groupContext,
    groupDocTitle,
    groupState,
    reminders,
    reaction,
    selectedGroupId,
    sseStatus,
    tr,
  ]);
}
