import { Suspense, lazy, useEffect, useMemo, useRef } from "react";

import {
  buildHeadlessPreviewRenderGroups,
  buildHeadlessPreviewTimelineEntries,
  type HeadlessPreviewTimelineEntry,
} from "../../pages/chat/headlessPreviewTimeline";
import type { HeadlessPreviewSession, StreamingActivity } from "../../types";
import { classNames } from "../../utils/classNames";
import { formatTime } from "../../utils/time";
import {
  formatStreamingActivityKind,
  getStructuredStreamingActivityLabel,
} from "../messageBubble/helpers";

type HeadlessLiveTraceDensity = "compact" | "expanded";

type HeadlessLiveTraceProps = {
  previewSessions?: HeadlessPreviewSession[];
  fallbackText?: string;
  fallbackActivities?: StreamingActivity[];
  fallbackUpdatedAt?: string;
  fallbackPendingEventId?: string;
  fallbackStreamId?: string;
  fallbackStreamPhase?: string;
  fallbackPhase?: string;
  emptyLabel: string;
  recentLabel?: string;
  isDark: boolean;
  density?: HeadlessLiveTraceDensity;
  className?: string;
};

const LazyMarkdownRenderer = lazy(() =>
  import("../MarkdownRenderer").then((module) => ({ default: module.MarkdownRenderer }))
);

function shouldRenderPreviewMarkdown(streamPhase: string | null | undefined): boolean {
  return String(streamPhase || "").trim().toLowerCase() === "final_answer";
}

function normalizeMetaLines(lines: Array<string | undefined>): string[] {
  const normalized: string[] = [];
  const seen = new Set<string>();
  for (const line of lines) {
    const text = String(line || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    normalized.push(text);
  }
  return normalized;
}

function normalizeInlineText(value: string | undefined): string {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function truncateText(value: string, maxLength: number): string {
  const normalized = normalizeInlineText(value);
  if (!normalized || normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(0, maxLength - 1)).trimEnd()}...`;
}

function getPathTail(path: string, depth = 2): string {
  const normalized = normalizeInlineText(path).replace(/\\/g, "/");
  if (!normalized) return "";
  const segments = normalized.split("/").filter(Boolean);
  if (segments.length <= depth) return normalized;
  return segments.slice(-depth).join("/");
}

function summarizeFilePaths(filePaths: string[], density: HeadlessLiveTraceDensity): string {
  const visibleCount = density === "expanded" ? 2 : 1;
  const visible = filePaths
    .slice(0, visibleCount)
    .map((path) => getPathTail(path, 2))
    .filter(Boolean);
  if (visible.length <= 0) return "";
  const remainingCount = filePaths.length - visible.length;
  return remainingCount > 0 ? `${visible.join(", ")} +${remainingCount}` : visible.join(", ");
}

function buildActivityNarrative(
  activity: StreamingActivity,
  density: HeadlessLiveTraceDensity
): {
  kindLabel: string;
  primaryLabel: string;
  primaryTitle: string;
  metaLines: string[];
  title: string;
} {
  const summary = normalizeInlineText(activity.summary);
  const detail = normalizeInlineText(activity.detail);
  const cwd = normalizeInlineText(activity.cwd);
  const query = normalizeInlineText(activity.query);
  const command = normalizeInlineText(activity.command);
  const structuredLabel = normalizeInlineText(getStructuredStreamingActivityLabel(activity));
  const filePaths = Array.isArray(activity.file_paths)
    ? activity.file_paths.map((path) => normalizeInlineText(path)).filter(Boolean)
    : [];
  const primaryTitle = command
    || (filePaths.length > 0 ? filePaths.join(", ") : "")
    || structuredLabel
    || summary
    || formatStreamingActivityKind(activity.kind);
  const primaryLabel = command
    ? truncateText(command, density === "expanded" ? 92 : 76)
    : filePaths.length > 0
      ? summarizeFilePaths(filePaths, density)
      : truncateText(structuredLabel || summary || formatStreamingActivityKind(activity.kind), density === "expanded" ? 88 : 72);
  const metaLines = normalizeMetaLines([
    summary && summary !== primaryTitle ? summary : undefined,
    detail,
    query && query !== primaryTitle ? query : undefined,
    cwd ? `cwd ${cwd}` : undefined,
  ]).map((line) => truncateText(line, density === "expanded" ? 96 : 72));
  const title = normalizeMetaLines([
    primaryTitle,
    summary && summary !== primaryTitle ? summary : undefined,
    detail,
    query && query !== primaryTitle ? query : undefined,
    cwd ? `cwd ${cwd}` : undefined,
  ]).join("\n");
  return {
    kindLabel: formatStreamingActivityKind(activity.kind),
    primaryLabel: primaryLabel || summary || formatStreamingActivityKind(activity.kind),
    primaryTitle,
    metaLines,
    title,
  };
}

function getMessageTextClassName(density: HeadlessLiveTraceDensity, isDark: boolean): string {
  if (density === "expanded") {
    return classNames(
      "whitespace-pre-wrap break-words text-[13.5px] leading-[1.6]",
      isDark ? "text-slate-100" : "text-gray-900"
    );
  }
  return classNames(
    "whitespace-pre-wrap break-words text-[13px] leading-[1.55]",
    isDark ? "text-slate-100" : "text-gray-900"
  );
}

function getTracePhaseLabel(streamPhase: string): string {
  const normalized = String(streamPhase || "").trim().toLowerCase();
  if (normalized === "final_answer") return "Reply";
  return "";
}

function getActivityBandClassName(density: HeadlessLiveTraceDensity): string {
  return density === "expanded" ? "min-w-0 flex flex-col gap-1.5" : "min-w-0 flex flex-col gap-1";
}

function getActivityRowClassName(density: HeadlessLiveTraceDensity, isDark: boolean, live: boolean): string {
  return classNames(
    "flex min-w-0 items-center gap-2 border",
    density === "expanded"
      ? "min-h-[28px] rounded-xl px-2.5 py-1.5"
      : "min-h-[24px] rounded-lg px-2 py-1",
    live
      ? (isDark ? "border-cyan-300/16 bg-cyan-300/[0.07]" : "border-cyan-500/14 bg-cyan-500/[0.07]")
      : (isDark ? "border-white/8 bg-white/[0.025]" : "border-slate-200 bg-slate-50/90")
  );
}

function getActivityKindBadgeClassName(density: HeadlessLiveTraceDensity, isDark: boolean, live: boolean): string {
  return classNames(
    "inline-flex shrink-0 rounded-full border font-mono font-semibold uppercase tracking-[0.14em]",
    density === "expanded" ? "px-1.5 py-[3px] text-[9px]" : "px-1.5 py-[2px] text-[8px]",
    live
      ? (isDark ? "border-cyan-300/20 bg-cyan-300/10 text-cyan-100" : "border-cyan-500/18 bg-cyan-500/10 text-cyan-700")
      : (isDark ? "border-white/10 bg-white/[0.04] text-slate-300" : "border-slate-200 bg-white text-slate-600")
  );
}

function ActivityRow({
  entry,
  density,
  isDark,
}: {
  entry: Extract<HeadlessPreviewTimelineEntry, { kind: "activity" }>;
  density: HeadlessLiveTraceDensity;
  isDark: boolean;
}) {
  const narrative = buildActivityNarrative(entry.activity, density);
  const trailingMeta = density === "expanded" ? narrative.metaLines[0] || "" : "";

  return (
    <div className={getActivityRowClassName(density, isDark, entry.live)} title={narrative.title || narrative.primaryTitle}>
      <span className={getActivityKindBadgeClassName(density, isDark, entry.live)}>
        {narrative.kindLabel}
      </span>
      <div
        className={classNames(
          "min-w-0 flex-1 truncate whitespace-nowrap font-medium",
          density === "expanded" ? "text-[12.5px] leading-5" : "text-[11.5px] leading-4",
          isDark ? "text-slate-100" : "text-slate-900"
        )}
        title={narrative.primaryTitle}
      >
        {narrative.primaryLabel}
      </div>
      {trailingMeta ? (
        <div
          className={classNames(
            "min-w-0 max-w-[34%] shrink truncate whitespace-nowrap text-[10px] leading-4",
            isDark ? "text-slate-400" : "text-slate-600"
          )}
          title={trailingMeta}
        >
          {trailingMeta}
        </div>
      ) : null}
    </div>
  );
}

export function HeadlessLiveTrace({
  previewSessions,
  fallbackText,
  fallbackActivities,
  fallbackUpdatedAt,
  fallbackPendingEventId,
  fallbackStreamId,
  fallbackStreamPhase,
  fallbackPhase,
  emptyLabel,
  recentLabel = "Recent",
  isDark,
  density = "compact",
  className,
}: HeadlessLiveTraceProps) {
  const outputRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const timelineEntries = useMemo(
    () => buildHeadlessPreviewTimelineEntries({
      previewSessions,
      fallbackText,
      fallbackActivities,
      fallbackUpdatedAt,
      fallbackPendingEventId,
      fallbackStreamId,
      fallbackStreamPhase,
      fallbackPhase,
    }),
    [
      fallbackActivities,
      fallbackPhase,
      fallbackPendingEventId,
      fallbackStreamId,
      fallbackStreamPhase,
      fallbackText,
      fallbackUpdatedAt,
      previewSessions,
    ]
  );
  const timelineGroups = useMemo(() => buildHeadlessPreviewRenderGroups(timelineEntries), [timelineEntries]);
  const sessionCount = Array.isArray(previewSessions) && previewSessions.length > 0
    ? previewSessions.length
    : (timelineEntries.length > 0 ? 1 : 0);
  const latestTimelineGroup = timelineGroups[timelineGroups.length - 1];
  const latestTimelineSignal = latestTimelineGroup
    ? `${latestTimelineGroup.id}:${latestTimelineGroup.ts}:${latestTimelineGroup.live ? "live" : "static"}`
    : "";
  const latestPendingEventId = String(
    latestTimelineGroup?.pendingEventId
    || fallbackPendingEventId
    || previewSessions?.[previewSessions.length - 1]?.pendingEventId
    || ""
  ).trim();

  useEffect(() => {
    shouldStickToBottomRef.current = true;
    const node = outputRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [latestPendingEventId]);

  useEffect(() => {
    const node = outputRef.current;
    if (!node || !shouldStickToBottomRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [latestTimelineSignal, timelineEntries.length]);

  if (timelineGroups.length <= 0) {
    return <div className={classNames("text-sm", isDark ? "text-slate-500" : "text-slate-400", className)}>{emptyLabel}</div>;
  }

  return (
    <div
      ref={outputRef}
      onScroll={() => {
        const node = outputRef.current;
        if (!node) return;
        shouldStickToBottomRef.current = node.scrollTop + node.clientHeight >= node.scrollHeight - 24;
      }}
      className={className}
    >
      <div className={classNames(density === "expanded" ? "space-y-3 pb-3" : "space-y-2.5 pb-3")}>
        {timelineGroups.map((group, index) => {
          const previousGroup = index > 0 ? timelineGroups[index - 1] : null;
          const showSessionDivider = sessionCount > 1 && previousGroup?.pendingEventId !== group.pendingEventId;
          return (
            <div key={group.id} className={classNames(density === "expanded" ? "space-y-3" : "space-y-2.5")}>
              {showSessionDivider ? (
                <div className="flex items-center gap-3 py-1">
                  <span className={classNames("h-px flex-1", isDark ? "bg-white/8" : "bg-slate-200")} />
                  <span className={classNames("shrink-0 text-[10px] font-medium uppercase tracking-[0.14em]", isDark ? "text-slate-500" : "text-slate-500")}>
                    {group.ts ? formatTime(group.ts) : recentLabel}
                  </span>
                  <span className={classNames("h-px flex-1", isDark ? "bg-white/8" : "bg-slate-200")} />
                </div>
              ) : null}

              {group.kind === "message" ? (
                <div className={classNames("grid gap-3", density === "expanded" ? "grid-cols-[12px_minmax(0,1fr)]" : "grid-cols-[10px_minmax(0,1fr)]")}>
                  <div className={classNames(density === "expanded" ? "pt-[0.55rem]" : "pt-2")}>
                    <span
                      className={classNames(
                        "block rounded-full",
                        density === "expanded" ? "h-3 w-3" : "h-2.5 w-2.5",
                        group.entry.streamPhase === "final_answer"
                          ? (group.entry.live ? (isDark ? "bg-sky-300" : "bg-sky-500") : (isDark ? "bg-sky-300/50" : "bg-sky-500/45"))
                          : (group.entry.live ? (isDark ? "bg-cyan-300" : "bg-cyan-500") : (isDark ? "bg-white/30" : "bg-slate-400"))
                      )}
                    />
                  </div>
                  <div className={classNames(
                    "min-w-0 border-l pl-3",
                    density === "expanded" ? "pb-1" : "",
                    group.entry.streamPhase === "final_answer"
                      ? (isDark ? "border-sky-300/30" : "border-sky-500/26")
                      : (isDark ? "border-white/10" : "border-slate-200")
                  )}>
                    {getTracePhaseLabel(group.entry.streamPhase) ? (
                      <div className={classNames(
                        "mb-1 text-[10px] font-semibold uppercase tracking-[0.14em]",
                        isDark ? "text-sky-200" : "text-sky-700"
                      )}>
                        {getTracePhaseLabel(group.entry.streamPhase)}
                      </div>
                    ) : null}
                    {shouldRenderPreviewMarkdown(group.entry.streamPhase) ? (
                      <Suspense fallback={<div className={getMessageTextClassName(density, isDark)}>{group.entry.text}</div>}>
                        <LazyMarkdownRenderer
                          content={group.entry.text}
                          isDark={isDark}
                          invertText={isDark}
                          className={classNames(
                            density === "expanded"
                              ? "text-[13.5px] leading-[1.6] [&_p]:mb-2.5 [&_pre]:border [&_pre]:p-3 [&_.code-block-header]:rounded-t-2xl [&_.code-block-header]:border [&_.code-block-header]:px-3 [&_.code-block-header]:py-2"
                              : "text-[13px] leading-[1.55] [&_p]:mb-2 [&_pre]:border [&_pre]:p-3 [&_.code-block-header]:rounded-t-2xl [&_.code-block-header]:border [&_.code-block-header]:px-3 [&_.code-block-header]:py-2",
                            isDark
                              ? "[&_pre]:border-white/10 [&_pre]:bg-white/[0.03] [&_.code-block-header]:border-white/10 [&_.code-block-header]:bg-white/[0.04] [&_.code-block-header]:text-slate-300"
                              : "[&_pre]:border-slate-200 [&_pre]:bg-white [&_.code-block-header]:border-slate-200 [&_.code-block-header]:bg-slate-100 [&_.code-block-header]:text-slate-600"
                          )}
                        />
                      </Suspense>
                    ) : (
                      <div className={getMessageTextClassName(density, isDark)}>{group.entry.text}</div>
                    )}
                  </div>
                </div>
              ) : (
                <div className={classNames("grid gap-3", density === "expanded" ? "grid-cols-[12px_minmax(0,1fr)]" : "grid-cols-[10px_minmax(0,1fr)]")}>
                  <div className={classNames(density === "expanded" ? "pt-[0.55rem]" : "pt-[0.4rem]")}>
                    <span className={classNames(
                      "block rounded-full",
                      density === "expanded" ? "h-3 w-3" : "h-2.5 w-2.5",
                      group.live ? (isDark ? "bg-cyan-300" : "bg-cyan-500") : (isDark ? "bg-white/20" : "bg-slate-400")
                    )} />
                  </div>
                  <div className={getActivityBandClassName(density)}>
                    {group.entries.map((entry) => (
                      <ActivityRow
                        key={entry.id}
                        entry={entry}
                        density={density}
                        isDark={isDark}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
