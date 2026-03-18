// Web Pet type definitions

export type CatState = "napping" | "working" | "busy" | "needs_you";

export const CAT_STATES: readonly CatState[] = [
  "napping",
  "working",
  "busy",
  "needs_you",
] as const;

export interface AgentSummary {
  id: string;
  state: string;
  focus: string;
}

export interface ActionItem {
  id: string;
  agent: string;
  summary: string;
  action?: ReminderAction;
}

export type ReminderKind =
  | "waiting_user"
  | "reply_required"
  | "stalled_peer"
  | "mention";

export type ReminderAction =
  | {
      type: "open_chat";
      groupId: string;
      eventId: string;
    }
  | {
      type: "open_task";
      groupId: string;
      taskId: string;
    }
  | {
      type: "open_panel";
      groupId: string;
    }
  | {
      type: "complete_task";
      groupId: string;
      taskId: string;
    };

export interface PetReminder {
  id: string;
  kind: ReminderKind;
  priority: number;
  summary: string;
  agent: string;
  ephemeral?: boolean;
  source: {
    eventId?: string;
    taskId?: string;
    actorId?: string;
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
  actionItems: ActionItem[];
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
