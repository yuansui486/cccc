import { FloatingPortal } from "@floating-ui/react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { classNames } from "../../utils/classNames";
import { CloseIcon, ImageIcon } from "../Icons";

const IMAGE_ASPECT_RATIO_CACHE = new Map<string, number>();
const IMAGE_LOAD_ERROR_CACHE = new Set<string>();

export function ImagePreview({
  href,
  alt,
  isSvg,
  isUserMessage,
  isDark,
}: {
  href: string;
  alt: string;
  isSvg: boolean;
  isUserMessage: boolean;
  isDark: boolean;
}) {
  const [loadError, setLoadError] = useState(() => IMAGE_LOAD_ERROR_CACHE.has(href));
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);
  const [resolvedHref, setResolvedHref] = useState<string>(isSvg ? "" : href);
  const [isResolvingSvg, setIsResolvingSvg] = useState<boolean>(isSvg);
  const [aspectRatio, setAspectRatio] = useState<number | null>(() => IMAGE_ASPECT_RATIO_CACHE.get(href) ?? null);
  const [displaySrc, setDisplaySrc] = useState<string>(isSvg ? "" : href);
  const { t } = useTranslation("chat");

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";

    setLoadError(false);
    if (!isSvg || href.startsWith("blob:") || href.startsWith("data:")) {
      setResolvedHref(href);
      setIsResolvingSvg(false);
      return undefined;
    }

    setIsResolvingSvg(true);

    void (async () => {
      try {
        const resp = await fetch(href, { credentials: "same-origin" });
        if (!resp.ok) {
          throw new Error(`svg_fetch_failed:${resp.status}`);
        }
        const blob = await resp.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!cancelled) {
          setResolvedHref(objectUrl);
          setIsResolvingSvg(false);
        }
      } catch {
        if (!cancelled) {
          setLoadError(true);
          setIsResolvingSvg(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [href, isSvg]);

  useEffect(() => {
    const nextSrc = resolvedHref || href;
    if (!nextSrc) {
      setDisplaySrc("");
      return undefined;
    }
    if (nextSrc === displaySrc) {
      return undefined;
    }
    if (!displaySrc || nextSrc.startsWith("blob:") || nextSrc.startsWith("data:")) {
      setDisplaySrc(nextSrc);
      return undefined;
    }

    let cancelled = false;
    const img = new Image();
    const finalize = () => {
      if (cancelled) return;
      setDisplaySrc(nextSrc);
    };
    const fail = () => {
      if (cancelled) return;
      IMAGE_LOAD_ERROR_CACHE.add(href);
      setLoadError(true);
    };

    img.onload = finalize;
    img.onerror = fail;
    img.src = nextSrc;
    if (typeof img.decode === "function") {
      void img.decode().then(finalize).catch(() => {
        void 0;
      });
    }

    return () => {
      cancelled = true;
      img.onload = null;
      img.onerror = null;
    };
  }, [displaySrc, href, resolvedHref]);

  useEffect(() => {
    if (!isLightboxOpen) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsLightboxOpen(false);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isLightboxOpen]);

  useEffect(() => {
    const src = displaySrc || resolvedHref || href;
    if (!src || isResolvingSvg || IMAGE_LOAD_ERROR_CACHE.has(href)) {
      return undefined;
    }
    const cachedAspectRatio = IMAGE_ASPECT_RATIO_CACHE.get(href);
    if (typeof cachedAspectRatio === "number" && cachedAspectRatio > 0) {
      return undefined;
    }
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      const width = Number(img.naturalWidth || 0);
      const height = Number(img.naturalHeight || 0);
      if (width > 0 && height > 0) {
        const nextAspectRatio = width / height;
        IMAGE_ASPECT_RATIO_CACHE.set(href, nextAspectRatio);
        setAspectRatio(nextAspectRatio);
      }
    };
    img.onerror = () => {
      if (cancelled) return;
      IMAGE_LOAD_ERROR_CACHE.add(href);
      setLoadError(true);
    };
    img.src = src;
    return () => {
      cancelled = true;
    };
  }, [displaySrc, href, isResolvingSvg, resolvedHref]);

  if (loadError) {
    return (
      <a
        href={href}
        className={classNames(
          "inline-flex max-w-full items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors",
          isUserMessage
            ? "bg-blue-700/50 text-white border border-blue-500 hover:bg-blue-700"
            : isDark
              ? "bg-slate-900/50 text-slate-300 border border-slate-700 hover:bg-slate-900"
              : "bg-gray-50 text-gray-700 border border-gray-200 hover:bg-gray-100",
        )}
        title={t("download", { name: alt })}
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
        className={classNames(
          "group overflow-hidden rounded-lg",
          isSvg ? "block" : "inline-flex max-w-[min(22rem,70vw)] sm:max-w-[min(30rem,60vw)]",
        )}
        onClick={() => setIsLightboxOpen(true)}
        aria-label={t("openImagePreview", { name: alt })}
        title={t("openImagePreview", { name: alt })}
        disabled={isResolvingSvg}
        style={isSvg ? { width: "12rem", maxWidth: "100%" } : undefined}
      >
        {isResolvingSvg ? (
          <div
            className={classNames(
              "flex min-h-28 min-w-28 items-center justify-center rounded-lg border px-4 py-6 text-xs",
              isUserMessage
                ? "border-blue-500/50 bg-blue-700/30 text-blue-50"
                : "border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]",
            )}
          >
            {alt}
          </div>
        ) : (
          <img
            src={displaySrc || resolvedHref || href}
            alt={alt}
            className={classNames(
              "cursor-zoom-in rounded-lg object-contain transition-opacity group-hover:opacity-95",
              isSvg ? "block h-auto w-full max-h-64 sm:max-h-80" : "block w-full",
              isSvg
                ? null
                : isUserMessage
                  ? "bg-blue-700/20"
                  : isDark
                    ? "bg-slate-900/40"
                    : "bg-gray-100",
            )}
            style={
              isSvg
                ? undefined
                : {
                    aspectRatio: aspectRatio ?? "4 / 3",
                    maxHeight: "20rem",
                  }
            }
            loading={isSvg ? "lazy" : "eager"}
            decoding="async"
            onError={() => {
              IMAGE_LOAD_ERROR_CACHE.add(href);
              setLoadError(true);
            }}
            onLoad={(event) => {
              if (isSvg) return;
              const target = event.currentTarget;
              const width = Number(target.naturalWidth || 0);
              const height = Number(target.naturalHeight || 0);
              if (width > 0 && height > 0) {
                const nextAspectRatio = width / height;
                IMAGE_ASPECT_RATIO_CACHE.set(href, nextAspectRatio);
                setAspectRatio(nextAspectRatio);
              }
            }}
          />
        )}
      </button>

      {isLightboxOpen && (
        <FloatingPortal>
          <div className="fixed inset-0 z-[80] flex items-center justify-center p-3 sm:p-6 animate-fade-in">
            <button
              type="button"
              className={classNames("absolute inset-0", "glass-overlay")}
              onClick={() => setIsLightboxOpen(false)}
              aria-label={t("common:close")}
            />

            <div
              className={classNames(
                "relative z-[81] flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border shadow-2xl",
                "glass-modal",
              )}
              role="dialog"
              aria-modal="true"
              aria-label={t("imagePreviewDialog")}
              onClick={(event) => event.stopPropagation()}
            >
              <div className={classNames("flex items-center justify-between gap-3 border-b px-4 py-3", "border-[var(--glass-border-subtle)]")}>
                <div className="min-w-0">
                  <p className={classNames("truncate text-sm font-medium", "text-[var(--color-text-primary)]")}>{alt}</p>
                  <p className={classNames("text-xs", "text-[var(--color-text-tertiary)]")}>{t("imagePreviewHint")}</p>
                </div>

                <div className="flex items-center gap-2">
                  <a
                    href={href}
                    download
                    className={classNames(
                      "inline-flex items-center rounded-lg px-3 py-2 text-xs font-medium transition-colors",
                      isDark ? "bg-slate-800 text-slate-100 hover:bg-slate-700" : "bg-gray-100 text-gray-700 hover:bg-gray-200",
                    )}
                    title={t("download", { name: alt })}
                  >
                    {t("download", { name: alt })}
                  </a>

                  <button
                    type="button"
                    onClick={() => setIsLightboxOpen(false)}
                    className={classNames(
                      "inline-flex items-center justify-center rounded-lg p-2 transition-colors",
                      isDark ? "text-slate-300 hover:bg-slate-800 hover:text-slate-100" : "text-gray-500 hover:bg-gray-100 hover:text-gray-700",
                    )}
                    aria-label={t("common:close")}
                  >
                    <CloseIcon size={18} />
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-center overflow-auto p-4 sm:p-6">
                <img
                  src={displaySrc || resolvedHref || href}
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
