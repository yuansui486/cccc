import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useGroupStore, useUIStore } from "../../stores";
import { getWebPetPosition, useWebPetStore } from "../../stores/useWebPetStore";
import {
  fetchActors,
  fetchAutomation,
  fetchContext,
  fetchGroup,
  fetchLedgerTail,
  fetchPetPeerContext,
  fetchSettings,
  manageAutomation,
  recordPetDecisionOutcome,
  requestPetPeerReview,
  restartActor,
} from "../../services/api";
import { PetPanel } from "./PetPanel";
import { PetReminderBubble } from "./PetReminderBubble";
import { WebPetBubble } from "./WebPetBubble";
import { diagnosePetManualReview } from "./reviewDiagnostics";
import { shouldSurfaceReminder, useWebPetData } from "./useWebPetData";
import { buildPetPeerContext, usePetPeerContext } from "./petPeerContext";
import { stagePetReminderDraft } from "./petSuggestionDraft";
import { getBackgroundRefreshDelayMs } from "./reviewTiming";
import { WEB_PET_BUBBLE_SIZE } from "./constants";
import { getLatestPetContextRefreshMarker } from "./petContextRefresh";
import type { PetReminder } from "./types";
import type { Actor, GroupContext, GroupDoc, GroupSettings, LedgerEvent } from "../../types";
import i18n from "../../i18n";

const lastKnownDesktopPetEnabledByGroup: Record<string, boolean> = {};
const BACKGROUND_REFRESH_TIMEOUT_MS = 10_000;
const MANUAL_PET_REVIEW_POLL_MS = 900;
const MANUAL_PET_REVIEW_MAX_ATTEMPTS = 8;
const EMPTY_EVENTS: LedgerEvent[] = [];

type RemotePetGroupState = {
  groupDoc: GroupDoc | null;
  actors: Actor[];
  groupContext: GroupContext | null;
  groupSettings: GroupSettings | null;
  events: LedgerEvent[];
};

function tPet(key: string, fallback: string, vars?: Record<string, unknown>): string {
  return String(i18n.t(`webPet:${key}`, { defaultValue: fallback, ...(vars || {}) }));
}

function handleReminderAction(
  reminder: PetReminder,
  onExecuted?: () => void,
) {
  const action = reminder.action;
  switch (action.type) {
    case "draft_message":
    case "task_proposal": {
      if (!stagePetReminderDraft(reminder)) return;
      void recordPetDecisionOutcome(action.groupId, {
        fingerprint: reminder.fingerprint,
        outcome: "executed",
        decisionId: reminder.id,
        actionType: action.type,
        sourceEventId: reminder.source.eventId,
      });
      onExecuted?.();
      useUIStore.getState().showNotice({
        message: tPet("notice.suggestionDrafted", "Draft added to composer"),
      });
      break;
    }
    case "restart_actor": {
      void restartActor(action.groupId, action.actorId).then((resp) => {
        if (!resp.ok) {
          useUIStore.getState().showError(`${resp.error.code}: ${resp.error.message}`);
          return;
        }
        void recordPetDecisionOutcome(action.groupId, {
          fingerprint: reminder.fingerprint,
          outcome: "executed",
          decisionId: reminder.id,
          actionType: action.type,
          sourceEventId: reminder.source.eventId,
        });
        onExecuted?.();
        void useGroupStore.getState().refreshActors(action.groupId, { includeUnread: false });
        void useGroupStore.getState().refreshGroups();
        useUIStore.getState().showNotice({
          message: tPet("notice.restartRequested", "Restart requested"),
        });
      }).catch((error) => {
        const message =
          error instanceof Error
            ? error.message
            : tPet("notice.actorRestartFailed", "Failed to restart actor");
        useUIStore.getState().showError(message);
      });
      break;
    }
    case "automation_proposal": {
      void fetchAutomation(action.groupId).then((automationResp) => {
        if (!automationResp.ok) {
          useUIStore.getState().showError(`${automationResp.error.code}: ${automationResp.error.message}`);
          return;
        }
        const expectedVersion = Number(automationResp.result?.version || 0) || undefined;
        return manageAutomation(action.groupId, action.actions, expectedVersion).then((resp) => {
          if (!resp.ok) {
            useUIStore.getState().showError(`${resp.error.code}: ${resp.error.message}`);
            return;
          }
          void recordPetDecisionOutcome(action.groupId, {
            fingerprint: reminder.fingerprint,
            outcome: "executed",
            decisionId: reminder.id,
            actionType: action.type,
            sourceEventId: reminder.source.eventId,
          });
          onExecuted?.();
          useUIStore.getState().showNotice({
            message: tPet("notice.automationProposalApplied", "Automation proposal applied"),
          });
        });
      }).catch((error) => {
        const message =
          error instanceof Error
            ? error.message
            : tPet("notice.automationProposalApplyFailed", "Failed to apply automation proposal");
        useUIStore.getState().showError(message);
      });
      break;
    }
  }
}

function buildEmptyRemoteState(): RemotePetGroupState {
  return {
    groupDoc: null,
    actors: [],
    groupContext: null,
    groupSettings: null,
    events: [],
  };
}

export function WebPet({
  groupId,
  stackIndex = 0,
}: {
  groupId: string;
  stackIndex?: number;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selectedGroupId = useGroupStore((state) => state.selectedGroupId);
  const selectedGroupDoc = useGroupStore((state) => state.groupDoc);
  const selectedGroupSettings = useGroupStore((state) => state.groupSettings);
  const selectedGroupContext = useGroupStore((state) => state.groupContext);
  const selectedEvents = useGroupStore((state) =>
    state.selectedGroupId === groupId ? state.chatByGroup[groupId]?.events || state.events : EMPTY_EVENTS,
  );
  const positions = useWebPetStore((state) => state.positions);
  const position = getWebPetPosition(groupId, positions, stackIndex);
  const [isPanelOpen, setIsPanelOpen] = useState(false);
  const [selectedReminderFingerprint, setSelectedReminderFingerprint] = useState("");
  const [reviewInFlight, setReviewInFlight] = useState(false);
  const [petContextRefreshToken, setPetContextRefreshToken] = useState(0);
  const [remoteState, setRemoteState] = useState<RemotePetGroupState>(() => buildEmptyRemoteState());
  const remoteRefreshEpochRef = useRef(0);
  const remoteRefreshInFlightRef = useRef(false);
  const remoteRefreshFailureCountRef = useRef(0);
  const remoteRefreshAbortRef = useRef<AbortController | null>(null);
  const remoteRefreshTimerRef = useRef<number | null>(null);
  const reviewSessionRef = useRef(0);
  const petContextRefreshGroupIdRef = useRef("");
  const latestPetContextRefreshMarkerRef = useRef("");

  const isSelectedGroup = String(selectedGroupId || "").trim() === String(groupId || "").trim();
  const groupDoc = isSelectedGroup ? selectedGroupDoc : remoteState.groupDoc;
  const groupSettings = isSelectedGroup ? selectedGroupSettings : remoteState.groupSettings;
  const groupContext = isSelectedGroup ? selectedGroupContext : remoteState.groupContext;
  const events = isSelectedGroup ? selectedEvents : remoteState.events;
  const petContext = usePetPeerContext({ groupId, refreshToken: petContextRefreshToken });
  const petContextRefreshMarker = useMemo(
    () => getLatestPetContextRefreshMarker(events),
    [events],
  );
  const {
    catState,
    hint,
    reminders,
    activeReminder,
    autoPeekReminder,
    unseenReminderCount,
    dismissReminder,
    markRemindersSeen,
    reaction,
  } =
    useWebPetData({
      groupId,
      groupDoc,
      groupContext,
      events,
      petContext,
    });
  const selectedReminder = reminders.find(
    (reminder) => reminder.fingerprint === selectedReminderFingerprint,
  ) || activeReminder || null;
  const handleReminderActionWithDismiss = useCallback(
    (reminder: PetReminder) => {
      handleReminderAction(reminder, () => {
        dismissReminder(reminder.fingerprint, { outcome: null });
      });
    },
    [dismissReminder],
  );
  const openPanel = useCallback(() => {
    setIsPanelOpen(true);
    markRemindersSeen();
    setSelectedReminderFingerprint((current) => current || reminders[0]?.fingerprint || "");
  }, [markRemindersSeen, reminders]);

  const closePanel = useCallback(() => {
    setIsPanelOpen(false);
  }, []);

  const handleBubblePress = useCallback(() => {
    if (isPanelOpen) {
      closePanel();
      return;
    }
    openPanel();
  }, [closePanel, isPanelOpen, openPanel]);

  const handleReviewNow = useCallback(() => {
    if (reviewInFlight) return;
    setReviewInFlight(true);
    reviewSessionRef.current += 1;
    const sessionId = reviewSessionRef.current;

    void (async () => {
      const reviewResp = await requestPetPeerReview(groupId);
      if (!reviewResp.ok) {
        if (reviewSessionRef.current === sessionId) {
          setReviewInFlight(false);
        }
        useUIStore.getState().showError(`${reviewResp.error.code}: ${reviewResp.error.message}`);
        return;
      }

      let reminderReady = false;
      for (let attempt = 0; attempt < MANUAL_PET_REVIEW_MAX_ATTEMPTS; attempt += 1) {
        if (reviewSessionRef.current !== sessionId) {
          return;
        }
        const contextResp = await fetchPetPeerContext(groupId, { fresh: true });
        if (reviewSessionRef.current !== sessionId) {
          return;
        }
        if (contextResp.ok) {
          const refreshedContext = buildPetPeerContext(contextResp.result, { status: "loaded" });
          const refreshedReminder =
            refreshedContext.decisions.find((decision) => shouldSurfaceReminder(decision)) || null;
          if (refreshedReminder) {
            reminderReady = true;
            setPetContextRefreshToken((current) => current + 1);
            break;
          }
        }
        if (attempt < MANUAL_PET_REVIEW_MAX_ATTEMPTS - 1) {
          await new Promise((resolve) => window.setTimeout(resolve, MANUAL_PET_REVIEW_POLL_MS));
        }
      }

      if (reviewSessionRef.current !== sessionId) {
        return;
      }

      if (reminderReady) {
        setReviewInFlight(false);
        return;
      }

      const diagnosis = await diagnosePetManualReview(groupId);
      if (reviewSessionRef.current !== sessionId) {
        return;
      }

      if (diagnosis.kind === "runtime_unavailable") {
        setReviewInFlight(false);
        useUIStore.getState().showError(
          tPet("notice.reviewPetUnavailable", "Pet runtime is unavailable"),
        );
        return;
      }

      if (diagnosis.kind === "runtime_not_running") {
        setReviewInFlight(false);
        useUIStore.getState().showError(
          tPet("notice.reviewPetNotRunning", "Pet runtime is not running"),
        );
        return;
      }

      if (diagnosis.kind === "runtime_auth_expired") {
        setReviewInFlight(false);
        useUIStore.getState().showError(
          tPet("notice.reviewPetAuthExpired", "Pet runtime authentication expired. Re-login and retry."),
        );
        return;
      }

      setReviewInFlight(false);
      useUIStore.getState().showNotice({
        message: tPet("notice.reviewNoReminders", "No current reminders"),
      });
    })();
  }, [groupId, reviewInFlight]);

  useEffect(() => {
    const gid = String(groupId || "").trim();
    if (!gid || !groupSettings) return;
    lastKnownDesktopPetEnabledByGroup[gid] = Boolean(groupSettings.desktop_pet_enabled);
  }, [groupId, groupSettings]);

  useEffect(() => {
    const gid = String(groupId || "").trim();
    if (!gid || isSelectedGroup || groupSettings !== null) return;
    const epoch = remoteRefreshEpochRef.current + 1;
    remoteRefreshEpochRef.current = epoch;
    let cancelled = false;
    void fetchSettings(gid).then((resp) => {
      if (cancelled || remoteRefreshEpochRef.current !== epoch || !resp.ok || !resp.result.settings) return;
      setRemoteState((state) => ({
        ...state,
        groupSettings: resp.result.settings,
      }));
    }).catch(() => {
      // Keep the pet on the last-known state when the background settings read fails.
    });
    return () => {
      cancelled = true;
    };
  }, [groupId, groupSettings, isSelectedGroup]);

  useEffect(() => {
    const gid = String(groupId || "").trim();
    if (!gid || isSelectedGroup) return;
    if (!groupSettings?.desktop_pet_enabled) return;

    let cancelled = false;
    const clearScheduledRefresh = () => {
      if (remoteRefreshTimerRef.current !== null) {
        window.clearTimeout(remoteRefreshTimerRef.current);
        remoteRefreshTimerRef.current = null;
      }
    };
    const scheduleRefresh = (delayMs: number) => {
      clearScheduledRefresh();
      if (cancelled) return;
      remoteRefreshTimerRef.current = window.setTimeout(() => {
        remoteRefreshTimerRef.current = null;
        void refresh();
      }, delayMs);
    };

    const refresh = async () => {
      if (remoteRefreshInFlightRef.current) return;
      const epoch = remoteRefreshEpochRef.current + 1;
      remoteRefreshEpochRef.current = epoch;
      remoteRefreshInFlightRef.current = true;
      const controller = new AbortController();
      remoteRefreshAbortRef.current?.abort();
      remoteRefreshAbortRef.current = controller;
      const timeout = window.setTimeout(() => {
        controller.abort();
      }, BACKGROUND_REFRESH_TIMEOUT_MS);
      try {
        const [groupResp, actorsResp, contextResp, ledgerResp, settingsResp] =
          await Promise.all([
            fetchGroup(gid, { noCache: true, signal: controller.signal }),
            fetchActors(gid, false, { noCache: true, signal: controller.signal }),
            fetchContext(gid, { detail: "summary", noCache: true, signal: controller.signal }),
            fetchLedgerTail(gid, 120, { noCache: true, signal: controller.signal }),
            fetchSettings(gid, { noCache: true, signal: controller.signal }),
          ]);
        if (cancelled || controller.signal.aborted || remoteRefreshEpochRef.current !== epoch) return;

        const hadFailure = [groupResp, actorsResp, contextResp, ledgerResp, settingsResp].some((resp) => !resp.ok);
        remoteRefreshFailureCountRef.current = hadFailure
          ? remoteRefreshFailureCountRef.current + 1
          : 0;

        setRemoteState({
          groupDoc: groupResp.ok ? groupResp.result.group : null,
          actors: actorsResp.ok ? actorsResp.result.actors || [] : [],
          groupContext: contextResp.ok ? contextResp.result : null,
          groupSettings: settingsResp.ok ? settingsResp.result.settings || null : groupSettings,
          events: ledgerResp.ok ? ledgerResp.result.events || [] : [],
        });
      } finally {
        window.clearTimeout(timeout);
        if (remoteRefreshAbortRef.current === controller) {
          remoteRefreshAbortRef.current = null;
        }
        remoteRefreshInFlightRef.current = false;
        if (!cancelled) {
          scheduleRefresh(getBackgroundRefreshDelayMs(remoteRefreshFailureCountRef.current));
        }
      }
    };

    void refresh();
    return () => {
      cancelled = true;
      clearScheduledRefresh();
      remoteRefreshAbortRef.current?.abort();
      remoteRefreshAbortRef.current = null;
      remoteRefreshInFlightRef.current = false;
    };
  }, [groupId, groupSettings, isSelectedGroup]);

  useEffect(() => {
    if (!isPanelOpen) return;
    markRemindersSeen();
  }, [isPanelOpen, markRemindersSeen, reminders]);

  useEffect(() => {
    const gid = String(groupId || "").trim();
    if (!gid) {
      petContextRefreshGroupIdRef.current = "";
      latestPetContextRefreshMarkerRef.current = "";
      return;
    }
    if (petContextRefreshGroupIdRef.current !== gid) {
      petContextRefreshGroupIdRef.current = gid;
      latestPetContextRefreshMarkerRef.current = petContextRefreshMarker;
      return;
    }
    if (!petContextRefreshMarker || petContextRefreshMarker === latestPetContextRefreshMarkerRef.current) {
      return;
    }
    latestPetContextRefreshMarkerRef.current = petContextRefreshMarker;
    setPetContextRefreshToken((current) => current + 1);
  }, [groupId, petContextRefreshMarker]);

  useEffect(() => {
    if (!isPanelOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (rootRef.current?.contains(target)) return;
      closePanel();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closePanel();
    };

    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown, true);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [closePanel, isPanelOpen]);

  useEffect(() => {
    const nextFingerprint = reminders[0]?.fingerprint || "";
    if (!selectedReminderFingerprint) {
      if (nextFingerprint) {
        setSelectedReminderFingerprint(nextFingerprint);
      }
      return;
    }
    const stillExists = reminders.some(
      (reminder) => reminder.fingerprint === selectedReminderFingerprint,
    );
    if (!stillExists) {
      setSelectedReminderFingerprint(nextFingerprint);
    }
  }, [reminders, selectedReminderFingerprint]);

  useEffect(() => {
    setIsPanelOpen(false);
    setSelectedReminderFingerprint("");
    setReviewInFlight(false);
    setPetContextRefreshToken(0);
    petContextRefreshGroupIdRef.current = "";
    latestPetContextRefreshMarkerRef.current = "";
    reviewSessionRef.current += 1;
  }, [groupId]);

  const desktopPetEnabled = (() => {
    const gid = String(groupId || "").trim();
    if (!gid) return false;
    if (groupSettings) {
      return Boolean(groupSettings.desktop_pet_enabled);
    }
    return Boolean(lastKnownDesktopPetEnabledByGroup[gid]);
  })();

  if (!groupId || !desktopPetEnabled) {
    return null;
  }

  return (
    <div
      ref={rootRef}
      className="pointer-events-none fixed z-[1100] overflow-visible"
      style={{
        left: position.x,
        top: position.y,
        width: WEB_PET_BUBBLE_SIZE,
        height: WEB_PET_BUBBLE_SIZE,
      }}
    >
      {!isPanelOpen && autoPeekReminder ? (
        <PetReminderBubble
          reminder={autoPeekReminder}
          additionalCount={Math.max(0, reminders.length - 1)}
          onDismiss={dismissReminder}
          onAction={handleReminderActionWithDismiss}
          onOpenPanel={openPanel}
        />
      ) : null}
      {isPanelOpen ? (
        <PetPanel
          reminder={selectedReminder}
          reminders={reminders}
          reviewInFlight={reviewInFlight}
          onDismiss={dismissReminder}
          onAction={handleReminderActionWithDismiss}
          onReviewNow={handleReviewNow}
          onSelectReminder={setSelectedReminderFingerprint}
        />
      ) : null}
      {reminders.length > 1 ? (
        <div className="pointer-events-none absolute -right-1 top-1 z-[1111] flex h-5 min-w-[20px] items-center justify-center rounded-full bg-[var(--color-accent)] px-1.5 text-[10px] font-semibold text-white shadow-lg">
          {unseenReminderCount > 0 ? unseenReminderCount : reminders.length}
        </div>
      ) : null}
      <WebPetBubble
        groupId={groupId}
        stackIndex={stackIndex}
        state={catState}
        hint={hint}
        reaction={reaction}
        onPress={handleBubblePress}
      />
    </div>
  );
}
