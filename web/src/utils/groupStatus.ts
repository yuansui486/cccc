import { getGroupStatusLabel } from "./displayText";

export type GroupStatusKey = "run" | "paused" | "idle" | "stop";

export type GroupStatus = {
  key: GroupStatusKey;
  label: string;
  pillClass: string;
  dotClass: string;
};

function buildStatus(key: GroupStatusKey, label: string, dotClass: string): GroupStatus {
  return {
    key,
    label,
    pillClass: `glass-status-pill glass-status-pill-${key}`,
    dotClass,
  };
}

export function getGroupStatus(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", getGroupStatusLabel("stop"), "bg-slate-400 ring-slate-400/20");
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", getGroupStatusLabel("paused"), "bg-amber-400 ring-amber-400/25");
    case "idle":
      return buildStatus("idle", getGroupStatusLabel("idle"), "bg-sky-400 ring-sky-400/25");
    default:
      break;
  }
  return buildStatus("run", getGroupStatusLabel("run"), "bg-emerald-400 ring-emerald-400/30 shadow-[0_0_12px_rgba(52,211,153,0.35)]");
}

export function getGroupStatusLight(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", getGroupStatusLabel("stop"), "bg-slate-400 ring-slate-300/70");
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", getGroupStatusLabel("paused"), "bg-amber-500 ring-amber-200/90");
    case "idle":
      return buildStatus("idle", getGroupStatusLabel("idle"), "bg-sky-500 ring-sky-200/90");
    default:
      break;
  }
  return buildStatus("run", getGroupStatusLabel("run"), "bg-emerald-500 ring-emerald-200/90 shadow-[0_0_10px_rgba(16,185,129,0.2)]");
}

/** Unified group status using dark: prefix - no isDark dependency needed */
export function getGroupStatusUnified(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", getGroupStatusLabel("stop"), "bg-slate-400 ring-slate-300/70 dark:ring-slate-400/20");
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", getGroupStatusLabel("paused"), "bg-amber-500 ring-amber-200/90 dark:bg-amber-400 dark:ring-amber-400/25");
    case "idle":
      return buildStatus("idle", getGroupStatusLabel("idle"), "bg-sky-500 ring-sky-200/90 dark:bg-sky-400 dark:ring-sky-400/25");
    default:
      break;
  }
  return buildStatus("run", getGroupStatusLabel("run"), "bg-emerald-500 ring-emerald-200/90 shadow-[0_0_10px_rgba(16,185,129,0.2)] dark:bg-emerald-400 dark:ring-emerald-400/30 dark:shadow-[0_0_12px_rgba(52,211,153,0.35)]");
}
