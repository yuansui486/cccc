import { getGroupStatusLabel } from "./displayText";
import { getGroupPresenceDotClass } from "./statusIndicators";

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
    return buildStatus("stop", getGroupStatusLabel("stop"), getGroupPresenceDotClass("stop"));
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", getGroupStatusLabel("paused"), getGroupPresenceDotClass("paused"));
    case "idle":
      return buildStatus("idle", getGroupStatusLabel("idle"), getGroupPresenceDotClass("idle"));
    default:
      break;
  }
  return buildStatus("run", getGroupStatusLabel("run"), getGroupPresenceDotClass("run"));
}

export function getGroupStatusLight(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", getGroupStatusLabel("stop"), getGroupPresenceDotClass("stop"));
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", getGroupStatusLabel("paused"), getGroupPresenceDotClass("paused"));
    case "idle":
      return buildStatus("idle", getGroupStatusLabel("idle"), getGroupPresenceDotClass("idle"));
    default:
      break;
  }
  return buildStatus("run", getGroupStatusLabel("run"), getGroupPresenceDotClass("run"));
}

/** Unified group status using dark: prefix - no isDark dependency needed */
export function getGroupStatusUnified(running: boolean, state?: string): GroupStatus {
  if (!running) {
    return buildStatus("stop", getGroupStatusLabel("stop"), getGroupPresenceDotClass("stop"));
  }
  switch (state) {
    case "paused":
      return buildStatus("paused", getGroupStatusLabel("paused"), getGroupPresenceDotClass("paused"));
    case "idle":
      return buildStatus("idle", getGroupStatusLabel("idle"), getGroupPresenceDotClass("idle"));
    default:
      break;
  }
  return buildStatus("run", getGroupStatusLabel("run"), getGroupPresenceDotClass("run"));
}
