import type { MessageAttachment } from "../types";

const IMAGE_ATTACHMENT_EXTENSIONS = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".svg",
  ".bmp",
  ".avif",
]);

export function normalizeAttachmentPath(value: string): string {
  return String(value || "").trim().replace(/\\/g, "/");
}

export function getAttachmentBlobName(attachment: MessageAttachment): string {
  const normalizedPath = normalizeAttachmentPath(String(attachment.path || ""));
  if (!normalizedPath) return "";
  const parts = normalizedPath.split("/");
  return parts[parts.length - 1] || "";
}

export function hasBlobAttachmentPath(attachment: MessageAttachment): boolean {
  return normalizeAttachmentPath(String(attachment.path || "")).startsWith("state/blobs/");
}

export function normalizeMessageAttachment(attachment: MessageAttachment): MessageAttachment {
  return {
    ...attachment,
    kind: String(attachment.kind || "file"),
    path: String(attachment.path || ""),
    title: String(attachment.title || ""),
    bytes: Number(attachment.bytes || 0),
    mime_type: String(attachment.mime_type || ""),
    local_preview_url: "local_preview_url" in attachment ? String(attachment.local_preview_url || "") : "",
    download_url: "download_url" in attachment ? String(attachment.download_url || "") : "",
    decryption_key: "decryption_key" in attachment ? String(attachment.decryption_key || "") : "",
    aeskey: "aeskey" in attachment ? String(attachment.aeskey || "") : "",
  };
}

function attachmentPathOrName(attachment: MessageAttachment): string {
  return normalizeAttachmentPath(String(attachment.path || attachment.title || "")).toLowerCase();
}

function attachmentExtension(attachment: MessageAttachment): string {
  const raw = attachmentPathOrName(attachment);
  const queryIndex = raw.indexOf("?");
  const clean = queryIndex >= 0 ? raw.slice(0, queryIndex) : raw;
  const dot = clean.lastIndexOf(".");
  if (dot < 0) return "";
  return clean.slice(dot);
}

export function isImageAttachment(attachment: MessageAttachment): boolean {
  const kind = String(attachment.kind || "").trim().toLowerCase();
  if (kind === "image") return true;
  const mime = String(attachment.mime_type || "").trim().toLowerCase();
  if (mime.startsWith("image/")) return true;
  return IMAGE_ATTACHMENT_EXTENSIONS.has(attachmentExtension(attachment));
}

export function isSvgAttachment(attachment: MessageAttachment): boolean {
  const mime = String(attachment.mime_type || "").trim().toLowerCase();
  if (mime === "image/svg+xml") return true;
  return attachmentExtension(attachment) === ".svg";
}

export function hasRenderableAttachmentSource(attachment: MessageAttachment): boolean {
  const previewUrl = String(attachment.local_preview_url || "").trim();
  if (previewUrl) return true;
  const downloadUrl = String(attachment.download_url || "").trim();
  if (downloadUrl) return true;
  return hasBlobAttachmentPath(attachment);
}

export function isRedundantWecomImagePlaceholder(
  text: string,
  attachments: MessageAttachment[],
  sourcePlatform?: string,
): boolean {
  if (String(sourcePlatform || "").trim().toLowerCase() !== "wecom") return false;
  if (!attachments.length || !attachments.every((attachment) => isImageAttachment(attachment))) return false;
  const normalized = String(text || "").trim().toLowerCase();
  return normalized === "[image]" || /^\[file(?:: [^\]]+)?\](?:\s+\S+)?$/.test(normalized);
}

export function getAttachmentAwareMessageText(
  text: string,
  attachments: MessageAttachment[],
  sourcePlatform?: string,
): string {
  if (isRedundantWecomImagePlaceholder(text, attachments, sourcePlatform)) {
    return "";
  }
  return String(text || "");
}
