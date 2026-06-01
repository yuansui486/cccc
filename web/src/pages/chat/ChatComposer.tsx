// ChatComposer renders the chat message composer.
import type { Dispatch, RefObject, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Actor, PresentationMessageRef, ReplyTarget } from "../../types";
import { classNames } from "../../utils/classNames";
import { AttachmentIcon, SendIcon, ChevronDownIcon, ReplyIcon, CloseIcon, SparklesIcon } from "../../components/Icons";
import { ScrollFade } from "../../components/ScrollFade";
import { getPresentationRefChipLabel } from "../../utils/presentationRefs";
import { useTranslation } from 'react-i18next';
import { SlashCommandMenu } from "./SlashCommandMenu";
import { filterSlashCommands, getVisibleSlashCommandPage, type SlashCommandItem, type SlashSkillScope } from "../../utils/slashCommands";

const SLASH_COMMAND_PAGE_SIZE = 8;

export interface ChatComposerProps {
  isDark: boolean;
  isSmallScreen: boolean;
  selectedGroupId: string;
  actors: Actor[];
  recipientActors: Actor[];
  recipientActorsBusy?: boolean;
  destGroupId: string;
  composerGroupSettled: boolean;
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
  selectedSkillCommand: string;
  setSelectedSkillCommand: (command: string) => void;
  priority: "normal" | "attention";
  replyRequired: boolean;
  collaborationRequired: boolean;
  setPriority: (priority: "normal" | "attention") => void;
  setReplyRequired: (value: boolean) => void;
  setCollaborationRequired: (value: boolean) => void;
  onSendMessage: () => void;

  // Mention menu
  showMentionMenu: boolean;
  setShowMentionMenu: Dispatch<SetStateAction<boolean>>;
  mentionSuggestions: string[];
  mentionSelectedIndex: number;
  setMentionSelectedIndex: Dispatch<SetStateAction<number>>;
  setMentionFilter: Dispatch<SetStateAction<string>>;
  onAppendRecipientToken: (token: string) => void;
  slashCommands: SlashCommandItem[];
  allSlashCommands: SlashCommandItem[];
  slashSkillScope: SlashSkillScope;
  setSlashSkillScope: (scope: SlashSkillScope) => void;
}


export function ChatComposer({
  isDark,
  isSmallScreen,
  selectedGroupId,
  actors,
  recipientActors,
  recipientActorsBusy,
  destGroupId,
  composerGroupSettled,
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
  selectedSkillCommand,
  setSelectedSkillCommand,
  priority,
  replyRequired,
  collaborationRequired,
  setPriority,
  setReplyRequired,
  setCollaborationRequired,
  onSendMessage,
  showMentionMenu,
  setShowMentionMenu,
  mentionSuggestions,
  mentionSelectedIndex,
  setMentionSelectedIndex,
  setMentionFilter,
  onAppendRecipientToken,
  slashCommands,
  allSlashCommands,
  slashSkillScope,
  setSlashSkillScope,
}: ChatComposerProps) {
  const composerHeightRef = useRef(0);
  const isUserInputRef = useRef(false);
  const [showSkillMenu, setShowSkillMenu] = useState(false);
  const [skillSearchQuery, setSkillSearchQuery] = useState("");
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const [slashSelectedIndex, setSlashSelectedIndex] = useState(0);
  const [slashVisibleCount, setSlashVisibleCount] = useState(SLASH_COMMAND_PAGE_SIZE);
  const skillMenuRef = useRef<HTMLDivElement | null>(null);
  const { t } = useTranslation('chat');

  const readRootFontScale = () => {
    if (typeof document === "undefined") return 1;
    const rootFontSize = parseFloat(window.getComputedStyle(document.documentElement).fontSize);
    if (!Number.isFinite(rootFontSize) || rootFontSize <= 0) return 1;
    return rootFontSize / 16;
  };

  const [rootFontScale, setRootFontScale] = useState(readRootFontScale);
  const baseComposerHeight = (isSmallScreen ? 44 : 48) * rootFontScale;
  const maxComposerHeight = 128 * rootFontScale;
  const composerFontSize = (isSmallScreen ? 15 : 14) * rootFontScale;
  const composerLineHeight = (isSmallScreen ? 24 : 20) * rootFontScale;

  const resizeComposer = useCallback((node: HTMLTextAreaElement) => {
    node.style.height = "auto";
    const nextHeight = Math.min(Math.max(node.scrollHeight, baseComposerHeight), maxComposerHeight);
    node.style.height = `${nextHeight}px`;
    composerHeightRef.current = nextHeight;
  }, [baseComposerHeight, maxComposerHeight]);

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
        resizeComposer(el);
      });
    });

    return () => cancelAnimationFrame(rafId);
  }, [composerText, composerRef, resizeComposer]);

  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;

    let rafId = 0;
    const observer = new MutationObserver(() => {
      setRootFontScale(readRootFontScale());
      cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        resizeComposer(el);
      });
    });

    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
    return () => {
      cancelAnimationFrame(rafId);
      observer.disconnect();
    };
  }, [composerRef, resizeComposer]);

  useEffect(() => {
    if (!showSkillMenu) return;

    const onPointerDown = (event: MouseEvent | TouchEvent) => {
      const node = skillMenuRef.current;
      if (!node) return;
      const target = event.target;
      if (target instanceof Node && !node.contains(target)) {
        setShowSkillMenu(false);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
    };
  }, [showSkillMenu]);

  const chipBaseClass =
    "flex h-6 flex-shrink-0 items-center justify-center whitespace-nowrap rounded-lg border px-2 text-[10px] font-medium leading-none transition-all sm:px-2.5 sm:text-[11px]";
  const chipActiveClass = isDark
    ? "border-blue-400 bg-blue-500 text-white shadow-[0_6px_16px_-10px_rgba(59,130,246,0.9)]"
    : "border-blue-600 bg-blue-600 text-white shadow-[0_6px_16px_-10px_rgba(37,99,235,0.65)]";
  const chipInactiveClass = isDark
    ? "bg-white/[0.06] text-[var(--color-text-secondary)] border-white/[0.08] hover:bg-white/[0.1] hover:border-white/[0.14] hover:text-[var(--color-text-primary)]"
    : "bg-[rgb(245,245,245)] text-[rgb(35,36,37)] border-transparent hover:bg-[rgb(237,237,237)] hover:border-black/5 hover:text-[rgb(20,20,22)]";
  const skillPickerActiveClass = isDark
    ? "border-white/[0.16] bg-white/[0.09] text-[var(--color-text-primary)] shadow-[0_6px_16px_-12px_rgba(15,23,42,0.9)] hover:bg-white/[0.12]"
    : "border-black/[0.08] bg-[rgb(238,240,243)] text-[rgb(20,24,30)] shadow-[0_6px_16px_-14px_rgba(15,23,42,0.35)] hover:bg-[rgb(230,233,238)]";

  // Get display name for reply target
  const replyByDisplayName = useMemo(() => {
    if (!replyTarget?.by) return "";
    if (replyTarget.by === "user") return "user";
    const actor = actors.find(a => a.id === replyTarget.by);
    return actor?.title || replyTarget.by;
  }, [replyTarget, actors]);
  const quotedPresentationRefLabel = useMemo(
    () => (quotedPresentationRef ? getPresentationRefChipLabel(quotedPresentationRef) : ""),
    [quotedPresentationRef],
  );
  const recipientLabelMap = useMemo(() => {
    const map = new Map<string, { label: string; secondary?: string }>();
    for (const actor of recipientActors) {
      const id = String(actor.id || "").trim();
      if (!id) continue;
      const title = String(actor.title || "").trim();
      map.set(id, title && title !== id ? { label: title, secondary: id } : { label: title || id });
    }
    return map;
  }, [recipientActors]);
  const renderRecipientChipContent = useCallback((label: string) => (
    <span className="truncate">{label}</span>
  ), []);
  const skillCommands = useMemo(
    () => allSlashCommands.filter((item) => item.sourceType === "capsule_skill"),
    [allSlashCommands],
  );
  const scopedSkillCommands = useMemo(
    () => slashCommands.filter((item) => item.sourceType === "capsule_skill"),
    [slashCommands],
  );
  const visibleSkillCommands = useMemo(() => {
    const query = skillSearchQuery.trim().toLowerCase();
    if (!query) return scopedSkillCommands;
    return scopedSkillCommands.filter((item) => {
      const haystacks = [item.displayName || "", item.name, item.command, item.description || "", item.capabilityId]
        .map((value) => String(value || "").toLowerCase());
      return haystacks.some((value) => value.includes(query));
    });
  }, [scopedSkillCommands, skillSearchQuery]);
  const selectedSkill = useMemo(
    () => scopedSkillCommands.find((item) => item.capabilityId === selectedSkillCommand)
      || skillCommands.find((item) => item.capabilityId === selectedSkillCommand)
      || scopedSkillCommands.find((item) => item.command === selectedSkillCommand)
      || skillCommands.find((item) => item.command === selectedSkillCommand)
      || null,
    [scopedSkillCommands, selectedSkillCommand, skillCommands],
  );
  useEffect(() => {
    if (!selectedSkillCommand) return;
    if (scopedSkillCommands.some((item) => item.capabilityId === selectedSkillCommand || item.command === selectedSkillCommand)) return;
    setSelectedSkillCommand("");
  }, [scopedSkillCommands, selectedSkillCommand, setSelectedSkillCommand]);
  const slashSuggestions = useMemo(() => filterSlashCommands(slashCommands, composerText), [composerText, slashCommands]);
  const visibleSlashSuggestions = useMemo(
    () => getVisibleSlashCommandPage(slashSuggestions, slashVisibleCount),
    [slashSuggestions, slashVisibleCount],
  );
  const hasMoreSlashSuggestions = visibleSlashSuggestions.length < slashSuggestions.length;

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
      resizeComposer(target);
    });

    const slashModeActive = val === val.trimStart() && val.startsWith("/") && !val.slice(1).includes(" ");
    if (slashModeActive) {
      const nextSuggestions = filterSlashCommands(slashCommands, val);
      setShowSlashMenu(nextSuggestions.length > 0 || val === "/");
      setSlashSelectedIndex(0);
      setSlashVisibleCount(SLASH_COMMAND_PAGE_SIZE);
      setShowMentionMenu(false);
      return;
    }
    setShowSlashMenu(false);
    setSlashVisibleCount(SLASH_COMMAND_PAGE_SIZE);

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
    if (e.key === "Backspace" && selectedSkillCommand) {
      const target = e.currentTarget;
      const selectionStart = target.selectionStart ?? 0;
      const selectionEnd = target.selectionEnd ?? selectionStart;
      if (selectionStart === 0 && selectionEnd === 0) {
        e.preventDefault();
        setSelectedSkillCommand("");
        return;
      }
    }
    if (showSlashMenu && visibleSlashSuggestions.length > 0) {
      const maxIndex = visibleSlashSuggestions.length - 1;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashSelectedIndex((prev) => {
          const next = prev >= maxIndex ? 0 : prev + 1;
          if (hasMoreSlashSuggestions && next === maxIndex) {
            setSlashVisibleCount((count) => Math.min(count + SLASH_COMMAND_PAGE_SIZE, slashSuggestions.length));
          }
          return next;
        });
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashSelectedIndex((prev) => (prev <= 0 ? maxIndex : prev - 1));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        selectSlashCommand(visibleSlashSuggestions[slashSelectedIndex]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setShowSlashMenu(false);
        setSlashSelectedIndex(0);
        setShowSkillMenu(false);
        return;
      }
    }
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
      if (showSlashMenu) return;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        if (composerGroupSettled) onSendMessage();
      }
    } else if (e.key === "Escape") {
      setShowMentionMenu(false);
      setShowSlashMenu(false);
      setShowModeMenu(false);
      setShowSkillMenu(false);
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

  const selectSlashCommand = (selected: SlashCommandItem | undefined) => {
    if (!selected) return;
    setComposerText(`/${selected.name} `);
    setShowSlashMenu(false);
    setSlashSelectedIndex(0);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const canSend = composerGroupSettled && (composerText.trim() || composerFiles.length > 0);
  const isAttention = priority === "attention";
  const isCrossGroup = !!destGroupId && destGroupId !== selectedGroupId;

  type MessageMode = "normal" | "attention" | "task" | "collaboration";
  const messageMode: MessageMode = replyRequired
    ? "task"
    : collaborationRequired
      ? "collaboration"
      : isAttention
        ? "attention"
        : "normal";
  const toggleAttentionMode = () => {
    if (messageMode === "attention") {
      setPriority("normal");
      setReplyRequired(false);
      setCollaborationRequired(false);
      return;
    }
    setPriority("attention");
    setReplyRequired(false);
    setCollaborationRequired(false);
  };
  const toggleReplyRequiredMode = () => {
    if (messageMode === "task") {
      setPriority("normal");
      setReplyRequired(false);
      setCollaborationRequired(false);
      return;
    }
    setPriority("normal");
    setReplyRequired(true);
    setCollaborationRequired(false);
  };
  const toggleCollaborationRequiredMode = () => {
    if (messageMode === "collaboration") {
      setPriority("normal");
      setReplyRequired(false);
      setCollaborationRequired(false);
      return;
    }
    setPriority("normal");
    setReplyRequired(false);
    setCollaborationRequired(true);
  };

  const fileDisabledReason = (() => {
    if (!selectedGroupId) return t('selectGroupFirst');
    if (busy === "send") return t('busy');
    if (isCrossGroup) return t('crossGroupAttachment');
    return t('attachFile');
  })();
  const sendShortcutLabel = useMemo(() => {
    if (typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || "")) {
      return "⌘+Enter";
    }
    return "Ctrl+Enter";
  }, []);
  const sendButtonTitle = t("sendMessageWithShortcut", {
    shortcut: sendShortcutLabel,
    defaultValue: "Send message ({{shortcut}})",
  });

  return (
    <footer
      className={classNames(
        "relative z-40 flex-shrink-0 border-t px-2 py-1.5 safe-area-bottom-compact transition-colors sm:px-2.5 sm:py-2",
        isDark ? "border-white/5 bg-slate-950/72 backdrop-blur-md" : "border-black/5 bg-white/78 backdrop-blur-md"
      )}
    >
        {/* Reply indicator */}
        {replyTarget && (
          <div className={classNames(
            "mb-2.5 flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px]",
            isDark
              ? "border-white/[0.06] bg-white/[0.035] text-[var(--color-text-tertiary)]"
              : "border-black/[0.05] bg-black/[0.025] text-gray-500"
          )}>
            <ReplyIcon size={12} className="flex-shrink-0 opacity-45" />
            <span className="min-w-0 flex-1 truncate">
              <span className="mr-1 opacity-55">{t('replyingTo')}</span>
              <span className={classNames("font-medium", isDark ? "text-slate-300/90" : "text-gray-700")}>
                {replyByDisplayName}
              </span>
              <span className="mx-1 opacity-40">"</span>
              <span className="opacity-75">{replyTarget.text}</span>
              <span className="opacity-40">"</span>
            </span>
            <button
              className={classNames(
                "rounded-full p-1 transition-colors",
                isDark
                  ? "text-[var(--color-text-tertiary)] hover:bg-white/[0.08] hover:text-[var(--color-text-primary)]"
                  : "text-gray-400 hover:bg-black/[0.06] hover:text-gray-600"
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
              "mb-2.5 flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px]",
              isDark
                ? "border-cyan-400/12 bg-cyan-500/6 text-[var(--color-text-tertiary)]"
                : "border-cyan-200/70 bg-cyan-50/70 text-gray-600",
            )}
          >
            <span className={classNames("flex-shrink-0 font-medium", isDark ? "text-cyan-100/90" : "text-cyan-700")}>
              {t("presentationQuotedViewLabel", { defaultValue: "Quoted view" })}
            </span>
            <span className="min-w-0 flex-1 truncate opacity-80" title={quotedPresentationRef.title || quotedPresentationRefLabel}>
              {quotedPresentationRefLabel}
            </span>
            <button
              className={classNames(
                "rounded-full p-1 transition-colors",
                isDark
                  ? "text-[var(--color-text-tertiary)] hover:bg-white/[0.08] hover:text-[var(--color-text-primary)]"
                  : "text-gray-400 hover:bg-black/[0.06] hover:text-gray-600",
              )}
              onClick={onClearQuotedPresentationRef}
              title={t("presentationRemoveQuotedView", { defaultValue: "Remove quoted view" })}
              aria-label={t("presentationRemoveQuotedView", { defaultValue: "Remove quoted view" })}
            >
              <CloseIcon size={14} />
            </button>
          </div>
        )}

        {/* File list */}
        {composerFiles.length > 0 && (
          <div className="mb-3 flex flex-wrap gap-2 animate-in fade-in slide-in-from-bottom-2 duration-300">
            {composerFiles.map((f, idx) => (
              <div
                key={`${f.name}:${idx}`}
                className={classNames(
                  "inline-flex max-w-full items-center gap-2 rounded-xl border px-3 py-1.5 text-xs shadow-sm transition-all",
                  "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)]"
                )}
              >
                <AttachmentIcon
                  size={12}
                  className="flex-shrink-0 text-[var(--color-text-tertiary)]"
                />
                <span
                  className="truncate font-medium text-[var(--color-text-primary)]"
                  title={f.name}
                >
                  {f.name}
                </span>
                <button
                  className={classNames(
                    "flex-shrink-0 p-1.5 -mr-1 rounded-full",
                    "text-[var(--color-text-tertiary)] hover:bg-[var(--glass-tab-bg-hover)] hover:text-[var(--color-text-primary)]"
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

        {/* Integrated composer */}
        <div className="flex flex-col">
          <div
            className={classNames(
              "relative flex min-w-0 flex-1 flex-col transition-[background-color] duration-200",
              isDark
                ? "bg-white/[0.025] focus-within:bg-white/[0.045]"
                : "bg-white/55 focus-within:bg-white/80",
            )}
          >
            {/* Row 1 — Skill picker and recipients */}
            <div
              className={classNames(
                "flex items-center gap-1.5 border-b px-2.5 py-1",
                isDark ? "border-white/[0.04]" : "border-black/[0.04]",
              )}
            >
              <div ref={skillMenuRef} className="relative flex-shrink-0">
                <button
                  type="button"
                  className={classNames(
                    chipBaseClass,
                    "gap-1.5 px-2.5",
                    selectedSkill ? skillPickerActiveClass : chipInactiveClass,
                  )}
                  onClick={() => setShowSkillMenu((value) => !value)}
                  disabled={busy === "send"}
                  aria-expanded={showSkillMenu}
                  aria-label={t("skillPicker", { defaultValue: "技能选择" })}
                  title={t("skillPicker", { defaultValue: "技能选择" })}
                >
                  <SparklesIcon size={12} />
                  <span className="max-w-[9rem] truncate">
                    {selectedSkill ? (selectedSkill.displayName || selectedSkill.name) : t("skillPicker", { defaultValue: "技能选择" })}
                  </span>
                  <ChevronDownIcon size={12} className={classNames("transition-transform", showSkillMenu ? "rotate-180" : "")} />
                </button>

                {showSkillMenu && (
                  <div
                    className={classNames(
                      "absolute bottom-full left-0 z-40 mb-2 w-[min(22rem,calc(100vw-1rem))] overflow-hidden rounded-2xl border shadow-2xl",
                      isDark
                        ? "border-white/10 bg-slate-950/95 text-slate-100"
                        : "border-black/10 bg-white text-gray-900",
                    )}
                  >
                    <div className={classNames("flex items-center gap-1 border-b p-2", isDark ? "border-white/8" : "border-black/8")}>
                      {(["team", "global"] as SlashSkillScope[]).map((scope) => {
                        const active = slashSkillScope === scope;
                        return (
                          <button
                            key={scope}
                            type="button"
                            className={classNames(
                              "flex-1 rounded-xl px-3 py-1.5 text-xs font-semibold transition-colors",
                              active
                                ? "bg-blue-600 text-white"
                                : isDark
                                  ? "text-slate-400 hover:bg-white/[0.06] hover:text-slate-100"
                                  : "text-gray-500 hover:bg-gray-100 hover:text-gray-900",
                            )}
                            onClick={() => setSlashSkillScope(scope)}
                          >
                            {scope === "team"
                              ? t("teamSkills", { defaultValue: "团队 skills" })
                              : t("globalSkills", { defaultValue: "全局 skills" })}
                          </button>
                        );
                      })}
                    </div>
                    <div className={classNames("border-b p-2", isDark ? "border-white/8" : "border-black/8")}>
                      <input
                        type="search"
                        value={skillSearchQuery}
                        onChange={(event) => setSkillSearchQuery(event.target.value)}
                        onKeyDown={(event) => event.stopPropagation()}
                        placeholder={t("searchSkills", { defaultValue: "搜索 skill" })}
                        className={classNames(
                          "h-8 w-full rounded-xl border px-3 text-xs outline-none transition-colors",
                          isDark
                            ? "border-white/10 bg-white/[0.05] text-slate-100 placeholder:text-slate-500 focus:border-blue-400/60 focus:bg-white/[0.08]"
                            : "border-gray-200 bg-gray-50 text-gray-900 placeholder:text-gray-400 focus:border-blue-300 focus:bg-white",
                        )}
                        autoFocus
                      />
                    </div>
                    <div className="max-h-72 overflow-auto py-1 scrollbar-subtle">
                      {visibleSkillCommands.length === 0 ? (
                        <div className={classNames("px-4 py-6 text-center text-xs", isDark ? "text-slate-400" : "text-gray-500")}>
                          {skillSearchQuery.trim()
                            ? t("noMatchedSkills", { defaultValue: "没有匹配的 skill" })
                            : slashSkillScope === "team"
                              ? t("noTeamSkills", { defaultValue: "当前团队没有可用 skill" })
                              : t("noGlobalSkills", { defaultValue: "没有可用的全局 skill" })}
                        </div>
                      ) : (
                        visibleSkillCommands.map((item) => {
                          const active = selectedSkillCommand === item.capabilityId || selectedSkillCommand === item.command;
                          return (
                            <button
                              key={`${item.capabilityId}:${item.name}`}
                              type="button"
                              className={classNames(
                                "flex w-full items-start gap-3 px-3 py-2.5 text-left transition-colors",
                                active
                                  ? "bg-blue-600 text-white"
                                  : isDark
                                    ? "text-slate-200 hover:bg-white/[0.06]"
                                    : "text-gray-800 hover:bg-gray-50",
                              )}
                              onClick={() => {
                                setSelectedSkillCommand(item.capabilityId);
                                setShowSkillMenu(false);
                                requestAnimationFrame(() => composerRef.current?.focus());
                              }}
                            >
                              <span className={classNames(
                                "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
                                active
                                  ? "bg-white/18 text-white"
                                  : isDark
                                    ? "bg-white/[0.06] text-blue-200"
                                    : "bg-blue-50 text-blue-600",
                              )}>
                                <SparklesIcon size={14} />
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-sm font-semibold">{item.displayName || item.name}</span>
                                {item.description ? (
                                  <span className={classNames(
                                    "mt-0.5 line-clamp-2 block text-xs leading-5",
                                    active ? "text-blue-50/90" : isDark ? "text-slate-400" : "text-gray-500",
                                  )}>
                                    {item.description}
                                  </span>
                                ) : null}
                              </span>
                            </button>
                          );
                        })
                      )}
                    </div>
                  </div>
                )}
              </div>

              <div className="flex flex-shrink-0 items-center gap-1.5">
                <span className={classNames("text-[10px] font-medium tracking-[0.08em]", isDark ? "text-[var(--color-text-tertiary)]" : "text-gray-400")}>
                  {t("messageModeLabel", { defaultValue: "重要度" })}
                </span>
                <button
                  type="button"
                  className={classNames(
                    chipBaseClass,
                    messageMode === "attention"
                      ? isDark
                        ? "border-amber-400 bg-amber-500 text-white shadow-[0_6px_16px_-10px_rgba(251,191,36,0.8)]"
                        : "border-amber-500 bg-amber-500 text-white shadow-[0_6px_16px_-10px_rgba(245,158,11,0.65)]"
                      : chipInactiveClass,
                  )}
                  onClick={toggleAttentionMode}
                  disabled={busy === "send" || !selectedGroupId}
                  aria-pressed={messageMode === "attention"}
                  aria-label={t("modeImportant", { defaultValue: "需确认" })}
                  title={t("modeImportantDesc", { defaultValue: "需要收件人确认收到" })}
                >
                  {t("modeImportant", { defaultValue: "需确认" })}
                </button>
                <button
                  type="button"
                  className={classNames(
                    chipBaseClass,
                    messageMode === "task"
                      ? isDark
                        ? "border-violet-400 bg-violet-500 text-white shadow-[0_6px_16px_-10px_rgba(168,85,247,0.8)]"
                        : "border-violet-500 bg-violet-500 text-white shadow-[0_6px_16px_-10px_rgba(139,92,246,0.65)]"
                      : chipInactiveClass,
                  )}
                  onClick={toggleReplyRequiredMode}
                  disabled={busy === "send" || !selectedGroupId}
                  aria-pressed={messageMode === "task"}
                  aria-label={t("modeNeedReply", { defaultValue: "需回复" })}
                  title={t("modeNeedReplyDesc", { defaultValue: "需要收件人给出具体回复" })}
                >
                  {t("modeNeedReply", { defaultValue: "需回复" })}
                </button>
                <button
                  type="button"
                  className={classNames(
                    chipBaseClass,
                    messageMode === "collaboration"
                      ? isDark
                        ? "border-blue-400 bg-[linear-gradient(180deg,rgba(59,130,246,0.96),rgba(37,99,235,0.90))] text-white shadow-[0_6px_14px_rgba(37,99,235,0.16),0_1px_0_rgba(255,255,255,0.14)]"
                        : "border-blue-500 bg-[linear-gradient(180deg,rgba(59,130,246,0.98),rgba(37,99,235,0.92))] text-white shadow-[0_6px_14px_rgba(37,99,235,0.16),0_1px_0_rgba(255,255,255,0.14)]"
                      : chipInactiveClass,
                  )}
                  onClick={toggleCollaborationRequiredMode}
                  disabled={busy === "send" || !selectedGroupId}
                  aria-pressed={messageMode === "collaboration"}
                  aria-label={t("modeNeedCollaboration", { defaultValue: "需协作" })}
                  title={t("modeNeedCollaborationDesc", { defaultValue: "要求负责人按协作流程拆分、验收并持续推进" })}
                >
                  {t("modeNeedCollaboration", { defaultValue: "需协作" })}
                </button>
              </div>

              <span className={classNames("ml-1 flex-shrink-0 text-[10px] font-medium tracking-[0.08em]", isDark ? "text-[var(--color-text-tertiary)]" : "text-gray-400")}>
                {t('to', 'To')}
              </span>

              <ScrollFade
                className="min-w-0 flex-1"
                innerClassName="w-full max-w-full"
                fadeWidth={20}
              >
                <div
                  className={classNames(
                    "flex min-w-max items-center gap-1 transition-opacity",
                    recipientActorsBusy ? "opacity-50 pointer-events-none" : "",
                  )}
                >
                  {["@all"].map((tok) => {
                    const active = toTokens.includes(tok);
                    return (
                      <button
                        key={tok}
                        className={classNames(
                          chipBaseClass,
                          active
                            ? chipActiveClass
                            : chipInactiveClass,
                        )}
                        onClick={() => onToggleRecipient(tok)}
                        disabled={!selectedGroupId || busy === "send"}
                        aria-pressed={active}
                      >
                        {renderRecipientChipContent(tok)}
                      </button>
                    );
                  })}
                  {recipientActors.map((actor) => {
                    const id = String(actor.id || "");
                    if (!id) return null;
                    const active = toTokens.includes(id);
                    return (
                      <button
                        key={id}
                        className={classNames(
                          chipBaseClass,
                          active
                            ? chipActiveClass
                            : chipInactiveClass,
                        )}
                        onClick={() => onToggleRecipient(id)}
                        disabled={!selectedGroupId || busy === "send" || !!recipientActorsBusy}
                        aria-pressed={active}
                      >
                        {renderRecipientChipContent(actor.title || id)}
                      </button>
                    );
                  })}
                </div>
              </ScrollFade>

              {toTokens.length > 0 && (
                <button
                  className={classNames(
                    "flex-shrink-0 h-7 w-7 rounded-full flex items-center justify-center transition-colors opacity-50 hover:opacity-100",
                    isDark ? "text-[var(--color-text-tertiary)] hover:bg-white/10 hover:text-[var(--color-text-primary)]" : "text-gray-400 hover:bg-black/5 hover:text-gray-700",
                  )}
                  onClick={onClearRecipients}
                  disabled={busy === "send"}
                  aria-label={t('clearRecipients')}
                  title={t('clearRecipients')}
                >
                  <CloseIcon size={12} />
                </button>
              )}
            </div>

            {/* Row 2 — Textarea */}
            <div className="relative min-w-0 flex-1">
              {selectedSkill && (
                <div className="px-4 pt-2.5">
                  <div
                    className={classNames(
                      "inline-flex max-w-full items-center gap-1.5 rounded-lg border px-2 py-1 text-[11px] font-semibold leading-none shadow-sm",
                      isDark
                        ? "border-blue-400/40 bg-blue-500 text-white shadow-blue-950/30"
                        : "border-blue-600 bg-blue-600 text-white shadow-blue-200/70",
                    )}
                  >
                    <SparklesIcon size={11} className="shrink-0" />
                    <span className="min-w-0 truncate">{selectedSkill.displayName || selectedSkill.name}</span>
                    <button
                      type="button"
                      className="rounded-full p-0.5 text-white/80 transition-colors hover:bg-white/18 hover:text-white"
                      onClick={() => setSelectedSkillCommand("")}
                      aria-label={t("removeSelectedSkill", { defaultValue: "移除已选 skill" })}
                      title={t("removeSelectedSkill", { defaultValue: "移除已选 skill" })}
                    >
                      <CloseIcon size={10} />
                    </button>
                  </div>
                </div>
              )}
              <textarea
                ref={composerRef as RefObject<HTMLTextAreaElement>}
                className={classNames(
                  "w-full bg-transparent border-0 px-4 resize-none overflow-y-auto scrollbar-hide focus:outline-none focus:ring-0",
                  selectedSkill ? "pb-3 pt-2" : "py-3",
                  isDark
                    ? "text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)]"
                    : "text-gray-900 placeholder-gray-400",
                )}
                style={{
                  minHeight: `${Math.max(baseComposerHeight + 6, 52)}px`,
                  maxHeight: `${maxComposerHeight}px`,
                  fontSize: `${composerFontSize}px`,
                  lineHeight: `${composerLineHeight}px`,
                }}
                placeholder={isSmallScreen ? t('messagePlaceholder') : t('messagePlaceholderDesktop')}
                rows={1}
                value={composerText}
                onPaste={handlePaste}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
                onBlur={() => setTimeout(() => setShowMentionMenu(false), 150)}
                aria-label={t('messageInput')}
              />

              {/* Mention menu */}
              {showMentionMenu && mentionSuggestions.length > 0 && (
                <div
                  className={classNames(
                    "glass-panel absolute bottom-full left-2 mb-3 w-64 max-h-60 overflow-auto scrollbar-subtle rounded-2xl border shadow-2xl z-30 animate-in fade-in zoom-in-95 duration-200",
                  )}
                  role="listbox"
                >
                  {mentionSuggestions.slice(0, 8).map((s, idx) => (
                    (() => {
                      const option = recipientLabelMap.get(s);
                      const primaryLabel = option?.label || s;
                      const secondaryLabel = option?.secondary;
                      return (
                        <button
                          key={s}
                          className={classNames(
                            "w-full text-left px-4 py-3 text-sm transition-colors",
                            isDark ? "text-slate-200 border-b border-white/5" : "text-gray-700 border-b border-black/5",
                            idx === mentionSelectedIndex
                              ? "bg-[var(--glass-tab-bg-active)] text-[var(--color-text-primary)] font-medium"
                              : isDark ? "hover:bg-white/5" : "hover:bg-gray-50",
                          )}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            selectMention(s);
                            composerRef.current?.focus();
                          }}
                          onMouseEnter={() => setMentionSelectedIndex(idx)}
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="opacity-60 flex-shrink-0">@</span>
                            <div className="min-w-0">
                              <div className="truncate">{primaryLabel}</div>
                              {secondaryLabel ? (
                                <div className={classNames("truncate text-[11px]", isDark ? "text-slate-400" : "text-gray-500")}>
                                  @{secondaryLabel}
                                </div>
                              ) : null}
                            </div>
                          </div>
                        </button>
                      );
                    })()
                  ))}
                </div>
              )}

              {showSlashMenu && visibleSlashSuggestions.length > 0 && (
                <SlashCommandMenu
                  isDark={isDark}
                  suggestions={visibleSlashSuggestions}
                  selectedIndex={Math.min(slashSelectedIndex, visibleSlashSuggestions.length - 1)}
                  hasMore={hasMoreSlashSuggestions}
                  loadMoreLabel={t("slashCommandLoadMore", { defaultValue: "Scroll for more" })}
                  onSelect={selectSlashCommand}
                  onHover={setSlashSelectedIndex}
                  onLoadMore={() => {
                    setSlashVisibleCount((count) => Math.min(count + SLASH_COMMAND_PAGE_SIZE, slashSuggestions.length));
                  }}
                />
              )}
            </div>

            {/* Row 3 — Action bar */}
            <div
              className={classNames(
                "grid grid-cols-[2.75rem_minmax(0,1fr)_2.75rem] items-center gap-2 px-2 pb-2 pt-1 sm:flex sm:justify-between",
              )}
            >
              <button
                className={classNames(
                  "flex h-11 w-11 items-center justify-center rounded-lg border text-[var(--color-text-secondary)] transition-colors disabled:cursor-not-allowed disabled:text-[var(--color-text-tertiary)] disabled:opacity-60 sm:h-9 sm:w-9",
                  isDark ? "border-white/10 bg-white/[0.04]" : "border-black/5 bg-[rgb(245,245,245)]",
                  busy !== "send" && selectedGroupId && !isCrossGroup
                    ? isDark ? "hover:bg-white/10 hover:text-[var(--color-text-primary)]" : "hover:bg-[rgb(237,237,237)] hover:text-gray-800"
                    : "",
                )}
                onClick={() => fileInputRef.current?.click()}
                disabled={!selectedGroupId || busy === "send" || isCrossGroup}
                aria-label={t('attachFile')}
                title={fileDisabledReason}
              >
                <AttachmentIcon size={18} />
              </button>

              <button
                className={classNames(
                  "flex h-11 w-11 items-center justify-center rounded-lg font-semibold transition-[background-color,box-shadow,transform] duration-150 disabled:cursor-not-allowed sm:h-9 sm:w-[5.5rem]",
                  busy === "send" || !canSend
                    ? isDark ? "bg-white/[0.06] text-[var(--color-text-tertiary)]" : "bg-gray-100 text-gray-400"
                    : "border border-blue-600 bg-blue-600 text-white shadow-[var(--glass-accent-shadow)] hover:border-blue-700 hover:bg-blue-700 active:scale-[0.97] dark:border-blue-400 dark:bg-blue-500 dark:hover:border-blue-300 dark:hover:bg-blue-400",
                )}
                onClick={onSendMessage}
                disabled={busy === "send" || !canSend}
                aria-label={t('sendMessage')}
                title={sendButtonTitle}
              >
                {busy === "send" ? (
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                ) : (
                  <>
                    <SendIcon size={16} className="sm:hidden" />
                    <span className="hidden sm:inline">{t('send')}</span>
                  </>
                )}
              </button>
            </div>
          </div>

        </div>
    </footer>
  );
}
