import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LedgerEvent, StreamingActivity } from "../../types";
import { classNames } from "../../utils/classNames";
import { useGroupStore } from "../../stores";
import {
  normalizeStreamingActivities,
  formatStreamingActivityKind,
  getStructuredStreamingActivityLabel,
  getEffectiveStreamingActivities,
  deriveStreamingRenderPhase,
  getStreamingPendingDelayMs,
} from "./helpers";

const STREAMING_STATUS_EXIT_MS = 140;
const STREAMING_ACTIVITY_DISPLAY_LIMIT = 5;
const EMPTY_STREAMING_ACTIVITIES: StreamingActivity[] = [];
const EMPTY_STREAMING_EVENTS: LedgerEvent[] = [];

function PlainMessageText({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  return (
    <div
      className={classNames(
        "break-words whitespace-pre-wrap [overflow-wrap:anywhere]",
        className,
      )}
    >
      {text}
    </div>
  );
}

const StreamingMessageText = memo(function StreamingMessageText({
  text,
  showPlaceholder,
  placeholderLabel,
}: {
  text: string;
  showPlaceholder: boolean;
  placeholderLabel?: string;
}) {
  const hasText = !!String(text || "").trim();
  const placeholderText = String(placeholderLabel || "").trim() || "Working...";

  return (
    <div className="w-full">
      <div
        className={classNames(
          "flex min-h-[1.75rem] items-center gap-2 transition-opacity duration-150",
          hasText ? "opacity-100" : "opacity-85 text-[var(--color-text-secondary)]",
        )}
      >
        {!hasText && showPlaceholder ? (
          <span className="inline-flex items-center gap-1 text-[var(--color-text-tertiary)]">
            {[0, 1, 2].map((index) => (
              <span
                key={index}
                className="h-1.5 w-1.5 rounded-full bg-current"
                style={{
                  animation: "ccccMessageTypingDot 1.1s ease-in-out infinite",
                  animationDelay: `${index * 140}ms`,
                }}
              />
            ))}
          </span>
        ) : null}
        <PlainMessageText
          text={hasText ? text : placeholderText}
          className="max-w-full"
        />
      </div>
    </div>
  );
});

const StreamingActivityList = memo(function StreamingActivityList({
  activities,
}: {
  activities: StreamingActivity[];
}) {
  const displayActivities = activities.slice(-STREAMING_ACTIVITY_DISPLAY_LIMIT);
  if (displayActivities.length <= 0) return null;

  return (
    <div className="flex flex-col gap-1 rounded-xl border border-[var(--glass-border-subtle)]/80 bg-[var(--glass-tab-bg)]/70 px-2.5 py-2 cccc-streaming-status-panel">
      {displayActivities.map((activity, index) => (
        <div key={activity.id} className="relative min-w-0 pl-4 text-[11px] leading-4 text-[var(--color-text-secondary)]">
          <div className="flex min-w-0 items-baseline gap-2">
            <span
              className={classNames(
                "absolute left-0 top-[0.35rem] h-2 w-2 rounded-full border border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg-hover)]",
                activity.status === "completed" ? "opacity-70" : "opacity-100",
              )}
            />
            {index < displayActivities.length - 1 ? (
              <span className="absolute left-[3px] top-[0.85rem] bottom-[-10px] w-px bg-[var(--glass-border-subtle)]/90" />
            ) : null}
            <span className="min-w-[2.75rem] font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {formatStreamingActivityKind(activity.kind)}
            </span>
            <span className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">
              {getStructuredStreamingActivityLabel(activity)}
              {activity.summary && getStructuredStreamingActivityLabel(activity) !== String(activity.summary).trim() ? (
                <span className="block text-[10px] text-[var(--color-text-tertiary)]">
                  {activity.summary}
                </span>
              ) : null}
              {activity.detail ? (
                <span className="block text-[10px] text-[var(--color-text-tertiary)]">
                  {activity.detail}
                </span>
              ) : null}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
});

const StreamingContent = memo(function StreamingContent({
  groupId,
  streamId,
  actorId,
  pendingEventId,
  fallbackText,
  fallbackActivities,
  isQueuedOnlyFallbackPlaceholder,
  placeholderLabel,
}: {
  groupId: string;
  streamId: string;
  actorId: string;
  pendingEventId: string;
  fallbackText: string;
  fallbackActivities: StreamingActivity[];
  isQueuedOnlyFallbackPlaceholder: boolean;
  placeholderLabel: string;
}) {
  const liveStreamingText = useGroupStore(useCallback((state) => {
    if (!streamId) return "";
    const bucket = state.chatByGroup[String(groupId || "").trim()];
    return String(bucket?.streamingTextByStreamId?.[streamId] || "");
  }, [groupId, streamId]));
  const streamingEvents = useGroupStore(useCallback((state) => {
    const bucket = state.chatByGroup[String(groupId || "").trim()];
    return Array.isArray(bucket?.streamingEvents) ? bucket.streamingEvents : EMPTY_STREAMING_EVENTS;
  }, [groupId]));
  const streamedActivities = useGroupStore(useCallback((state) => {
    if (!streamId) return EMPTY_STREAMING_ACTIVITIES;
    const bucket = state.chatByGroup[String(groupId || "").trim()];
    const activities = bucket?.streamingActivitiesByStreamId?.[streamId];
    return Array.isArray(activities) ? activities : EMPTY_STREAMING_ACTIVITIES;
  }, [groupId, streamId]));
  const liveStreamingActivities = useMemo(() => {
    return getEffectiveStreamingActivities({
      streamId,
      actorId,
      pendingEventId,
      bucket: {
        streamingActivitiesByStreamId: streamId
          ? { [streamId]: streamedActivities }
          : undefined,
        streamingEvents,
      },
      fallbackActivities,
    });
  }, [actorId, fallbackActivities, pendingEventId, streamId, streamedActivities, streamingEvents]);

  const effectiveText = liveStreamingText || fallbackText;
  const effectiveStreamingActivities = normalizeStreamingActivities(
    liveStreamingActivities.length > 0 ? liveStreamingActivities : fallbackActivities,
  );
  const hasText = !!String(effectiveText || "").trim();
  const isQueuedOnlyPlaceholder =
    !hasText &&
    effectiveStreamingActivities.length === 1 &&
    effectiveStreamingActivities[0]?.kind === "queued" &&
    effectiveStreamingActivities[0]?.summary === "queued"
      ? true
      : isQueuedOnlyFallbackPlaceholder;
  const [renderPhase, setRenderPhase] = useState<"pending" | "active" | "exiting" | "completed">(() =>
    deriveStreamingRenderPhase({
      isStreaming: true,
      hasText,
      activities: effectiveStreamingActivities,
    })
  );
  const [exitSnapshot, setExitSnapshot] = useState<{
    activities: StreamingActivity[];
    showPlaceholder: boolean;
    placeholderLabel: string;
    queuedOnly: boolean;
  } | null>(null);
  const pendingStartedAtRef = useRef<number | null>(null);
  const desiredPhase = useMemo(() => deriveStreamingRenderPhase({
    isStreaming: true,
    hasText,
    activities: effectiveStreamingActivities,
    previousPhase: renderPhase,
  }), [effectiveStreamingActivities, hasText, renderPhase]);

  useEffect(() => {
    const scheduledTimers: number[] = [];
    const scheduleTask = (task: () => void) => {
      const timerId = window.setTimeout(task, 0);
      scheduledTimers.push(timerId);
      return timerId;
    };
    const scheduleRenderPhase = (nextPhase: "pending" | "active" | "completed") => {
      scheduleTask(() => {
        setRenderPhase(nextPhase);
      });
    };
    const scheduleClearExitSnapshot = () => {
      scheduleTask(() => {
        setExitSnapshot(null);
      });
    };
    if (renderPhase !== "exiting" && desiredPhase === renderPhase) {
      if (renderPhase === "pending" && pendingStartedAtRef.current == null) {
        pendingStartedAtRef.current = Date.now();
      }
      if (renderPhase !== "pending") {
        pendingStartedAtRef.current = null;
      }
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    if (desiredPhase === "pending") {
      scheduleClearExitSnapshot();
      pendingStartedAtRef.current = Date.now();
      if (renderPhase !== "active") {
        scheduleRenderPhase("pending");
      }
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    if (desiredPhase === "completed") {
      pendingStartedAtRef.current = null;
      if (renderPhase !== "completed" && renderPhase !== "exiting") {
        scheduleTask(() => {
          setExitSnapshot({
            activities: effectiveStreamingActivities,
            showPlaceholder: renderPhase === "pending" || (!hasText && effectiveStreamingActivities.length === 0),
            placeholderLabel,
            queuedOnly: isQueuedOnlyPlaceholder,
          });
          setRenderPhase("exiting");
        });
      }
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    scheduleClearExitSnapshot();
    if (renderPhase !== "pending" || pendingStartedAtRef.current == null) {
      pendingStartedAtRef.current = null;
      scheduleRenderPhase("active");
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    const remainingMs = getStreamingPendingDelayMs(pendingStartedAtRef.current, Date.now());
    if (remainingMs <= 0) {
      pendingStartedAtRef.current = null;
      scheduleRenderPhase("active");
      return () => {
        scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
      };
    }

    const timeoutId = window.setTimeout(() => {
      pendingStartedAtRef.current = null;
      setRenderPhase("active");
    }, remainingMs);
    return () => {
      window.clearTimeout(timeoutId);
      scheduledTimers.forEach((timerId) => window.clearTimeout(timerId));
    };
  }, [desiredPhase, effectiveStreamingActivities, hasText, isQueuedOnlyPlaceholder, placeholderLabel, renderPhase]);

  useEffect(() => {
    if (renderPhase !== "exiting") return undefined;
    const timeoutId = window.setTimeout(() => {
      setExitSnapshot(null);
      setRenderPhase("completed");
    }, STREAMING_STATUS_EXIT_MS);
    return () => window.clearTimeout(timeoutId);
  }, [renderPhase]);

  const visibleActivities = renderPhase === "pending"
    ? EMPTY_STREAMING_ACTIVITIES
    : renderPhase === "exiting"
      ? (exitSnapshot?.activities || EMPTY_STREAMING_ACTIVITIES)
      : effectiveStreamingActivities;
  const shouldShowText = hasText || renderPhase !== "completed";
  const showActivitiesPanel = visibleActivities.length > 0 && !isQueuedOnlyPlaceholder;

  return (
    <div className={classNames("flex flex-col gap-1.5", isQueuedOnlyPlaceholder ? "" : "min-h-[4.25rem]")}>
      {showActivitiesPanel ? (
        <div className="flex min-h-[2rem] items-start transition-opacity duration-150 opacity-100" aria-hidden={false}>
          <div className="flex w-full flex-col gap-2">
            <div className="cccc-streaming-status-layer cccc-streaming-status-layer-active" aria-hidden={false}>
              <StreamingActivityList activities={visibleActivities} />
            </div>
          </div>
        </div>
      ) : null}

      {shouldShowText ? (
        <div className="flex min-h-[1.75rem] items-start">
          <StreamingMessageText
            text={effectiveText}
            showPlaceholder={!hasText}
            placeholderLabel={placeholderLabel}
          />
        </div>
      ) : null}
    </div>
  );
});

export function StreamingMessageBody({
  groupId,
  streamId,
  actorId,
  pendingEventId,
  fallbackText,
  streamingActivities,
  isQueuedOnlyPlaceholder,
  streamingPlaceholderLabel,
}: {
  groupId: string;
  streamId: string;
  actorId: string;
  pendingEventId: string;
  fallbackText: string;
  streamingActivities: StreamingActivity[];
  isQueuedOnlyPlaceholder: boolean;
  streamingPlaceholderLabel: string;
}) {
  return (
    <StreamingContent
      groupId={groupId}
      streamId={streamId}
      actorId={actorId}
      pendingEventId={pendingEventId}
      fallbackText={fallbackText}
      fallbackActivities={streamingActivities}
      isQueuedOnlyFallbackPlaceholder={isQueuedOnlyPlaceholder}
      placeholderLabel={streamingPlaceholderLabel}
    />
  );
}
