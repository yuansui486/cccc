import { lazy, memo, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { FloatingPortal, autoUpdate, flip, offset, shift, useFloating } from "@floating-ui/react";
import { useTranslation } from "react-i18next";
import { LedgerEvent, Actor, AgentState, getActorAccentColor, ChatMessageData, MessageAttachment, PresentationMessageRef, StreamingActivity } from "../types";
import { formatFullTime, formatMessageTimestamp, formatTime } from "../utils/time";
import { classNames } from "../utils/classNames";
import { getReplyEventId } from "../utils/chatReply";
import { getPresentationMessageRefs, getPresentationRefChipLabel } from "../utils/presentationRefs";
import { isRedundantWecomImagePlaceholder } from "../utils/messageAttachments";
import { selectStreamingReplySession, useGroupStore } from "../stores";
import { MessageAttachments } from "./messageBubble/MessageAttachments";
import { MessageFooter, MessageMetadataHeader } from "./messageBubble/MessageBubbleChrome";
import { withAuthToken } from "../services/api/base";
import {
    buildToLabel,
    buildVisibleReadStatusEntries,
    computeAckSummary,
    computeObligationSummary,
    getSenderDisplayName,
} from "./messageBubble/model";
import { ActorAvatar } from "./ActorAvatar";
import {
    StreamingMessageBody,
} from "./messageBubble/StreamingMessageBody";
import {
    formatEventLine,
    getMessageBubbleMotionClass,
    isQueuedOnlyStreamingPlaceholder,
    mayContainMarkdown,
    normalizeStreamingActivities,
} from "./messageBubble/helpers";

const LazyMarkdownRenderer = lazy(() =>
    import("./MarkdownRenderer").then((module) => ({ default: module.MarkdownRenderer }))
);

const TYPING_DOT_STYLE_ID = "cccc-message-bubble-typing-dot-style";
function ensureTypingDotStyle(): void {
    if (typeof document === "undefined") return;
    if (document.getElementById(TYPING_DOT_STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = TYPING_DOT_STYLE_ID;
    style.textContent = `
      @keyframes ccccMessageTypingDot {
        0%, 70%, 100% { transform: translateY(0) scale(0.92); opacity: 0.28; }
        35% { transform: translateY(-3px) scale(1); opacity: 0.95; }
      }
    `;
    document.head.appendChild(style);
}

function buildSenderAvatarUrl(groupId: string, senderAvatarPath?: string): string {
    const gid = String(groupId || "").trim();
    const relPath = String(senderAvatarPath || "").trim();
    if (!gid || !relPath.startsWith("state/blobs/")) return "";
    const blobName = relPath.split("/").pop() || "";
    if (!blobName) return "";
    return withAuthToken(`/api/v1/groups/${encodeURIComponent(gid)}/blobs/${encodeURIComponent(blobName)}`);
}

function PlainMessageText({
    text,
    className,
}: {
    text: string;
    className?: string;
}) {
    return (
        <div
            className={classNames(
                "break-words whitespace-pre-wrap [overflow-wrap:anywhere]",
                className
            )}
        >
            {text}
        </div>
    );
}

async function copyText(value: string): Promise<boolean> {
    const text = String(value || "");
    if (!text.trim()) return false;
    try {
        if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch {
        // Fall through to the prompt fallback below.
    }
    if (typeof window !== "undefined" && typeof window.prompt === "function") {
        window.prompt("Copy to clipboard:", text);
        return true;
    }
    return false;
}

function buildMessageCopyText({
    quoteText,
    messageText,
    presentationRefs,
    attachments,
}: {
    quoteText?: string;
    messageText: string;
    presentationRefs: PresentationMessageRef[];
    attachments: { title: string; path: string }[];
}): string {
    const sections: string[] = [];
    const trimmedQuote = String(quoteText || "").trim();
    const trimmedMessage = String(messageText || "").trim();

    if (trimmedQuote) {
        sections.push(`> ${trimmedQuote}`);
    }
    if (trimmedMessage) {
        sections.push(trimmedMessage);
    }
    if (presentationRefs.length > 0) {
        sections.push([
            "Presentation refs:",
            ...presentationRefs.map((ref) => `- ${getPresentationRefChipLabel(ref)}`),
        ].join("\n"));
    }
    if (attachments.length > 0) {
        sections.push([
            "Attachments:",
            ...attachments.map((attachment) => {
                const rawTitle = String(attachment.title || "").trim();
                if (rawTitle) return `- ${rawTitle}`;
                const parts = String(attachment.path || "").split("/");
                return `- ${parts[parts.length - 1] || "file"}`;
            }),
        ].join("\n"));
    }
    return sections.join("\n\n").trim();
}


function MessageBubbleBody({
    event,
    isUserMessage,
    isDark,
    groupId,
    groupLabelById,
    hasSource,
    srcGroupId,
    srcEventId,
    hasDestination,
    dstGroupId,
    dstTo,
    relayChipClass,
    quoteText,
    presentationRefs,
    isStreaming,
    streamingActivities,
    streamId,
    actorId,
    pendingEventId,
    messageText,
    isQueuedOnlyPlaceholder,
    streamingPlaceholderLabel,
    shouldRenderMarkdown,
    blobAttachments,
    blobGroupId,
    stableMessageAttachmentKey,
    onOpenSource,
    onOpenPresentationRef,
}: {
    event: LedgerEvent;
    isUserMessage: boolean;
    isDark: boolean;
    groupId: string;
    groupLabelById: Record<string, string>;
    hasSource: boolean;
    srcGroupId: string;
    srcEventId: string;
    hasDestination: boolean;
    dstGroupId: string;
    dstTo: string[];
    relayChipClass: string;
    quoteText?: string;
    presentationRefs: PresentationMessageRef[];
    isStreaming: boolean;
    streamingActivities: StreamingActivity[];
    streamId: string;
    actorId: string;
    pendingEventId: string;
    messageText: string;
    isQueuedOnlyPlaceholder: boolean;
    streamingPlaceholderLabel: string;
    shouldRenderMarkdown: boolean;
    blobAttachments: Array<{
        kind: string;
        path: string;
        title: string;
        bytes: number;
        mime_type: string;
        local_preview_url: string;
    }>;
    blobGroupId: string;
    stableMessageAttachmentKey: string;
    onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
    onOpenPresentationRef?: (ref: PresentationMessageRef, event: LedgerEvent) => void;
}) {
    const { t } = useTranslation("chat");

    return (
        <>
            {hasSource ? (
                <button
                    type="button"
                    className={classNames(
                        "mb-2 inline-flex items-center gap-2 text-xs font-medium rounded-lg px-2 py-1 border",
                        relayChipClass,
                        onOpenSource ? "cursor-pointer" : "cursor-default"
                    )}
                    onClick={() => onOpenSource?.(srcGroupId, srcEventId)}
                    disabled={!onOpenSource}
                    title={t("openOriginalMessage")}
                >
                    <span className="opacity-70">↗</span>
                    <span className="truncate">
                        {t("relayedFrom", { groupId: srcGroupId, eventId: srcEventId.slice(0, 8) })}
                    </span>
                </button>
            ) : null}

            {hasDestination ? (() => {
                const dstLabel = String(groupLabelById?.[dstGroupId] || "").trim() || dstGroupId;
                const dstToLabel = dstTo.length > 0 ? dstTo.join(", ") : "@all";
                return (
                    <div
                        className={classNames(
                            "mb-2 inline-flex items-center gap-2 text-xs font-medium rounded-lg px-2 py-1 border",
                            relayChipClass
                        )}
                        title={t("sentTo", { label: dstGroupId, to: dstToLabel })}
                    >
                        <span className="opacity-70">↗</span>
                        <span className="truncate">
                            {t("sentTo", { label: dstLabel, to: dstToLabel })}
                        </span>
                    </div>
                );
            })() : null}

            {quoteText ? (
                <div
                    className={`mb-2 text-xs border-l-2 pl-2 italic truncate opacity-80 ${isUserMessage ? "border-blue-400" : "border-[var(--glass-border-subtle)]"}`}
                >
                    "{quoteText}"
                </div>
            ) : null}

            {presentationRefs.length > 0 ? (
                <div className="mb-2 flex flex-wrap gap-1.5">
                    {presentationRefs.map((ref, index) => (
                        <button
                            key={`${String(event.id || "message")}:presentation-ref:${index}:${String(ref.slot_id || "")}`}
                            type="button"
                            onClick={() => onOpenPresentationRef?.(ref, event)}
                            className={classNames(
                                "inline-flex max-w-full items-center rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                                isUserMessage
                                    ? "border-white/15 bg-white/10 text-white hover:bg-white/15"
                                    : "border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                            )}
                            title={getPresentationRefChipLabel(ref)}
                        >
                            <span className="truncate">{getPresentationRefChipLabel(ref)}</span>
                        </button>
                    ))}
                </div>
            ) : null}

            <MessageContent
                isStreaming={isStreaming}
                groupId={groupId}
                streamId={streamId}
                actorId={actorId}
                pendingEventId={pendingEventId}
                fallbackText={messageText}
                streamingActivities={streamingActivities}
                isQueuedOnlyPlaceholder={isQueuedOnlyPlaceholder}
                streamingPlaceholderLabel={streamingPlaceholderLabel}
                shouldRenderMarkdown={shouldRenderMarkdown}
                isDark={isDark}
                isUserMessage={isUserMessage}
            />

            <MessageAttachments
                attachments={blobAttachments}
                blobGroupId={blobGroupId}
                isUserMessage={isUserMessage}
                isDark={isDark}
                attachmentKeyPrefix={stableMessageAttachmentKey}
                downloadTitle={(name) => t("download", { name })}
            />
        </>
    );
}

function MessageContent({
    isStreaming,
    groupId,
    streamId,
    actorId,
    pendingEventId,
    fallbackText,
    streamingActivities,
    isQueuedOnlyPlaceholder,
    streamingPlaceholderLabel,
    shouldRenderMarkdown,
    isDark,
    isUserMessage,
}: {
    isStreaming: boolean;
    groupId: string;
    streamId: string;
    actorId: string;
    pendingEventId: string;
    fallbackText: string;
    streamingActivities: StreamingActivity[];
    isQueuedOnlyPlaceholder: boolean;
    streamingPlaceholderLabel: string;
    shouldRenderMarkdown: boolean;
    isDark: boolean;
    isUserMessage: boolean;
}) {
    if (isStreaming) {
        return (
            <StreamingMessageBody
                groupId={groupId}
                streamId={streamId}
                actorId={actorId}
                pendingEventId={pendingEventId}
                fallbackText={fallbackText}
                streamingActivities={streamingActivities}
                isQueuedOnlyPlaceholder={isQueuedOnlyPlaceholder}
                streamingPlaceholderLabel={streamingPlaceholderLabel}
            />
        );
    }

    if (shouldRenderMarkdown) {
        return (
            <Suspense
                fallback={
                    <PlainMessageText
                        text={fallbackText}
                        className="max-w-full"
                    />
                }
            >
                <LazyMarkdownRenderer
                    content={fallbackText}
                    isDark={isDark}
                    invertText={isUserMessage}
                    className="break-words [overflow-wrap:anywhere] max-w-full"
                />
            </Suspense>
        );
    }

    return (
        <PlainMessageText
            text={fallbackText}
            className="max-w-full"
        />
    );
}

function AgentStateTooltip({
    isOpen,
    canShow,
    isPositioned,
    setFloating,
    floatingStyles,
    senderDisplayName,
    updatedAt,
    agentStateDisplay,
    stateTask,
    blockerCount,
    stateNext,
    stateChanged,
}: {
    isOpen: boolean;
    canShow: boolean;
    isPositioned: boolean;
    setFloating: (node: HTMLElement | null) => void;
    floatingStyles: CSSProperties;
    senderDisplayName: string;
    updatedAt?: string;
    agentStateDisplay: string;
    stateTask: string;
    blockerCount: number;
    stateNext: string;
    stateChanged: string;
}) {
    const { t } = useTranslation("chat");

    if (!isOpen || !canShow) return null;

    return (
        <div
            ref={setFloating}
            style={floatingStyles}
            className={classNames(
                "pointer-events-none z-[80] w-[min(360px,calc(100vw-32px))] rounded-2xl px-3 py-2 shadow-2xl transition-opacity duration-150",
                "glass-modal text-[var(--color-text-primary)]",
                isPositioned ? "opacity-100" : "opacity-0"
            )}
            role="status"
        >
            <div className="flex items-center gap-2">
                <div className="text-xs font-semibold text-[var(--color-text-primary)]">
                    {senderDisplayName}
                </div>
                {updatedAt ? (
                    <div
                        className={classNames(
                            "ml-auto text-xs tabular-nums",
                            "text-[var(--color-text-tertiary)]"
                        )}
                        title={formatFullTime(updatedAt)}
                    >
                        {t("updated", { time: formatTime(updatedAt) })}
                    </div>
                ) : null}
            </div>
            <div className="mt-1 text-xs whitespace-pre-wrap text-[var(--color-text-secondary)]">
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
                        <div className={classNames("text-[11px]", "text-[var(--color-text-tertiary)]")}>
                            {t("changedShort", { value: stateChanged })}
                        </div>
                    ) : null}
                </div>
            ) : null}
        </div>
    );
}

export interface MessageBubbleProps {
    event: LedgerEvent;
    actorById: Map<string, Actor>;
    actors: Actor[];
    displayNameMap: Map<string, string>;
    agentState: AgentState | null;
    isDark: boolean;
    readOnly?: boolean;
    groupId: string;
    groupLabelById: Record<string, string>;
    isHighlighted?: boolean;
    collapseHeader?: boolean;
    onReply: () => void;
    onShowRecipients: () => void;
    onCopyLink?: (eventId: string) => void;
    onCopyContent?: (ev: LedgerEvent) => void;
    onRelay?: (ev: LedgerEvent) => void;
    onOpenSource?: (srcGroupId: string, srcEventId: string) => void;
    onOpenPresentationRef?: (ref: PresentationMessageRef, event: LedgerEvent) => void;
}

export const MessageBubble = memo(function MessageBubble({
    event: ev,
    actorById,
    actors,
    displayNameMap,
    agentState,
    isDark,
    readOnly,
    groupId,
    groupLabelById,
    isHighlighted,
    collapseHeader,
    onReply,
    onShowRecipients,
    onCopyLink,
    onCopyContent: _onCopyContent,
    onRelay,
    onOpenSource,
    onOpenPresentationRef,
}: MessageBubbleProps) {
    const isUserMessage = ev.by === "user";
    const isOptimistic = !!(ev.data as Record<string, unknown> | undefined)?._optimistic;
    const senderAccent = !isUserMessage ? getActorAccentColor(String(ev.by || ""), isDark) : null;
    const isStreaming = !!ev._streaming;
    const streamId = ev.data && typeof ev.data === "object"
        ? String((ev.data as { stream_id?: unknown }).stream_id || "").trim()
        : "";
    const canReply = !!getReplyEventId(ev);
    const messageText = useMemo(() => formatEventLine(ev), [ev]);

    const [isAgentStateOpen, setIsAgentStateOpen] = useState(false);
    const [copiedMessageText, setCopiedMessageText] = useState(false);
    const floatingMiddleware = useMemo(() => [offset(8), flip(), shift({ padding: 8 })], []);
    const { refs, floatingStyles, context } = useFloating({
        open: isAgentStateOpen,
        onOpenChange: setIsAgentStateOpen,
        placement: "bottom-start",
        middleware: floatingMiddleware,
        whileElementsMounted: autoUpdate,
        strategy: "fixed",
    });
    const isAgentStatePositioned = context.isPositioned;
    const setAgentStateReference = useCallback((node: HTMLElement | null) => {
        refs.setReference(node);
    }, [refs]);
    const setAgentStateFloating = useCallback((node: HTMLElement | null) => {
        refs.setFloating(node);
    }, [refs]);

    const canShowAgentState = useMemo(() => {
        if (isUserMessage) return false;
        const id = String(ev.by || "");
        if (!id) return false;
        return true;
    }, [ev.by, isUserMessage]);

    const { t } = useTranslation('chat');

    useEffect(() => {
        ensureTypingDotStyle();
    }, []);
    const agentStateText = String(agentState?.hot?.focus || "").trim();
    const agentStateDisplay = agentStateText || t('noAgentStateYet');
    const stateTask = String(agentState?.hot?.active_task_id || "").trim();
    const stateNext = String(agentState?.hot?.next_action || "").trim();
    const stateChanged = String(agentState?.warm?.what_changed || "").trim();
    const blockerCount = Array.isArray(agentState?.hot?.blockers) ? agentState.hot.blockers.length : 0;


    // Treat data as ChatMessageData.
    const msgData = ev.data as ChatMessageData | undefined;
    const quoteText = msgData?.quote_text;
    const senderSnapshotTitle = typeof msgData?.sender_title === "string" ? String(msgData.sender_title || "").trim() : "";
    const senderSnapshotRuntime = typeof msgData?.sender_runtime === "string" ? String(msgData.sender_runtime || "").trim() : "";
    const senderSnapshotAvatarPath = typeof msgData?.sender_avatar_path === "string" ? String(msgData.sender_avatar_path || "").trim() : "";
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
    const rawAttachments: MessageAttachment[] = Array.isArray(msgData?.attachments) ? msgData.attachments : [];
    const sourcePlatform = typeof msgData?.source_platform === "string" ? String(msgData.source_platform || "").trim() : "";
    const blobAttachments = rawAttachments
        .filter((a): a is MessageAttachment => a != null && typeof a === "object")
        .map((a) => ({
            kind: String(a.kind || "file"),
            path: String(a.path || ""),
            title: String(a.title || ""),
            bytes: Number(a.bytes || 0),
            mime_type: String(a.mime_type || ""),
            local_preview_url: "local_preview_url" in a ? String(a.local_preview_url || "") : "",
        }))
        .filter((a) => a.path.startsWith("state/blobs/") || a.local_preview_url.startsWith("blob:"));
    const displayMessageText = useMemo(() => {
        if (isRedundantWecomImagePlaceholder(messageText, blobAttachments, sourcePlatform)) {
            return "";
        }
        return messageText;
    }, [blobAttachments, messageText, sourcePlatform]);
    const presentationRefs = useMemo(() => getPresentationMessageRefs(msgData?.refs), [msgData?.refs]);
    const shouldRenderMarkdown = useMemo(() => !isStreaming && mayContainMarkdown(displayMessageText), [displayMessageText, isStreaming]);
    const streamingActivities = useMemo(() => {
        return normalizeStreamingActivities((msgData as { activities?: unknown } | undefined)?.activities);
    }, [msgData]);
    const pendingEventId = String(
        (msgData as { pending_event_id?: unknown; reply_to?: unknown } | undefined)?.pending_event_id
        || (msgData as { pending_event_id?: unknown; reply_to?: unknown } | undefined)?.reply_to
        || ""
    ).trim();
    const actorId = String(ev.by || "").trim();
    const hasLiveReplySession = useGroupStore(useCallback((state) => {
        if (isUserMessage || !actorId || !pendingEventId) return false;
        const session = selectStreamingReplySession(state, groupId, {
            pendingEventId,
            streamId,
            actorId,
        });
        return !!session && (session.phase === "pending" || session.phase === "streaming");
    }, [actorId, groupId, isUserMessage, pendingEventId, streamId]));
    const shouldRenderStreamingBody = isStreaming || hasLiveReplySession;
    const isQueuedOnlyPlaceholder = useMemo(() => {
        return isQueuedOnlyStreamingPlaceholder({
            isStreaming: shouldRenderStreamingBody,
            messageText: displayMessageText,
            liveStreamingText: "",
            blobAttachmentCount: blobAttachments.length,
            presentationRefCount: presentationRefs.length,
            activities: streamingActivities,
        });
    }, [blobAttachments.length, displayMessageText, presentationRefs.length, shouldRenderStreamingBody, streamingActivities]);
    const streamPhase = String((msgData as { stream_phase?: unknown } | undefined)?.stream_phase || "").trim().toLowerCase();
    const bubbleMotionClass = useMemo(() => getMessageBubbleMotionClass({
        isStreaming,
        isOptimistic,
        streamPhase,
    }), [isOptimistic, isStreaming, streamPhase]);
    const streamingPlaceholderLabel = useMemo(() => {
        if (!isStreaming) return "";
        if (streamPhase === "commentary") {
            return t("streamCommentaryPending");
        }
        if (streamPhase === "final_answer") {
            return t("streamFinalAnswerPending");
        }
        return t("streamWorkingPending");
    }, [isStreaming, streamPhase, t]);
    const stableMessageAttachmentKey = useMemo(() => {
        const clientId = typeof msgData?.client_id === "string" ? String(msgData.client_id || "").trim() : "";
        if (clientId) return `client:${clientId}`;
        const eventId = typeof ev.id === "string" ? String(ev.id || "").trim() : "";
        return eventId || `row:${String(ev.ts || "")}:${String(ev.by || "")}`;
    }, [ev.id, ev.ts, ev.by, msgData]);
    const copyableMessageText = useMemo(
        () =>
            buildMessageCopyText({
                quoteText,
                messageText: displayMessageText,
                presentationRefs,
                attachments: blobAttachments.map((attachment) => ({
                    title: attachment.title,
                    path: attachment.path || attachment.local_preview_url,
                })),
            }),
        [blobAttachments, displayMessageText, presentationRefs, quoteText]
    );
    const messageTimestamp = formatMessageTimestamp(ev.ts);
    const fullMessageTimestamp = formatFullTime(ev.ts);

    // Use event's group_id for blob URLs (attachments are stored in the event's original group)
    const blobGroupId = String(ev.group_id || "").trim() || groupId;

    const readStatus = ev._read_status;
    const ackStatus = ev._ack_status;
    const recipients = msgData?.to;

    const visibleReadStatusEntries = useMemo(() => {
        return buildVisibleReadStatusEntries(actors, readStatus);
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
        return computeAckSummary({
            hideDirectUserObligationSummary,
            isAttention,
            replyRequired,
            ackStatus,
            isUserMessage,
        });
    }, [ackStatus, hideDirectUserObligationSummary, isAttention, isUserMessage, replyRequired]);

    const obligationSummary = useMemo(() => {
        return computeObligationSummary({
            hideDirectUserObligationSummary,
            obligationStatus: ev._obligation_status,
        });
    }, [ev._obligation_status, hideDirectUserObligationSummary]);

    const toLabel = useMemo(() => {
        return buildToLabel({
            hasDestination,
            dstGroupId,
            dstTo,
            groupLabelById,
            recipients,
            displayNameMap,
        });
    }, [displayNameMap, dstGroupId, dstTo, groupLabelById, hasDestination, recipients]);

    // Sender display name (use title if available)
    const senderActor = useMemo(() => {
        if (isUserMessage) return null;
        const senderId = String(ev.by || "").trim();
        if (!senderId) return null;
        return actorById.get(senderId) || null;
    }, [actorById, ev.by, isUserMessage]);

    const senderDisplayName = useMemo(() => {
        return getSenderDisplayName({
            senderId: String(ev.by || ""),
            senderActor,
            senderTitle: senderSnapshotTitle,
            displayNameMap,
        });
    }, [displayNameMap, ev.by, senderActor, senderSnapshotTitle]);
    const senderAvatarUrl = useMemo(() => {
        return buildSenderAvatarUrl(blobGroupId, senderSnapshotAvatarPath) || String(senderActor?.avatar_url || "").trim();
    }, [blobGroupId, senderActor?.avatar_url, senderSnapshotAvatarPath]);
    const senderRuntime = senderSnapshotRuntime || String(senderActor?.runtime || "").trim();

    const readPreviewEntries = visibleReadStatusEntries.slice(0, 3);
    const readPreviewOverflow = Math.max(0, visibleReadStatusEntries.length - readPreviewEntries.length);
    const relayChipClass = isUserMessage
        ? "border-white/14 bg-white/8 text-blue-100 shadow-none hover:bg-white/12"
        : "glass-btn border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]";

    useEffect(() => {
        if (!copiedMessageText) return undefined;
        const timer = window.setTimeout(() => {
            setCopiedMessageText(false);
        }, 1400);
        return () => window.clearTimeout(timer);
    }, [copiedMessageText]);

    const handleCopyMessageText = useCallback(async () => {
        const ok = await copyText(copyableMessageText);
        if (ok) {
            setCopiedMessageText(true);
        }
    }, [copyableMessageText]);

    return (
        <div
            className={classNames(
                "relative flex w-full min-w-0 gap-2 sm:gap-3 group",
                isUserMessage
                    ? "flex-col items-end sm:items-start sm:flex-row-reverse"
                    : "flex-col items-start sm:flex-row",
                isOptimistic ? "opacity-95" : ""
            )}
        >
            {/* Desktop Avatar (Hidden on mobile) */}
            <div className="relative hidden sm:block">
                <div
                    className={classNames(
                        "mt-1 h-8 w-8 flex-shrink-0",
                        collapseHeader ? "opacity-0 pointer-events-none" : "",
                        canShowAgentState && !collapseHeader ? "cursor-help" : ""
                    )}
                    ref={canShowAgentState && !collapseHeader ? setAgentStateReference : undefined}
                    onMouseEnter={canShowAgentState && !collapseHeader ? () => setIsAgentStateOpen(true) : undefined}
                    onMouseLeave={canShowAgentState && !collapseHeader ? () => setIsAgentStateOpen(false) : undefined}
                    onFocus={canShowAgentState && !collapseHeader ? () => setIsAgentStateOpen(true) : undefined}
                    onBlur={canShowAgentState && !collapseHeader ? () => setIsAgentStateOpen(false) : undefined}
                    tabIndex={canShowAgentState && !collapseHeader ? 0 : undefined}
                    aria-label={canShowAgentState && !collapseHeader ? t('agentStateTooltipLabel', { defaultValue: 'View agent state' }) : undefined}
                >
                    <ActorAvatar
                        avatarUrl={senderAvatarUrl || undefined}
                        runtime={senderRuntime || undefined}
                        title={senderDisplayName}
                        isUser={isUserMessage}
                        isDark={isDark}
                        accentRingClassName={senderAccent?.ring}
                    />
                </div>
            </div>
            <FloatingPortal>
                <AgentStateTooltip
                    isOpen={isAgentStateOpen && !collapseHeader}
                    canShow={canShowAgentState && !collapseHeader}
                    isPositioned={isAgentStatePositioned}
                    setFloating={setAgentStateFloating}
                    floatingStyles={floatingStyles}
                    senderDisplayName={senderDisplayName}
                    updatedAt={agentState?.updated_at ? String(agentState.updated_at) : undefined}
                    agentStateDisplay={agentStateDisplay}
                    stateTask={stateTask}
                    blockerCount={blockerCount}
                    stateNext={stateNext}
                    stateChanged={stateChanged}
                />
            </FloatingPortal>

            {/* Message Content */}
            <div
                className={classNames(
                    "flex min-w-0 flex-col w-full md:w-auto md:max-w-[82%] xl:max-w-[75%]",
                    isUserMessage ? "items-end" : "items-start"
                )}
            >
                {!collapseHeader ? (
                    <>
                        <MessageMetadataHeader
                            mobile={true}
                            isUserMessage={isUserMessage}
                            isDark={isDark}
                            senderAccentTextClass={senderAccent?.text}
                            senderDisplayName={senderDisplayName}
                            messageTimestamp={messageTimestamp}
                            fullMessageTimestamp={fullMessageTimestamp}
                            toLabel={toLabel}
                            senderAvatarUrl={senderAvatarUrl || undefined}
                            senderRuntime={senderRuntime || undefined}
                            avatarRingClassName={senderAccent?.ring}
                        />

                        <MessageMetadataHeader
                            isUserMessage={isUserMessage}
                            isDark={isDark}
                            senderAccentTextClass={senderAccent?.text}
                            senderDisplayName={senderDisplayName}
                            messageTimestamp={messageTimestamp}
                            fullMessageTimestamp={fullMessageTimestamp}
                            toLabel={toLabel}
                        />
                    </>
                ) : null}

                {/* Bubble wrapper (allows badge to overflow) */}
                <div
                    className={classNames(
                        "relative max-w-full min-w-0 md:w-auto",
                        isUserMessage ? "w-auto self-end" : "w-full"
                    )}
                    style={isAttention ? { minWidth: "min(8.5rem, 85vw)" } : undefined}
                >
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
                        "inline-flex max-w-full flex-col px-4 py-2.5 text-sm leading-relaxed transition-[opacity,transform,box-shadow,background-color] duration-200 ease-out",
                        isQueuedOnlyPlaceholder ? "min-h-0 px-3 py-2" : "",
                        isStreaming ? "opacity-95 translate-y-0" : "opacity-100 translate-y-0",
                        bubbleMotionClass,
                        isUserMessage
                            ? "bg-blue-600 text-white rounded-2xl rounded-tr-none shadow-sm"
                            : "glass-bubble rounded-2xl rounded-tl-none text-[var(--color-text-primary)]"
                        ,
                        isAttention ? "ring-1 ring-amber-400/40 dark:ring-amber-500/40" : ""
                        ,
                        isHighlighted ? "outline outline-2 outline-sky-500/30 outline-offset-2" : ""
                    )}
                >

                    <MessageBubbleBody
                        event={ev}
                        isUserMessage={isUserMessage}
                        isDark={isDark}
                        groupId={groupId}
                        groupLabelById={groupLabelById}
                        hasSource={hasSource}
                        srcGroupId={srcGroupId}
                        srcEventId={srcEventId}
                        hasDestination={hasDestination}
                        dstGroupId={dstGroupId}
                        dstTo={dstTo}
                        relayChipClass={relayChipClass}
                        quoteText={quoteText}
                        presentationRefs={presentationRefs}
                        isStreaming={shouldRenderStreamingBody}
                        streamingActivities={streamingActivities}
                        streamId={streamId}
                        actorId={actorId}
                        pendingEventId={pendingEventId}
                        messageText={displayMessageText}
                        isQueuedOnlyPlaceholder={isQueuedOnlyPlaceholder}
                        streamingPlaceholderLabel={streamingPlaceholderLabel}
                        shouldRenderMarkdown={shouldRenderMarkdown}
                        blobAttachments={blobAttachments}
                        blobGroupId={blobGroupId}
                        stableMessageAttachmentKey={stableMessageAttachmentKey}
                        onOpenSource={onOpenSource}
                        onOpenPresentationRef={onOpenPresentationRef}
                    />
                </div>
                </div>

                <MessageFooter
                    readOnly={readOnly}
                    obligationSummary={obligationSummary}
                    ackSummary={ackSummary}
                    visibleReadStatusEntries={visibleReadStatusEntries}
                    readPreviewEntries={readPreviewEntries}
                    readPreviewOverflow={readPreviewOverflow}
                    displayNameMap={displayNameMap}
                    isDark={isDark}
                    replyRequired={replyRequired}
                    copiedMessageText={copiedMessageText}
                    copyableMessageText={copyableMessageText}
                    onCopyMessageText={() => void handleCopyMessageText()}
                    onShowRecipients={onShowRecipients}
                    onCopyLink={onCopyLink}
                    onRelay={onRelay}
                    onReply={onReply}
                    canReply={canReply}
                    eventId={typeof ev.id === "string" ? String(ev.id) : undefined}
                    event={ev}
                />
            </div>
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
        prevProps.collapseHeader === nextProps.collapseHeader &&
        prevProps.onRelay === nextProps.onRelay &&
        prevProps.onOpenSource === nextProps.onOpenSource &&
        prevProps.onOpenPresentationRef === nextProps.onOpenPresentationRef
    );
});
