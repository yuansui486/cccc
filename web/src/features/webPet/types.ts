// Web Pet type definitions

export type CatState = "napping" | "working" | "busy";

export const CAT_STATES: readonly CatState[] = [
  "napping",
  "working",
  "busy",
] as const;

export interface AgentSummary {
  id: string;
  state: string;
  focus: string;
  activeTaskId?: string;
}

export interface PetCompanionProfile {
  name: string;
  species: string;
  identity: string;
  temperament: string;
  speechStyle: string;
  careStyle: string;
}

export type TaskProposalStylePolicy = {
  tone: "cautious" | "direct";
  ownershipDriftMode: "reconfirm" | "reassign";
  stalledActiveMode: "reconfirm" | "escalate";
  waitingUserMode: "sync" | "close";
};

export type TaskProposalReason =
  | {
      kind: "move_active";
      actorId: string;
    }
  | {
      kind: "sync_waiting_user";
      actorId: string;
      focus: string;
    }
  | {
      kind: "sync_blocked";
      actorId: string;
      blockers: string[];
    }
  | {
      kind: "stalled_active_task";
      actorId: string;
      focus: string;
      mountedMinutes: number;
      blockers: string[];
      suggestedOperation: "update" | "handoff";
    }
  | {
      kind: "ownership_drift";
      actorId: string;
      currentActiveTaskId?: string;
    }
  | {
      kind: "assign_active_owner";
      actorId: string;
    }
  | {
      kind: "escalated_waiting_user";
      actorId: string;
      focus: string;
      mountedMinutes: number;
    };

export type ReminderKind =
  | "suggestion"
  | "actor_down"
  ;

export type ReminderAction =
  | {
      type: "draft_message";
      groupId: string;
      text: string;
      to?: string[];
      replyTo?: string;
    }
  | {
      type: "task_proposal";
      groupId: string;
      operation: "create" | "update" | "move" | "handoff" | "archive";
      taskId?: string;
      title?: string;
      status?: string;
      assignee?: string;
      text?: string;
      reason?: TaskProposalReason;
      style?: TaskProposalStylePolicy;
    }
  | {
      type: "restart_actor";
      groupId: string;
      actorId: string;
    };

export interface PetReminder {
  id: string;
  kind: ReminderKind;
  priority: number;
  summary: string;
  confidence?: "low" | "medium" | "high";
  reasoningBrief?: string;
  agent: string;
  ephemeral?: boolean;
  source: {
    eventId?: string;
    taskId?: string;
    actorId?: string;
    actorRole?: string;
    errorReason?: string;
    suggestionKind?: "mention" | "reply_required";
  };
  fingerprint: string;
  action: ReminderAction;
}

export interface ConnectionStatus {
  connected: boolean;
  message: string;
}

export interface PanelData {
  teamName: string;
  agents: AgentSummary[];
  connection: ConnectionStatus;
  taskProgress?: {
    total: number;
    done: number;
    active: number;
  };
}

export type WebPetSpriteUrls = Record<CatState, string>;

export type PetReaction = {
  kind: "mention" | "success" | "error";
  durationMs: number;
  reason: string;
  source?: {
    eventId?: string;
    taskId?: string;
  };
} | null;

export interface CatEngineOptions {
  canvas: HTMLCanvasElement;
  spriteUrls: WebPetSpriteUrls;
}

export interface CatEngine {
  load(): Promise<void>;
  setState(state: CatState): void;
  setHint(hint: string): void;
  playReaction(kind: "mention" | "success" | "error"): void;
  destroy(): void;
}
