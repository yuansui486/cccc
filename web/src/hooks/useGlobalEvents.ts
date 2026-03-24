// useGlobalEvents - Subscribe to global event stream for group/actor updates
// Falls back to polling after consecutive SSE errors

import { useEffect, useRef } from "react";
import * as api from "../services/api";

const GLOBAL_REFRESH_EVENT_KINDS = new Set([
  "group.created",
  "group.updated",
  "group.deleted",
  "group.state_changed",
  "actor.start",
  "actor.stop",
  "actor.restart",
]);

export function shouldRefreshGroupsAfterGlobalEventsOpen(_hasConnectedOnce: boolean): boolean {
  return true;
}

interface UseGlobalEventsOptions {
  /** Callback to refresh groups when events are received */
  refreshGroups: () => void;
}

/**
 * Subscribes to the global events stream to keep sidebar status in sync.
 * Falls back to polling after 3 consecutive SSE errors.
 */
export function useGlobalEvents({ refreshGroups }: UseGlobalEventsOptions): void {
  // Use ref to avoid recreating SSE connection when refreshGroups reference changes
  const refreshGroupsRef = useRef(refreshGroups);
  const hasConnectedOnceRef = useRef(false);
  useEffect(() => {
    refreshGroupsRef.current = refreshGroups;
  }, [refreshGroups]);

  useEffect(() => {
    let es: EventSource | null = null;
    let fallbackTimer: number | null = null;
    let fallbackDelayMs = 10000;
    let errorCount = 0;

    function clearFallbackTimer() {
      if (fallbackTimer) {
        window.clearTimeout(fallbackTimer);
        fallbackTimer = null;
      }
    }

    function invalidateAndRefreshGroups() {
      api.invalidateGroupsRead();
      refreshGroupsRef.current();
    }

    function scheduleFallbackPoll() {
      if (fallbackTimer) return;
      fallbackTimer = window.setTimeout(() => {
        fallbackTimer = null;
        if (!document.hidden) {
          invalidateAndRefreshGroups();
          // While in polling fallback, periodically attempt to restore SSE.
          // If reconnect succeeds, onopen() clears fallback polling.
          if (!es) {
            connectSSE();
          }
        }
        fallbackDelayMs = Math.min(fallbackDelayMs * 2, 60000);
        scheduleFallbackPoll();
      }, fallbackDelayMs);
    }

    function connectSSE() {
      if (es) return;
      es = new EventSource(api.withAuthToken("/api/v1/events/stream"));
      es.addEventListener("event", (e) => {
        try {
          const ev = JSON.parse((e as MessageEvent).data || "{}");
          const kind = typeof ev?.kind === "string" ? ev.kind : "";
          if (GLOBAL_REFRESH_EVENT_KINDS.has(kind)) {
            invalidateAndRefreshGroups();
          }
        } catch {
          /* ignore parse errors */
        }
      });
      es.onopen = () => {
        const shouldRefresh = shouldRefreshGroupsAfterGlobalEventsOpen(hasConnectedOnceRef.current);
        errorCount = 0; // Reset on successful connection
        fallbackDelayMs = 10000;
        clearFallbackTimer();
        hasConnectedOnceRef.current = true;
        // Global event streams start from EOF, so both first connect and
        // reconnect need a catch-up refresh to cover the open window.
        if (shouldRefresh) {
          invalidateAndRefreshGroups();
        }
      };
      es.onerror = () => {
        errorCount++;
        // After 3 consecutive errors, fallback to polling
        if (errorCount >= 3 && !fallbackTimer) {
          es?.close();
          es = null;
          scheduleFallbackPoll();
        }
      };
    }

    connectSSE();

    return () => {
      es?.close();
      clearFallbackTimer();
      hasConnectedOnceRef.current = false;
    };
  }, []); // Empty deps - only run on mount/unmount
}
