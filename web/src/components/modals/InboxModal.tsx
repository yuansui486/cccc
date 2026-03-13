import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Actor, LedgerEvent } from "../../types";
import { formatFullTime, formatTime } from "../../utils/time";
import { MarkdownRenderer } from "../MarkdownRenderer";
import { useModalA11y } from "../../hooks/useModalA11y";
import { getRecipientDisplayLabel } from "../../utils/displayText";

function formatEventLine(
  ev: LedgerEvent,
  getDisplayName: (id: string) => string
): string {
  if (ev.kind === "chat.message" && ev.data && typeof ev.data === "object") {
    const d = ev.data as Record<string, unknown>;
    return typeof d.text === "string" ? d.text : "";
  }
  if (ev.kind === "system.notify" && ev.data && typeof ev.data === "object") {
    const d = ev.data as Record<string, unknown>;
    const kind = typeof d.kind === "string" ? d.kind : "info";
    const title = typeof d.title === "string" ? d.title : "";
    const message = typeof d.message === "string" ? d.message : "";
    const targetId = typeof d.target_actor_id === "string" ? d.target_actor_id : "";
    const target = targetId ? ` → ${getDisplayName(targetId)}` : "";
    return `[${kind}]${target}: ${title}${message ? ` - ${message}` : ""}`;
  }
  const k = String(ev.kind || "event");
  const byId = ev.by ? String(ev.by) : "";
  const by = byId ? ` by ${getDisplayName(byId)}` : "";
  return `${k}${by}`;
}

export interface InboxModalProps {
  isOpen: boolean;
  actorId: string;
  actors: Actor[];
  messages: LedgerEvent[];
  busy: string;
  onClose: () => void;
  onMarkAllRead: () => void;
}

export function InboxModal({ isOpen, actorId, actors, messages, busy, onClose, onMarkAllRead }: InboxModalProps) {
  const { t } = useTranslation("modals");
  const { modalRef } = useModalA11y(isOpen, onClose);
  // Helper to get display name for actor
  const getDisplayName = useMemo(() => {
    const map = new Map<string, string>();
    for (const actor of actors) {
      const id = String(actor.id || "");
      if (id) map.set(id, actor.title || id);
    }
    return (id: string) => {
      return getRecipientDisplayLabel(id, map);
    };
  }, [actors]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="inbox-title"
    >
      <div
        ref={modalRef}
        className="w-full h-full sm:h-auto sm:max-h-[calc(100dvh-8rem)] sm:max-w-2xl sm:mt-16 shadow-2xl animate-scale-in flex flex-col rounded-none sm:rounded-2xl glass-modal"
      >
        <div className="px-4 sm:px-6 py-4 border-b flex items-center justify-between gap-3 safe-area-inset-top border-[var(--glass-border-subtle)]">
          <div className="min-w-0">
            <div id="inbox-title" className="text-lg font-semibold truncate text-[var(--color-text-primary)]">
              {t("inbox.title", { actorId })}
            </div>
            <div className="text-sm text-[var(--color-text-muted)]">{t("inbox.unreadMessages", { count: messages.length })}</div>
          </div>
          <div className="flex gap-2">
            <button
              className="rounded-xl px-4 py-2 text-sm font-medium disabled:opacity-50 transition-colors min-h-[44px] glass-btn text-[var(--color-text-secondary)]"
              onClick={onMarkAllRead}
              disabled={!messages.length || busy.startsWith("inbox")}
            >
              {t("inbox.markAllRead")}
            </button>
            <button
              className="rounded-xl px-4 py-2 text-sm font-medium transition-colors min-h-[44px] glass-btn text-[var(--color-text-primary)]"
              onClick={onClose}
            >
              {t("common:close")}
            </button>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-auto p-4 space-y-2">
          {messages.map((ev, idx) => (
            <div
              key={String(ev.id || idx)}
              className="rounded-xl px-4 py-3 glass-panel"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs truncate text-[var(--color-text-muted)]" title={formatFullTime(ev.ts)}>
                  {formatTime(ev.ts)}
                </div>
                <div className="text-xs font-medium truncate text-[var(--color-text-secondary)]">{getDisplayName(ev.by || "") || "—"}</div>
              </div>
              <div className="mt-2 text-sm break-words">
                <MarkdownRenderer
                  content={formatEventLine(ev, getDisplayName)}
                  className="text-[var(--color-text-primary)]"
                />
              </div>
            </div>
          ))}
          {!messages.length && (
            <div className="text-center py-8">
              <div className="text-3xl mb-2">📭</div>
              <div className="text-sm text-[var(--color-text-muted)]">{t("inbox.noUnread")}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
