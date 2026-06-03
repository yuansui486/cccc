import { useMemo } from "react";
import { useTheme } from "../../hooks/useTheme";
import { useViewportHeight } from "../../hooks/useViewportHeight";
import { classNames } from "../../utils/classNames";
import { RemotionEditorPage } from "./RemotionEditorPage";

function getUrlSpecPath(): string {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.search);
  return String(params.get("spec") || "").trim();
}

export function VideoEditorStandaloneApp() {
  const { isDark } = useTheme();
  useViewportHeight();

  const specPath = useMemo(() => getUrlSpecPath(), []);
  const headerExtra = specPath ? (
    <span className="max-w-[320px] truncate rounded-lg border border-[var(--glass-border-subtle)] px-2 py-1.5 text-xs font-medium text-[var(--color-text-muted)]" title={specPath}>
      {specPath}
    </span>
  ) : null;

  return (
    <div
      className={classNames(
        "h-full min-h-0 w-full overflow-hidden",
        isDark ? "bg-[#101113] text-slate-100" : "bg-[#f5f6f8] text-slate-950",
      )}
      style={{ height: "calc(100% - var(--vk-offset, 0px))" }}
    >
      <RemotionEditorPage isDark={isDark} specPath={specPath} headerExtra={headerExtra} />
    </div>
  );
}
