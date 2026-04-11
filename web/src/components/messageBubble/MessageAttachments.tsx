import type { MessageAttachment } from "../../types";
import { withAuthToken } from "../../services/api/base";
import { classNames } from "../../utils/classNames";
import { getAttachmentBlobName, hasRenderableAttachmentSource, isImageAttachment, isSvgAttachment } from "../../utils/messageAttachments";
import { FileIcon } from "../Icons";
import { ImagePreview } from "./ImagePreview";

function resolveAttachmentHref(attachment: MessageAttachment, blobGroupId: string): string {
  const previewUrl = String(attachment.local_preview_url || "").trim();
  if (previewUrl) return previewUrl;

  const downloadUrl = String(attachment.download_url || "").trim();
  if (downloadUrl) return downloadUrl;

  const blobName = getAttachmentBlobName(attachment);
  if (!blobName) return "";

  return withAuthToken(
    `/api/v1/groups/${encodeURIComponent(blobGroupId)}/blobs/${encodeURIComponent(blobName)}`
  );
}

export function MessageAttachments({
  attachments,
  blobGroupId,
  isUserMessage,
  isDark,
  attachmentKeyPrefix,
  downloadTitle,
}: {
  attachments: MessageAttachment[];
  blobGroupId: string;
  isUserMessage: boolean;
  isDark: boolean;
  attachmentKeyPrefix: string;
  downloadTitle: (name: string) => string;
}) {
  const imageAttachments = attachments.filter((attachment) => isImageAttachment(attachment));
  const fileAttachments = attachments.filter((attachment) => !isImageAttachment(attachment));

  if (attachments.length <= 0 || !blobGroupId) return null;

  return (
    <>
      {imageAttachments.length > 0 && (
        <div className="mt-3 flex max-w-full flex-wrap items-start gap-2">
          {imageAttachments.map((attachment, index) => {
            if (!hasRenderableAttachmentSource(attachment)) return null;
            const href = resolveAttachmentHref(attachment, blobGroupId);
            if (!href) return null;
            const blobName = getAttachmentBlobName(attachment);
            const label = attachment.title || blobName || "image";
            return (
              <ImagePreview
                key={`img:${attachmentKeyPrefix}:${index}`}
                href={href}
                alt={label}
                isSvg={isSvgAttachment(attachment)}
                isUserMessage={isUserMessage}
                isDark={isDark}
              />
            );
          })}
        </div>
      )}
      {fileAttachments.length > 0 && (
        <div className="mt-3 flex max-w-full flex-wrap items-start gap-2">
          {fileAttachments.map((attachment, index) => {
            if (!hasRenderableAttachmentSource(attachment)) return null;
            const href = resolveAttachmentHref(attachment, blobGroupId);
            if (!href) return null;
            const blobName = getAttachmentBlobName(attachment);
            const label = attachment.title || blobName || "file";
            return (
              <a
                key={`file:${attachmentKeyPrefix}:${index}`}
                href={href}
                className={classNames(
                  "inline-flex max-w-full items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors",
                  isUserMessage
                    ? "bg-blue-700/50 hover:bg-blue-700 text-white border border-blue-500"
                    : "glass-btn border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]",
                )}
                title={downloadTitle(label)}
                download
              >
                <FileIcon size={14} className="opacity-70 flex-shrink-0" />
                <span className="truncate">{label}</span>
              </a>
            );
          })}
        </div>
      )}
    </>
  );
}
