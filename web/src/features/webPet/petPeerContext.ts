import { useEffect, useState } from "react";
import type { PetPeerContextResponse } from "../../services/api";
import { fetchPetPeerContext } from "../../services/api";
import type { PetReminder } from "./types";

export type PetPeerContextStatus = "idle" | "loading" | "loaded" | "error";

export type PetPeerContext = {
  decisions: PetReminder[];
  signals: {
    replyPressure: {
      severity: string;
      pendingCount: number;
      overdueCount: number;
      oldestPendingSeconds: number;
      baselineMedianReplySeconds: number;
    };
    coordinationRhythm: {
      severity: string;
      foremanId?: string;
      silenceSeconds: number;
      baselineMedianGapSeconds: number;
    };
    taskPressure: {
      severity: string;
      score: number;
      trendScore: number;
      blockedCount: number;
      waitingUserCount: number;
      handoffCount: number;
      plannedBacklogCount: number;
      recentBlockedUpdates: number;
      recentWaitingUserUpdates: number;
      recentHandoffUpdates: number;
      recentTaskCreateOps: number;
      recentTaskUpdateOps: number;
      recentTaskMoveOps: number;
      recentTaskRestoreOps: number;
      recentTaskDeleteOps: number;
      recentTaskChangeCount: number;
      recentTaskContextSyncEvents: number;
      ledgerTrendScore: number;
    };
    proposalReady: {
      ready: boolean;
      focus: string;
      severity: string;
      summary: string;
      pendingReplyCount: number;
      overdueReplyCount: number;
      waitingUserCount: number;
      blockedCount: number;
      handoffCount: number;
      recentTaskChangeCount: number;
      foremanSilenceSeconds: number;
    };
  };
  persona: string;
  help: string;
  prompt: string;
  snapshot: string;
  source: "help" | "default";
  status: PetPeerContextStatus;
};

function mapTaskProposalOperation(value: unknown): "create" | "update" | "move" | "handoff" | "archive" {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "create") return "create";
  if (normalized === "move") return "move";
  if (normalized === "handoff") return "handoff";
  if (normalized === "archive") return "archive";
  return "update";
}

function mapSignals(raw?: PetPeerContextResponse["signals"] | null): PetPeerContext["signals"] {
  const reply = raw?.reply_pressure || {};
  const rhythm = raw?.coordination_rhythm || {};
  const task = raw?.task_pressure || {};
  const proposal = raw?.proposal_ready || {};
  return {
    replyPressure: {
      severity: String(reply.severity || "low").trim() || "low",
      pendingCount: Number(reply.pending_count || 0),
      overdueCount: Number(reply.overdue_count || 0),
      oldestPendingSeconds: Number(reply.oldest_pending_seconds || 0),
      baselineMedianReplySeconds: Number(reply.baseline_median_reply_seconds || 0),
    },
    coordinationRhythm: {
      severity: String(rhythm.severity || "low").trim() || "low",
      foremanId: String(rhythm.foreman_id || "").trim() || undefined,
      silenceSeconds: Number(rhythm.silence_seconds || 0),
      baselineMedianGapSeconds: Number(rhythm.baseline_median_gap_seconds || 0),
    },
    taskPressure: {
      severity: String(task.severity || "low").trim() || "low",
      score: Number(task.score || 0),
      trendScore: Number(task.trend_score || 0),
      blockedCount: Number(task.blocked_count || 0),
      waitingUserCount: Number(task.waiting_user_count || 0),
      handoffCount: Number(task.handoff_count || 0),
      plannedBacklogCount: Number(task.planned_backlog_count || 0),
      recentBlockedUpdates: Number(task.recent_blocked_updates || 0),
      recentWaitingUserUpdates: Number(task.recent_waiting_user_updates || 0),
      recentHandoffUpdates: Number(task.recent_handoff_updates || 0),
      recentTaskCreateOps: Number(task.recent_task_create_ops || 0),
      recentTaskUpdateOps: Number(task.recent_task_update_ops || 0),
      recentTaskMoveOps: Number(task.recent_task_move_ops || 0),
      recentTaskRestoreOps: Number(task.recent_task_restore_ops || 0),
      recentTaskDeleteOps: Number(task.recent_task_delete_ops || 0),
      recentTaskChangeCount: Number(task.recent_task_change_count || 0),
      recentTaskContextSyncEvents: Number(task.recent_task_context_sync_events || 0),
      ledgerTrendScore: Number(task.ledger_trend_score || 0),
    },
    proposalReady: {
      ready: !!proposal.ready,
      focus: String(proposal.focus || "none").trim() || "none",
      severity: String(proposal.severity || "low").trim() || "low",
      summary: String(proposal.summary || "").trim(),
      pendingReplyCount: Number(proposal.pending_reply_count || 0),
      overdueReplyCount: Number(proposal.overdue_reply_count || 0),
      waitingUserCount: Number(proposal.waiting_user_count || 0),
      blockedCount: Number(proposal.blocked_count || 0),
      handoffCount: Number(proposal.handoff_count || 0),
      recentTaskChangeCount: Number(proposal.recent_task_change_count || 0),
      foremanSilenceSeconds: Number(proposal.foreman_silence_seconds || 0),
    },
  };
}

function mapDecision(raw: NonNullable<PetPeerContextResponse["decisions"]>[number]): PetReminder | null {
  const id = String(raw?.id || "").trim();
  const kind = String(raw?.kind || "").trim();
  const fingerprint = String(raw?.fingerprint || "").trim();
  const actionType = String(raw?.action?.type || "").trim();
  if (!id || !kind || !fingerprint || !actionType) return null;

  const action =
    actionType === "restart_actor"
      ? {
          type: "restart_actor" as const,
          groupId: String(raw?.action?.group_id || "").trim(),
          actorId: String(raw?.action?.actor_id || "").trim(),
        }
      : actionType === "task_proposal"
        ? {
            type: "task_proposal" as const,
            groupId: String(raw?.action?.group_id || "").trim(),
            operation: mapTaskProposalOperation(raw?.action?.operation),
            taskId: String(raw?.action?.task_id || "").trim() || undefined,
            title: String(raw?.action?.title || "").trim() || undefined,
            status: String(raw?.action?.status || "").trim() || undefined,
            assignee: String(raw?.action?.assignee || "").trim() || undefined,
            text: String(raw?.action?.text || "").trim() || undefined,
          }
      : actionType === "automation_proposal"
        ? {
            type: "automation_proposal" as const,
            groupId: String(raw?.action?.group_id || "").trim(),
            title: String(raw?.action?.title || "").trim() || undefined,
            summary: String(raw?.action?.summary || "").trim() || undefined,
            actions: Array.isArray(raw?.action?.actions)
              ? raw.action.actions.filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
              : [],
          }
      : actionType === "draft_message"
        ? {
          type: "draft_message" as const,
          groupId: String(raw?.action?.group_id || "").trim(),
          text: String(raw?.action?.text || "").trim(),
          to: Array.isArray(raw?.action?.to)
            ? raw.action.to.map((entry) => String(entry || "").trim()).filter(Boolean)
            : undefined,
          replyTo: String(raw?.action?.reply_to || "").trim() || undefined,
        }
        : null;

  if (!action) return null;

  if (action.type === "restart_actor" && (!action.groupId || !action.actorId)) return null;
  if (action.type === "draft_message" && !action.text) return null;
  if (action.type === "task_proposal" && !action.groupId) return null;
  if (action.type === "automation_proposal" && (!action.groupId || action.actions.length === 0)) return null;

  return {
    id,
    kind: kind === "actor_down" ? "actor_down" : "suggestion",
    priority: Number(raw?.priority || 0),
    summary: String(raw?.summary || "").trim(),
    agent: String(raw?.agent || "").trim(),
    ephemeral: !!raw?.ephemeral,
    source: {
      eventId: String(raw?.source?.event_id || "").trim() || undefined,
      taskId: String(raw?.source?.task_id || "").trim() || undefined,
      actorId: String(raw?.source?.actor_id || "").trim() || undefined,
      actorRole: String(raw?.source?.actor_role || "").trim() || undefined,
      errorReason: String(raw?.source?.error_reason || "").trim() || undefined,
      suggestionKind:
        String(raw?.source?.suggestion_kind || "").trim() === "reply_required"
          ? "reply_required"
          : String(raw?.source?.suggestion_kind || "").trim() === "mention"
            ? "mention"
            : undefined,
    },
    fingerprint,
    action,
  };
}

export function buildPetPeerContext(
  raw?: Partial<PetPeerContextResponse> | null,
  opts?: { status?: PetPeerContextStatus },
): PetPeerContext {
  const persona = String(raw?.persona || "").trim();
  const help = String(raw?.help || "").trim();
  const prompt = String(raw?.prompt || "").trim();
  const snapshot = String(raw?.snapshot || "").trim();
  const decisions = Array.isArray(raw?.decisions)
    ? raw.decisions.map((item) => mapDecision(item)).filter((item): item is PetReminder => item !== null)
    : [];

  return {
    decisions,
    signals: mapSignals(raw?.signals),
    persona,
    help,
    prompt,
    snapshot,
    source: raw?.source === "help" ? "help" : "default",
    status: opts?.status || "loaded",
  };
}

export function usePetPeerContext(input: {
  groupId: string | null | undefined;
  refreshToken?: number;
}): PetPeerContext {
  const groupId = String(input.groupId || "").trim();
  const refreshToken = Number(input.refreshToken || 0);
  const [state, setState] = useState<{
    groupId: string;
    rawContext: Partial<PetPeerContextResponse> | null;
    status: PetPeerContextStatus;
  }>({
    groupId: "",
    rawContext: null,
    status: "idle",
  });

  useEffect(() => {
    if (!groupId) return;

    let cancelled = false;
    // 同步更新 state.groupId 以避免在 fetch 期间产生 state.groupId !== groupId 的
    // "中间态"，该中间态会导致每次重渲染创建新对象，引发 VirtualMessageList 级联刷新
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 有意为之的同步状态机转换
    setState({
      groupId,
      rawContext: null,
      status: "loading",
    });
    void fetchPetPeerContext(groupId)
      .then((resp) => {
        if (cancelled) return;
        setState({
          groupId,
          rawContext: resp.ok ? resp.result || null : null,
          status: resp.ok ? "loaded" : "error",
        });
      })
      .catch((error) => {
        if (cancelled) return;
        console.warn("failed to load pet peer context", error);
        setState({
          groupId,
          rawContext: null,
          status: "error",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [groupId, refreshToken]);

  if (!groupId || state.groupId !== groupId) {
    return buildPetPeerContext(null, { status: !groupId ? "idle" : "loading" });
  }

  return buildPetPeerContext(state.rawContext, { status: state.status });
}
