import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type WheelEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  fetchPresentationBrowserSurfaceSession,
  getPresentationBrowserSurfaceWebSocketUrl,
  startPresentationBrowserSurfaceSession,
} from "../../services/api";
import type { PresentationBrowserSurfaceState } from "../../types";
import { classNames } from "../../utils/classNames";

type PresentationBrowserSurfacePanelProps = {
  groupId: string;
  slotId: string;
  url: string;
  isDark: boolean;
  refreshNonce: number;
  viewportClassName?: string;
  onFrameUpdate?: (frame: PresentationBrowserFrame | null) => void;
};

export type PresentationBrowserFrame = {
  seq: number;
  dataUrl: string;
  width: number;
  height: number;
  capturedAt: string;
  url: string;
};

type BrowserEventPayload =
  | ({ t: "state" } & PresentationBrowserSurfaceState)
  | {
      t: "frame";
      seq?: number;
      data_base64?: string | null;
      width?: number;
      height?: number;
      captured_at?: string | null;
      url?: string | null;
      mime?: string | null;
    }
  | {
      t: "error";
      code?: string | null;
      message?: string | null;
    };

const SPECIAL_KEY_MAP: Record<string, string> = {
  Enter: "Enter",
  Tab: "Tab",
  Backspace: "Backspace",
  Escape: "Escape",
  Delete: "Delete",
  ArrowUp: "ArrowUp",
  ArrowDown: "ArrowDown",
  ArrowLeft: "ArrowLeft",
  ArrowRight: "ArrowRight",
  Home: "Home",
  End: "End",
  PageUp: "PageUp",
  PageDown: "PageDown",
};

function normalizeState(raw: PresentationBrowserSurfaceState | null | undefined): PresentationBrowserSurfaceState {
  return {
    active: !!raw?.active,
    state: String(raw?.state || "idle").trim() || "idle",
    message: String(raw?.message || "").trim(),
    error: raw?.error
      ? {
          code: String(raw.error.code || "").trim(),
          message: String(raw.error.message || "").trim(),
        }
      : null,
    strategy: String(raw?.strategy || "").trim(),
    url: String(raw?.url || "").trim(),
    width: Number.isFinite(Number(raw?.width)) ? Number(raw?.width) : 0,
    height: Number.isFinite(Number(raw?.height)) ? Number(raw?.height) : 0,
    started_at: String(raw?.started_at || "").trim(),
    updated_at: String(raw?.updated_at || "").trim(),
    last_frame_seq: Number.isFinite(Number(raw?.last_frame_seq)) ? Number(raw?.last_frame_seq) : 0,
    last_frame_at: String(raw?.last_frame_at || "").trim(),
    controller_attached: !!raw?.controller_attached,
  };
}

function buttonFromMouseEvent(button: number): "left" | "middle" | "right" {
  if (button === 1) return "middle";
  if (button === 2) return "right";
  return "left";
}

export function PresentationBrowserSurfacePanel({
  groupId,
  slotId,
  url,
  isDark,
  refreshNonce,
  viewportClassName,
  onFrameUpdate,
}: PresentationBrowserSurfacePanelProps) {
  const { t } = useTranslation("chat");
  const containerRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const resizeTimerRef = useRef<number | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const runIdRef = useRef(0);
  const lastRefreshNonceRef = useRef(refreshNonce);

  const [runNonce, setRunNonce] = useState(0);
  const [sessionState, setSessionState] = useState<PresentationBrowserSurfaceState>(() =>
    normalizeState({
      active: true,
      state: "starting",
      message: "",
    })
  );
  const [frame, setFrame] = useState<PresentationBrowserFrame | null>(null);
  const [panelError, setPanelError] = useState("");

  useEffect(() => {
    return () => {
      onFrameUpdate?.(null);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        ws.close(1000, "Browser surface cleanup");
      }
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = null;
      }
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };
  }, [onFrameUpdate]);

  const sendCommand = (payload: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(payload));
  };

  useEffect(() => {
    const runId = runIdRef.current + 1;
    runIdRef.current = runId;
    let disposed = false;

    const cleanupTransport = () => {
      const ws = wsRef.current;
      if (ws) {
        wsRef.current = null;
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close(1000, "Browser surface cleanup");
        }
      }
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = null;
      }
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const attachSocket = () => {
      const ws = new WebSocket(getPresentationBrowserSurfaceWebSocketUrl(groupId, slotId));
      wsRef.current = ws;

      ws.onmessage = (event) => {
        if (disposed || runIdRef.current !== runId) return;
        try {
          const payload = JSON.parse(String(event.data || "")) as BrowserEventPayload;
          if (payload.t === "state") {
            setSessionState(normalizeState(payload));
            if (payload.state === "failed") {
              const message = String(payload.error?.message || payload.message || "").trim();
              if (message) setPanelError(message);
            }
            return;
          }
          if (payload.t === "frame") {
            const rawBase64 = String(payload.data_base64 || "").trim();
            if (!rawBase64) return;
            const mime = String(payload.mime || "image/jpeg").trim() || "image/jpeg";
            const nextFrame = {
              seq: Number(payload.seq || 0) || 0,
              dataUrl: `data:${mime};base64,${rawBase64}`,
              width: Number(payload.width || 0) || 0,
              height: Number(payload.height || 0) || 0,
              capturedAt: String(payload.captured_at || "").trim(),
              url: String(payload.url || "").trim(),
            };
            setFrame(nextFrame);
            onFrameUpdate?.(nextFrame);
            return;
          }
          if (payload.t === "error") {
            const message = String(payload.message || "").trim();
            if (message) setPanelError(message);
          }
        } catch {
          // Ignore malformed websocket payloads.
        }
      };

      ws.onclose = () => {
        if (disposed || runIdRef.current !== runId) return;
        wsRef.current = null;
        void (async () => {
          const info = await fetchPresentationBrowserSurfaceSession(groupId, slotId);
          if (disposed || runIdRef.current !== runId) return;
          if (
            info.ok &&
            info.result.browser_surface.active &&
            (info.result.browser_surface.state === "ready" || info.result.browser_surface.state === "starting") &&
            reconnectAttemptsRef.current < 3
          ) {
            reconnectAttemptsRef.current += 1;
            setSessionState(
              normalizeState({
                ...info.result.browser_surface,
                message: t("presentationBrowserReconnecting", { defaultValue: "Reconnecting interactive view..." }),
              })
            );
            reconnectTimerRef.current = window.setTimeout(() => {
              reconnectTimerRef.current = null;
              attachSocket();
            }, 300);
            return;
          }
          if (info.ok) {
            setSessionState(
              normalizeState({
                ...info.result.browser_surface,
                active: false,
                state: info.result.browser_surface.state === "failed" ? "failed" : "closed",
                message:
                  info.result.browser_surface.state === "failed"
                    ? info.result.browser_surface.message
                    : t("presentationBrowserClosed", { defaultValue: "Interactive view closed." }),
              })
            );
            const message = String(
              info.result.browser_surface.error?.message || info.result.browser_surface.message || ""
            ).trim();
            if (message && info.result.browser_surface.state === "failed") {
              setPanelError(message);
            }
            return;
          }
          setSessionState(
            normalizeState({
              active: false,
              state: "closed",
              message: t("presentationBrowserClosed", { defaultValue: "Interactive view closed." }),
              error: { code: info.error.code, message: info.error.message },
            })
          );
          setPanelError(`${info.error.code}: ${info.error.message}`);
        })();
      };
    };

    const open = async () => {
      reconnectAttemptsRef.current = 0;
      setPanelError("");
      setFrame(null);
      onFrameUpdate?.(null);
      const container = containerRef.current;
      const width = Math.max(960, Math.round(container?.clientWidth || 1280));
      const height = Math.max(640, Math.round(container?.clientHeight || 800));
      const existing = await fetchPresentationBrowserSurfaceSession(groupId, slotId);
      if (disposed) return;

      if (
        existing.ok &&
        existing.result.browser_surface.active &&
        ["starting", "ready"].includes(String(existing.result.browser_surface.state || "").trim())
      ) {
        setSessionState(normalizeState(existing.result.browser_surface));
        attachSocket();
        return;
      }

      const started = await startPresentationBrowserSurfaceSession(groupId, {
        slotId,
        url,
        width,
        height,
      });
      if (disposed) {
        return;
      }
      if (!started.ok) {
        const message = `${started.error.code}: ${started.error.message}`;
        setPanelError(message);
        setSessionState(
          normalizeState({
            active: false,
            state: "failed",
            message,
            error: { code: started.error.code, message: started.error.message },
          })
        );
        return;
      }

      setSessionState(normalizeState(started.result.browser_surface));
      attachSocket();
    };

    void open();

    return () => {
      disposed = true;
      cleanupTransport();
    };
  }, [groupId, onFrameUpdate, runNonce, slotId, t, url]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;

    const sendResize = () => {
      const width = Math.max(640, Math.round(container.clientWidth || 0));
      const height = Math.max(480, Math.round(container.clientHeight || 0));
      if (!width || !height) return;
      sendCommand({ t: "resize", width, height });
    };

    const scheduleResize = () => {
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
      }
      resizeTimerRef.current = window.setTimeout(() => {
        resizeTimerRef.current = null;
        sendResize();
      }, 120);
    };

    const observer = new ResizeObserver(() => {
      scheduleResize();
    });
    observer.observe(container);
    scheduleResize();
    return () => {
      observer.disconnect();
      if (resizeTimerRef.current !== null) {
        window.clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = null;
      }
    };
  }, []);

  const handleBack = () => {
    setPanelError("");
    sendCommand({ t: "back" });
  };

  const handleReconnect = () => {
    setFrame(null);
    setPanelError("");
    reconnectAttemptsRef.current = 0;
    setSessionState(
      normalizeState({
        active: true,
        state: "starting",
        message: t("presentationBrowserStarting", { defaultValue: "Preparing interactive view..." }),
      })
    );
    setRunNonce((value) => value + 1);
  };

  useEffect(() => {
    if (refreshNonce === lastRefreshNonceRef.current) return;
    lastRefreshNonceRef.current = refreshNonce;
    if (sessionState.state === "failed" || sessionState.state === "closed") {
      const timer = window.setTimeout(() => {
        setFrame(null);
        setPanelError("");
        reconnectAttemptsRef.current = 0;
        setSessionState(
          normalizeState({
            active: true,
            state: "starting",
            message: t("presentationBrowserStarting", { defaultValue: "Preparing interactive view..." }),
          })
        );
        setRunNonce((value) => value + 1);
      }, 0);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => {
      setPanelError("");
      sendCommand({ t: "refresh" });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshNonce, sessionState.state, t]);

  const handleMouseDown = (event: MouseEvent<HTMLImageElement>) => {
    if (!frame || !imageRef.current) return;
    const rect = imageRef.current.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0 || frame.width <= 0 || frame.height <= 0) return;
    const relativeX = (event.clientX - rect.left) / rect.width;
    const relativeY = (event.clientY - rect.top) / rect.height;
    if (relativeX < 0 || relativeX > 1 || relativeY < 0 || relativeY > 1) return;
    sendCommand({
      t: "click",
      x: Math.round(relativeX * frame.width),
      y: Math.round(relativeY * frame.height),
      button: buttonFromMouseEvent(event.button),
    });
    containerRef.current?.focus();
    event.preventDefault();
  };

  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    sendCommand({
      t: "scroll",
      dx: Math.round(event.deltaX),
      dy: Math.round(event.deltaY),
    });
    event.preventDefault();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.metaKey || event.ctrlKey) return;
    if (!event.altKey && event.key.length === 1) {
      sendCommand({ t: "text", text: event.key });
      event.preventDefault();
      return;
    }
    const special = SPECIAL_KEY_MAP[event.key];
    if (!special) return;
    sendCommand({ t: "key", key: special });
    event.preventDefault();
  };

  const showReconnect = sessionState.state === "failed" || sessionState.state === "closed";

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onWheel={handleWheel}
      onKeyDown={handleKeyDown}
      className={classNames(
        viewportClassName || "flex-1 min-h-0",
        "relative flex flex-col overflow-hidden rounded-3xl border outline-none",
        isDark ? "border-white/10 bg-slate-950/80" : "border-black/10 bg-[linear-gradient(180deg,#ffffff_0%,#f6f8fb_100%)]"
      )}
    >
      <div
        className={classNames(
          "flex flex-wrap items-center gap-2 border-b px-4 py-3 text-xs",
          isDark ? "border-white/10 bg-slate-950/70 text-slate-300" : "border-black/10 bg-white/75 text-gray-700"
        )}
      >
        <span
          className={classNames(
            "rounded-full px-2.5 py-1 font-medium",
            sessionState.state === "ready"
              ? isDark
                ? "bg-emerald-500/15 text-emerald-200"
                : "bg-emerald-50 text-emerald-700"
              : sessionState.state === "failed"
                ? isDark
                  ? "bg-rose-500/15 text-rose-200"
                  : "bg-rose-50 text-rose-700"
                : isDark
                  ? "bg-cyan-500/15 text-cyan-200"
                  : "bg-cyan-50 text-cyan-700"
          )}
        >
          {sessionState.state === "ready"
            ? t("presentationBrowserReady", { defaultValue: "Interactive view ready" })
            : sessionState.state === "failed"
              ? t("presentationBrowserFailed", { defaultValue: "Interactive view failed" })
              : sessionState.state === "closed"
                ? t("presentationBrowserClosedShort", { defaultValue: "Interactive view closed" })
                : t("presentationBrowserStarting", { defaultValue: "Preparing interactive view..." })}
        </span>
        <span className="min-w-0 flex-1 truncate">{sessionState.url || url}</span>
        <button
          type="button"
          onClick={handleBack}
          className={classNames(
            "rounded-full px-3 py-1 font-medium transition-colors",
            isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-gray-100 text-gray-800 hover:bg-gray-200"
          )}
        >
          {t("presentationBrowserBack", { defaultValue: "Back" })}
        </button>
        {showReconnect ? (
          <button
            type="button"
            onClick={handleReconnect}
            className={classNames(
              "rounded-full px-3 py-1 font-medium transition-colors",
              isDark ? "bg-slate-800 text-slate-200 hover:bg-slate-700" : "bg-gray-100 text-gray-800 hover:bg-gray-200"
            )}
          >
            {t("presentationBrowserReconnect", { defaultValue: "Reconnect" })}
          </button>
        ) : null}
      </div>

      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden p-4">
        {frame ? (
          <img
            ref={imageRef}
            src={frame.dataUrl}
            alt={t("presentationBrowserFrameAlt", { defaultValue: "Interactive view frame" })}
            onMouseDown={handleMouseDown}
            onContextMenu={(event) => event.preventDefault()}
            className="max-h-full max-w-full select-none rounded-2xl border border-[var(--glass-border-subtle)] object-contain shadow-2xl"
            draggable={false}
          />
        ) : (
          <div
            className={classNames(
              "flex h-full min-h-[320px] w-full items-center justify-center rounded-2xl border border-dashed text-sm",
              isDark ? "border-white/10 text-slate-400" : "border-black/10 text-gray-500"
            )}
          >
            {sessionState.message ||
              (sessionState.state === "starting"
                ? t("presentationBrowserStarting", { defaultValue: "Preparing interactive view..." })
                : t("presentationBrowserWaiting", { defaultValue: "Waiting for interactive view..." }))}
          </div>
        )}

        {panelError ? (
          <div
            className={classNames(
              "pointer-events-none absolute bottom-4 left-4 right-4 rounded-2xl border px-4 py-3 text-sm shadow-xl",
              isDark ? "border-rose-500/20 bg-rose-500/10 text-rose-100" : "border-rose-200 bg-white/90 text-rose-700"
            )}
          >
            {panelError}
          </div>
        ) : null}
      </div>
    </div>
  );
}
