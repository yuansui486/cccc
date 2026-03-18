import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from 'react-i18next';

import {
    useFloating,
    autoUpdate,
    offset,
    flip,
    shift,
    useHover,
    useInteractions,
    useDismiss,
    FloatingPortal,
} from "@floating-ui/react";
import { LedgerEvent, Actor, AgentState, getActorAccentColor, ChatMessageData, EventAttachment } from "../types";
import { formatFullTime, formatTime } from "../utils/time";
import { classNames } from "../utils/classNames";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { getRecipientDisplayName } from "../hooks/useActorDisplayName";
import { formatRecipientList, getRecipientDisplayLabel } from "../utils/displayText";
import { ImageIcon, FileIcon, CloseIcon } from "./Icons";

const RUNTIME_LOGO_BASE = import.meta.env.BASE_URL;
const RUNTIME_LOGO: Record<string, string> = {
    claude: `${RUNTIME_LOGO_BASE}logos/claude.png`,
    codex: `${RUNTIME_LOGO_BASE}logos/codex.png`,
    gemini: `${RUNTIME_LOGO_BASE}logos/gemini.png`,
};

function resolveSenderActor(actors: Actor[], senderId: string): Actor | null {
    const key = String(senderId || "").trim();
    if (!key) return null;

    const exactId = actors.find((actor) => String(actor.id || "").trim() === key);
    if (exactId) return exactId;

    // 兼容历史消息：旧 `by` 可能已经不是现行 actor id，但仍等于当前 title。
    const exactTitle = actors.find((actor) => String(actor.title || "").trim() === key);
    if (exactTitle) return exactTitle;

    const lower = key.toLowerCase();
    return actors.find((actor) => {
        const actorId = String(actor.id || "").trim().toLowerCase();
        const actorTitle = String(actor.title || "").trim().toLowerCase();
        return actorId === lower || actorTitle === lower;
    }) || null;
}


function formatEventLine(ev: LedgerEvent): string {
    if (ev.kind === "chat.message" && ev.data && typeof ev.data === "object") {
        const msg = ev.data as ChatMessageData;
        return String(msg.text || "");
    }
    return "";
}

// Image preview component with loading state and error handling
function ImagePreview({
    href,
    alt,
    isUserMessage,
    isDark,
}: {
    href: string;
    alt: string;
    isUserMessage: boolean;
    isDark: boolean;
}) {
    const [loadError, setLoadError] = useState(false);
    const [isLightboxOpen, setIsLightboxOpen] = useState(false);
    const { t } = useTranslation('chat');

    useEffect(() => {
        if (!isLightboxOpen) {
            return undefined;
        }

        // 支持 ESC 关闭，保持图片预览可快速退出。
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setIsLightboxOpen(false);
            }
        };

        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [isLightboxOpen]);

    if (loadError) {
        // Fallback to file download link on error
        return (
            <a
                href={href}
                className={classNames(
                    "inline-flex items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors max-w-full",
                    isUserMessage
                        ? "bg-blue-700/50 hover:bg-blue-700 text-white border border-blue-500"
                        : isDark
                            ? "bg-slate-900/50 hover:bg-slate-900 text-slate-300 border border-slate-700"
                            : "bg-gray-50 hover:bg-gray-100 text-gray-700 border border-gray-200"
                )}
                title={t('download', { name: alt })}
                download
            >
                <ImageIcon size={14} className="opacity-70 flex-shrink-0" />
                <span className="truncate">{alt}</span>
            </a>
        );
    }

    return (
        <>
            <button
                type="button"
                className="group block max-w-full overflow-hidden rounded-lg"
                onClick={() => setIsLightboxOpen(true)}
                aria-label={t('openImagePreview', { name: alt })}
                title={t('openImagePreview', { name: alt })}
            >
                <img
                    src={href}
                    alt={alt}
                    className="max-w-full max-h-64 cursor-zoom-in object-contain rounded-lg transition-opacity group-hover:opacity-95 sm:max-h-80"
                    loading="lazy"
                    onError={() => setLoadError(true)}
                />
            </button>

            {isLightboxOpen && (
                <FloatingPortal>
                    <div className="fixed inset-0 z-[80] flex items-center justify-center p-3 sm:p-6 animate-fade-in">
                        <button
                            type="button"
                            className={classNames(
                                "absolute inset-0",
                                "glass-overlay"
                            )}
                            onClick={() => setIsLightboxOpen(false)}
                            aria-label={t('common:close')}
                        />

                        <div
                            className={classNames(
                                "relative z-[81] flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border shadow-2xl",
                                "glass-modal"
                            )}
                            role="dialog"
                            aria-modal="true"
                            aria-label={t('imagePreviewDialog')}
                            onClick={(event) => event.stopPropagation()}
                        >
                            <div className={classNames(
                                "flex items-center justify-between gap-3 border-b px-4 py-3",
                                "border-[var(--glass-border-subtle)]"
                            )}>
                                <div className="min-w-0">
                                    <p className={classNames(
                                        "truncate text-sm font-medium",
                                        "text-[var(--color-text-primary)]"
                                    )}>
                                        {alt}
                                    </p>
                                    <p className={classNames(
                                        "text-xs",
                                        "text-[var(--color-text-tertiary)]"
                                    )}>
                                        {t('imagePreviewHint')}
                                    </p>
                                </div>

                                <div className="flex items-center gap-2">
                                    <a
                                        href={href}
                                        download
                                        className={classNames(
                                            "inline-flex items-center rounded-lg px-3 py-2 text-xs font-medium transition-colors",
                                            isDark
                                                ? "bg-slate-800 text-slate-100 hover:bg-slate-700"
                                                : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                                        )}
                                        title={t('download', { name: alt })}
                                    >
                                        {t('download', { name: alt })}
                                    </a>

                                    <button
                                        type="button"
                                        onClick={() => setIsLightboxOpen(false)}
                                        className={classNames(
                                            "inline-flex items-center justify-center rounded-lg p-2 transition-colors",
                                            isDark
                                                ? "text-slate-300 hover:bg-slate-800 hover:text-slate-100"
                                                : "text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                                        )}
                                        aria-label={t('common:close')}
                                    >
                                        <CloseIcon size={18} />
                                    </button>
                                </div>
                            </div>

                            <div className="flex items-center justify-center overflow-auto p-4 sm:p-6">
                                <img
                                    src={href}
                                    alt={alt}
                                    className="max-h-[75vh] w-auto max-w-full rounded-xl object-contain"
                                />
                            </div>
                        </div>
                    </div>
                </FloatingPortal>
            )}
        </>
    );
}

export interface MessageBubbleProps {
    event: LedgerEvent;
    actors: Actor[];
    displayNameMap: Map<string, string>;
    agentState: AgentState | null;
    isDark: boolean;
    readOnly?: boolean;
    groupId: string;
    groupLabelById: Record<string, string>;
    isHighlighted?: boolean;
    onReply: () => void;
    onShowRecipients: () => void;
    onCopyLink?: (eventId: string) => void;
    onRelay?: (ev: LedgerEvent) => void;
    onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
}

export const MessageBubble = memo(function MessageBubble({
    event: ev,
    actors,
    displayNameMap,
    agentState,
    isDark,
    readOnly,
    groupId,
    groupLabelById,
    isHighlighted,
    onReply,
    onShowRecipients,
    onCopyLink,
    onRelay,
    onOpenSource,
}: MessageBubbleProps) {
    const isUserMessage = ev.by === "user";
    const isOptimistic = !!(ev.data as Record<string, unknown> | undefined)?._optimistic;
    const senderAccent = !isUserMessage ? getActorAccentColor(String(ev.by || ""), isDark) : null;

    // Floating UI for agent-state tooltip
    const [isAgentStateOpen, setIsAgentStateOpen] = useState(false);

    const canShowAgentState = useMemo(() => {
        if (isUserMessage) return false;
        const id = String(ev.by || "");
        if (!id) return false;
        return true;
    }, [ev.by, isUserMessage]);

    const { refs, floatingStyles, context } = useFloating({
        open: isAgentStateOpen && canShowAgentState,
        onOpenChange: setIsAgentStateOpen,
        placement: "bottom-start",
        middleware: [
            offset(10),
            flip({ fallbackPlacements: ["top-start", "bottom-end", "top-end"] }),
            shift({ padding: 10 }),
        ],
        whileElementsMounted: autoUpdate,
    });

    const setAgentStateReference = useCallback(
        (node: HTMLElement | null) => {
            refs.setReference(node);
        },
        [refs]
    );

    const setAgentStateFloating = useCallback(
        (node: HTMLElement | null) => {
            refs.setFloating(node);
        },
        [refs]
    );

    const hover = useHover(context, {
        delay: { open: 100, close: 150 },
        enabled: canShowAgentState,
    });
    const dismiss = useDismiss(context);
    const { getReferenceProps, getFloatingProps } = useInteractions([hover, dismiss]);
    const { t } = useTranslation('chat');
    const agentStateText = String(agentState?.hot?.focus || "").trim();
    const agentStateDisplay = agentStateText || t('noAgentStateYet');
    const stateTask = String(agentState?.hot?.active_task_id || "").trim();
    const stateNext = String(agentState?.hot?.next_action || "").trim();
    const stateChanged = String(agentState?.warm?.what_changed || "").trim();
    const blockerCount = Array.isArray(agentState?.hot?.blockers) ? agentState.hot.blockers.length : 0;


    // Treat data as ChatMessageData.
    const msgData = ev.data as ChatMessageData | undefined;
    const quoteText = msgData?.quote_text;
    const isAttention = String(msgData?.priority || "normal") === "attention";
    const replyRequired = !!msgData?.reply_required;
    const srcGroupId = typeof msgData?.src_group_id === "string" ? String(msgData.src_group_id || "").trim() : "";
    const srcEventId = typeof msgData?.src_event_id === "string" ? String(msgData.src_event_id || "").trim() : "";
    const hasSource = !!(srcGroupId && srcEventId);
    const dstGroupId = typeof msgData?.dst_group_id === "string" ? String(msgData.dst_group_id || "").trim() : "";
    const dstTo = useMemo(() => {
        const raw = msgData?.dst_to;
        if (!Array.isArray(raw)) return [];
        return raw.map((t) => String(t || "").trim()).filter((t) => t);
    }, [msgData?.dst_to]);
    const hasDestination = !!dstGroupId;
    const rawAttachments: EventAttachment[] = Array.isArray(msgData?.attachments) ? msgData.attachments : [];
    const blobAttachments = rawAttachments
        .filter((a): a is EventAttachment => a != null && typeof a === "object")
        .map((a) => ({
            kind: String(a.kind || "file"),
            path: String(a.path || ""),
            title: String(a.title || ""),
            bytes: Number(a.bytes || 0),
            mime_type: String(a.mime_type || ""),
        }))
        .filter((a) => a.path.startsWith("state/blobs/"));

    // Use event's group_id for blob URLs (attachments are stored in the event's original group)
    const blobGroupId = String(ev.group_id || "").trim() || groupId;

    const readStatus = ev._read_status;
    const ackStatus = ev._ack_status;
    const recipients = msgData?.to;

    const visibleReadStatusEntries = useMemo(() => {
        if (!readStatus) return [];
        return actors
            .map((a) => String(a.id || ""))
            .filter((id) => id && Object.prototype.hasOwnProperty.call(readStatus, id))
            .map((id) => [id, !!readStatus[id]] as const);
    }, [actors, readStatus]);

    const isDirectUserMessage = useMemo(() => {
        if (isUserMessage) return false;
        if (!Array.isArray(recipients)) return false;
        const ids = recipients.map((id) => String(id || "").trim()).filter((id) => id);
        return ids.length === 1 && ids[0] === "user";
    }, [isUserMessage, recipients]);

    const hideDirectUserObligationSummary = useMemo(() => {
        if (isUserMessage) return false;
        const os = ev._obligation_status;
        if (os && typeof os === "object") {
            const ids = Object.keys(os);
            return ids.length === 1 && ids[0] === "user";
        }
        if (ackStatus && typeof ackStatus === "object") {
            const ids = Object.keys(ackStatus);
            return ids.length === 1 && ids[0] === "user";
        }
        return isDirectUserMessage;
    }, [ackStatus, ev._obligation_status, isDirectUserMessage, isUserMessage]);

    const ackSummary = useMemo(() => {
        if (hideDirectUserObligationSummary) return null;
        if ((!isAttention && !replyRequired) || !ackStatus || typeof ackStatus !== "object") return null;
        const ids = Object.keys(ackStatus);
        if (ids.length === 0) return null;
        const done = ids.reduce((n, id) => n + (ackStatus[id] ? 1 : 0), 0);
        const needsUserAck =
            Object.prototype.hasOwnProperty.call(ackStatus, "user") && !ackStatus["user"] && !isUserMessage;
        return { done, total: ids.length, needsUserAck };
    }, [ackStatus, hideDirectUserObligationSummary, isAttention, isUserMessage, replyRequired]);

    const obligationSummary = useMemo(() => {
        if (hideDirectUserObligationSummary) return null;
        const os = ev._obligation_status;
        if (!os || typeof os !== "object") return null;
        const ids = Object.keys(os);
        if (ids.length === 0) return null;

        const requiresReply = ids.some((id) => !!os[id]?.reply_required);
        if (requiresReply) {
            const done = ids.reduce((n, id) => n + (os[id]?.replied ? 1 : 0), 0);
            return { kind: "reply" as const, done, total: ids.length };
        }
        const done = ids.reduce((n, id) => n + (os[id]?.acked ? 1 : 0), 0);
        return { kind: "ack" as const, done, total: ids.length };
    }, [ev._obligation_status, hideDirectUserObligationSummary]);

    const toLabel = useMemo(() => {
        if (hasDestination) {
            const dstLabel = String(groupLabelById?.[dstGroupId] || "").trim() || dstGroupId;
            const dstToLabel = formatRecipientList(dstTo, displayNameMap);
            return `${dstLabel} · ${dstToLabel}`;
        }
        return formatRecipientList(recipients || [], displayNameMap);
    }, [displayNameMap, dstGroupId, dstTo, groupLabelById, hasDestination, recipients]);

    // Sender display name (use title if available)
    const senderActor = useMemo(() => {
        if (isUserMessage) return null;
        return resolveSenderActor(actors, String(ev.by || ""));
    }, [actors, ev.by, isUserMessage]);

    const senderDisplayName = useMemo(() => {
        const by = String(ev.by || "");
        if (!by) return by;
        if (by === "user") return getRecipientDisplayLabel(by, displayNameMap);
        return String(senderActor?.title || "").trim() || displayNameMap.get(by) || by;
    }, [displayNameMap, ev.by, senderActor]);

    // Sender runtime logo path (for actor avatars)
    const senderLogoSrc = useMemo(() => {
        if (isUserMessage) return null;
        const runtime = String(senderActor?.runtime || "").toLowerCase();
        if (!runtime) return null;
        return RUNTIME_LOGO[runtime] || null;
    }, [isUserMessage, senderActor]);

    const readPreviewEntries = visibleReadStatusEntries.slice(0, 3);
    const readPreviewOverflow = Math.max(0, visibleReadStatusEntries.length - readPreviewEntries.length);

    return (
        <div
            className={classNames(
                "flex gap-2 sm:gap-3 group",
                isUserMessage
                    ? "flex-col items-end sm:items-start sm:flex-row-reverse"
                    : "flex-col items-start sm:flex-row",
                isOptimistic ? "opacity-95" : ""
            )}
        >
            {/* Desktop Avatar (Hidden on mobile) */}
            <div
                className={classNames(
                    "hidden sm:flex flex-shrink-0 w-8 h-8 rounded-full items-center justify-center text-xs font-bold shadow-sm mt-1 overflow-hidden",
                    isUserMessage
                        ? "bg-gradient-to-br from-blue-500 to-blue-600 text-white"
                        : isDark
                            ? "bg-slate-700 text-slate-200"
                            : "bg-white border border-gray-200 text-gray-700",
                    !isUserMessage && senderAccent ? `ring-1 ring-inset ${senderAccent.ring}` : ""
                )}
                ref={setAgentStateReference}
                {...getReferenceProps()}
            >
                {senderLogoSrc
                    ? <img src={senderLogoSrc} alt="" className="w-full h-full object-cover" />
                    : isUserMessage ? "U" : (senderDisplayName || "?")[0].toUpperCase()}
            </div>

            {/* Message Content */}
            <div
                className={classNames(
                    "flex flex-col w-full sm:w-auto sm:max-w-[75%] min-w-0",
                    isUserMessage ? "items-end" : "items-start"
                )}
            >
                {/* Mobile Header Row (Visible only on mobile) */}
                <div
                    className={classNames(
                        "flex items-center gap-2 mb-1 sm:hidden min-w-0",
                        isUserMessage ? "justify-end" : "justify-start"
                    )}
                >
                    <div
                        className={classNames(
                            "flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold shadow-sm overflow-hidden",
                            isUserMessage
                                ? "bg-gradient-to-br from-blue-500 to-blue-600 text-white"
                                : isDark
                                    ? "bg-slate-700 text-slate-200"
                                    : "bg-white border border-gray-200 text-gray-700",
                            !isUserMessage && senderAccent ? `ring-1 ring-inset ${senderAccent.ring}` : ""
                        )}
                    >
                        {senderLogoSrc
                            ? <img src={senderLogoSrc} alt="" className="w-full h-full object-cover" />
                            : isUserMessage ? "U" : (senderDisplayName || "?")[0].toUpperCase()}
                    </div>
                    <span
                        className={classNames(
                            "text-xs font-medium flex-shrink-0",
                            isUserMessage
                                ? isDark
                                    ? "text-slate-300"
                                    : "text-gray-700"
                                : senderAccent
                                    ? senderAccent.text
                                    : isDark
                                        ? "text-slate-300"
                                        : "text-gray-700"
                        )}
                    >
                        {senderDisplayName}
                    </span>
                    <span className={`text-[10px] flex-shrink-0 text-[var(--color-text-muted)]`}>
                        {formatTime(ev.ts)}
                    </span>
                    <span
                        className={classNames(
                            "text-[10px] min-w-0 truncate",
                            "text-[var(--color-text-muted)]"
                        )}
                        title={t("toRecipient", { label: toLabel })}
                    >
                        {t("toRecipient", { label: toLabel })}
                    </span>
                </div>

                {/* Desktop Metadata Header (Hidden on mobile) */}
                <div className="hidden sm:flex items-center gap-2 mb-1 px-1 min-w-0">
                    <span
                        className={classNames(
                            "text-[11px] font-medium flex-shrink-0",
                            isUserMessage
                                ? isDark
                                    ? "text-slate-400"
                                    : "text-gray-500"
                                : senderAccent
                                    ? senderAccent.text
                                    : isDark
                                        ? "text-slate-400"
                                        : "text-gray-500"
                        )}
                    >
                        {senderDisplayName}
                    </span>
                    <span className={`text-[10px] flex-shrink-0 text-[var(--color-text-muted)]`}>
                        {formatTime(ev.ts)}
                    </span>
                    <span className={classNames("text-[10px] min-w-0 truncate", "text-[var(--color-text-muted)]")} title={t("toRecipient", { label: toLabel })}>
                        {t("toRecipient", { label: toLabel })}
                    </span>
                </div>

                {/* Bubble wrapper (allows badge to overflow) */}
                <div className="relative max-w-[85vw] sm:max-w-full min-w-0">
                    {isAttention && (
                        <span
                            className={classNames(
                                "absolute -top-2 z-10 text-[10px] font-semibold px-2 py-0.5 rounded-full border shadow-sm",
                                isUserMessage ? "left-3" : "right-3",
                                "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200 border-amber-300 dark:border-amber-700"
                            )}
                        >
                            {t('important')}
                        </span>
                    )}
                <div
                    className={classNames(
                        "px-4 py-2.5 text-sm leading-relaxed",
                        isUserMessage
                            ? "bg-blue-600 text-white rounded-2xl rounded-tr-none shadow-sm"
                            : "glass-bubble rounded-2xl rounded-tl-none text-[var(--color-text-primary)]"
                        ,
                        isAttention ? "ring-1 ring-amber-400/40 dark:ring-amber-500/40" : ""
                        ,
                        isHighlighted ? "outline outline-2 outline-sky-500/30 outline-offset-2" : ""
                    )}
                >

                    {/* Cross-group relay provenance */}
                    {hasSource ? (
                        <button
                            type="button"
                            className={classNames(
                                "mb-2 inline-flex items-center gap-2 text-xs font-medium rounded-lg px-2 py-1 border",
                                "glass-btn border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]",
                                onOpenSource ? "cursor-pointer" : "cursor-default"
                            )}
                            onClick={() => onOpenSource?.(srcGroupId, srcEventId)}
                            disabled={!onOpenSource}
                            title={t('openOriginalMessage')}
                        >
                            <span className="opacity-70">↗</span>
                            <span className="truncate">
                                {t('relayedFrom', { groupId: srcGroupId, eventId: srcEventId.slice(0, 8) })}
                            </span>
                        </button>
                    ) : null}
                    {/* Outbound cross-group send record */}
                    {hasDestination ? (() => {
                        const dstLabel = String(groupLabelById?.[dstGroupId] || "").trim() || dstGroupId;
                        const dstToLabel = formatRecipientList(dstTo, displayNameMap);
                        return (
                            <div
                                className={classNames(
                                    "mb-2 inline-flex items-center gap-2 text-xs font-medium rounded-lg px-2 py-1 border",
                                    "glass-btn border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]"
                                )}
                                title={t('sentTo', { label: dstGroupId, to: dstToLabel })}
                            >
                                <span className="opacity-70">↗</span>
                                <span className="truncate">
                                    {t('sentTo', { label: dstLabel, to: dstToLabel })}
                                </span>
                            </div>
                        );
                    })() : null}
                    {/* Reply Context */}
                    {quoteText && (
                        <div
                            className={`mb-2 text-xs border-l-2 pl-2 italic truncate opacity-80 ${isUserMessage ? "border-blue-400" : "border-[var(--glass-border-subtle)]"
                                }`}
                        >
                            "{quoteText}"
                        </div>
                    )}

                    {/* Text Content */}
                    <MarkdownRenderer
                        content={formatEventLine(ev)}
                        isDark={isDark}
                        invertText={isUserMessage}
                        className="break-words [overflow-wrap:anywhere] max-w-full"
                    />

                    {/* Attachments */}
                    {blobAttachments.length > 0 && blobGroupId && (() => {
                        const imageAttachments = blobAttachments.filter((a) =>
                            a.mime_type.startsWith("image/")
                        );
                        const fileAttachments = blobAttachments.filter((a) =>
                            !a.mime_type.startsWith("image/")
                        );
                        return (
                            <>
                                {/* Image previews */}
                                {imageAttachments.length > 0 && (
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        {imageAttachments.map((a, i) => {
                                            const parts = a.path.split("/");
                                            const blobName = parts[parts.length - 1] || "";
                                            const href = `/api/v1/groups/${encodeURIComponent(blobGroupId)}/blobs/${encodeURIComponent(blobName)}`;
                                            const label = a.title || blobName;
                                            return (
                                                <ImagePreview
                                                    key={`img-${blobName}:${i}`}
                                                    href={href}
                                                    alt={label}
                                                    isUserMessage={isUserMessage}
                                                    isDark={isDark}
                                                />
                                            );
                                        })}
                                    </div>
                                )}
                                {/* File attachments */}
                                {fileAttachments.length > 0 && (
                                    <div className="mt-3 flex flex-wrap gap-2">
                                        {fileAttachments.map((a, i) => {
                                            const parts = a.path.split("/");
                                            const blobName = parts[parts.length - 1] || "";
                                            const href = `/api/v1/groups/${encodeURIComponent(blobGroupId)}/blobs/${encodeURIComponent(blobName)}`;
                                            const label = a.title || blobName || "file";
                                            return (
                                                <a
                                                    key={`file-${blobName}:${i}`}
                                                    href={href}
                                                    className={classNames(
                                                        "inline-flex items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors max-w-full",
                                                        isUserMessage
                                                            ? "bg-blue-700/50 hover:bg-blue-700 text-white border border-blue-500"
                                                            : "glass-btn border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]"
                                                    )}
                                                    title={t('download', { name: label })}
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
                    })()}
                </div>
                </div>

                <div
                    className={classNames(
                        "flex items-center gap-3 mt-1 px-1 text-[10px] transition-opacity",
                        (obligationSummary || ackSummary || visibleReadStatusEntries.length > 0 || replyRequired) ? "justify-between" : "justify-end",
                        "opacity-70 group-hover:opacity-100",
                        "text-[var(--color-text-muted)]"
                    )}
                >
                    {obligationSummary ? (
                        readOnly ? (
                            <div className="flex items-center gap-2 min-w-0 rounded-lg px-2 py-1">
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        obligationSummary.done >= obligationSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {obligationSummary.kind === "reply" ? t('reply') : t('ack')} {obligationSummary.done}/{obligationSummary.total}
                                </span>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm flex items-center gap-2 min-w-0 rounded-lg px-2 py-1",
                                    "hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={onShowRecipients}
                                aria-label={t('showObligationStatus')}
                            >
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        obligationSummary.done >= obligationSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {obligationSummary.kind === "reply" ? t('reply') : t('ack')} {obligationSummary.done}/{obligationSummary.total}
                                </span>
                            </button>
                        )
                    ) : ackSummary ? (
                        readOnly ? (
                            <div className="flex items-center gap-2 min-w-0 rounded-lg px-2 py-1">
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        ackSummary.done >= ackSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {t('ack')} {ackSummary.done}/{ackSummary.total}
                                </span>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm flex items-center gap-2 min-w-0 rounded-lg px-2 py-1",
                                    "hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={onShowRecipients}
                                aria-label={t('showAckStatus')}
                            >
                                <span
                                    className={classNames(
                                        "text-[10px] font-semibold tracking-tight",
                                        ackSummary.done >= ackSummary.total
                                            ? "text-emerald-600 dark:text-emerald-400"
                                            : "text-amber-600 dark:text-amber-400"
                                    )}
                                >
                                    {t('ack')} {ackSummary.done}/{ackSummary.total}
                                </span>
                            </button>
                        )
                    ) : visibleReadStatusEntries.length > 0 ? (
                        readOnly ? (
                            <div className="flex items-center gap-2 min-w-0 rounded-lg px-2 py-1">
                                <div className="flex items-center gap-2 min-w-0">
                                    {readPreviewEntries.map(([id, cleared]) => (
                                        <span key={id} className="inline-flex items-center gap-1 min-w-0">
                                            <span className="truncate max-w-[10ch]">{displayNameMap.get(id) || id}</span>
                                            <span
                                                className={classNames(
                                                    "text-[10px] font-semibold tracking-tight",
                                                    cleared
                                                        ? isDark
                                                            ? "text-emerald-400"
                                                            : "text-emerald-600"
                                                        : isDark
                                                            ? "text-slate-500"
                                                            : "text-gray-500"
                                                )}
                                                aria-label={cleared ? t('read') : t('pending')}
                                            >
                                                {cleared ? "✓✓" : "✓"}
                                            </span>
                                        </span>
                                    ))}
                                    {readPreviewOverflow > 0 && (
                                        <span className={classNames("text-[10px]", "text-[var(--color-text-muted)]")}>
                                            +{readPreviewOverflow}
                                        </span>
                                    )}
                                </div>
                            </div>
                        ) : (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm flex items-center gap-2 min-w-0 rounded-lg px-2 py-1",
                                    "hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={onShowRecipients}
                                aria-label={t('showRecipientStatus')}
                            >
                                <div className="flex items-center gap-2 min-w-0">
                                    {readPreviewEntries.map(([id, cleared]) => (
                                        <span key={id} className="inline-flex items-center gap-1 min-w-0">
                                            <span className="truncate max-w-[10ch]">{displayNameMap.get(id) || id}</span>
                                            <span
                                                className={classNames(
                                                    "text-[10px] font-semibold tracking-tight",
                                                    cleared
                                                        ? isDark
                                                            ? "text-emerald-400"
                                                            : "text-emerald-600"
                                                        : isDark
                                                            ? "text-slate-500"
                                                            : "text-gray-500"
                                                )}
                                                aria-label={cleared ? t('read') : t('pending')}
                                            >
                                                {cleared ? "✓✓" : "✓"}
                                            </span>
                                        </span>
                                    ))}
                                    {readPreviewOverflow > 0 && (
                                        <span className={classNames("text-[10px]", "text-[var(--color-text-muted)]")}>
                                            +{readPreviewOverflow}
                                        </span>
                                    )}
                                </div>
                            </button>
                        )
                    ) : null}

                    {!obligationSummary && !ackSummary && replyRequired && (
                        <span
                            className={classNames(
                                "text-[10px] font-semibold tracking-tight",
                                "text-violet-700 dark:text-violet-300"
                            )}
                        >
                            {t('needReply')}
                        </span>
                    )}

                    {!readOnly && (
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {ev.id && onCopyLink ? (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm px-2 py-1 rounded-lg text-[11px] font-medium transition-colors",
                                    "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={() => onCopyLink(String(ev.id))}
                                title={t('copyLink')}
                            >
                                {t('copyLink')}
                            </button>
                        ) : null}
                        {ev.id && onRelay ? (
                            <button
                                type="button"
                                className={classNames(
                                    "touch-target-sm px-2 py-1 rounded-lg text-[11px] font-medium transition-colors",
                                    "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                                )}
                                onClick={() => onRelay(ev)}
                                title={t('relayToGroup')}
                            >
                                {t('relay')}
                            </button>
                        ) : null}
                        <button
                            type="button"
                            className={classNames(
                                "touch-target-sm px-2 py-1 rounded-lg text-[11px] font-medium transition-colors",
                                "text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                            )}
                            onClick={onReply}
                        >
                            {t('reply')}
                        </button>
                      </div>
                    )}
                </div>
            </div>

            {isAgentStateOpen && canShowAgentState && (
                <FloatingPortal>
                    <div
                        ref={setAgentStateFloating}
                        style={floatingStyles}
                        {...getFloatingProps()}
                        className={classNames(
                            "glass-modal z-tooltip w-[min(360px,calc(100vw-32px))] px-3 py-2 text-[var(--color-text-primary)]"
                        )}
                        role="status"
                    >
                        <div className="flex items-center gap-2">
                            <div
                                className="text-xs font-semibold text-[var(--color-text-primary)]"
                            >
                                {senderDisplayName}
                            </div>
                            {agentState?.updated_at ? (
                                <div
                                    className={classNames(
                                        "ml-auto text-xs tabular-nums",
                                        "text-[var(--color-text-tertiary)]"
                                    )}
                                    title={formatFullTime(agentState.updated_at)}
                                >
                                    {t('updated', { time: formatTime(agentState.updated_at) })}
                                </div>
                            ) : null}
                        </div>
                        <div
                            className="mt-1 text-xs whitespace-pre-wrap text-[var(--color-text-secondary)]"
                        >
                            {agentStateDisplay}
                        </div>
                        {(stateTask || blockerCount > 0 || stateNext || stateChanged) ? (
                            <div className="mt-2 space-y-1">
                                <div className="flex flex-wrap items-center gap-1.5">
                                    {stateTask ? (
                                        <span className="text-[11px] px-2 py-0.5 rounded bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                                            {t("taskShort", { id: stateTask })}
                                        </span>
                                    ) : null}
                                    {blockerCount > 0 ? (
                                        <span className="text-[11px] px-2 py-0.5 rounded bg-rose-500/15 text-rose-600 dark:text-rose-300">
                                            {t("blockersShort", { count: blockerCount })}
                                        </span>
                                    ) : null}
                                </div>
                                {stateNext ? (
                                    <div className="text-[11px] text-[var(--color-text-tertiary)]">
                                        {t("nextShort", { value: stateNext })}
                                    </div>
                                ) : null}
                                {stateChanged ? (
                                    <div className={classNames("text-[11px]", "text-[var(--color-text-muted)]")}>
                                        {t("changedShort", { value: stateChanged })}
                                    </div>
                                ) : null}
                            </div>
                        ) : null}
                    </div>
                </FloatingPortal>
            )}
        </div>
    );
}, (prevProps, nextProps) => {
    return (
        prevProps.event === nextProps.event &&
        prevProps.actors === nextProps.actors &&
        prevProps.displayNameMap === nextProps.displayNameMap &&
        prevProps.agentState === nextProps.agentState &&
        prevProps.isDark === nextProps.isDark &&
        prevProps.groupId === nextProps.groupId &&
        prevProps.groupLabelById === nextProps.groupLabelById &&
        prevProps.isHighlighted === nextProps.isHighlighted &&
        prevProps.onRelay === nextProps.onRelay &&
        prevProps.onOpenSource === nextProps.onOpenSource
    );
});
