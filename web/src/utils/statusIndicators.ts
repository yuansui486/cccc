export type RuntimeIndicatorTone = "stop" | "run" | "working" | "waiting" | "stuck";

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
  "bg-transparent ring-[1.5px] ring-inset ring-emerald-500/75 dark:ring-emerald-400/75";

const WORKING_INDICATOR_DOT_CLASS =
  "bg-emerald-500 ring-[1.5px] ring-emerald-200/90 shadow-[0_0_0_3px_rgba(16,185,129,0.12),0_0_18px_rgba(52,211,153,0.75)] dark:bg-emerald-400 dark:ring-emerald-400/35 dark:shadow-[0_0_0_3px_rgba(16,185,129,0.10),0_0_18px_rgba(52,211,153,0.75)] scale-110";

const WAITING_INDICATOR_DOT_CLASS =
  "bg-sky-500 ring-[1.5px] ring-sky-200/90 shadow-[0_0_12px_rgba(56,189,248,0.28)] dark:bg-sky-400 dark:ring-sky-400/25 dark:shadow-[0_0_12px_rgba(56,189,248,0.35)]";

const STUCK_INDICATOR_DOT_CLASS =
  "bg-amber-500 ring-[1.5px] ring-amber-200/90 shadow-[0_0_0_3px_rgba(245,158,11,0.10),0_0_14px_rgba(251,191,36,0.35)] dark:bg-amber-400 dark:ring-amber-400/30 dark:shadow-[0_0_0_3px_rgba(245,158,11,0.10),0_0_14px_rgba(251,191,36,0.45)]";

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
      labelClass: "text-emerald-700 dark:text-emerald-300",
      pulse: true,
      strongPulse: true,
    };
  }

  if (workingState === "stuck") {
    return {
      tone: "stuck",
      dotClass: STUCK_INDICATOR_DOT_CLASS,
      labelClass: "text-amber-700 dark:text-amber-300",
      pulse: true,
      strongPulse: false,
    };
  }

  if (workingState === "waiting") {
    return {
      tone: "waiting",
      dotClass: WAITING_INDICATOR_DOT_CLASS,
      labelClass: "",
      pulse: true,
      strongPulse: false,
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
