import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchPresentationBrowserSurfaceSession } from "../../services/api";
import { classNames } from "../../utils/classNames";
import { shouldPreferPresentationLiveBrowser } from "../../utils/presentation";
import { shouldAutoOpenInteractivePresentation } from "../../utils/presentationLocator";
import { PresentationBrowserSurfacePanel } from "./PresentationBrowserSurfacePanel";
import type { PresentationBrowserFrame } from "./PresentationBrowserSurfacePanel";

type PresentationWebPreviewPanelProps = {
  groupId: string;
  slotId: string;
  title: string;
  href: string;
  isDark: boolean;
  useSandboxedPreview: boolean;
  allowLiveBrowser: boolean;
  refreshNonce: number;
  viewportClassName?: string;
  onInteractiveFrameUpdate?: (frame: PresentationBrowserFrame | null) => void;
};

export function PresentationWebPreviewPanel({
  groupId,
  slotId,
  title,
  href,
  isDark,
  useSandboxedPreview,
  allowLiveBrowser,
  refreshNonce,
  viewportClassName,
  onInteractiveFrameUpdate,
}: PresentationWebPreviewPanelProps) {
  const { t } = useTranslation("chat");
  const preferInteractive = allowLiveBrowser && shouldPreferPresentationLiveBrowser(href);
  const initialMode: "embedded" | "interactive" = preferInteractive ? "interactive" : "embedded";
  const [mode, setMode] = useState<"embedded" | "interactive">(initialMode);

  useEffect(() => {
    let cancelled = false;
    if (!allowLiveBrowser) return;

    const run = async () => {
      const existing = await fetchPresentationBrowserSurfaceSession(groupId, slotId);
      if (cancelled || !existing.ok) return;
      if (shouldAutoOpenInteractivePresentation(allowLiveBrowser, existing.result.browser_surface)) {
        setMode("interactive");
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [allowLiveBrowser, groupId, slotId]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {allowLiveBrowser ? (
        <div
          className={classNames(
            "flex flex-wrap items-center justify-between gap-3 rounded-2xl border px-4 py-3",
            isDark ? "border-white/10 bg-slate-950/60" : "border-black/10 bg-white/85"
          )}
        >
          <button
            type="button"
            onClick={() => setMode((current) => (current === "interactive" ? "embedded" : "interactive"))}
            className={classNames(
              "rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
              mode === "interactive"
                ? isDark
                  ? "bg-slate-900 text-slate-200 hover:bg-slate-800"
                  : "bg-gray-100 text-gray-800 hover:bg-gray-200"
                : isDark
                  ? "bg-emerald-500/20 text-emerald-100 hover:bg-emerald-500/30"
                  : "bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
            )}
          >
            {mode === "interactive"
              ? t("presentationBackToEmbeddedAction", { defaultValue: "Back to standard mode" })
              : t("presentationContinueInCcccAction", { defaultValue: "Switch to enhanced mode" })}
          </button>
          <div className={classNames("text-xs", isDark ? "text-slate-400" : "text-gray-500")}>
            {mode === "interactive"
              ? t("presentationInteractiveModeHelp", {
                  defaultValue: "Enhanced mode works better for local or private pages and tries to keep navigation inside CCCC.",
                })
              : t("presentationEmbeddedModeHelp", {
                  defaultValue: "Standard mode is lightweight. If links jump out or the page cannot load, switch to enhanced mode.",
                })}
          </div>
        </div>
      ) : null}

      {mode === "interactive" && allowLiveBrowser ? (
        <PresentationBrowserSurfacePanel
          groupId={groupId}
          slotId={slotId}
          url={href}
          isDark={isDark}
          refreshNonce={refreshNonce}
          viewportClassName={viewportClassName}
          onFrameUpdate={onInteractiveFrameUpdate}
        />
      ) : (
        <iframe
          key={`embedded:${href}:${refreshNonce}`}
          title={title || t("presentationTypeWebPreview", { defaultValue: "Web" })}
          src={href}
          sandbox={useSandboxedPreview ? "allow-scripts allow-forms allow-modals allow-popups allow-downloads" : undefined}
          className={classNames(
            viewportClassName || "flex-1 min-h-0",
            "w-full flex-1 min-h-0 rounded-3xl border border-[var(--glass-border-subtle)] bg-white"
          )}
        />
      )}
    </div>
  );
}
