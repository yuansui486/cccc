import { useEffect, useMemo, useState } from "react";

import { getTerminalSignalKey, useTerminalSignalsStore } from "../stores";
import type { TerminalSignal } from "../stores/useTerminalSignalsStore";
import type { Actor } from "../types";
import { getActorDisplayWorkingState } from "../utils/terminalWorkingState";
import { getActorTabIndicatorState, type ActorTabIndicator } from "../components/tabBarIndicator";

export type ActorDisplayState = {
  isRunning: boolean;
  assumeRunning: boolean;
  workingState: string;
  indicator: ActorTabIndicator;
};

type UseActorDisplayStateInput = {
  groupId: string;
  actor: Actor;
  selectedGroupRunning?: boolean;
  selectedGroupActorsHydrating?: boolean;
};

type ComputeActorDisplayStateInput = {
  actor: Actor;
  selectedGroupRunning?: boolean;
  selectedGroupActorsHydrating?: boolean;
  terminalSignal?: TerminalSignal | null;
  now?: number;
};

const LIVE_TERMINAL_SIGNAL_TTL_MS = 5000;

function hasLiveTerminalSignal(signal: TerminalSignal | null | undefined, now: number): boolean {
  return !!signal && now - Number(signal.updatedAt || 0) <= LIVE_TERMINAL_SIGNAL_TTL_MS;
}

export function computeActorDisplayState({
  actor,
  selectedGroupRunning = false,
  selectedGroupActorsHydrating = false,
  terminalSignal,
  now = Date.now(),
}: ComputeActorDisplayStateInput): ActorDisplayState {
  const runningKnown = typeof actor.running === "boolean";
  const backendRunning = runningKnown ? Boolean(actor.running) : Boolean(actor.enabled ?? false);
  const backendWorkingState = String(actor.effective_working_state || "").trim().toLowerCase();
  const optimisticRunning = actor.enabled !== false && (
    hasLiveTerminalSignal(terminalSignal, now)
    || (selectedGroupRunning && selectedGroupActorsHydrating)
    || (!!backendWorkingState && backendWorkingState !== "stopped")
  );
  const isRunning = backendRunning || optimisticRunning;
  const assumeRunning = !backendRunning && optimisticRunning;
  let workingState = getActorDisplayWorkingState(
    isRunning === Boolean(actor.running) ? actor : { ...actor, running: isRunning },
    terminalSignal,
    now,
  );
  if (assumeRunning && workingState === "stopped") {
    workingState = selectedGroupActorsHydrating ? "waiting" : "idle";
  }
  const indicator = getActorTabIndicatorState({
    isRunning,
    workingState,
    assumeRunning,
  });

  return {
    isRunning,
    assumeRunning,
    workingState,
    indicator,
  };
}

export function useActorDisplayState({
  groupId,
  actor,
  selectedGroupRunning = false,
  selectedGroupActorsHydrating = false,
}: UseActorDisplayStateInput): ActorDisplayState {
  const terminalSignal = useTerminalSignalsStore((state) => state.signals[getTerminalSignalKey(groupId, actor.id)]);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!terminalSignal) return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [terminalSignal]);

  return useMemo(() => {
    return computeActorDisplayState({
      actor,
      selectedGroupRunning,
      selectedGroupActorsHydrating,
      terminalSignal,
      now,
    });
  }, [actor, now, selectedGroupActorsHydrating, selectedGroupRunning, terminalSignal]);
}
