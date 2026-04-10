import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useTerminalSignalsStore, useUIStore } from "../../stores";
import type { GroupContext, GroupDoc, LedgerEvent } from "../../types";
import { aggregateWebPetState } from "./aggregateWebPetState";
import { buildPetVoiceHint, buildPetVoiceReminderSummary } from "./petVoice";
import { useWebPetNotifications } from "./useWebPetNotifications";
import type { PetPeerContext } from "./petPeerContext";
import { getPetReminderActionPreviewText, getPetReminderDraftText } from "./reminderText";
import type { AgentSummary, PanelData, PetReminder } from "./types";

export function shouldSurfaceReminder(
  reminder: PetReminder,
): boolean {
  if (reminder.action.type === "restart_actor") {
    return !!reminder.action.groupId && !!reminder.action.actorId;
  }
  if (reminder.action.type === "task_proposal") {
    return !!reminder.action.groupId &&
      !!getPetReminderActionPreviewText(reminder);
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
  petContext: PetPeerContext,
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string,
): PetReminder {
  const agentLabel =
    reminder.agent === "system"
      ? tr("systemAgent", "System")
      : reminder.agent;

  let summary = reminder.summary;
  if (!summary.trim()) {
    if (reminder.kind === "suggestion" && reminder.source.suggestionKind === "reply_required") {
      summary = buildPetVoiceReminderSummary({
        companion: petContext.companion,
        reminder,
        actorLabel: agentLabel,
        tr,
      });
    } else if (reminder.kind === "suggestion") {
      summary = buildPetVoiceReminderSummary({
        companion: petContext.companion,
        reminder,
        actorLabel: agentLabel,
        tr,
      });
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

export function getPreferredAgentTaskHint(agents: AgentSummary[]): string {
  for (const agent of agents) {
    const activeTaskId = String(agent.activeTaskId || "").trim();
    const focus = String(agent.focus || "").trim();
    if (activeTaskId && focus) return `${activeTaskId} | ${focus}`;
    if (activeTaskId) return activeTaskId;
    if (focus) return focus;
  }
  return "";
}

export function getAgentTaskSummaries(agents: AgentSummary[], limit: number = 3): string[] {
  const summaries: string[] = [];
  const maxItems = Math.max(0, Number(limit || 0));
  if (maxItems <= 0) return summaries;
  for (const agent of agents) {
    const activeTaskId = String(agent.activeTaskId || "").trim();
    const focus = String(agent.focus || "").trim();
    if (!activeTaskId && !focus) continue;
    summaries.push(activeTaskId && focus ? `${activeTaskId} | ${focus}` : activeTaskId || focus);
    if (summaries.length >= maxItems) break;
  }
  return summaries;
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
  const terminalSignals = useTerminalSignalsStore((state) => state.signals);
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
      petContextStatus: petContext.status,
      petContext: {
        persona: petContext.persona,
        help: petContext.help,
        prompt: petContext.prompt,
      },
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
      terminalSignals,
    });
    const localizedPanelData = localizePanelData(rawPanelData, sseStatus, tr);
    const localizedReminders = reminders.map((reminder) =>
      localizeReminder(reminder, petContext, tr),
    );
    const filteredReminders = localizedReminders.filter((reminder) =>
      shouldSurfaceReminder(reminder),
    );
    const localizedActiveReminder = activeReminder
      ? localizeReminder(activeReminder, petContext, tr)
      : null;
    const localizedAutoPeekReminder = autoPeekReminder
      ? localizeReminder(autoPeekReminder, petContext, tr)
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
      hint = buildPetVoiceHint({
        companion: petContext.companion,
        summary: filteredActiveReminder.summary,
        tr,
      });
    } else if (petContext.status === "loading") {
      hint = buildPetVoiceHint({
        companion: petContext.companion,
        status: "loading",
        tr,
      });
    } else if (petContext.status === "error") {
      hint = buildPetVoiceHint({
        companion: petContext.companion,
        status: "error",
        tr,
      });
    } else if (
      localizedPanelData.taskProgress &&
      localizedPanelData.taskProgress.total > 0
    ) {
      const { done, total } = localizedPanelData.taskProgress;
      hint = buildPetVoiceHint({
        companion: petContext.companion,
        status: "progress",
        tr,
        done,
        total,
      });
    } else {
      hint = buildPetVoiceHint({
        companion: petContext.companion,
        status: "idle",
        tr,
        fallback:
          getPreferredAgentTaskHint(localizedPanelData.agents) ||
          petContext.snapshot.split("\n")[0]?.trim() ||
          localizedPanelData.teamName,
      });
    }

    return {
      catState,
      panelData: localizedPanelData,
      taskSummaries: getAgentTaskSummaries(localizedPanelData.agents),
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
    terminalSignals,
    tr,
    petContext,
    unseenReminderCount,
    markRemindersSeen,
  ]);
}
