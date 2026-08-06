import type { ChatMessageData, LedgerEvent, StreamingActivity } from "../../types";

const ACTOR_ACCENT_BORDER_CLASSES: Record<string, string> = {
  "text-sky-300": "border-l-sky-400/80",
  "text-indigo-300": "border-l-indigo-400/80",
  "text-violet-300": "border-l-violet-400/80",
  "text-fuchsia-300": "border-l-fuchsia-400/80",
  "text-cyan-300": "border-l-cyan-400/80",
  "text-teal-300": "border-l-teal-400/80",
  "text-emerald-300": "border-l-emerald-400/80",
  "text-amber-300": "border-l-amber-400/80",
  "text-sky-700": "border-l-sky-500",
  "text-indigo-700": "border-l-indigo-500",
  "text-violet-700": "border-l-violet-500",
  "text-fuchsia-700": "border-l-fuchsia-500",
  "text-cyan-700": "border-l-cyan-500",
  "text-teal-700": "border-l-teal-500",
  "text-emerald-700": "border-l-emerald-500",
  "text-amber-700": "border-l-amber-500",
};

export function getActorAccentBorderClass(accentTextClass?: string | null): string {
  return ACTOR_ACCENT_BORDER_CLASSES[String(accentTextClass || "").trim()]
    || "border-l-[var(--glass-border-subtle)]";
}

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
  if (/^\[onecolleague\]\s+(Attachments|References):/m.test(value)) return false;
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
    case "error":
      return "error";
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
  isNewlyArrived,
  isUserMessage,
  streamPhase,
}: {
  isStreaming: boolean;
  isOptimistic: boolean;
  isNewlyArrived?: boolean;
  isUserMessage?: boolean;
  streamPhase?: string;
}): string {
  const phase = String(streamPhase || "").trim().toLowerCase();
  if (!isStreaming && !isOptimistic) {
    if (!isNewlyArrived) return "";
    return isUserMessage
      ? "onecolleague-message-bubble-enter onecolleague-message-bubble-enter-outgoing"
      : "onecolleague-message-bubble-enter onecolleague-message-bubble-enter-incoming";
  }
  if (phase === "commentary") return "onecolleague-transient-bubble onecolleague-transient-bubble-commentary";
  return "onecolleague-transient-bubble";
}
