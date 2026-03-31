// ChatTab is the main chat page component.
// Refactored to use useChatTab hook for business logic, reducing prop drilling.

import { lazy, Suspense, useCallback, type MutableRefObject, type RefObject } from "react";
import { BookmarkIcon, CompassIcon } from "../../components/Icons";
import { Actor, GroupMeta, LedgerEvent, PresentationMessageRef } from "../../types";
import { VirtualMessageList } from "../../components/VirtualMessageList";
import { classNames } from "../../utils/classNames";
import { ChatComposer } from "./ChatComposer";
import { useChatTab } from "../../hooks/useChatTab";
import { useTranslation } from 'react-i18next';
import { useGroupStore, useModalStore, useUIStore } from "../../stores";
import { getChatSession } from "../../stores/useUIStore";

const PresentationRail = lazy(() =>
  import("../../components/presentation/PresentationRail").then((module) => ({ default: module.PresentationRail }))
);
const SetupChecklist = lazy(() =>
  import("./SetupChecklist").then((module) => ({ default: module.SetupChecklist }))
);

const EMPTY_PRESENTATION_ATTENTION: Record<string, boolean> = {};

function ChatLazyFallback({ className }: { className?: string }) {
  return <div className={classNames("min-h-0", className)} />;
}

export interface ChatTabProps {
  // UI configuration
  isDark: boolean;
  isSmallScreen: boolean;
  readOnly?: boolean;

  // Core data (must be passed from App)
  selectedGroupId: string;
  groupLabelById: Record<string, string>;
  actors: Actor[];
  groups: GroupMeta[];

  // Recipient actors for cross-group messaging
  recipientActors: Actor[];
  recipientActorsBusy?: boolean;
  destGroupScopeLabel?: string;

  // Refs (shared with App for external interactions)
  scrollRef: MutableRefObject<HTMLDivElement | null>;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;

  // Refs for scroll state (shared with App)
  chatAtBottomRef?: MutableRefObject<boolean>;

  // File handling (from useDragDrop)
  appendComposerFiles: (files: File[]) => void;

  // Group actions (from useGroupActions)
  onStartGroup: () => void;

  // Mention menu state (local state in App)
  showMentionMenu: boolean;
  setShowMentionMenu: React.Dispatch<React.SetStateAction<boolean>>;
  mentionSelectedIndex: number;
  setMentionSelectedIndex: React.Dispatch<React.SetStateAction<number>>;
  setMentionFilter: React.Dispatch<React.SetStateAction<string>>;
}

export function ChatTab({
  isDark,
  isSmallScreen,
  readOnly,
  selectedGroupId,
  groupLabelById,
  actors,
  groups,
  recipientActors,
  recipientActorsBusy,
  destGroupScopeLabel,
  scrollRef,
  composerRef,
  fileInputRef,
  chatAtBottomRef,
  appendComposerFiles,
  onStartGroup,
  showMentionMenu,
  setShowMentionMenu,
  mentionSelectedIndex,
  setMentionSelectedIndex,
  setMentionFilter,
}: ChatTabProps) {
  // Use the refactored hook for business logic
  const {
    // Chat state
    chatMessages,
    hasAnyChatMessages,
    chatFilter,
    setChatFilter,
    chatViewKey,
    chatWindowProps,
    chatInitialScrollTargetId,
    chatInitialScrollAnchorId,
    chatInitialScrollAnchorOffsetPx,
    chatHighlightEventId,
    isLoadingHistory,
    hasMoreHistory,
    loadMoreHistory,
    chatEmptyState,

    // UI state
    busy,
    showScrollButton,
    chatUnreadCount,

    // Setup checklist
    showSetupCard,
    needsScope,
    needsActors,
    needsStart,

    // Composer state
    composerText,
    setComposerText,
    composerFiles,
    removeComposerFile,
    replyTarget,
    quotedPresentationRef,
    cancelReply,
    clearQuotedPresentationRef,
    toTokens,
    toggleRecipient,
    clearRecipients,
    appendRecipientToken,
    priority,
    replyRequired,
    setPriority,
    setReplyRequired,
    destGroupId,
    setDestGroupId,
    mentionSuggestions,

    // Agent state
    agentStates,

    // Actions
    sendMessage,
    copyMessageLink,
    copyMessageText,
    startReply,
    showRecipients,
    relayMessage,
    openSourceMessage,
    exitChatWindow,
    handleScrollButtonClick,
    handleScrollChange,
    handleScrollSnapshot,
    addAgent,
  } = useChatTab({
    selectedGroupId,
    actors,
    recipientActors,
    composerRef,
    fileInputRef,
    chatAtBottomRef,
    scrollRef,
  });

  const { t } = useTranslation('chat');
  const groupPresentation = useGroupStore((state) => state.groupPresentation);
  const setPresentationViewer = useModalStore((state) => state.setPresentationViewer);
  const setPresentationPin = useModalStore((state) => state.setPresentationPin);
  const mobileSurface = useUIStore((state) =>
    selectedGroupId ? getChatSession(selectedGroupId, state.chatSessions).mobileSurface : "messages"
  );
  const presentationDockOpen = useUIStore((state) =>
    selectedGroupId ? getChatSession(selectedGroupId, state.chatSessions).presentationDockOpen : false
  );
  const setChatMobileSurface = useUIStore((state) => state.setChatMobileSurface);
  const setChatPresentationDockOpen = useUIStore((state) => state.setChatPresentationDockOpen);
  const presentationAttention = useModalStore((state) =>
    selectedGroupId ? (state.presentationAttention[selectedGroupId] || EMPTY_PRESENTATION_ATTENTION) : EMPTY_PRESENTATION_ATTENTION
  );

  const isHydratingEmptyState = chatMessages.length === 0 && chatEmptyState === "hydrating";
  const isBusinessEmptyState = chatMessages.length === 0 && chatEmptyState === "business_empty";
  const listIsLoadingHistory = isLoadingHistory || isHydratingEmptyState;
  const listHasMoreHistory = hasMoreHistory || isHydratingEmptyState;
  const hasPresentationAttention = Object.keys(presentationAttention).length > 0;

  const openPresentationSlot = useCallback((slotId: string) => {
    if (!selectedGroupId || !slotId) return;
    setPresentationViewer({ groupId: selectedGroupId, slotId });
  }, [selectedGroupId, setPresentationViewer]);

  const openPresentationRef = useCallback((ref: PresentationMessageRef, event: LedgerEvent) => {
    if (!selectedGroupId) return;
    const slotId = String(ref.slot_id || "").trim();
    if (!slotId) return;
    setPresentationViewer({
      groupId: selectedGroupId,
      slotId,
      focusRef: ref,
      focusEventId: String(event.id || "").trim() || null,
    });
  }, [selectedGroupId, setPresentationViewer]);

  const pinPresentationSlot = useCallback((slotId: string) => {
    if (!selectedGroupId || !slotId || readOnly) return;
    setPresentationPin({ groupId: selectedGroupId, slotId });
  }, [readOnly, selectedGroupId, setPresentationPin]);

  const setPresentationDockOpen = useCallback((next: boolean) => {
    if (!selectedGroupId) return;
    setChatPresentationDockOpen(selectedGroupId, next);
  }, [selectedGroupId, setChatPresentationDockOpen]);

  const filterOptions: Array<["all" | "user" | "attention" | "task", string]> = [
    ["all", t('filterAll')],
    ["user", t('filterUser')],
    ["attention", t('filterImportant')],
    ["task", t('filterNeedReply')],
  ];
  const showMessageFilters = !readOnly && !chatWindowProps && hasAnyChatMessages;

  return (
    <div className="flex flex-col h-full w-full overflow-hidden bg-transparent">
      {/* 1. Header Area: For critical banners/setup only, very space-efficient */}
      <header className="flex-shrink-0 z-10 flex flex-col w-full">
        {/* Jump-to window banner */}
        {chatWindowProps && (
          <div className="px-4 pt-4">
            <div
              className={classNames(
                "flex items-center justify-between gap-3 rounded-2xl border px-4 py-3 shadow-sm",
                isDark ? "border-slate-700/50 bg-slate-900/40" : "border-gray-200 bg-white/70"
              )}
              role="status"
              aria-label={t('viewingMessage')}
            >
              <div className="min-w-0">
                <div className={classNames("text-sm font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>
                  {t('viewingMessage')}
                </div>
                <div className={classNames("text-xs mt-0.5", isDark ? "text-slate-400" : "text-gray-600")}>
                  {isLoadingHistory
                    ? t('loadingContext')
                    : chatWindowProps.hasMoreBefore || chatWindowProps.hasMoreAfter
                      ? t('contextTruncated')
                      : t('contextLoaded')}
                </div>
              </div>
              {!readOnly && (
                <button
                  type="button"
                  className={classNames(
                    "flex-shrink-0 text-xs font-semibold px-3 py-1.5 min-h-[36px] flex items-center rounded-full border transition-colors",
                    isDark
                      ? "border-slate-600 text-slate-200 hover:bg-slate-800/60"
                      : "border-gray-200 text-gray-800 hover:bg-gray-100"
                  )}
                  onClick={exitChatWindow}
                >
                  {t('returnToLatest')}
                </button>
              )}
            </div>
          </div>
        )}

        {/* Compact setup card */}
        {!readOnly && showSetupCard && chatMessages.length > 0 && (
          <div className="px-4 pt-4 pb-2">
            <div
              className={classNames(
                "rounded-2xl border p-4 sm:p-5",
                isDark ? "border-slate-700/50 bg-slate-900/40" : "border-gray-200 bg-white/70"
              )}
              role="region"
              aria-label={t('setupChecklist')}
            >
              <div className={classNames("text-sm font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>
                {t('nextSteps')}
              </div>
              <Suspense fallback={<ChatLazyFallback />}>
                <SetupChecklist
                  isDark={isDark}
                  selectedGroupId={selectedGroupId}
                  busy={busy}
                  needsScope={needsScope}
                  needsActors={needsActors}
                  needsStart={needsStart}
                  onAddAgent={addAgent}
                  onStartGroup={onStartGroup}
                  variant="compact"
                />
              </Suspense>
            </div>
          </div>
        )}
      </header>

      {/* 2. Body Area: messages stay primary; presentation is a secondary surface */}
      <main className="flex flex-1 min-h-0 flex-col">
        <div className="relative flex min-h-0 flex-1">
          {(!isSmallScreen || mobileSurface === "messages") ? (
            <section className="relative flex min-h-0 min-w-0 flex-1 flex-col">
              {isSmallScreen && (
                <>
                  <div
                    className={classNames(
                      "flex-shrink-0 px-3 py-2 border-b",
                      isDark ? "border-white/5" : "border-black/5"
                    )}
                  >
                    <div className="flex items-center gap-3">
                      {showMessageFilters ? (
                        <div
                          className={classNames(
                            "min-w-0 flex-1 overflow-x-auto scrollbar-hide",
                          )}
                          role="tablist"
                          aria-label={t('chatFilters')}
                        >
                          <div className={classNames(
                            "inline-flex min-w-max items-center gap-1 rounded-full border p-1 backdrop-blur-md",
                            isDark
                              ? "border-slate-700/50 bg-transparent"
                              : "border-gray-200/70 bg-transparent"
                          )}>
                            {filterOptions.map(([key, label]) => {
                              const active = chatFilter === key;
                              return (
                                <button
                                  key={key}
                                  type="button"
                                  className={classNames(
                                    "min-w-0 rounded-full px-3 py-1.5 text-[11px] font-medium transition-all whitespace-nowrap",
                                    active
                                      ? "bg-blue-600 text-white shadow-sm"
                                      : isDark
                                        ? "text-slate-500 hover:text-slate-200 hover:bg-slate-800/60"
                                        : "text-gray-500 hover:text-gray-900 hover:bg-gray-100"
                                  )}
                                  onClick={() => setChatFilter(key)}
                                  aria-pressed={active}
                                >
                                  {label}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      ) : <div className="flex-1" />}

                      <button
                        type="button"
                        onClick={() => selectedGroupId && setChatMobileSurface(selectedGroupId, "presentation")}
                        className={classNames(
                          "relative flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full border backdrop-blur-xl transition-all duration-200",
                          isDark
                            ? "border-white/10 bg-transparent text-slate-100"
                            : "border-black/10 bg-transparent text-gray-900",
                          hasPresentationAttention &&
                            (isDark
                              ? "presentation-slot-attention presentation-slot-attention-dark"
                              : "presentation-slot-attention presentation-slot-attention-light")
                        )}
                        aria-label={t("presentationOpenDockAction", { defaultValue: "Open presentation" })}
                        title={t("presentationOpenDockAction", { defaultValue: "Open presentation" })}
                      >
                        <BookmarkIcon size={18} />
                        {hasPresentationAttention ? (
                          <>
                            <span
                              className={classNames(
                                "pointer-events-none absolute right-2 top-2 h-2 w-2 rounded-full animate-ping",
                                isDark ? "bg-cyan-300/70" : "bg-cyan-500/45"
                              )}
                            />
                            <span
                              className={classNames(
                                "pointer-events-none absolute right-2 top-2 h-2 w-2 rounded-full",
                                isDark ? "bg-cyan-200" : "bg-cyan-500"
                              )}
                            />
                          </>
                        ) : null}
                      </button>
                    </div>
                  </div>

                </>
              )}

              {showMessageFilters && (
                <div
                  className="hidden sm:block absolute top-4 left-4 z-20 pointer-events-none"
                  style={{ width: "calc(100% - 32px)" }}
                >
                  <div
                    className={classNames(
                      "inline-flex items-center gap-1 xl:gap-2 rounded-full border p-1 sm:p-1.5 shadow-xl pointer-events-auto backdrop-blur-xl transition-all duration-300",
                      isDark
                        ? "border-white/10 bg-slate-900/60 shadow-black/40 ring-1 ring-white/5"
                        : "border-black/5 bg-white/70 shadow-gray-200/50 ring-1 ring-black/5"
                    )}
                    role="tablist"
                    aria-label={t('chatFilters')}
                  >
                    {filterOptions.map(([key, label]) => {
                      const active = chatFilter === key;
                      return (
                        <button
                          key={key}
                          type="button"
                          className={classNames(
                            "text-xs px-4 py-1.5 rounded-full transition-all font-medium",
                            active
                              ? "bg-blue-600 text-white shadow-sm"
                              : isDark
                                ? "text-slate-500 hover:text-slate-200 hover:bg-slate-800/60"
                                : "text-gray-500 hover:text-gray-900 hover:bg-gray-100"
                          )}
                          onClick={() => setChatFilter(key)}
                          aria-pressed={active}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {isBusinessEmptyState && showSetupCard ? (
                <div
                  ref={scrollRef}
                  className="flex-1 min-h-0 overflow-auto px-4 py-4 relative"
                  role="log"
                  aria-label={t('chatMessages')}
                >
                  <div className="flex h-full flex-col items-center justify-center text-center pb-20">
                    <div className={classNames("w-full max-w-md", isDark ? "text-slate-200" : "text-gray-800")}>
                      <div className="mb-4 flex justify-center" aria-hidden="true">
                        <CompassIcon size={32} className={isDark ? "text-cyan-300" : "text-cyan-600"} />
                      </div>
                      <div className={classNames("text-sm font-semibold", isDark ? "text-slate-200" : "text-gray-800")}>
                        {t('nextSteps')}
                      </div>
                      {readOnly ? (
                        <div className={classNames("mt-3 text-sm", isDark ? "text-slate-400" : "text-gray-600")}>
                          {t('noMessagesYet')}
                        </div>
                      ) : (
                        <Suspense fallback={<ChatLazyFallback className="mt-4" />}>
                          <SetupChecklist
                            isDark={isDark}
                            selectedGroupId={selectedGroupId}
                            busy={busy}
                            needsScope={needsScope}
                            needsActors={needsActors}
                            needsStart={needsStart}
                            onAddAgent={addAgent}
                            onStartGroup={onStartGroup}
                            variant="full"
                          />
                        </Suspense>
                      )}
                    </div>
                  </div>
                </div>
              ) : (
                <VirtualMessageList
                  messages={chatMessages}
                  actors={actors}
                  agentStates={agentStates}
                  isDark={isDark}
                  readOnly={readOnly}
                  groupId={selectedGroupId}
                  groupLabelById={groupLabelById}
                  viewKey={chatViewKey}
                  initialScrollTargetId={chatInitialScrollTargetId}
                  initialScrollAnchorId={chatInitialScrollAnchorId}
                  initialScrollAnchorOffsetPx={chatInitialScrollAnchorOffsetPx}
                  highlightEventId={chatHighlightEventId}
                  scrollRef={scrollRef}
                  followBottomRef={chatAtBottomRef}
                  onReply={startReply}
                  onShowRecipients={showRecipients}
                  onCopyLink={copyMessageLink}
                  onCopyContent={copyMessageText}
                  onRelay={relayMessage}
                  onOpenSource={openSourceMessage}
                  onOpenPresentationRef={openPresentationRef}
                  showScrollButton={showScrollButton}
                  onScrollButtonClick={handleScrollButtonClick}
                  chatUnreadCount={chatUnreadCount}
                  onScrollChange={handleScrollChange}
                  onScrollSnapshot={handleScrollSnapshot}
                  isLoadingHistory={listIsLoadingHistory}
                  hasMoreHistory={listHasMoreHistory}
                  onLoadMore={loadMoreHistory}
                />
              )}
            </section>
          ) : null}

          {!isSmallScreen ? (
            <Suspense fallback={<ChatLazyFallback className="w-0" />}>
              <PresentationRail
                mode="dock"
                presentation={groupPresentation}
                isDark={isDark}
                readOnly={readOnly}
                isOpen={presentationDockOpen}
                onOpenChange={setPresentationDockOpen}
                attentionSlots={presentationAttention}
                onOpenSlot={openPresentationSlot}
                onPinSlot={pinPresentationSlot}
              />
            </Suspense>
          ) : null}

          {isSmallScreen && mobileSurface === "presentation" ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <Suspense fallback={<ChatLazyFallback className="flex-1" />}>
                <PresentationRail
                  mode="panel"
                  presentation={groupPresentation}
                  isDark={isDark}
                  readOnly={readOnly}
                  isOpen={mobileSurface === "presentation"}
                  onOpenChange={(open) => selectedGroupId && setChatMobileSurface(selectedGroupId, open ? "presentation" : "messages")}
                  attentionSlots={presentationAttention}
                  onOpenSlot={openPresentationSlot}
                  onPinSlot={pinPresentationSlot}
                />
              </Suspense>
            </div>
          ) : null}
        </div>
      </main>

      {/* 3. Footer Area: Composer */}
      {!readOnly && (
        <footer className="flex-shrink-0 w-full bg-transparent">
          <ChatComposer
            isDark={isDark}
            isSmallScreen={isSmallScreen}
            selectedGroupId={selectedGroupId}
            actors={actors}
            recipientActors={recipientActors}
            recipientActorsBusy={recipientActorsBusy}
            groups={groups}
            destGroupId={destGroupId}
            setDestGroupId={setDestGroupId}
            destGroupScopeLabel={destGroupScopeLabel}
            busy={busy}
            replyTarget={replyTarget}
            onCancelReply={cancelReply}
            quotedPresentationRef={quotedPresentationRef}
            onClearQuotedPresentationRef={clearQuotedPresentationRef}
            toTokens={toTokens}
            onToggleRecipient={toggleRecipient}
            onClearRecipients={clearRecipients}
            composerFiles={composerFiles}
            onRemoveComposerFile={removeComposerFile}
            appendComposerFiles={appendComposerFiles}
            fileInputRef={fileInputRef}
            composerRef={composerRef}
            composerText={composerText}
            setComposerText={setComposerText}
            priority={priority}
            replyRequired={replyRequired}
            setPriority={setPriority}
            setReplyRequired={setReplyRequired}
            onSendMessage={sendMessage}
            showMentionMenu={showMentionMenu}
            setShowMentionMenu={setShowMentionMenu}
            mentionSuggestions={mentionSuggestions}
            mentionSelectedIndex={mentionSelectedIndex}
            setMentionSelectedIndex={setMentionSelectedIndex}
            setMentionFilter={setMentionFilter}
            onAppendRecipientToken={appendRecipientToken}
          />
        </footer>
      )}
    </div>
  );
}
