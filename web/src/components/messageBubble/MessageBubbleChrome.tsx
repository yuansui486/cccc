import { useTranslation } from "react-i18next";
import type { LedgerEvent } from "../../types";
import { classNames } from "../../utils/classNames";
import { ActorAvatar } from "../ActorAvatar";

export function MessageMetadataHeader({
  mobile,
  isUserMessage,
  isDark,
  senderAccentTextClass,
  senderDisplayName,
  messageTimestamp,
  fullMessageTimestamp,
  toLabel,
  senderAvatarUrl,
  senderRuntime,
  avatarRingClassName,
}: {
  mobile?: boolean;
  isUserMessage: boolean;
  isDark: boolean;
  senderAccentTextClass?: string | null;
  senderDisplayName: string;
  messageTimestamp: string;
  fullMessageTimestamp: string;
  toLabel: string;
  senderAvatarUrl?: string;
  senderRuntime?: string;
  avatarRingClassName?: string;
}) {
  const senderTextClass = isUserMessage
    ? isDark
      ? "text-slate-300"
      : "text-gray-700"
    : senderAccentTextClass
      ? senderAccentTextClass
      : isDark
        ? "text-slate-300"
        : "text-gray-700";

  if (mobile) {
    return (
      <div
        className={classNames(
          "mb-1 flex min-w-0 items-center gap-2 sm:hidden",
          isUserMessage ? "justify-end" : "justify-start",
        )}
      >
        <ActorAvatar
          avatarUrl={senderAvatarUrl}
          runtime={senderRuntime}
          title={senderDisplayName}
          isUser={isUserMessage}
          isDark={isDark}
          accentRingClassName={avatarRingClassName}
          sizeClassName="h-6 w-6"
          textClassName="text-[10px]"
        />
        <span className={classNames("shrink-0 text-xs font-medium", senderTextClass)}>
          {senderDisplayName}
        </span>
        <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)]">
          <span title={fullMessageTimestamp}>{messageTimestamp}</span>
        </span>
        <span
          className={classNames("min-w-0 truncate text-[10px]", "text-[var(--color-text-tertiary)]")}
          title={`to ${toLabel}`}
        >
          to {toLabel}
        </span>
      </div>
    );
  }

  return (
    <div className="hidden min-w-0 items-center gap-2 px-1 sm:flex">
      <span
        className={classNames(
          "shrink-0 text-[11px] font-medium",
          isUserMessage
            ? isDark
              ? "text-[var(--color-text-secondary)]"
              : "text-gray-500"
            : senderAccentTextClass
              ? senderAccentTextClass
              : isDark
                ? "text-[var(--color-text-secondary)]"
                : "text-gray-500",
        )}
      >
        {senderDisplayName}
      </span>
      <span className="shrink-0 text-[10px] text-[var(--color-text-tertiary)]">
        <span title={fullMessageTimestamp}>{messageTimestamp}</span>
      </span>
      <span
        className={classNames("min-w-0 truncate text-[10px]", "text-[var(--color-text-tertiary)]")}
        title={`to ${toLabel}`}
      >
        to {toLabel}
      </span>
    </div>
  );
}

export function MessageFooter({
  readOnly,
  obligationSummary,
  ackSummary,
  visibleReadStatusEntries,
  readPreviewEntries,
  readPreviewOverflow,
  displayNameMap,
  isDark,
  replyRequired,
  copiedMessageText,
  copyableMessageText,
  onCopyMessageText,
  onShowRecipients,
  onCopyLink,
  onRelay,
  onReply,
  canReply,
  eventId,
  event,
}: {
  readOnly?: boolean;
  obligationSummary: { kind: "reply" | "ack"; done: number; total: number } | null;
  ackSummary: { done: number; total: number; needsUserAck: boolean } | null;
  visibleReadStatusEntries: readonly (readonly [string, boolean])[];
  readPreviewEntries: readonly (readonly [string, boolean])[];
  readPreviewOverflow: number;
  displayNameMap: Map<string, string>;
  isDark: boolean;
  replyRequired: boolean;
  copiedMessageText: boolean;
  copyableMessageText: string;
  onCopyMessageText: () => void;
  onShowRecipients: () => void;
  onCopyLink?: (eventId: string) => void;
  onRelay?: (ev: LedgerEvent) => void;
  onReply: () => void;
  canReply: boolean;
  eventId?: string;
  event: LedgerEvent;
}) {
  const { t } = useTranslation(["chat", "common"]);

  const renderRecipientStatus = () => (
    <div className="flex min-w-0 items-center gap-2">
      {readPreviewEntries.map(([id, cleared]) => (
        <span key={id} className="inline-flex min-w-0 items-center gap-1">
          <span className="max-w-[10ch] truncate">{displayNameMap.get(id) || id}</span>
          <span
            className={classNames(
              "text-[10px] font-semibold tracking-tight",
              cleared
                ? isDark ? "text-emerald-400" : "text-emerald-600"
                : isDark ? "text-slate-500" : "text-gray-500",
            )}
            aria-label={cleared ? t("read") : t("pending")}
          >
            {cleared ? "✓✓" : "✓"}
          </span>
        </span>
      ))}
      {readPreviewOverflow > 0 ? (
        <span className={classNames("text-[10px]", "text-[var(--color-text-tertiary)]")}>
          +{readPreviewOverflow}
        </span>
      ) : null}
    </div>
  );

  return (
    <div
      className={classNames(
        "mt-1 flex items-center gap-3 px-1 text-[10px] transition-opacity",
        (obligationSummary || ackSummary || visibleReadStatusEntries.length > 0 || replyRequired) ? "justify-between" : "justify-end",
        "opacity-85 group-hover:opacity-100",
        "text-[var(--color-text-tertiary)]",
      )}
    >
      {obligationSummary ? (
        readOnly ? (
          <div className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1">
            <span
              className={classNames(
                "text-[10px] font-semibold tracking-tight",
                obligationSummary.done >= obligationSummary.total
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-amber-600 dark:text-amber-400",
              )}
            >
              {obligationSummary.kind === "reply" ? t("reply") : t("ack")} {obligationSummary.done}/{obligationSummary.total}
            </span>
          </div>
        ) : (
          <button
            type="button"
            className={classNames(
              "touch-target-sm flex min-w-0 items-center gap-2 rounded-lg px-2 py-1",
              "hover:bg-black/8 dark:hover:bg-white/12",
            )}
            onClick={onShowRecipients}
            aria-label={t("showObligationStatus")}
          >
            <span
              className={classNames(
                "text-[10px] font-semibold tracking-tight",
                obligationSummary.done >= obligationSummary.total
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-amber-600 dark:text-amber-400",
              )}
            >
              {obligationSummary.kind === "reply" ? t("reply") : t("ack")} {obligationSummary.done}/{obligationSummary.total}
            </span>
          </button>
        )
      ) : ackSummary ? (
        readOnly ? (
          <div className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1">
            <span
              className={classNames(
                "text-[10px] font-semibold tracking-tight",
                ackSummary.done >= ackSummary.total
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-amber-600 dark:text-amber-400",
              )}
            >
              {t("ack")} {ackSummary.done}/{ackSummary.total}
            </span>
          </div>
        ) : (
          <button
            type="button"
            className={classNames(
              "touch-target-sm flex min-w-0 items-center gap-2 rounded-lg px-2 py-1",
              "hover:bg-black/8 dark:hover:bg-white/12",
            )}
            onClick={onShowRecipients}
            aria-label={t("showAckStatus")}
          >
            <span
              className={classNames(
                "text-[10px] font-semibold tracking-tight",
                ackSummary.done >= ackSummary.total
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-amber-600 dark:text-amber-400",
              )}
            >
              {t("ack")} {ackSummary.done}/{ackSummary.total}
            </span>
          </button>
        )
      ) : visibleReadStatusEntries.length > 0 ? (
        readOnly ? (
          <div className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1">
            {renderRecipientStatus()}
          </div>
        ) : (
          <button
            type="button"
            className={classNames(
              "touch-target-sm flex min-w-0 items-center gap-2 rounded-lg px-2 py-1",
              "hover:bg-black/8 dark:hover:bg-white/12",
            )}
            onClick={onShowRecipients}
            aria-label={t("showRecipientStatus")}
          >
            {renderRecipientStatus()}
          </button>
        )
      ) : null}

      {!obligationSummary && !ackSummary && replyRequired ? (
        <span className={classNames("text-[10px] font-semibold tracking-tight", "text-violet-700 dark:text-violet-300")}>
          {t("needReply")}
        </span>
      ) : null}

      {!readOnly ? (
        <div className="flex flex-wrap items-center gap-1.5">
          {copyableMessageText ? (
            <button
              type="button"
              className={classNames(
                "touch-target-sm rounded-lg px-2 py-1 text-[11px] font-medium transition-colors",
                "text-[var(--color-text-secondary)] hover:bg-black/8 hover:text-[var(--color-text-primary)] dark:hover:bg-white/12",
              )}
              onClick={() => void onCopyMessageText()}
              title={copiedMessageText ? t("common:copied") : t("copyText")}
            >
              {copiedMessageText ? t("common:copied") : t("copyText")}
            </button>
          ) : null}
          {eventId && onCopyLink ? (
            <button
              type="button"
              className={classNames(
                "touch-target-sm rounded-lg px-2 py-1 text-[11px] font-medium transition-colors",
                "text-[var(--color-text-secondary)] hover:bg-black/8 hover:text-[var(--color-text-primary)] dark:hover:bg-white/12",
              )}
              onClick={() => onCopyLink(eventId)}
              title={t("copyLink")}
            >
              {t("copyLink")}
            </button>
          ) : null}
          {eventId && onRelay ? (
            <button
              type="button"
              className={classNames(
                "touch-target-sm rounded-lg px-2 py-1 text-[11px] font-medium transition-colors",
                "text-[var(--color-text-secondary)] hover:bg-black/8 hover:text-[var(--color-text-primary)] dark:hover:bg-white/12",
              )}
              onClick={() => onRelay(event)}
              title={t("relayToGroup")}
            >
              {t("relay")}
            </button>
          ) : null}
          {canReply ? (
            <button
              type="button"
              className={classNames(
                "touch-target-sm rounded-lg px-2 py-1 text-[11px] font-medium transition-colors",
                "text-[var(--color-text-secondary)] hover:bg-black/8 hover:text-[var(--color-text-primary)] dark:hover:bg-white/12",
              )}
              onClick={onReply}
            >
              {t("reply")}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
