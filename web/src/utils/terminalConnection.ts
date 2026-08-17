export function buildTerminalConnectionKey(args: {
  activated: boolean;
  isRunning: boolean;
  isHeadless: boolean;
  groupId: string;
  actorId: string;
  termEpoch: number;
  reconnectTrigger: number;
  canControl: boolean;
}): string {
  return [
    args.activated ? "active" : "inactive",
    args.isRunning ? "running" : "stopped",
    args.isHeadless ? "headless" : "pty",
    String(args.groupId || "").trim(),
    String(args.actorId || "").trim(),
    String(args.termEpoch || 0),
    String(args.reconnectTrigger || 0),
    args.canControl ? "control" : "readonly",
  ].join(":");
}

export function buildTerminalWebSocketUrl(args: {
  protocol: string;
  host: string;
  groupId: string;
  actorId: string;
  since?: number | string | null;
  mode?: "control" | "viewer";
  takeover?: boolean;
}): string {
  const protocol = args.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//${args.host}/api/v1/groups/${encodeURIComponent(args.groupId)}/actors/${encodeURIComponent(args.actorId)}/term`;
  const params = new URLSearchParams();
  params.set("mode", args.mode === "viewer" ? "viewer" : "control");
  if (args.takeover) params.set("takeover", "true");
  if (args.since !== null && args.since !== undefined && String(args.since).trim()) {
    params.set("since", String(args.since));
  }
  return `${url}?${params.toString()}`;
}

export const TERMINAL_FRAME_INPUT = 48;
export const TERMINAL_FRAME_OUTPUT = 49;
export const TERMINAL_FRAME_RESIZE = 50;
export const TERMINAL_FRAME_ATTACH = 51;
export const TERMINAL_FRAME_INPUT_ACK = 52;

const terminalTextEncoder = new TextEncoder();
const terminalTextDecoder = new TextDecoder();
const STARTUP_RACE_RECONNECT_DELAY_MS = 750;
const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 30000;
const MAX_RECONNECT_ATTEMPTS = 10;
const ACTOR_STARTUP_RETRY_WINDOW_MS = 60000;

export type TerminalBinaryFrame =
  | { type: "input"; payload: Uint8Array }
  | { type: "output"; payload: Uint8Array }
  | { type: "resize"; payload: Uint8Array }
  | { type: "attach"; payload: Uint8Array }
  | { type: "input_ack"; payload: Uint8Array };

function buildTerminalFrame(opcode: number, payload?: Uint8Array): Uint8Array {
  const body = payload || new Uint8Array();
  const out = new Uint8Array(body.length + 1);
  out[0] = opcode;
  out.set(body, 1);
  return out;
}

export function encodeTerminalInputFrame(data: string): Uint8Array {
  return buildTerminalFrame(TERMINAL_FRAME_INPUT, terminalTextEncoder.encode(String(data || "")));
}

export function encodeTerminalResizeFrame(cols: number, rows: number): Uint8Array {
  return buildTerminalFrame(
    TERMINAL_FRAME_RESIZE,
    terminalTextEncoder.encode(
      JSON.stringify({ cols: Math.max(0, Math.floor(cols)), rows: Math.max(0, Math.floor(rows)) }),
    ),
  );
}

export function shouldMaintainTerminalConnection(args: {
  activated: boolean;
  isRunning: boolean;
  isHeadless: boolean;
  hasTerminal: boolean;
}): boolean {
  return Boolean(args.activated && args.isRunning && !args.isHeadless && args.hasTerminal);
}

export function seedTerminalReplayCursor(
  deliveredCursor: number | null,
  replayCursorValue: unknown,
): { cursor: number | null; resetTerminal: boolean } {
  const replayCursor = Number(replayCursorValue);
  if (!Number.isFinite(replayCursor)) {
    return { cursor: deliveredCursor, resetTerminal: false };
  }
  return {
    cursor: replayCursor,
    resetTerminal: deliveredCursor !== null && replayCursor > deliveredCursor,
  };
}

export function terminalAttachRetryDelayMs(args: {
  code: string;
  attempt: number;
  startupElapsedMs: number;
}): number | null {
  const code = String(args.code || "").trim();
  if (code === "actor_not_running") {
    return args.startupElapsedMs < ACTOR_STARTUP_RETRY_WINDOW_MS
      ? STARTUP_RACE_RECONNECT_DELAY_MS
      : null;
  }
  if (code === "terminal_attach_busy") {
    const attempt = Math.max(0, Math.floor(args.attempt));
    if (attempt >= MAX_RECONNECT_ATTEMPTS) return null;
    return Math.min(RECONNECT_BASE_DELAY_MS * Math.pow(2, attempt), RECONNECT_MAX_DELAY_MS);
  }
  return null;
}

export function decodeTerminalJsonFrame<T = Record<string, unknown>>(
  payload: Uint8Array,
): T | null {
  try {
    return JSON.parse(terminalTextDecoder.decode(payload)) as T;
  } catch {
    return null;
  }
}

export function parseTerminalBinaryFrame(data: ArrayBuffer): TerminalBinaryFrame | null {
  const bytes = new Uint8Array(data);
  if (bytes.length <= 0) return null;
  const payload = bytes.slice(1);
  switch (bytes[0]) {
    case TERMINAL_FRAME_INPUT:
      return { type: "input", payload };
    case TERMINAL_FRAME_OUTPUT:
      return { type: "output", payload };
    case TERMINAL_FRAME_RESIZE:
      return { type: "resize", payload };
    case TERMINAL_FRAME_ATTACH:
      return { type: "attach", payload };
    case TERMINAL_FRAME_INPUT_ACK:
      return { type: "input_ack", payload };
    default:
      return null;
  }
}

export function isTerminalAttachNonRetryableErrorCode(code: unknown): boolean {
  const normalized = String(code || "").trim();
  return [
    "actor_not_found",
    "auth_required",
    "group_not_found",
    "not_pty_actor",
    "permission_denied",
    "read_only_terminal",
  ].includes(normalized);
}

export function isTerminalAttachStartupRaceErrorCode(code: unknown): boolean {
  const normalized = String(code || "").trim();
  return normalized === "actor_not_running" || normalized === "terminal_attach_busy";
}

export function shouldSuppressTerminalAttachErrorOutput(code: unknown): boolean {
  const normalized = String(code || "").trim();
  return (
    normalized === "actor_not_running" ||
    normalized === "not_pty_actor" ||
    normalized === "terminal_attach_busy"
  );
}
