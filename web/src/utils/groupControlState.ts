import type { Actor, GroupDoc, GroupMeta, GroupRuntimeStatus, GroupServerControlState } from "../types";
import { getGroupStatusFromSource, type GroupStatus, type GroupStatusKey } from "./groupStatus";
import { getGroupControlVisual, getLaunchControlMode } from "./groupControls";

export type GroupControlIssueSeverity = "info" | "warning" | "error";

export type GroupControlStateIssue = {
  code: string;
  severity: GroupControlIssueSeverity;
  message: string;
  details?: Record<string, unknown>;
};

export type GroupControlState = {
  selectedGroupId: string;
  status: GroupStatus | null;
  statusKey: GroupStatusKey | null;
  runtimeStatus: GroupRuntimeStatus | null;
  backendControlState: GroupServerControlState | null;
  launchMode: "start" | "activate";
  launchControl: ReturnType<typeof getGroupControlVisual>;
  pauseControl: ReturnType<typeof getGroupControlVisual>;
  stopControl: ReturnType<typeof getGroupControlVisual>;
  launchHardUnavailable: boolean;
  pauseHardUnavailable: boolean;
  stopHardUnavailable: boolean;
  isGroupBusy: boolean;
  launchDisabled: boolean;
  pauseDisabled: boolean;
  stopDisabled: boolean;
  deliveryToggleIsPause: boolean;
  deliveryToggleKind: "pause" | "resume" | "start";
  deliveryToggleControl: ReturnType<typeof getGroupControlVisual>;
  deliveryToggleDisabled: boolean;
  deliveryToggleHardUnavailable: boolean;
  issues: GroupControlStateIssue[];
};

export type GroupControlStateInput = {
  selectedGroupId?: string;
  selectedGroupRunning?: boolean;
  selectedGroupRuntimeStatus?: GroupRuntimeStatus | null;
  groupDoc?: GroupDoc | null;
  groupMeta?: GroupMeta | null;
  actors?: Actor[];
  busy?: string;
};

const VALID_STATUS_KEYS = new Set(["run", "paused", "idle", "stop"]);
const VALID_PRIMARY_ACTIONS = new Set(["pause", "resume", "start"]);
const warnedIssueSignatures = new Set<string>();

function normalizeLifecycle(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

function normalizeBackendControlState(input: GroupControlStateInput): GroupServerControlState | null {
  return input.groupDoc?.control_state || input.groupMeta?.control_state || null;
}

function normalizeRuntimeStatus(input: GroupControlStateInput): GroupRuntimeStatus | null {
  const backendControlState = normalizeBackendControlState(input);
  if (backendControlState) {
    return {
      lifecycle_state: normalizeLifecycle(backendControlState.lifecycle_state) || "active",
      runtime_running: Boolean(backendControlState.runtime_running),
      running_actor_count: Number.isFinite(Number(backendControlState.running_actor_count))
        ? Number(backendControlState.running_actor_count)
        : 0,
      has_running_foreman: Boolean(backendControlState.has_running_foreman),
    };
  }

  const runtime =
    input.selectedGroupRuntimeStatus
    || input.groupDoc?.runtime_status
    || input.groupMeta?.runtime_status
    || null;

  const lifecycleState =
    normalizeLifecycle(runtime?.lifecycle_state)
    || normalizeLifecycle(input.groupDoc?.state)
    || normalizeLifecycle(input.groupMeta?.state)
    || "active";
  const selectedRunning = typeof input.selectedGroupRunning === "boolean" ? input.selectedGroupRunning : undefined;
  const running =
    runtime?.runtime_running
    ?? selectedRunning
    ?? input.groupDoc?.running
    ?? input.groupMeta?.running
    ?? false;

  return {
    lifecycle_state: lifecycleState,
    runtime_running: Boolean(running),
    running_actor_count: Number.isFinite(Number(runtime?.running_actor_count)) ? Number(runtime?.running_actor_count) : 0,
    has_running_foreman: Boolean(runtime?.has_running_foreman),
  };
}

function normalizeStatusKey(value: unknown): GroupStatusKey | null {
  const key = String(value || "").trim().toLowerCase();
  return VALID_STATUS_KEYS.has(key) ? key as GroupStatusKey : null;
}

function normalizeDeliveryToggleKind(value: unknown): GroupControlState["deliveryToggleKind"] | null {
  const kind = String(value || "").trim().toLowerCase();
  return VALID_PRIMARY_ACTIONS.has(kind) ? kind as GroupControlState["deliveryToggleKind"] : null;
}

function pushIssue(
  issues: GroupControlStateIssue[],
  code: string,
  severity: GroupControlIssueSeverity,
  message: string,
  details?: Record<string, unknown>,
) {
  issues.push({ code, severity, message, ...(details ? { details } : {}) });
}

function buildIssues(input: GroupControlStateInput): GroupControlStateIssue[] {
  const issues: GroupControlStateIssue[] = [];
  const selectedGroupId = String(input.selectedGroupId || "").trim();
  if (!selectedGroupId) {
    pushIssue(issues, "missing_selected_group", "info", "No selected group; controls should be unavailable.");
    return issues;
  }

  const backendControlState = normalizeBackendControlState(input);
  const backendIssues = Array.isArray(backendControlState?.issues) ? backendControlState.issues : [];

  if (!backendControlState) {
    pushIssue(issues, "missing_backend_control_state", "warning", "Backend did not provide group control_state; using display-only runtime fallback.", {
      selectedGroupId,
    });
    return issues;
  }

  for (const issue of backendIssues) {
    if (!issue || typeof issue !== "object") continue;
    pushIssue(
      issues,
      String(issue.code || "backend_control_state_issue"),
      issue.severity === "error" || issue.severity === "warning" || issue.severity === "info" ? issue.severity : "warning",
      String(issue.message || "Backend reported a group control state issue."),
      issue.details,
    );
  }
  if (!normalizeStatusKey(backendControlState.status_key)) {
    pushIssue(issues, "invalid_backend_status_key", "error", "Backend group control status key is not recognized.", {
      statusKey: backendControlState.status_key,
      selectedGroupId,
    });
  }
  if (!normalizeDeliveryToggleKind(backendControlState.primary_action)) {
    pushIssue(issues, "invalid_backend_primary_action", "error", "Backend group control primary action is not recognized.", {
      primaryAction: backendControlState.primary_action,
      selectedGroupId,
    });
  }

  return issues;
}

export function getGroupControlState(input: GroupControlStateInput): GroupControlState {
  const selectedGroupId = String(input.selectedGroupId || "").trim();
  const backendControlState = selectedGroupId ? normalizeBackendControlState(input) : null;
  const runtimeStatus = selectedGroupId ? normalizeRuntimeStatus(input) : null;
  const backendStatusKey = normalizeStatusKey(backendControlState?.status_key);
  const status = selectedGroupId && runtimeStatus
    ? getGroupStatusFromSource({
        running: runtimeStatus.runtime_running,
        state: runtimeStatus.lifecycle_state as GroupDoc["state"],
        runtime_status: runtimeStatus,
      })
    : null;
  const statusKey = backendStatusKey || status?.key || null;
  const launchMode = getLaunchControlMode(statusKey);
  const launchControl = getGroupControlVisual(statusKey, "launch", String(input.busy || ""));
  const pauseControl = getGroupControlVisual(statusKey, "pause", String(input.busy || ""));
  const stopControl = getGroupControlVisual(statusKey, "stop", String(input.busy || ""));
  const isGroupBusy = String(input.busy || "").startsWith("group-");
  const controls = backendControlState
    ? {
        isGroupBusy,
        launchHardUnavailable: !backendControlState.can_start,
        pauseHardUnavailable: !backendControlState.can_pause,
        stopHardUnavailable: !backendControlState.can_stop,
        launchDisabled: !backendControlState.can_start || isGroupBusy,
        pauseDisabled: !backendControlState.can_pause || isGroupBusy,
        stopDisabled: !backendControlState.can_stop || isGroupBusy,
      }
    : {
        isGroupBusy,
        launchHardUnavailable: true,
        pauseHardUnavailable: true,
        stopHardUnavailable: true,
        launchDisabled: true,
        pauseDisabled: true,
        stopDisabled: true,
      };
  const deliveryToggleKind = normalizeDeliveryToggleKind(backendControlState?.primary_action)
    || (statusKey === "run" ? "pause" : launchMode === "activate" ? "resume" : "start");
  const deliveryToggleIsPause = deliveryToggleKind === "pause";

  return {
    selectedGroupId,
    status,
    statusKey,
    runtimeStatus,
    backendControlState,
    launchMode,
    launchControl,
    pauseControl,
    stopControl,
    ...controls,
    deliveryToggleIsPause,
    deliveryToggleKind,
    deliveryToggleControl: deliveryToggleIsPause ? pauseControl : launchControl,
    deliveryToggleDisabled: deliveryToggleIsPause ? controls.pauseDisabled : controls.launchDisabled,
    deliveryToggleHardUnavailable: deliveryToggleIsPause ? controls.pauseHardUnavailable : controls.launchHardUnavailable,
    issues: buildIssues(input),
  };
}

export function reportGroupControlStateIssues(scope: string, state: GroupControlState) {
  const reportable = state.issues.filter((issue) => issue.severity === "warning" || issue.severity === "error");
  if (reportable.length === 0) return;

  const signature = `${scope}:${state.selectedGroupId}:${reportable.map((issue) => issue.code).join(",")}`;
  if (warnedIssueSignatures.has(signature)) return;
  warnedIssueSignatures.add(signature);

  console.warn("[group-control-state]", scope, {
    selectedGroupId: state.selectedGroupId,
    statusKey: state.statusKey,
    runtimeStatus: state.runtimeStatus,
    issues: reportable,
  });
}
