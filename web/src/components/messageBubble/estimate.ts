import type { LedgerEvent, MessageAttachment } from "../../types";

const DEFAULT_MESSAGE_HEIGHT = 72;
const DEFAULT_STREAMING_HEIGHT = 108;
const DEFAULT_QUEUED_PLACEHOLDER_HEIGHT = 84;
const DEFAULT_REPLY_CONTEXT_HEIGHT = 60;
const DEFAULT_IMAGE_ATTACHMENT_HEIGHT = 200;
const DEFAULT_FILE_ATTACHMENT_HEIGHT = 48;
const DEFAULT_CODE_BLOCK_HEIGHT = 80;
const AVG_CHARS_PER_LINE = 40;
const MAX_TEXT_HEIGHT = 400;

function getEstimatedTextHeight(text: string): number {
  if (!text) return 0;
  const lineCount = text.split("\n").length;
  const wrapLines = Math.ceil(text.length / AVG_CHARS_PER_LINE);
  const estimatedLines = Math.max(lineCount, wrapLines);
  return Math.min(estimatedLines * 20, MAX_TEXT_HEIGHT);
}

function getEstimatedAttachmentHeight(attachments: MessageAttachment[]): number {
  let total = 0;
  for (const attachment of attachments) {
    const mime = String(attachment?.mime_type || "");
    total += mime.startsWith("image/") ? DEFAULT_IMAGE_ATTACHMENT_HEIGHT : DEFAULT_FILE_ATTACHMENT_HEIGHT;
  }
  return total;
}

export function estimateMessageRowHeight(message: LedgerEvent | undefined, options?: { collapseHeader?: boolean }): number {
  if (!message) return 100;

  const data = message.data as {
    text?: string;
    attachments?: MessageAttachment[];
    quote_text?: string;
    activities?: Array<{ kind?: string; summary?: string }>;
  } | undefined;

  const text = String(data?.text || "");
  const attachments = Array.isArray(data?.attachments) ? data.attachments : [];
  const quoteText = String(data?.quote_text || "");
  const activities = Array.isArray(data?.activities) ? data.activities : [];

  const headerOffset = options?.collapseHeader ? -28 : 0;

  if (message._streaming) {
    const isQueuedOnlyPlaceholder =
      !text.trim() &&
      attachments.length === 0 &&
      activities.length === 1 &&
      String(activities[0]?.kind || "") === "queued" &&
      String(activities[0]?.summary || "") === "queued";

    if (isQueuedOnlyPlaceholder) {
      return Math.max(56, DEFAULT_QUEUED_PLACEHOLDER_HEIGHT + headerOffset);
    }
    return Math.max(72, DEFAULT_STREAMING_HEIGHT + getEstimatedTextHeight(text) + headerOffset);
  }

  let height = DEFAULT_MESSAGE_HEIGHT;
  height += getEstimatedTextHeight(text);

  const codeBlockCount = (text.match(/```/g) || []).length / 2;
  if (codeBlockCount > 0) {
    height += codeBlockCount * DEFAULT_CODE_BLOCK_HEIGHT;
  }

  height += getEstimatedAttachmentHeight(attachments);

  if (quoteText.trim()) {
    height += DEFAULT_REPLY_CONTEXT_HEIGHT;
  }

  return Math.max(64, height + headerOffset);
}
