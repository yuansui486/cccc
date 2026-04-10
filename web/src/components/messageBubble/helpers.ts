import type { ChatMessageData, LedgerEvent, StreamingActivity } from "../../types";
import { dedupeStreamingActivities } from "../../stores/chatStreamingSessions";
import { formatTime } from "../../utils/time";

const EMPTY_STREAMING_ACTIVITIES: StreamingActivity[] = [];
const EMPTY_STREAMING_EVENTS: LedgerEvent[] = [];
const STREAMING_PENDING_MIN_MS = 80;
const STREAMING_ACTIVITY_LOG_LIMIT = 12;

function isMarkdownTableSeparatorCell(cell: string): boolean {
  return /^:?-{3,}:?$/.test(String(cell || "").trim());
}

function containsMarkdownTable(text: string): boolean {
  const lines = String(text || "").split(/\r?\n/);
  for (let index = 0; index < lines.length - 1; index += 1) {
    const header = String(lines[index] || "").trim();
    const separator = String(lines[index + 1] || "").trim();
    if (!header || !separator || !header.includes("|") || !separator.includes("-")) continue;

    const headerCells = header.split("|").map((cell) => cell.trim()).filter(Boolean);
    const separatorCells = separator.split("|").map((cell) => cell.trim()).filter(Boolean);
    if (headerCells.length < 2) continue;
    if (separatorCells.length !== headerCells.length) continue;
    if (separatorCells.every(isMarkdownTableSeparatorCell)) return true;
  }
  return false;
}

export function mayContainMarkdown(text: string): boolean {
  const value = String(text || "");
  if (!value.trim()) return false;
  // Internal delivery manifests should stay compact plain text instead of
  // picking up prose list spacing from Markdown rendering.
  if (/^\[cccc\]\s+(Attachments|References):/m.test(value)) return false;
  if (containsMarkdownTable(value)) return true;
  return /(```|`[^`\n]+`|\[[^\]]+\]\([^)]+\)|^#{1,6}\s|^\s*[-*+]\s|^\s*\d+\.\s|^\s*>\s)/m.test(value);
}

export function formatStreamingActivityKind(kind: string): string {
  const normalized = String(kind || "").trim();
  switch (normalized) {
    case "queued":
      return "queue";
    case "thinking":
      return "think";
    case "plan":
      return "plan";
    case "search":
      return "search";
    case "command":
      return "run";
    case "patch":
      return "patch";
    case "tool":
      return "tool";
    case "reply":
      return "reply";
    default:
      return normalized || "step";
  }
}

export function getStructuredStreamingActivityLabel(activity: StreamingActivity): string {
  const command = String(activity.command || "").trim();
  if (command) return command;
  const filePaths = Array.isArray(activity.file_paths)
    ? activity.file_paths.map((item) => String(item || "").trim()).filter((item) => item)
    : [];
  if (filePaths.length > 0) return filePaths.join(", ");
  const toolName = String(activity.tool_name || "").trim();
  const serverName = String(activity.server_name || "").trim();
  if (toolName && serverName) return `${serverName}:${toolName}`;
  if (toolName) return toolName;
  const query = String(activity.query || "").trim();
  if (query) return query;
  return String(activity.summary || "").trim();
}

export function normalizeStreamingActivities(value: unknown): StreamingActivity[] {
  if (!Array.isArray(value)) return EMPTY_STREAMING_ACTIVITIES;
  const normalized = dedupeStreamingActivities(value
    .filter((item): item is StreamingActivity => !!item && typeof item === "object")
    .map((item) => ({
      id: String(item.id || ""),
      kind: String(item.kind || "thinking"),
      status: String(item.status || "updated"),
      summary: String(item.summary || ""),
      detail: item.detail ? String(item.detail) : undefined,
      ts: item.ts ? String(item.ts) : undefined,
      raw_item_type: item.raw_item_type ? String(item.raw_item_type) : undefined,
      tool_name: item.tool_name ? String(item.tool_name) : undefined,
      server_name: item.server_name ? String(item.server_name) : undefined,
      command: item.command ? String(item.command) : undefined,
      cwd: item.cwd ? String(item.cwd) : undefined,
      file_paths: Array.isArray(item.file_paths) ? item.file_paths.map((path) => String(path || "")).filter(Boolean) : undefined,
      query: item.query ? String(item.query) : undefined,
    }))
    .filter((item) => item.id && item.summary)
    .slice(-STREAMING_ACTIVITY_LOG_LIMIT));
  const hasRealActivities = normalized.some((item) => item.kind !== "queued" || item.summary !== "queued");
  return hasRealActivities
    ? normalized.filter((item) => item.kind !== "queued" || item.summary !== "queued")
    : normalized;
}

export function formatEventLine(ev: LedgerEvent): string {
  if (ev.kind === "chat.message" && ev.data && typeof ev.data === "object") {
    const msg = ev.data as ChatMessageData;
    return String(msg.text || "");
  }
  return "";
}

export function getMessageBubbleMotionClass({
  isStreaming,
  isOptimistic,
  streamPhase,
}: {
  isStreaming: boolean;
  isOptimistic: boolean;
  streamPhase?: string;
}): string {
  const phase = String(streamPhase || "").trim().toLowerCase();
  if (!isStreaming && !isOptimistic) return "";
  if (phase === "commentary") return "cccc-transient-bubble cccc-transient-bubble-commentary";
  return "cccc-transient-bubble";
}

export function getEffectiveStreamingActivities({
  streamId,
  actorId,
  pendingEventId,
  bucket,
  fallbackActivities,
}: {
  streamId: string;
  actorId: string;
  pendingEventId: string;
  bucket?: {
    streamingActivitiesByStreamId?: Record<string, StreamingActivity[]>;
    streamingEvents?: LedgerEvent[];
  } | null;
  fallbackActivities?: StreamingActivity[];
}): StreamingActivity[] {
  const normalizedFallback = Array.isArray(fallbackActivities) ? fallbackActivities : EMPTY_STREAMING_ACTIVITIES;
  const activitiesByStreamId = bucket?.streamingActivitiesByStreamId || {};
  const direct = streamId ? normalizeStreamingActivities(activitiesByStreamId[streamId]) : EMPTY_STREAMING_ACTIVITIES;
  if (direct.length > 0) return direct;
  if (normalizedFallback.length > 0) return normalizeStreamingActivities(normalizedFallback);
  if (streamId || !pendingEventId) return EMPTY_STREAMING_ACTIVITIES;

  const events = Array.isArray(bucket?.streamingEvents) ? (bucket?.streamingEvents || EMPTY_STREAMING_EVENTS) : EMPTY_STREAMING_EVENTS;

  const latestCandidate = events
    .filter((event) => {
      if (String(event.by || "").trim() !== actorId) return false;
      const data = event.data && typeof event.data === "object"
        ? event.data as { stream_id?: unknown; pending_event_id?: unknown }
        : {};
      const eventStreamId = String(data.stream_id || "").trim();
      const eventPendingEventId = String(data.pending_event_id || "").trim();
      if (streamId && eventStreamId === streamId) return true;
      if (pendingEventId && eventPendingEventId === pendingEventId) return true;
      return false;
    })
    .map((event, index) => {
      const data = event.data && typeof event.data === "object"
        ? event.data as { stream_id?: unknown; activities?: unknown }
        : {};
      const eventStreamId = String(data.stream_id || "").trim();
      const liveActivities = eventStreamId ? normalizeStreamingActivities(activitiesByStreamId[eventStreamId]) : EMPTY_STREAMING_ACTIVITIES;
      return {
        index,
        ts: String(event.ts || "").trim(),
        activities: liveActivities.length > 0 ? liveActivities : normalizeStreamingActivities(data.activities),
      };
    })
    .filter((item) => item.activities.length > 0)
    .sort((left, right) => {
      if (left.ts && right.ts && left.ts !== right.ts) return right.ts.localeCompare(left.ts);
      if (left.ts && !right.ts) return -1;
      if (!left.ts && right.ts) return 1;
      return right.index - left.index;
    })[0];

  if (latestCandidate?.activities?.length) return latestCandidate.activities;
  return EMPTY_STREAMING_ACTIVITIES;
}

export function isQueuedOnlyStreamingPlaceholder({
  isStreaming,
  messageText,
  liveStreamingText,
  blobAttachmentCount,
  presentationRefCount,
  activities,
}: {
  isStreaming: boolean;
  messageText: string;
  liveStreamingText: string;
  blobAttachmentCount: number;
  presentationRefCount: number;
  activities: StreamingActivity[];
}): boolean {
  if (!isStreaming) return false;
  if (String(messageText || "").trim()) return false;
  if (String(liveStreamingText || "").trim()) return false;
  if (blobAttachmentCount > 0 || presentationRefCount > 0) return false;
  if (activities.length !== 1) return false;
  const [activity] = activities;
  return activity.kind === "queued" && activity.summary === "queued";
}

export function getStreamingPlaceholderText({
  isQueuedOnlyPlaceholder,
  placeholderLabel,
}: {
  isQueuedOnlyPlaceholder: boolean;
  placeholderLabel: string;
}): string {
  if (isQueuedOnlyPlaceholder) return "queued";
  return String(placeholderLabel || "").trim() || "working";
}

export function shouldRenderStreamingStatusPanel({
  isStreaming,
  hasText,
  activities,
}: {
  isStreaming: boolean;
  hasText: boolean;
  activities: StreamingActivity[];
}): boolean {
  if (isStreaming) return true;
  if (activities.length > 0) return true;
  return !hasText;
}

export function shouldReserveStreamingStatusSpace({
  isStreaming,
  renderPhase,
}: {
  isStreaming: boolean;
  renderPhase: "pending" | "active" | "exiting" | "completed";
}): boolean {
  if (renderPhase === "completed") return false;
  return isStreaming || renderPhase === "exiting";
}

export function deriveStreamingRenderPhase({
  isStreaming,
  hasText,
  activities,
  previousPhase,
}: {
  isStreaming: boolean;
  hasText: boolean;
  activities: StreamingActivity[];
  previousPhase?: "pending" | "active" | "exiting" | "completed";
}): "pending" | "active" | "completed" {
  if (!isStreaming) return "completed";
  if (hasText || activities.length > 0) return "active";
  if (previousPhase === "active" || previousPhase === "exiting") return "active";
  return "pending";
}

export function getActivityTimelineTime(ts?: string): string {
  if (!ts) return "--:--:--";
  const parsed = new Date(ts);
  if (Number.isNaN(parsed.getTime())) return "--:--:--";
  return formatTime(parsed.toISOString());
}

export function getStreamingPendingDelayMs(startedAtMs: number | null, nowMs: number): number {
  if (startedAtMs == null) return 0;
  return Math.max(0, STREAMING_PENDING_MIN_MS - Math.max(0, nowMs - startedAtMs));
}
