import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import {
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  useFloating,
} from "@floating-ui/react";

import { ActorAvatar } from "../../components/ActorAvatar";
import { HeadlessLiveTrace } from "../../components/headless/HeadlessLiveTrace";
import { PlusIcon } from "../../components/Icons";
import { useActorDisplayState } from "../../hooks/useActorDisplayState";
import type { StreamingActivity, Actor } from "../../types";
import { classNames } from "../../utils/classNames";
import { formatTime } from "../../utils/time";
import type { LiveWorkCard } from "./liveWorkCards";
import { buildRuntimeDockItems, type RuntimeDockItem } from "./runtimeDockItems";

type PreviewSource = "hover" | "focus" | "auto";

type PreviewState = {
  groupId: string;
  actorId: string;
  source: PreviewSource;
} | null;

type RuntimeRingTone = "stopped" | "ready" | "queued" | "working" | "streaming" | "drafting" | "failed";

type RuntimeRingPresentation = {
  ringClassName: string;
  ringStyle: CSSProperties;
  haloClassName?: string;
  haloStyle?: CSSProperties;
  secondaryRingClassName?: string;
  secondaryRingStyle?: CSSProperties;
  previewBorderClassName: string;
  statusPillClassName: string;
  unreadBadgeClassName: string;
};

type HeadlessPreviewData = {
  previewSessions: NonNullable<LiveWorkCard["previewSessions"]>;
  fallbackActivities: StreamingActivity[];
  latestText: string;
};

const EMPTY_ACTIVITIES: StreamingActivity[] = [];
const EMPTY_PREVIEW_SESSIONS: NonNullable<LiveWorkCard["previewSessions"]> = [];
const AUTO_PREVIEW_CLOSE_DELAY_MS = 1800;

const RING_MASK_STYLE: CSSProperties = {
  WebkitMask: "radial-gradient(farthest-side, transparent calc(100% - 4px), #000 calc(100% - 3px))",
  mask: "radial-gradient(farthest-side, transparent calc(100% - 4px), #000 calc(100% - 3px))",
};

function buildMaskedRing(background: string): CSSProperties {
  return {
    ...RING_MASK_STYLE,
    background,
  };
}

function getRuntimeStatusLabel(
  isRunning: boolean,
  workingState: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  if (!isRunning) return t("stopped", { defaultValue: "Stopped" });
  if (workingState === "working") return t("working", { defaultValue: "Working" });
  return t("running", { defaultValue: "Running" });
}

function getLiveWorkBadgeLabel(card: LiveWorkCard, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (card.phase === "failed") {
    return t("liveWorkPhaseFailed", { defaultValue: "Failed" });
  }
  if (card.streamPhase === "final_answer") {
    return t("liveWorkPhaseFinalAnswer", { defaultValue: "Drafting reply" });
  }
  if (card.streamPhase === "commentary") {
    return t("liveWorkPhaseCommentary", { defaultValue: "Streaming commentary" });
  }
  if (card.phase === "pending") {
    return t("liveWorkPhaseQueued", { defaultValue: "Queued" });
  }
  if (card.phase === "completed") {
    return t("liveWorkPhaseCompleted", { defaultValue: "Recent" });
  }
  return t("liveWorkPhaseWorking", { defaultValue: "Working" });
}

function getRunnerLabel(runner: RuntimeDockItem["runner"], t: (key: string, options?: Record<string, unknown>) => string): string {
  if (runner === "headless") {
    return t("runtimeDockRunnerHeadless", { defaultValue: "Headless" });
  }
  return t("runtimeDockRunnerPty", { defaultValue: "PTY" });
}

function getRuntimeRingTone(item: RuntimeDockItem, isRunning: boolean, workingState: string): RuntimeRingTone {
  if (item.liveWorkCard?.phase === "failed") return "failed";
  if (item.liveWorkCard?.phase === "pending") return "queued";
  if (item.liveWorkCard?.streamPhase === "final_answer") return "drafting";
  if (item.liveWorkCard?.phase === "streaming" || item.liveWorkCard?.streamPhase === "commentary") return "streaming";
  if (workingState === "working") return "working";
  if (isRunning) return "ready";
  return "stopped";
}

function getRuntimeRingPresentation(tone: RuntimeRingTone, isDark: boolean): RuntimeRingPresentation {
  switch (tone) {
    case "queued":
      return {
        ringClassName: "absolute -inset-[3px] rounded-full animate-[spin_10s_linear_infinite] motion-reduce:animate-none",
        ringStyle: buildMaskedRing(
          "conic-gradient(from 90deg, transparent 0deg 40deg, rgba(245,158,11,0.96) 40deg 126deg, transparent 126deg 190deg, rgba(251,191,36,0.72) 190deg 260deg, transparent 260deg 360deg)"
        ),
        haloClassName: classNames(
          "absolute -inset-[6px] rounded-full blur-md",
          isDark ? "bg-amber-300/14" : "bg-amber-400/18"
        ),
        previewBorderClassName: isDark ? "border-amber-300/28" : "border-amber-500/24",
        statusPillClassName: isDark
          ? "border border-amber-300/16 bg-amber-300/10 text-amber-100"
          : "border border-amber-500/18 bg-amber-500/10 text-amber-700",
        unreadBadgeClassName: isDark ? "bg-amber-300/18 text-amber-50" : "bg-amber-500/14 text-amber-700",
      };
    case "working":
      return {
        ringClassName: "absolute -inset-[3px] rounded-full animate-[spin_5s_linear_infinite] motion-reduce:animate-none",
        ringStyle: buildMaskedRing(
          "conic-gradient(from 120deg, rgba(16,185,129,0.18) 0deg, rgba(16,185,129,0.98) 148deg, rgba(45,212,191,0.85) 246deg, rgba(16,185,129,0.22) 360deg)"
        ),
        haloClassName: classNames(
          "absolute -inset-[7px] rounded-full blur-md animate-pulse motion-reduce:animate-none",
          isDark ? "bg-emerald-300/18" : "bg-emerald-400/20"
        ),
        previewBorderClassName: isDark ? "border-emerald-300/28" : "border-emerald-500/24",
        statusPillClassName: isDark
          ? "border border-emerald-300/16 bg-emerald-300/10 text-emerald-100"
          : "border border-emerald-500/18 bg-emerald-500/10 text-emerald-700",
        unreadBadgeClassName: isDark ? "bg-emerald-300/18 text-emerald-50" : "bg-emerald-500/14 text-emerald-700",
      };
    case "streaming":
      return {
        ringClassName: "absolute -inset-[3px] rounded-full animate-[spin_3.2s_linear_infinite] motion-reduce:animate-none",
        ringStyle: buildMaskedRing(
          "conic-gradient(from 64deg, transparent 0deg 30deg, rgba(34,211,238,0.98) 30deg 116deg, transparent 116deg 172deg, rgba(56,189,248,0.92) 172deg 254deg, transparent 254deg 308deg, rgba(103,232,249,0.8) 308deg 360deg)"
        ),
        haloClassName: classNames(
          "absolute -inset-[7px] rounded-full blur-md animate-pulse motion-reduce:animate-none",
          isDark ? "bg-cyan-300/18" : "bg-cyan-400/18"
        ),
        previewBorderClassName: isDark ? "border-cyan-300/28" : "border-cyan-500/24",
        statusPillClassName: isDark
          ? "border border-cyan-300/16 bg-cyan-300/10 text-cyan-100"
          : "border border-cyan-500/18 bg-cyan-500/10 text-cyan-700",
        unreadBadgeClassName: isDark ? "bg-cyan-300/18 text-cyan-50" : "bg-cyan-500/14 text-cyan-700",
      };
    case "drafting":
      return {
        ringClassName: "absolute -inset-[3px] rounded-full animate-[spin_2.6s_linear_infinite] motion-reduce:animate-none",
        ringStyle: buildMaskedRing(
          "conic-gradient(from 54deg, transparent 0deg 20deg, rgba(59,130,246,0.98) 20deg 132deg, transparent 132deg 190deg, rgba(14,165,233,0.9) 190deg 300deg, transparent 300deg 360deg)"
        ),
        secondaryRingClassName:
          "absolute -inset-[5px] rounded-full animate-[spin_7s_linear_infinite_reverse] motion-reduce:animate-none",
        secondaryRingStyle: buildMaskedRing(
          "conic-gradient(from 180deg, rgba(125,211,252,0.18) 0deg 44deg, transparent 44deg 204deg, rgba(37,99,235,0.76) 204deg 296deg, transparent 296deg 360deg)"
        ),
        haloClassName: classNames(
          "absolute -inset-[8px] rounded-full blur-md animate-pulse motion-reduce:animate-none",
          isDark ? "bg-sky-300/18" : "bg-sky-400/18"
        ),
        previewBorderClassName: isDark ? "border-sky-300/28" : "border-sky-500/24",
        statusPillClassName: isDark
          ? "border border-sky-300/16 bg-sky-300/10 text-sky-100"
          : "border border-sky-500/18 bg-sky-500/10 text-sky-700",
        unreadBadgeClassName: isDark ? "bg-sky-300/18 text-sky-50" : "bg-sky-500/14 text-sky-700",
      };
    case "failed":
      return {
        ringClassName: "absolute -inset-[3px] rounded-full",
        ringStyle: buildMaskedRing(
          "conic-gradient(from 190deg, transparent 0deg 38deg, rgba(248,113,113,0.96) 38deg 122deg, transparent 122deg 196deg, rgba(239,68,68,0.92) 196deg 258deg, transparent 258deg 360deg)"
        ),
        haloClassName: classNames(
          "absolute -inset-[6px] rounded-full blur-md animate-pulse motion-reduce:animate-none",
          isDark ? "bg-rose-300/14" : "bg-rose-400/16"
        ),
        previewBorderClassName: isDark ? "border-rose-300/28" : "border-rose-500/24",
        statusPillClassName: isDark
          ? "border border-rose-300/16 bg-rose-300/10 text-rose-100"
          : "border border-rose-500/18 bg-rose-500/10 text-rose-700",
        unreadBadgeClassName: isDark ? "bg-rose-300/18 text-rose-50" : "bg-rose-500/14 text-rose-700",
      };
    case "ready":
      return {
        ringClassName: "absolute -inset-[3px] rounded-full",
        ringStyle: buildMaskedRing(
          "conic-gradient(from 180deg, rgba(34,197,94,0.16) 0deg, rgba(34,197,94,0.84) 180deg, rgba(34,197,94,0.18) 360deg)"
        ),
        previewBorderClassName: isDark ? "border-emerald-300/20" : "border-emerald-500/18",
        statusPillClassName: isDark
          ? "border border-emerald-300/14 bg-emerald-300/8 text-emerald-100"
          : "border border-emerald-500/14 bg-emerald-500/8 text-emerald-700",
        unreadBadgeClassName: isDark ? "bg-white/10 text-slate-100" : "bg-black/8 text-gray-800",
      };
    case "stopped":
    default:
      return {
        ringClassName: "absolute -inset-[3px] rounded-full",
        ringStyle: buildMaskedRing(
          isDark
            ? "conic-gradient(from 180deg, rgba(148,163,184,0.28) 0deg, rgba(148,163,184,0.56) 180deg, rgba(148,163,184,0.22) 360deg)"
            : "conic-gradient(from 180deg, rgba(148,163,184,0.18) 0deg, rgba(148,163,184,0.48) 180deg, rgba(148,163,184,0.16) 360deg)"
        ),
        previewBorderClassName: isDark ? "border-white/12" : "border-black/10",
        statusPillClassName: isDark
          ? "border border-white/10 bg-white/5 text-slate-200"
          : "border border-black/8 bg-black/5 text-gray-700",
        unreadBadgeClassName: isDark ? "bg-white/10 text-slate-100" : "bg-black/8 text-gray-800",
      };
  }
}

function getHeadlessPreviewData(item: RuntimeDockItem, args: {
  fallbackText?: string;
}): HeadlessPreviewData {
  const fallbackText = String(item.liveWorkCard?.text || args.fallbackText || "").trim();
  const fallbackActivities = Array.isArray(item.liveWorkCard?.activities) ? item.liveWorkCard?.activities || EMPTY_ACTIVITIES : EMPTY_ACTIVITIES;
  const previewSessions = Array.isArray(item.liveWorkCard?.previewSessions) && item.liveWorkCard.previewSessions.length > 0
    ? item.liveWorkCard.previewSessions
    : EMPTY_PREVIEW_SESSIONS;
  return {
    previewSessions,
    fallbackActivities,
    latestText: fallbackText,
  };
}

function isHeadlessCardPreviewActive(card: LiveWorkCard | null | undefined): boolean {
  if (!card) return false;
  return card.phase === "pending" || card.phase === "streaming";
}

function hasTranscriptContent(card: LiveWorkCard | null | undefined): boolean {
  if (!card) return false;
  if (Array.isArray(card.previewSessions) && card.previewSessions.some((session) => {
    const hasSessionTranscript = Array.isArray(session.transcriptBlocks)
      && session.transcriptBlocks.some((block) => String(block.text || "").trim());
    return hasSessionTranscript || Boolean(String(session.latestText || "").trim());
  })) return true;
  return card.transcriptBlocks.some((block) => String(block.text || "").trim()) || Boolean(String(card.text || "").trim());
}

function hasSubstantiveActivity(card: LiveWorkCard | null | undefined): boolean {
  if (!card) return false;
  if (Array.isArray(card.previewSessions) && card.previewSessions.some((session) =>
    session.activities.some((activity) => {
      const summary = String(activity.summary || "").trim();
      const kind = String(activity.kind || "").trim().toLowerCase();
      return summary && !(kind === "queued" && summary.toLowerCase() === "queued");
    })
  )) return true;
  return card.activities.some((activity) => {
    const summary = String(activity.summary || "").trim();
    const kind = String(activity.kind || "").trim().toLowerCase();
    return summary && !(kind === "queued" && summary.toLowerCase() === "queued");
  });
}

function shouldAutoPreviewCard(card: LiveWorkCard | null | undefined): boolean {
  if (!card || !isHeadlessCardPreviewActive(card)) return false;
  return hasTranscriptContent(card) || hasSubstantiveActivity(card);
}

function getHeadlessPreviewSignal(card: LiveWorkCard): string {
  return [
    String(card.actorId || "").trim(),
    String(card.pendingEventId || card.streamId || "").trim(),
  ].join(":");
}

function HeadlessPreviewSurface({
  item,
  data,
  statusLabel,
  runnerLabel,
  isDark,
  borderClassName,
  statusPillClassName,
}: {
  item: RuntimeDockItem;
  data: HeadlessPreviewData;
  statusLabel: string;
  runnerLabel: string;
  isDark: boolean;
  borderClassName: string;
  statusPillClassName: string;
}) {
  const { t } = useTranslation(["chat", "actors"]);
  const updatedAt = String(item.liveWorkCard?.updatedAt || "").trim();
  const updatedLabel = updatedAt
    ? t("chat:updated", { time: formatTime(updatedAt), defaultValue: `Updated ${formatTime(updatedAt)}` })
    : "";

  return (
    <section
      className={classNames(
        "w-[min(96vw,780px)] rounded-[28px] border px-5 py-5 shadow-[0_38px_120px_-52px_rgba(15,23,42,0.82)]",
        isDark ? "bg-slate-950/90 backdrop-blur-2xl backdrop-saturate-150" : "bg-white/90 backdrop-blur-2xl backdrop-saturate-150",
        borderClassName,
      )}
      aria-label={t("chat:runtimeDockPreviewLabel", {
        name: item.actorLabel,
        defaultValue: `${item.actorLabel} preview`,
      })}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex flex-1 items-center gap-2.5 overflow-hidden whitespace-nowrap">
          <div className={classNames("min-w-0 flex-1 truncate text-base font-semibold", isDark ? "text-slate-50" : "text-gray-950")}>
            {item.actorLabel}
          </div>
          <span
            className={classNames(
              "shrink-0 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.14em]",
              isDark ? "border-cyan-300/20 bg-cyan-300/10 text-cyan-100" : "border-cyan-500/18 bg-cyan-500/10 text-cyan-700"
            )}
          >
            {runnerLabel}
          </span>
          <span
            className={classNames(
              "shrink-0 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.14em]",
              isDark ? "border-white/10 bg-white/5 text-slate-300" : "border-black/8 bg-black/5 text-gray-600"
            )}
          >
            {item.runtime}
          </span>
          <span className={classNames("shrink-0 rounded-full px-2.5 py-1 text-[10px] font-medium", statusPillClassName)}>
            {statusLabel}
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-2 whitespace-nowrap">
          {updatedLabel ? (
            <div className={classNames("text-[11px]", isDark ? "text-slate-500" : "text-gray-500")}>
              {updatedLabel}
            </div>
          ) : null}
          {item.unreadCount > 0 ? (
            <span
              className={classNames(
                "inline-flex h-6 min-w-[24px] items-center justify-center rounded-full px-2 text-[11px] font-bold",
                isDark ? "bg-white/10 text-slate-100" : "bg-black/8 text-gray-800"
              )}
            >
              {item.unreadCount}
            </span>
          ) : null}
        </div>
      </div>

      <HeadlessLiveTrace
        previewSessions={data.previewSessions}
        fallbackText={data.latestText}
        fallbackActivities={data.fallbackActivities}
        fallbackUpdatedAt={String(item.liveWorkCard?.updatedAt || "").trim()}
        fallbackPendingEventId={String(item.liveWorkCard?.pendingEventId || item.liveWorkCard?.streamId || `preview:${item.actorId}`).trim()}
        fallbackStreamId={String(item.liveWorkCard?.streamId || "").trim()}
        fallbackStreamPhase={String(item.liveWorkCard?.streamPhase || "").trim().toLowerCase()}
        fallbackPhase={String(item.liveWorkCard?.phase || "").trim().toLowerCase()}
        emptyLabel={t("actors:noStreamingOutputYet", { defaultValue: "There is no streaming output to show yet." })}
        recentLabel={t("chat:liveWorkPhaseCompleted", { defaultValue: "Recent" })}
        isDark={isDark}
        density="compact"
        className={classNames(
          "mt-4 min-h-[320px] max-h-[min(68vh,560px)] overflow-y-auto pr-1",
          isDark ? "text-slate-100" : "text-gray-900"
        )}
      />
    </section>
  );
}

function PtyPreviewSurface({
  item,
  statusLabel,
  runnerLabel,
  isDark,
  borderClassName,
  statusPillClassName,
}: {
  item: RuntimeDockItem;
  statusLabel: string;
  runnerLabel: string;
  isDark: boolean;
  borderClassName: string;
  statusPillClassName: string;
}) {
  const { t } = useTranslation("chat");
  const updatedAt = String(item.actor.effective_working_updated_at || item.actor.updated_at || "").trim();
  const updatedLabel = updatedAt
    ? t("updated", { time: formatTime(updatedAt), defaultValue: `Updated ${formatTime(updatedAt)}` })
    : "";

  return (
    <section
      className={classNames(
        "w-[min(94vw,420px)] rounded-[24px] border px-4 py-3.5 shadow-[0_30px_84px_-48px_rgba(15,23,42,0.82)]",
        isDark ? "bg-slate-950/90 backdrop-blur-2xl backdrop-saturate-150" : "bg-white/90 backdrop-blur-2xl backdrop-saturate-150",
        borderClassName,
      )}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex flex-1 items-center gap-2 overflow-hidden whitespace-nowrap">
          <div className={classNames("min-w-0 flex-1 truncate text-base font-semibold", isDark ? "text-slate-50" : "text-gray-950")}>
            {item.actorLabel}
          </div>
          <span
            className={classNames(
              "shrink-0 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.14em]",
              isDark ? "border-white/10 bg-white/6 text-slate-200" : "border-black/8 bg-black/5 text-gray-700"
            )}
          >
            {runnerLabel}
          </span>
          <span
            className={classNames(
              "shrink-0 rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.14em]",
              isDark ? "border-white/10 bg-white/5 text-slate-300" : "border-black/8 bg-black/5 text-gray-600"
            )}
          >
            {item.runtime}
          </span>
          <span className={classNames("shrink-0 rounded-full px-2.5 py-1 text-[10px] font-medium", statusPillClassName)}>
            {statusLabel}
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-2 whitespace-nowrap">
          {updatedLabel ? (
            <div className={classNames("text-[11px]", isDark ? "text-slate-500" : "text-gray-500")}>
              {updatedLabel}
            </div>
          ) : null}
          {item.unreadCount > 0 ? (
            <span className={classNames("inline-flex h-6 min-w-[24px] items-center justify-center rounded-full px-2 text-[11px] font-bold", isDark ? "bg-white/10 text-slate-100" : "bg-black/8 text-gray-800")}>
              {item.unreadCount}
            </span>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function RuntimeDockActorButton({
  groupId,
  item,
  isDark,
  isSmallScreen,
  isPreviewVisible,
  isInspectorOpen,
  selectedGroupRunning,
  selectedGroupActorsHydrating,
  onOpenPreview,
  onSchedulePreviewClose,
  onClosePreview,
  onOpenInspector,
}: {
  groupId: string;
  item: RuntimeDockItem;
  isDark: boolean;
  isSmallScreen: boolean;
  isPreviewVisible: boolean;
  isInspectorOpen: boolean;
  selectedGroupRunning: boolean;
  selectedGroupActorsHydrating: boolean;
  onOpenPreview: (actorId: string, source: PreviewSource) => void;
  onSchedulePreviewClose: (actorId: string, source: PreviewSource) => void;
  onClosePreview: (actorId: string, source: PreviewSource) => void;
  onOpenInspector: (actorId: string) => void;
}) {
  const { t } = useTranslation(["chat", "actors"]);
  const { isRunning, workingState } = useActorDisplayState({
    groupId,
    actor: item.actor,
    selectedGroupRunning,
    selectedGroupActorsHydrating,
  });
  const ringTone = getRuntimeRingTone(item, isRunning, workingState);
  const ringPresentation = getRuntimeRingPresentation(ringTone, isDark);
  const runnerLabel = getRunnerLabel(item.runner, (key, options) => t(`chat:${key}`, options));
  const statusLabel = item.liveWorkCard
    ? getLiveWorkBadgeLabel(item.liveWorkCard, (key, options) => t(`chat:${key}`, options))
    : getRuntimeStatusLabel(isRunning, workingState, (key, options) => t(`actors:${key}`, options));
  const headlessPreviewData = getHeadlessPreviewData(item, {});
  const hasLiveIndicator = shouldAutoPreviewCard(item.liveWorkCard);

  const { refs, floatingStyles } = useFloating({
    open: isPreviewVisible,
    placement: "top",
    middleware: [offset(16), flip({ padding: 12 }), shift({ padding: 12 })],
    whileElementsMounted: autoUpdate,
    strategy: "fixed",
  });
  const setReference = useCallback((node: HTMLButtonElement | null) => refs.setReference(node), [refs]);
  const setFloating = useCallback((node: HTMLDivElement | null) => refs.setFloating(node), [refs]);

  const handleOpenInspector = () => {
    onClosePreview(item.actorId, "auto");
    onClosePreview(item.actorId, "focus");
    onClosePreview(item.actorId, "hover");
    onOpenInspector(item.actorId);
  };

  return (
    <div
      className="relative flex items-end"
      onMouseEnter={() => {
        if (isSmallScreen) return;
        onOpenPreview(item.actorId, "hover");
      }}
      onMouseLeave={() => onSchedulePreviewClose(item.actorId, "hover")}
    >
      {isPreviewVisible ? (
        <FloatingPortal>
          <div
            ref={setFloating}
            style={floatingStyles}
            className="pointer-events-auto z-40"
            onMouseEnter={() => onOpenPreview(item.actorId, "hover")}
            onMouseLeave={() => onSchedulePreviewClose(item.actorId, "hover")}
          >
            <div className="animate-scale-in origin-bottom">
              {item.runner === "headless" ? (
              <HeadlessPreviewSurface
                item={item}
                data={headlessPreviewData}
                statusLabel={statusLabel}
                runnerLabel={runnerLabel}
                isDark={isDark}
                borderClassName={ringPresentation.previewBorderClassName}
                statusPillClassName={ringPresentation.statusPillClassName}
              />
            ) : (
              <PtyPreviewSurface
                item={item}
                statusLabel={statusLabel}
                runnerLabel={runnerLabel}
                isDark={isDark}
                borderClassName={ringPresentation.previewBorderClassName}
                statusPillClassName={ringPresentation.statusPillClassName}
              />
            )}
          </div>
          </div>
        </FloatingPortal>
      ) : null}

      <button
        ref={setReference}
        type="button"
        onClick={handleOpenInspector}
        onFocus={() => onOpenPreview(item.actorId, "focus")}
        onBlur={() => onClosePreview(item.actorId, "focus")}
        className={classNames(
          "group relative flex h-[52px] w-[52px] items-center justify-center rounded-full border shadow-[0_20px_44px_-30px_rgba(15,23,42,0.68)] backdrop-blur-xl transition-all duration-200 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/55 focus-visible:ring-offset-0",
          item.runner === "headless"
            ? isDark
              ? "border-cyan-300/16 bg-slate-950/86 hover:bg-slate-950/94"
              : "border-cyan-500/18 bg-white/96 hover:bg-white"
            : isDark
              ? "border-white/10 bg-slate-950/84 hover:bg-slate-950/94"
              : "border-black/10 bg-white/94 hover:bg-white",
          isInspectorOpen ? "scale-[1.05] shadow-[0_18px_40px_-28px_rgba(14,165,233,0.65)]" : "hover:scale-[1.03]",
        )}
        aria-label={item.runner === "headless"
          ? t("chat:runtimeDockOpenLiveWork", {
              name: item.actorLabel,
              defaultValue: `Open live work for ${item.actorLabel}`,
            })
          : t("chat:runtimeDockOpenTerminal", {
              name: item.actorLabel,
              defaultValue: `Open terminal for ${item.actorLabel}`,
            })}
      >
        {ringPresentation.haloClassName ? (
          <span className={classNames("pointer-events-none", ringPresentation.haloClassName)} style={ringPresentation.haloStyle} />
        ) : null}
        <span className={classNames("pointer-events-none", ringPresentation.ringClassName)} style={ringPresentation.ringStyle} />
        {ringPresentation.secondaryRingClassName ? (
          <span className={classNames("pointer-events-none", ringPresentation.secondaryRingClassName)} style={ringPresentation.secondaryRingStyle} />
        ) : null}

        <ActorAvatar
          avatarUrl={item.actor.avatar_url || undefined}
          runtime={item.runtime}
          title={item.actorLabel}
          isDark={isDark}
          sizeClassName={isSmallScreen ? "h-9 w-9" : "h-10 w-10"}
          className={classNames(
            "relative z-10 shadow-[0_18px_34px_-22px_rgba(15,23,42,0.68)]",
            item.runner === "headless"
              ? isDark
                ? "bg-slate-900"
                : "bg-slate-50"
              : undefined
          )}
          accentRingClassName={isInspectorOpen ? (isDark ? "ring-white/12" : "ring-black/8") : null}
        />

        {item.unreadCount > 0 ? (
          <span className={classNames("absolute -right-1 -top-1 z-20 inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1.5 text-[10px] font-bold shadow-sm", ringPresentation.unreadBadgeClassName)}>
            {item.unreadCount}
          </span>
        ) : null}

        {hasLiveIndicator ? (
          <span className={classNames(
            "absolute -bottom-1 left-1/2 z-20 inline-flex -translate-x-1/2 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] shadow-sm",
            isDark ? "border-cyan-300/20 bg-slate-950/92 text-cyan-100" : "border-cyan-500/18 bg-white text-cyan-700"
          )}>
            <span className="h-1.5 w-1.5 rounded-full bg-current animate-pulse motion-reduce:animate-none" />
            {t("chat:liveWorkPhaseWorking", { defaultValue: "Live" })}
          </span>
        ) : null}
      </button>
    </div>
  );
}

export interface RuntimeDockProps {
  groupId: string;
  runtimeActors: Actor[];
  liveWorkCards: LiveWorkCard[];
  activeRuntimeActorId?: string;
  isDark: boolean;
  isSmallScreen: boolean;
  readOnly?: boolean;
  selectedGroupRunning: boolean;
  selectedGroupActorsHydrating: boolean;
  onAddAgent?: () => void;
  onOpenRuntimeActor: (actorId: string) => void;
}

export function RuntimeDock({
  groupId,
  runtimeActors,
  liveWorkCards,
  activeRuntimeActorId,
  isDark,
  isSmallScreen,
  readOnly,
  selectedGroupRunning,
  selectedGroupActorsHydrating,
  onAddAgent,
  onOpenRuntimeActor,
}: RuntimeDockProps) {
  const { t } = useTranslation("chat");
  const [preview, setPreview] = useState<PreviewState>(null);
  const previewCloseTimerRef = useRef<number | null>(null);
  const previewSyncTimerRef = useRef<number | null>(null);
  const lastAutoPreviewSignalRef = useRef("");

  const items = useMemo(() => buildRuntimeDockItems({ actors: runtimeActors, liveWorkCards }), [runtimeActors, liveWorkCards]);
  const autoPreviewCandidate = useMemo(() => {
    if (isSmallScreen) return null;
    const runtimeActorIds = new Set(runtimeActors.map((actor) => String(actor.id || "").trim()).filter(Boolean));
    const card = liveWorkCards.find((candidate) => {
      const actorId = String(candidate.actorId || "").trim();
      return actorId && runtimeActorIds.has(actorId) && shouldAutoPreviewCard(candidate);
    });
    if (!card) return null;
    return {
      actorId: String(card.actorId || "").trim(),
      signal: `${String(groupId || "").trim()}:${getHeadlessPreviewSignal(card)}`,
    };
  }, [groupId, isSmallScreen, liveWorkCards, runtimeActors]);
  const clearPreviewCloseTimer = useCallback(() => {
    if (previewCloseTimerRef.current === null) return;
    window.clearTimeout(previewCloseTimerRef.current);
    previewCloseTimerRef.current = null;
  }, []);
  const clearPreviewSyncTimer = useCallback(() => {
    if (previewSyncTimerRef.current === null) return;
    window.clearTimeout(previewSyncTimerRef.current);
    previewSyncTimerRef.current = null;
  }, []);
  const openPreview = useCallback((actorId: string, source: PreviewSource) => {
    clearPreviewCloseTimer();
    clearPreviewSyncTimer();
    setPreview({ actorId, source, groupId });
  }, [clearPreviewCloseTimer, clearPreviewSyncTimer, groupId]);

  const closePreviewNow = useCallback((actorId: string, source: PreviewSource) => {
    clearPreviewCloseTimer();
    clearPreviewSyncTimer();
    setPreview((current) => {
      if (!current) return current;
      if (current.actorId !== actorId) return current;
      if (current.groupId !== groupId) return current;
      if (current.source !== source) return current;
      return null;
    });
  }, [clearPreviewCloseTimer, clearPreviewSyncTimer, groupId]);

  const schedulePreviewClose = useCallback((actorId: string, source: PreviewSource) => {
    clearPreviewCloseTimer();
    clearPreviewSyncTimer();
    previewCloseTimerRef.current = window.setTimeout(() => {
      previewCloseTimerRef.current = null;
      setPreview((current) => {
        if (!current) return current;
        if (current.actorId !== actorId) return current;
        if (current.groupId !== groupId) return current;
        if (current.source !== source) return current;
        return null;
      });
    }, source === "hover" ? 220 : (source === "auto" ? AUTO_PREVIEW_CLOSE_DELAY_MS : 0));
  }, [clearPreviewCloseTimer, clearPreviewSyncTimer, groupId]);
  const visiblePreview = useMemo(() => {
    if (!preview || preview.groupId !== groupId) return null;
    return items.some((item) => item.actorId === preview.actorId) ? preview : null;
  }, [groupId, items, preview]);

  useEffect(() => () => {
    clearPreviewCloseTimer();
    clearPreviewSyncTimer();
  }, [clearPreviewCloseTimer, clearPreviewSyncTimer]);

  useEffect(() => {
    if (activeRuntimeActorId && preview?.source === "auto") {
      clearPreviewCloseTimer();
      clearPreviewSyncTimer();
      const actorId = preview.actorId;
      previewSyncTimerRef.current = window.setTimeout(() => {
        previewSyncTimerRef.current = null;
        closePreviewNow(actorId, "auto");
      }, 0);
      return clearPreviewSyncTimer;
    }
  }, [activeRuntimeActorId, clearPreviewCloseTimer, clearPreviewSyncTimer, closePreviewNow, preview?.actorId, preview?.source]);

  useEffect(() => {
    if (activeRuntimeActorId) return;
    if (!autoPreviewCandidate) {
      if (preview?.source === "auto" && previewCloseTimerRef.current === null) {
        previewCloseTimerRef.current = window.setTimeout(() => {
          previewCloseTimerRef.current = null;
          setPreview((current) => {
            if (!current || current.source !== "auto") return current;
            return null;
          });
        }, AUTO_PREVIEW_CLOSE_DELAY_MS);
      }
      return;
    }
    if (preview && preview.source !== "auto") return;
    if (lastAutoPreviewSignalRef.current === autoPreviewCandidate.signal) return;
    lastAutoPreviewSignalRef.current = autoPreviewCandidate.signal;
    clearPreviewCloseTimer();
    clearPreviewSyncTimer();
    const actorId = autoPreviewCandidate.actorId;
    previewSyncTimerRef.current = window.setTimeout(() => {
      previewSyncTimerRef.current = null;
      openPreview(actorId, "auto");
    }, 0);
    return clearPreviewSyncTimer;
  }, [activeRuntimeActorId, autoPreviewCandidate, clearPreviewCloseTimer, clearPreviewSyncTimer, openPreview, preview]);

  if (items.length <= 0) return null;

  return (
    <div className="pointer-events-none relative z-30 px-3 sm:px-4">
      <div className="mx-auto flex w-full max-w-[1400px] justify-center">
        <div className={classNames("pointer-events-auto flex items-end", isSmallScreen ? "max-w-[calc(100vw-2.5rem)] gap-2.5 overflow-x-auto pb-1 scrollbar-hide" : "gap-3.5") }>
          {items.map((item) => (
            <RuntimeDockActorButton
              key={item.actorId}
              groupId={groupId}
              item={item}
              isDark={isDark}
              isSmallScreen={isSmallScreen}
              isPreviewVisible={visiblePreview?.actorId === item.actorId}
              isInspectorOpen={activeRuntimeActorId === item.actorId}
              selectedGroupRunning={selectedGroupRunning}
              selectedGroupActorsHydrating={selectedGroupActorsHydrating}
              onOpenPreview={openPreview}
              onSchedulePreviewClose={schedulePreviewClose}
              onClosePreview={closePreviewNow}
              onOpenInspector={onOpenRuntimeActor}
            />
          ))}

          {!readOnly && onAddAgent ? (
            <button
              type="button"
              onClick={onAddAgent}
              className={classNames(
                "relative flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-full border shadow-[0_18px_40px_-28px_rgba(15,23,42,0.68)] transition-all duration-200",
                isDark ? "border-white/10 bg-slate-950/86 text-slate-100 hover:bg-slate-950" : "border-black/8 bg-white text-gray-900 hover:bg-white",
              )}
              aria-label={t("addAgent", { defaultValue: "Add an agent" })}
              title={t("addAgent", { defaultValue: "Add an agent" })}
            >
              <PlusIcon size={18} />
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
