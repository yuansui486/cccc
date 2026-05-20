export type RuntimeIndicatorTone = "stop" | "run" | "working";

export type RuntimeIndicatorState = {
  tone: RuntimeIndicatorTone;
  dotClass: string;
  labelClass: string;
  pulse: boolean;
  strongPulse: boolean;
};

export type GroupPresenceTone = "run" | "paused" | "idle" | "stop";

export const STOPPED_INDICATOR_DOT_CLASS =
  "bg-slate-400/70 ring-[1.5px] ring-inset ring-slate-300/70 dark:ring-slate-400/20 opacity-70";

export const QUIET_RUN_INDICATOR_DOT_CLASS =
  "bg-transparent ring-[1.5px] ring-inset ring-sky-500/75 dark:ring-sky-400/75";

const WORKING_INDICATOR_DOT_CLASS =
  "bg-amber-500 ring-[1.5px] ring-amber-200/90 shadow-[0_0_0_3px_rgba(245,158,11,0.14),0_0_18px_rgba(251,191,36,0.72)] dark:bg-amber-400 dark:ring-amber-400/35 dark:shadow-[0_0_0_3px_rgba(245,158,11,0.12),0_0_18px_rgba(251,191,36,0.72)] scale-110";

const PAUSED_INDICATOR_DOT_CLASS =
  "bg-amber-500 ring-[1.5px] ring-amber-200/90 dark:bg-amber-400 dark:ring-amber-400/25";

const IDLE_INDICATOR_DOT_CLASS =
  "bg-sky-500 ring-[1.5px] ring-sky-200/90 dark:bg-sky-400 dark:ring-sky-400/25";

export function getRuntimeIndicatorState(input: {
  isRunning: boolean;
  workingState: string;
}): RuntimeIndicatorState {
  const workingState = String(input.workingState || "").trim().toLowerCase();
  if (!input.isRunning) {
    return {
      tone: "stop",
      dotClass: STOPPED_INDICATOR_DOT_CLASS,
      labelClass: "",
      pulse: false,
      strongPulse: false,
    };
  }

  if (workingState === "working") {
    return {
      tone: "working",
      dotClass: WORKING_INDICATOR_DOT_CLASS,
      labelClass: "text-amber-700 dark:text-amber-300",
      pulse: true,
      strongPulse: true,
    };
  }

  return {
    tone: "run",
    dotClass: QUIET_RUN_INDICATOR_DOT_CLASS,
    labelClass: "",
    pulse: false,
    strongPulse: false,
  };
}

export function getGroupPresenceDotClass(tone: GroupPresenceTone): string {
  switch (tone) {
    case "paused":
      return PAUSED_INDICATOR_DOT_CLASS;
    case "idle":
      return IDLE_INDICATOR_DOT_CLASS;
    case "stop":
      return STOPPED_INDICATOR_DOT_CLASS;
    case "run":
    default:
      return QUIET_RUN_INDICATOR_DOT_CLASS;
  }
}
