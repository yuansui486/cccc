import { getGroupPresenceDotClass } from "./statusIndicators";
import { getGroupStatusLabel } from "./displayText";
import type { GroupDoc, GroupMeta, GroupRuntimeStatus } from "../types";

export type GroupStatusKey = "run" | "paused" | "idle" | "stop";

export type GroupStatus = {
  key: GroupStatusKey;
  label: string;
  pillClass: string;
  dotClass: string;
};

type GroupStatusSource = Pick<GroupMeta, "running" | "state" | "runtime_status"> | Pick<GroupDoc, "running" | "state" | "runtime_status">;

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
  switch (state) {
    case "paused":
      return buildStatus("paused", getGroupStatusLabel("paused"), getGroupPresenceDotClass("paused"));
    case "stopped":
      return buildStatus("stop", getGroupStatusLabel("stop"), getGroupPresenceDotClass("stop"));
    default:
      break;
  }
  if (!running) {
    return buildStatus("stop", getGroupStatusLabel("stop"), getGroupPresenceDotClass("stop"));
  }
  switch (state) {
    case "idle":
      return buildStatus("idle", getGroupStatusLabel("idle"), getGroupPresenceDotClass("idle"));
    default:
      break;
  }
  return buildStatus("run", getGroupStatusLabel("run"), getGroupPresenceDotClass("run"));
}

export function getGroupRuntimeStatus(source?: GroupStatusSource | null): GroupRuntimeStatus {
  const runtime = source?.runtime_status;
  return {
    lifecycle_state: String(runtime?.lifecycle_state || source?.state || "active"),
    runtime_running: Boolean(runtime?.runtime_running ?? source?.running ?? false),
    running_actor_count: Number.isFinite(Number(runtime?.running_actor_count)) ? Number(runtime?.running_actor_count) : 0,
    has_running_foreman: Boolean(runtime?.has_running_foreman ?? false),
  };
}

export function getGroupStatusFromSource(source?: GroupStatusSource | null): GroupStatus {
  const runtime = getGroupRuntimeStatus(source);
  return getGroupStatusUnified(runtime.runtime_running, runtime.lifecycle_state);
}
