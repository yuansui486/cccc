// ChatComposer renders the chat message composer.
import type { Dispatch, RefObject, SetStateAction } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Actor, GroupMeta, PresentationMessageRef, ReplyTarget } from "../../types";
import { classNames } from "../../utils/classNames";
import { getRecipientDisplayLabel } from "../../utils/displayText";
import { AttachmentIcon, SendIcon, ChevronDownIcon, ReplyIcon, CloseIcon, AlertIcon, MicrophoneIcon, StopIcon } from "../../components/Icons";
import { ScrollFade } from "../../components/ScrollFade";
import { getPresentationRefChipLabel } from "../../utils/presentationRefs";
import { useTranslation } from 'react-i18next';

const VOICE_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
];

function pickVoiceMimeType(): string {
  if (typeof window === "undefined" || typeof MediaRecorder === "undefined") return "";
  if (typeof MediaRecorder.isTypeSupported !== "function") return "";
  for (const candidate of VOICE_MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(candidate)) return candidate;
    } catch {
      continue;
    }
  }
  return "";
}

function guessVoiceFileExtension(mimeType: string): string {
  const normalized = String(mimeType || "").toLowerCase();
  if (normalized.includes("mp4")) return ".m4a";
  if (normalized.includes("ogg")) return ".ogg";
  if (normalized.includes("mpeg")) return ".mp3";
  if (normalized.includes("wav")) return ".wav";
  return ".webm";
}

function formatRecordingDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const minutes = Math.floor(seconds / 60);
  const remain = seconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remain).padStart(2, "0")}`;
}

export interface ChatComposerProps {
  isDark: boolean;
  isSmallScreen: boolean;
  selectedGroupId: string;
  actors: Actor[];
  recipientActors: Actor[];
  recipientActorsBusy?: boolean;
  groups: GroupMeta[];
  destGroupId: string;
  setDestGroupId: (groupId: string) => void;
  destGroupScopeLabel?: string;
  busy: string;

  // Reply
  replyTarget: ReplyTarget;
  onCancelReply: () => void;
  quotedPresentationRef: PresentationMessageRef | null;
  onClearQuotedPresentationRef: () => void;

  // Recipients
  toTokens: string[];
  onToggleRecipient: (token: string) => void;
  onClearRecipients: () => void;

  // Files
  composerFiles: File[];
  onRemoveComposerFile: (index: number) => void;
  appendComposerFiles: (files: File[]) => void;
  fileInputRef: RefObject<HTMLInputElement | null>;

  // Text input
  composerRef: RefObject<HTMLTextAreaElement | null>;
  composerText: string;
  setComposerText: Dispatch<SetStateAction<string>>;
  priority: "normal" | "attention";
  replyRequired: boolean;
  setPriority: (priority: "normal" | "attention") => void;
  setReplyRequired: (value: boolean) => void;
  onSendMessage: () => void;
  onVoiceError: (message: string) => void;

  // Mention menu
  showMentionMenu: boolean;
  setShowMentionMenu: Dispatch<SetStateAction<boolean>>;
  mentionSuggestions: string[];
  mentionSelectedIndex: number;
  setMentionSelectedIndex: Dispatch<SetStateAction<number>>;
  setMentionFilter: Dispatch<SetStateAction<string>>;
  onAppendRecipientToken: (token: string) => void;
}

export function ChatComposer({
  isDark,
  isSmallScreen,
  selectedGroupId,
  actors,
  recipientActors,
  recipientActorsBusy,
  groups,
  destGroupId,
  setDestGroupId,
  destGroupScopeLabel: _destGroupScopeLabel,
  busy,
  replyTarget,
  onCancelReply,
  quotedPresentationRef,
  onClearQuotedPresentationRef,
  toTokens,
  onToggleRecipient,
  onClearRecipients,
  composerFiles,
  onRemoveComposerFile,
  appendComposerFiles,
  fileInputRef,
  composerRef,
  composerText,
  setComposerText,
  priority,
  replyRequired,
  setPriority,
  setReplyRequired,
  onSendMessage,
  onVoiceError,
  showMentionMenu,
  setShowMentionMenu,
  mentionSuggestions,
  mentionSelectedIndex,
  setMentionSelectedIndex,
  setMentionFilter,
  onAppendRecipientToken,
}: ChatComposerProps) {
  const composerHeightRef = useRef(0);
  const isUserInputRef = useRef(false);
  const [showModeMenu, setShowModeMenu] = useState(false);
  const [voiceState, setVoiceState] = useState<"idle" | "recording">("idle");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const modeMenuRef = useRef<HTMLDivElement | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const voiceChunksRef = useRef<Blob[]>([]);
  const ignoreVoiceStopRef = useRef(false);
  const recordingStartedAtRef = useRef<number>(0);
  const { t } = useTranslation('chat');

  // Auto-adjust textarea height when composerText changes programmatically
  // (e.g. mention selection). Skips when handleChange already handled resize.
  useEffect(() => {
    if (isUserInputRef.current) {
      isUserInputRef.current = false;
      return;
    }
    const el = composerRef.current;
    if (!el) return;

    const rafId = requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.style.height = "auto";
        const nextHeight = Math.min(el.scrollHeight, 140);
        el.style.height = `${nextHeight}px`;
        composerHeightRef.current = nextHeight;
      });
    });

    return () => cancelAnimationFrame(rafId);
  }, [composerText, composerRef]);

  useEffect(() => {
    if (!showModeMenu) return;

    const onPointerDown = (event: MouseEvent | TouchEvent) => {
      const node = modeMenuRef.current;
      if (!node) return;
      const target = event.target;
      if (target instanceof Node && !node.contains(target)) {
        setShowModeMenu(false);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
    };
  }, [showModeMenu]);

  useEffect(() => {
    if (voiceState !== "recording") return;
    const tick = window.setInterval(() => {
      const startedAt = recordingStartedAtRef.current || Date.now();
      setRecordingSeconds(Math.max(0, Math.floor((Date.now() - startedAt) / 1000)));
    }, 250);
    return () => window.clearInterval(tick);
  }, [voiceState]);

  useEffect(() => {
    return () => {
      ignoreVoiceStopRef.current = true;
      const recorder = mediaRecorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // ignore teardown failure
        }
      }
      const stream = mediaStreamRef.current;
      if (stream) {
        for (const track of stream.getTracks()) track.stop();
      }
      mediaRecorderRef.current = null;
      mediaStreamRef.current = null;
      voiceChunksRef.current = [];
    };
  }, []);

  const chipBaseClass =
    "flex-shrink-0 whitespace-nowrap text-[10px] sm:text-[11px] px-2.5 sm:px-3 rounded-full border transition-all flex items-center justify-center font-medium";
  const composerToolButtonClass =
    "glass-btn flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl sm:rounded-2xl border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)] transition-[background-color,border-color,color,transform,box-shadow] duration-200 disabled:cursor-not-allowed disabled:text-[var(--color-text-tertiary)] disabled:opacity-70";
  const composerInlineToolButtonClass =
    "glass-btn flex h-9 w-9 items-center justify-center rounded-xl border border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)] transition-[background-color,border-color,color,transform,box-shadow] duration-200 sm:h-10 sm:w-10";

  // Get display name for reply target
  const replyByDisplayName = useMemo(() => {
    if (!replyTarget?.by) return "";
    if (replyTarget.by === "user") return getRecipientDisplayLabel("user");
    const actor = actors.find(a => a.id === replyTarget.by);
    return actor?.title || replyTarget.by;
  }, [replyTarget, actors]);
  const quotedPresentationRefLabel = useMemo(
    () => (quotedPresentationRef ? getPresentationRefChipLabel(quotedPresentationRef) : ""),
    [quotedPresentationRef],
  );

  // Handle pasted files (clipboard items).
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const dt = e.clipboardData;
    if (!dt) return;

    const files: File[] = [];
    try {
      const items = Array.from(dt.items || []);
      for (const it of items) {
        if (!it || it.kind !== "file") continue;
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    } catch {
      // ignore
    }
    if (files.length === 0) {
      try {
        files.push(...Array.from(dt.files || []));
      } catch {
        // ignore
      }
    }

    if (files.length === 0) return;
    if (!selectedGroupId) return;

    // De-duplicate within a single paste.
    const seen = new Set<string>();
    const unique: File[] = [];
    for (const f of files) {
      const key = `${f.name}:${f.size}:${f.type}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(f);
    }
    if (unique.length === 0) return;

    e.preventDefault();
    appendComposerFiles(unique);
  };

  // Handle text changes.
  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    isUserInputRef.current = true;
    setComposerText(val);
    const target = e.target;
    // Use requestAnimationFrame to avoid forced reflow during layout.
    requestAnimationFrame(() => {
      target.style.height = "auto";
      const nextHeight = Math.min(target.scrollHeight, 140);
      target.style.height = `${nextHeight}px`;
      composerHeightRef.current = nextHeight;
    });

    // Detect @ mentions for the recipient helper menu.
    const lastAt = val.lastIndexOf("@");
    if (lastAt >= 0) {
      const afterAt = val.slice(lastAt + 1);
      if (
        (lastAt === 0 || val[lastAt - 1] === " " || val[lastAt - 1] === "\n") &&
        !afterAt.includes(" ") &&
        !afterAt.includes("\n")
      ) {
        setMentionFilter(afterAt);
        setShowMentionMenu(true);
        setMentionSelectedIndex(0);
      } else {
        setShowMentionMenu(false);
      }
    } else {
      setShowMentionMenu(false);
    }
  };

  // Handle keyboard shortcuts and mention navigation.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showMentionMenu && mentionSuggestions.length > 0) {
      const maxIndex = Math.min(mentionSuggestions.length, 8) - 1;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionSelectedIndex((prev) => (prev >= maxIndex ? 0 : prev + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionSelectedIndex((prev) => (prev <= 0 ? maxIndex : prev - 1));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        selectMention(mentionSuggestions[mentionSelectedIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setShowMentionMenu(false);
        setMentionSelectedIndex(0);
        return;
      }
    }
    if (e.key === "Enter" && !showMentionMenu) {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        onSendMessage();
      }
    } else if (e.key === "Escape") {
      setShowMentionMenu(false);
      setShowModeMenu(false);
      onCancelReply();
    }
  };

  // Select a mention from the menu.
  const selectMention = (selected: string | undefined) => {
    if (!selected) return;
    const lastAt = composerText.lastIndexOf("@");
    if (lastAt >= 0) {
      const before = composerText.slice(0, lastAt);
      setComposerText(before + selected + " ");
    }
    if (!toTokens.includes(selected)) {
      onAppendRecipientToken(selected);
    }
    setShowMentionMenu(false);
    setMentionSelectedIndex(0);
  };

  const canSend = composerText.trim() || composerFiles.length > 0;
  const isAttention = priority === "attention";
  const isCrossGroup = !!destGroupId && destGroupId !== selectedGroupId;
  const voiceBusy = voiceState !== "idle";
  const showVoiceAction = !canSend;
  const canChooseDestGroup =
    !!selectedGroupId && busy !== "send" && !voiceBusy && !replyTarget && !quotedPresentationRef && composerFiles.length === 0;

  type MessageMode = "normal" | "attention" | "task";
  const modeOptions: Array<{ key: MessageMode; label: string; description: string }> = [
    { key: "normal", label: t('modeNormal'), description: t('modeNormalDesc') },
    { key: "attention", label: t('modeImportant'), description: t('modeImportantDesc') },
    { key: "task", label: t('modeNeedReply'), description: t('modeNeedReplyDesc') },
  ];

  const messageMode: MessageMode = replyRequired
    ? "task"
    : isAttention
      ? "attention"
      : "normal";
  const setMessageMode = (mode: MessageMode) => {
    if (mode === "normal") {
      setPriority("normal");
      setReplyRequired(false);
      return;
    }
    if (mode === "attention") {
      setPriority("attention");
      setReplyRequired(false);
      return;
    }
    setPriority("normal");
    setReplyRequired(true);
  };
  const activeMode = modeOptions.find((opt) => opt.key === messageMode) || modeOptions[0];
  const modeNotice = messageMode === "task"
    ? t('modeNoticeNeedReply')
    : messageMode === "attention"
      ? t('modeNoticeImportant')
      : "";

  const fileDisabledReason = (() => {
    if (!selectedGroupId) return t('selectGroupFirst');
    if (busy === "send") return t('busy');
    if (isCrossGroup) return t('crossGroupAttachment');
    return t('attachFile');
  })();

  const groupOptions = useMemo(() => {
    const cur = String(selectedGroupId || "").trim();
    const list = (groups || []).filter((g) => String(g.group_id || "").trim());
    // Prefer showing the current group first.
    const sorted = list.slice().sort((a, b) => {
      const aId = String(a.group_id || "");
      const bId = String(b.group_id || "");
      if (aId === cur && bId !== cur) return -1;
      if (bId === cur && aId !== cur) return 1;
      const aTitle = String(a.title || "").trim().toLowerCase();
      const bTitle = String(b.title || "").trim().toLowerCase();
      if (aTitle && bTitle) return aTitle.localeCompare(bTitle);
      if (aTitle && !bTitle) return -1;
      if (!aTitle && bTitle) return 1;
      return aId.localeCompare(bId);
    });
    return sorted.map((g) => {
      const gid = String(g.group_id || "").trim();
      const title = String(g.title || "").trim();
      const topic = String(g.topic || "").trim();
      const label = title || topic || t('untitledGroup');
      return { gid, label };
    });
  }, [groups, selectedGroupId, t]);

  const groupSelectClass = useMemo(() => {
    if (!canChooseDestGroup || groupOptions.length === 0) {
      return isDark
        ? "bg-white/[0.07] text-[var(--color-text-tertiary)] border-white/[0.08]"
        : "bg-white text-gray-400 border-gray-200";
    }
    if (isCrossGroup) {
      return isDark
        ? "bg-blue-600/20 text-blue-100 border-blue-500/40 hover:border-blue-400/60"
        : "bg-blue-50 text-blue-700 border-blue-200 hover:border-blue-300";
    }
    // Match the neutral look of recipient buttons
    return isDark
      ? "bg-white/[0.08] text-[var(--color-text-secondary)] border-white/[0.1] hover:border-white/[0.16]"
      : "bg-black/5 text-gray-800 border-transparent hover:border-black/5";
  }, [canChooseDestGroup, groupOptions.length, isCrossGroup, isDark]);

  const groupCaretClass = useMemo(() => {
    if (!canChooseDestGroup || groupOptions.length === 0) return isDark ? "text-[var(--color-text-tertiary)]" : "text-gray-400";
    if (isCrossGroup) return isDark ? "text-blue-200" : "text-blue-700";
    return isDark ? "text-[var(--color-text-tertiary)]" : "text-gray-500";
  }, [canChooseDestGroup, groupOptions.length, isCrossGroup, isDark]);

  const stopVoiceStream = () => {
    const stream = mediaStreamRef.current;
    if (!stream) return;
    for (const track of stream.getTracks()) track.stop();
    mediaStreamRef.current = null;
  };

  const resetVoiceCapture = () => {
    mediaRecorderRef.current = null;
    voiceChunksRef.current = [];
    stopVoiceStream();
    recordingStartedAtRef.current = 0;
  };

  const beginVoiceRecording = async () => {
    if (!selectedGroupId || busy === "send" || voiceBusy || !showVoiceAction) return;
    if (isCrossGroup) {
      onVoiceError(fileDisabledReason);
      return;
    }
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onVoiceError(t("voiceUnsupported"));
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = pickVoiceMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      voiceChunksRef.current = [];
      ignoreVoiceStopRef.current = false;
      recordingStartedAtRef.current = Date.now();
      setRecordingSeconds(0);

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          voiceChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const shouldIgnore = ignoreVoiceStopRef.current;
        const chunks = voiceChunksRef.current.slice();
        const resolvedType = recorder.mimeType || chunks[0]?.type || "audio/webm";
        resetVoiceCapture();
        if (shouldIgnore) {
          setVoiceState("idle");
          setRecordingSeconds(0);
          return;
        }
        const blob = new Blob(chunks, { type: resolvedType });
        if (blob.size === 0) {
          setVoiceState("idle");
          onVoiceError(t("voiceRecordingEmpty"));
          return;
        }
        const file = new File(
          [blob],
          `voice-${Date.now()}${guessVoiceFileExtension(resolvedType)}`,
          { type: resolvedType, lastModified: Date.now() }
        );
        appendComposerFiles([file]);
        setVoiceState("idle");
        setRecordingSeconds(0);
      };

      recorder.start();
      setVoiceState("recording");
    } catch (error) {
      resetVoiceCapture();
      setVoiceState("idle");
      const message = error instanceof Error ? error.message : t("voicePermissionDenied");
      onVoiceError(message || t("voicePermissionDenied"));
    }
  };

  const finishVoiceRecording = () => {
    const recorder = mediaRecorderRef.current;
    if (!recorder) {
      resetVoiceCapture();
      setVoiceState("idle");
      return;
    }
    if (recorder.state === "inactive") {
      resetVoiceCapture();
      setVoiceState("idle");
      return;
    }
    try {
      recorder.stop();
    } catch {
      resetVoiceCapture();
      setVoiceState("idle");
      onVoiceError(t("voiceStopFailed"));
    }
  };

  const handlePrimaryAction = () => {
    if (!showVoiceAction) {
      onSendMessage();
      return;
    }
    if (voiceState === "recording") {
      finishVoiceRecording();
      return;
    }
    if (voiceState === "idle") {
      void beginVoiceRecording();
    }
  };

  const primaryButtonTitle =
    voiceState === "recording"
      ? t("voiceStopRecording")
      : showVoiceAction
        ? t("voiceStartRecording")
        : t("sendMessage");

  return (
    <footer
      className={classNames(
        "flex-shrink-0 border-t px-3 sm:px-4 py-2 sm:py-3 safe-area-bottom-compact transition-colors",
        isDark ? "border-white/5 bg-slate-950/72 backdrop-blur-md" : "border-black/5 bg-white/78 backdrop-blur-md"
      )}
    >
      {/* Reply indicator */}
      {replyTarget && (
        <div className={classNames(
          "mb-2 flex items-center gap-2 text-xs rounded-xl px-3 py-1.5",
          isDark ? "text-[var(--color-text-tertiary)] bg-white/[0.08] border border-white/[0.08]" : "text-gray-500 bg-black/5"
        )}>
          <ReplyIcon size={14} className="flex-shrink-0 opacity-60" />
          <span className="font-medium truncate flex-1">
            <span className="opacity-60 mr-1">{t('replyingTo')}</span>
            <span className={isDark ? "text-slate-300" : "text-gray-700"}>{replyByDisplayName}</span>
            <span className="mx-1 opacity-40">"</span>
            <span className="italic opacity-80">{replyTarget.text}</span>
            <span className="opacity-40">"</span>
          </span>
          <button
            className={classNames(
              "p-2.5 -m-1.5 rounded-full transition-colors",
              isDark ? "hover:bg-white/10 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]" : "hover:bg-black/10 text-gray-400 hover:text-gray-600"
            )}
            onClick={onCancelReply}
            title={t('cancelReply')}
            aria-label={t('cancelReply')}
          >
            <CloseIcon size={14} />
          </button>
        </div>
      )}

      {quotedPresentationRef && (
        <div
          className={classNames(
            "mb-2 flex items-center gap-2 text-xs rounded-xl px-3 py-1.5",
            isDark ? "text-[var(--color-text-tertiary)] bg-cyan-500/12 border border-cyan-400/15" : "text-gray-600 bg-cyan-50",
          )}
        >
          <span className={classNames("font-medium flex-shrink-0", isDark ? "text-cyan-100" : "text-cyan-700")}>
            {t("presentationQuotedViewLabel", { defaultValue: "Quoted view" })}
          </span>
          <span className="min-w-0 flex-1 truncate" title={quotedPresentationRef.title || quotedPresentationRefLabel}>
            {quotedPresentationRefLabel}
          </span>
          <button
            className={classNames(
              "p-2.5 -m-1.5 rounded-full transition-colors",
              isDark ? "hover:bg-white/10 text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]" : "hover:bg-black/10 text-gray-400 hover:text-gray-600",
            )}
            onClick={onClearQuotedPresentationRef}
            title={t("presentationRemoveQuotedView", { defaultValue: "Remove quoted view" })}
            aria-label={t("presentationRemoveQuotedView", { defaultValue: "Remove quoted view" })}
          >
            <CloseIcon size={14} />
          </button>
        </div>
      )}

      {/* Recipient Selector Row */}
      <div className="mb-2.5 sm:mb-4 flex flex-col sm:flex-row sm:items-center gap-1.5 sm:gap-2">
        <ScrollFade className="-mx-4 sm:mx-0" innerClassName="flex items-center gap-1.5 sm:gap-2 px-4 sm:px-0" fadeWidth={20}>
          <div className={classNames("text-[10px] font-medium uppercase tracking-wide flex-shrink-0", isDark ? "text-[var(--color-text-tertiary)]" : "text-gray-500")}>{t('to', 'To')}</div>

          {/* Group Selector - Styled to match buttons */}
          <div className="relative flex-shrink-0">
            <select
              value={destGroupId || selectedGroupId || ""}
              onChange={(e) => setDestGroupId(e.target.value)}
              style={{ colorScheme: isDark ? "dark" : "light" }}
              className={classNames(
                "appearance-none pr-8 truncate min-w-[120px] max-w-[180px] sm:max-w-[240px]",
                "h-7 sm:h-8 transition-colors cursor-pointer",
                chipBaseClass,
                groupSelectClass
              )}
              disabled={!canChooseDestGroup || groupOptions.length === 0}
              aria-label={t('destinationGroup')}
            >
              {groupOptions.map((g) => (
                <option key={g.gid} value={g.gid}>
                  {g.label}
                </option>
              ))}
            </select>
            <div className={classNames("pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 opacity-60", groupCaretClass)}>
              <ChevronDownIcon size={12} />
            </div>
          </div>

          <div className="w-[1px] h-4 bg-current opacity-10 flex-shrink-0 mx-1 hidden sm:block" />

          {/* Recipients List - Scrollable horizontally on mobile */}
          <div className={classNames(
            "flex items-center gap-1 sm:gap-1.5 min-w-0 transition-opacity",
            recipientActorsBusy ? "opacity-50 pointer-events-none" : ""
          )}>
            {/* Special tokens */}
            {["@all", "@peers"].map((tok) => {
              const active = toTokens.includes(tok);
              return (
                <button
                  key={tok}
                  className={classNames(
                    "h-[26px] sm:h-8",
                    chipBaseClass,
                    active
                      ? "bg-blue-600 text-white border-blue-500 shadow-sm shadow-blue-500/20"
                      : isDark
                        ? "bg-white/[0.08] text-[var(--color-text-secondary)] border-white/[0.1] hover:border-white/[0.16] hover:text-[var(--color-text-primary)]"
                        : "bg-black/5 text-gray-600 border-transparent hover:border-black/10 hover:text-gray-800"
                  )}
                onClick={() => onToggleRecipient(tok)}
                  disabled={!selectedGroupId || busy === "send" || voiceBusy}
                  aria-pressed={active}
                >
                  {getRecipientDisplayLabel(tok)}
                </button>
              );
            })}
            {/* Actor tokens */}
            {recipientActors.map((actor) => {
              const id = String(actor.id || "");
              if (!id) return null;
              const active = toTokens.includes(id);
              return (
                <button
                  key={id}
                  className={classNames(
                    "h-[26px] sm:h-8",
                    chipBaseClass,
                    active
                      ? "bg-blue-600 text-white border-blue-500 shadow-sm shadow-blue-500/20"
                      : isDark
                        ? "bg-white/[0.08] text-[var(--color-text-secondary)] border-white/[0.1] hover:border-white/[0.16] hover:text-[var(--color-text-primary)]"
                        : "bg-black/5 text-gray-600 border-transparent hover:border-black/10 hover:text-gray-800"
                  )}
                  onClick={() => onToggleRecipient(id)}
                  disabled={!selectedGroupId || busy === "send" || voiceBusy || !!recipientActorsBusy}
                  aria-pressed={active}
                >
                  {actor.title || id}
                </button>
              );
            })}

            {toTokens.length > 0 && (
              <button
                className={classNames(
                  "p-2 rounded-full transition-all flex-shrink-0 opacity-40 hover:opacity-100",
                  isDark ? "text-[var(--color-text-tertiary)] hover:bg-white/10 hover:text-[var(--color-text-primary)]" : "hover:bg-black/10"
                )}
                onClick={onClearRecipients}
                disabled={busy === "send" || voiceBusy}
                aria-label={t('clearRecipients')}
                title={t('clearRecipients')}
              >
                <CloseIcon size={14} />
              </button>
            )}
          </div>
        </ScrollFade>
      </div>

      {/* File list */}
      {composerFiles.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
          {composerFiles.map((f, idx) => (
            <div
              key={`${f.name}:${idx}`}
              className={classNames(
                "inline-flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs max-w-full shadow-sm transition-all",
                isDark ? "border-white/10 bg-slate-900/50 text-slate-300" : "border-black/5 bg-gray-50 text-gray-700"
              )}
            >
              <AttachmentIcon size={12} className="opacity-60" />
              <span className="truncate">{f.name}</span>
              <button
                className={classNames(
                  "flex-shrink-0 p-1.5 -mr-1 rounded-full",
                  isDark ? "text-[var(--color-text-tertiary)] hover:bg-white/10 hover:text-[var(--color-text-primary)]" : "hover:bg-black/10 text-gray-400 hover:text-gray-700"
                )}
                onClick={() => onRemoveComposerFile(idx)}
                aria-label={t('removeAttachment', { name: f.name })}
                title={t('removeAttachment', { name: f.name })}
              >
                <CloseIcon size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {modeNotice ? (
        <div
          className={classNames(
            "mb-2 rounded-lg border px-3 py-1.5 text-[11px] leading-5",
            messageMode === "task"
              ? isDark
                ? "border-violet-500/30 bg-violet-500/10 text-violet-200"
                : "border-violet-200 bg-violet-50 text-violet-700"
              : isDark
                ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                : "border-amber-200 bg-amber-50 text-amber-700"
          )}
          role="status"
          aria-live="polite"
        >
          {modeNotice}
        </div>
      ) : null}

      {/* Main Input Area - Perfectly Centered for Better Alignment */}
      <div className="flex gap-2 sm:gap-2.5 relative items-center">
        <input
          ref={fileInputRef as RefObject<HTMLInputElement>}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            const files = Array.from(e.target.files || []);
            if (files.length > 0) appendComposerFiles(files);
            e.target.value = "";
          }}
        />

        {/* Attachment Button */}
        <button
          className={classNames(
            composerToolButtonClass,
            busy !== "send" && !voiceBusy && selectedGroupId && !isCrossGroup && "hover:text-[var(--color-text-primary)] active:scale-95"
          )}
          onClick={() => fileInputRef.current?.click()}
          disabled={!selectedGroupId || busy === "send" || voiceBusy || isCrossGroup}
          aria-label={t('attachFile')}
          title={fileDisabledReason}
        >
          <AttachmentIcon size={18} className="sm:w-5 sm:h-5 transition-transform" />
        </button>

        {/* Text Area Wrapper */}
        <div className="flex-1 relative min-w-0">
          <textarea
            ref={composerRef as RefObject<HTMLTextAreaElement>}
            className={classNames(
                "w-full rounded-xl sm:rounded-2xl border px-3.5 sm:px-5 pr-10 sm:pr-14 py-2.5 sm:py-3.5 text-[15px] sm:text-sm resize-none min-h-[40px] sm:min-h-[48px] max-h-[128px] overflow-y-auto scrollbar-hide transition-all duration-300 ease-out",
                "focus:outline-none focus:ring-2 focus:ring-offset-0 flex items-center shadow-sm",
                isDark
                ? "bg-white/[0.08] border-white/[0.1] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:ring-blue-500/35 focus:border-blue-400/45"
                : "bg-black/5 border-transparent text-gray-900 placeholder-gray-400 focus:ring-blue-400/30 focus:border-blue-400/40"
            )}
            placeholder={isSmallScreen ? t('messagePlaceholder') : t('messagePlaceholderDesktop')}
            rows={1}
            value={composerText}
            disabled={voiceBusy}
            onPaste={handlePaste}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            onBlur={() => setTimeout(() => setShowMentionMenu(false), 150)}
            aria-label={t('messageInput')}
          />

          {/* Message Type Selector */}
          <div ref={modeMenuRef} className="absolute right-2 top-1/2 -translate-y-1/2 z-20">
            <button
              type="button"
              className={classNames(
                composerInlineToolButtonClass,
                "h-8 w-8 sm:h-10 sm:w-10",
                busy === "send" || voiceBusy || !selectedGroupId
                  ? "text-[var(--color-text-tertiary)]"
                  : messageMode === "task"
                    ? isDark
                      ? "border-violet-400/20 bg-violet-500/20 text-violet-200 hover:bg-violet-500/28"
                      : "border-violet-200 bg-violet-100 text-violet-700 hover:bg-violet-200"
                    : messageMode === "attention"
                      ? isDark
                        ? "border-amber-400/20 bg-amber-500/20 text-amber-200 hover:bg-amber-500/28"
                        : "border-amber-200 bg-amber-100 text-amber-700 hover:bg-amber-200"
                      : isDark
                        ? "text-slate-100 hover:bg-slate-700/70"
                        : "text-gray-700 hover:bg-gray-50"
              )}
              disabled={busy === "send" || voiceBusy || !selectedGroupId}
              onClick={() => setShowModeMenu((v) => !v)}
              aria-label={t('messageType')}
              aria-haspopup="menu"
              aria-expanded={showModeMenu}
              title={t('messageMode', { mode: activeMode.label })}
            >
              {messageMode === "task" ? (
                <ReplyIcon size={12} className="opacity-95" />
              ) : messageMode === "attention" ? (
                <AlertIcon size={12} className="opacity-95" />
              ) : (
                <span className="text-[11px] sm:text-xs font-black italic leading-none">N</span>
              )}
            </button>

            {showModeMenu && (
              <div
                className={classNames(
                  "glass-panel absolute bottom-full right-0 mb-2 z-40 w-56 sm:w-64 rounded-2xl border p-1.5 shadow-2xl pointer-events-auto"
                )}
                role="menu"
                aria-label={t('messageTypeOptions')}
              >
                {modeOptions.map((opt) => {
                  const active = messageMode === opt.key;
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      className={classNames(
                        "w-full rounded-xl px-3 py-2.5 text-left flex items-center gap-2.5 transition-colors",
                        active
                          ? isDark
                            ? "bg-white/10"
                            : "bg-black/5"
                          : isDark
                            ? "hover:bg-white/5"
                            : "hover:bg-black/5"
                      )}
                      role="menuitemradio"
                      aria-checked={active}
                      onClick={() => {
                        setMessageMode(opt.key);
                        setShowModeMenu(false);
                      }}
                    >
                      <span
                        className={classNames(
                          "w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0",
                          opt.key === "task"
                            ? isDark
                              ? "bg-violet-500/25 text-violet-200"
                              : "bg-violet-100 text-violet-700"
                            : opt.key === "attention"
                              ? isDark
                                ? "bg-amber-500/25 text-amber-200"
                                : "bg-amber-100 text-amber-700"
                              : isDark
                                ? "bg-slate-700 text-slate-200"
                                : "bg-gray-100 text-gray-700"
                        )}
                      >
                        {opt.key === "task" ? (
                          <ReplyIcon size={13} />
                        ) : opt.key === "attention" ? (
                          <AlertIcon size={13} />
                        ) : (
                          <span className="text-[11px] font-black italic leading-none">N</span>
                        )}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className={classNames("block text-sm font-semibold", isDark ? "text-slate-100" : "text-gray-900")}>
                          {opt.label}
                        </span>
                        <span className={classNames("block text-[11px]", isDark ? "text-[var(--color-text-tertiary)]" : "text-gray-500")}>
                          {opt.description}
                        </span>
                      </span>
                      {active && <span className={classNames("text-xs font-semibold", isDark ? "text-emerald-300" : "text-emerald-600")}>✓</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Mention menu */}
          {showMentionMenu && mentionSuggestions.length > 0 && (
            <div
              className={classNames(
                "glass-panel absolute bottom-full left-0 mb-3 w-64 max-h-60 overflow-auto scrollbar-subtle rounded-2xl border shadow-2xl z-30 animate-in fade-in zoom-in-95 duration-200"
              )}
              role="listbox"
            >
              {mentionSuggestions.slice(0, 8).map((s, idx) => (
                <button
                  key={s}
                  className={classNames(
                    "w-full text-left px-4 py-3 text-sm transition-colors",
                    isDark ? "text-slate-200 border-b border-white/5" : "text-gray-700 border-b border-black/5",
                    idx === mentionSelectedIndex
                      ? isDark ? "bg-blue-600/30 text-blue-300" : "bg-blue-50 text-blue-700 font-medium"
                      : isDark ? "hover:bg-white/5" : "hover:bg-gray-50"
                  )}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    selectMention(s);
                    composerRef.current?.focus();
                  }}
                  onMouseEnter={() => setMentionSelectedIndex(idx)}
                >
                  <span className="opacity-60 mr-1">@</span>{s}
                </button>
              ))}
            </div>
          )}
        </div>

        <button
          className={classNames(
            "rounded-xl sm:rounded-2xl border flex-shrink-0 transition-all duration-300 ease-out",
            showVoiceAction
              ? voiceState === "recording"
                ? "h-10 w-[8.75rem] sm:h-11 sm:w-[11rem] px-3"
                : "h-10 w-10 sm:h-11 sm:w-11"
              : "h-10 w-10 sm:min-w-[6.25rem] sm:h-11 sm:px-3",
            "flex items-center justify-center",
            showVoiceAction
              ? voiceState === "recording"
                ? "border-rose-500 bg-rose-500 text-white shadow-lg shadow-rose-500/30 active:scale-[0.99]"
                : selectedGroupId && !isCrossGroup
                  ? "border-blue-500 bg-blue-600 text-white shadow-lg shadow-blue-500/25 hover:bg-blue-500 active:scale-95"
                  : isDark
                    ? "border-white/[0.12] bg-white/[0.08] text-[var(--color-text-tertiary)] shadow-none disabled:opacity-100"
                    : "border-gray-200 bg-gray-100 text-gray-400 shadow-none disabled:opacity-100"
              : busy === "send" || !canSend
                ? isDark
                  ? "border-white/[0.12] bg-white/[0.08] text-[var(--color-text-tertiary)] shadow-none disabled:opacity-100"
                  : "border-gray-200 bg-gray-100 text-gray-400 shadow-none disabled:opacity-100"
                : "border-blue-500 bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/30 active:scale-95 active:shadow-sm group"
          )}
          onClick={handlePrimaryAction}
          disabled={busy === "send" || (showVoiceAction ? (!selectedGroupId || isCrossGroup) : !canSend)}
          aria-label={primaryButtonTitle}
          title={primaryButtonTitle}
        >
          {busy === "send" ? (
            <div className="w-4 h-4 sm:w-5 sm:h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          ) : showVoiceAction ? (
            voiceState === "recording" ? (
              <span className="flex w-full items-center justify-between gap-2 overflow-hidden">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="relative flex h-2.5 w-2.5 flex-shrink-0">
                    <span className="absolute inset-0 rounded-full bg-white/35 animate-ping" />
                    <span className="relative rounded-full bg-white h-2.5 w-2.5" />
                  </span>
                  <span className="truncate text-[11px] font-semibold sm:text-xs">
                    {t("voiceRecordingActive", { duration: formatRecordingDuration(recordingSeconds) })}
                  </span>
                </span>
                <StopIcon size={16} className="flex-shrink-0" />
              </span>
            ) : (
              <MicrophoneIcon size={18} className="sm:w-5 sm:h-5" />
            )
          ) : (
            <>
              <SendIcon size={18} className="sm:hidden" />
              <span className="hidden sm:inline font-bold">{t('send')}</span>
            </>
          )}
        </button>
      </div>
    </footer>
  );
}
