import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Actor, ChatMessageData, GroupMeta, LedgerEvent } from "../../types";
import { classNames } from "../../utils/classNames";
import { formatRecipientList, getRecipientDisplayLabel } from "../../utils/displayText";
import * as api from "../../services/api";
import { useModalA11y } from "../../hooks/useModalA11y";

export interface RelayMessageModalProps {
  isOpen: boolean;
  busy: boolean;
  srcGroupId: string;
  srcEvent: LedgerEvent | null;
  groups: GroupMeta[];
  onCancel: () => void;
  onSubmit: (dstGroupId: string, to: string[], note: string) => void;
}

export function RelayMessageModal({
  isOpen,
  busy,
  srcGroupId,
  srcEvent,
  groups,
  onCancel,
  onSubmit,
}: RelayMessageModalProps) {
  const { t } = useTranslation("modals");
  const dstGroups = useMemo(() => {
    return (groups || [])
      .map((g) => ({ group_id: String(g.group_id || ""), title: String(g.title || "") }))
      .filter((g) => g.group_id && g.group_id !== srcGroupId);
  }, [groups, srcGroupId]);

  const defaultDstGroupId = useMemo(() => {
    return dstGroups[0]?.group_id || "";
  }, [dstGroups]);

  const [dstGroupId, setDstGroupId] = useState(() => defaultDstGroupId);
  const [dstActors, setDstActors] = useState<Actor[]>([]);
  const [dstActorsBusy, setDstActorsBusy] = useState(() => !!defaultDstGroupId);
  const [note, setNote] = useState("");
  const [toTokens, setToTokens] = useState<string[]>(["@all"]);

  const srcEventId = srcEvent?.id ? String(srcEvent.id) : "";
  const srcBy = srcEvent?.by ? String(srcEvent.by) : "";
  const srcText = useMemo(() => {
    const d = srcEvent?.data as ChatMessageData | undefined;
    const t = d?.text;
    return typeof t === "string" ? t : "";
  }, [srcEvent]);

  useEffect(() => {
    if (!isOpen) return;
    const gid = String(dstGroupId || "").trim();
    if (!gid) return;
    let cancelled = false;
    void api
      .fetchActors(gid)
      .then((resp) => {
        if (cancelled) return;
        if (!resp.ok) {
          setDstActors([]);
          return;
        }
        setDstActors(resp.result.actors || []);
      })
      .finally(() => {
        if (cancelled) return;
        setDstActorsBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [dstGroupId, isOpen]);

  const availableTokens = useMemo(() => {
    const base = ["@all", "@foreman", "@peers"];
    const actorIds = (dstActors || []).map((a) => String(a.id || "")).filter((id) => id);
    actorIds.sort();
    return [...base, ...actorIds];
  }, [dstActors]);
  const dstDisplayNameMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const actor of dstActors || []) {
      const id = String(actor.id || "").trim();
      if (!id) continue;
      map.set(id, String(actor.title || id));
    }
    return map;
  }, [dstActors]);

  const toggleToken = (token: string) => {
    const t = token.trim();
    if (!t) return;
    setToTokens((prev) => {
      const exists = prev.includes(t);
      if (exists) return prev.filter((x) => x !== t);
      return prev.concat([t]);
    });
  };

  const { modalRef } = useModalA11y(isOpen, onCancel);

  if (!isOpen) return null;

  const canSubmit = !!dstGroupId && !!srcEventId && !!(srcText || note).trim() && !busy;

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="relay-modal-title"
    >
      <div
        ref={modalRef}
        className="w-full h-full min-h-0 sm:h-auto sm:max-h-[calc(100dvh-7rem)] sm:max-w-2xl sm:mt-14 shadow-2xl animate-scale-in overflow-hidden flex flex-col rounded-none sm:rounded-2xl glass-modal"
      >
        <div className="px-6 py-4 border-b safe-area-inset-top border-[var(--glass-border-subtle)]">
          <div id="relay-modal-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
            {t("relay.title")}
          </div>
          <div className="text-xs mt-1 text-[var(--color-text-muted)]">
            {t("relay.subtitle")}
          </div>
        </div>

        <div className="p-6 flex flex-1 min-h-0 flex-col gap-5">
          {/* Source preview */}
          <div className="shrink-0 overflow-hidden rounded-xl p-4 glass-panel">
            <div className="text-xs font-semibold text-[var(--color-text-primary)]">
              {t("relay.source")}
            </div>
            <div className="text-[11px] mt-1 text-[var(--color-text-muted)]">
              {srcGroupId} · {srcEventId ? srcEventId : "—"} · {srcBy || "—"}
            </div>
            <div className="mt-2 max-h-[min(40vh,20rem)] overflow-x-hidden overflow-y-auto overscroll-contain whitespace-pre-wrap break-words pr-1 text-sm text-[var(--color-text-primary)]">
              {srcText || t("relay.emptyMessage")}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <div className="space-y-5">
              {/* Destination group */}
              <div>
                <label className="block text-xs font-medium mb-2 text-[var(--color-text-secondary)]">
                  {t("relay.destinationGroup")}
                </label>
                <select
                  value={dstGroupId}
                  onChange={(e) => {
                    const gid = e.target.value;
                    setDstGroupId(gid);
                    setDstActors([]);
                    setDstActorsBusy(true);
                  }}
                  className="w-full rounded-xl px-3 py-2.5 text-sm min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)]"
                  disabled={busy || dstGroups.length === 0}
                >
                  {dstGroups.length === 0 ? (
                    <option value="">{t("relay.noOtherGroups")}</option>
                  ) : null}
                  {dstGroups.map((g) => (
                    <option key={g.group_id} value={g.group_id}>
                      {g.title ? `${g.title} (${g.group_id})` : g.group_id}
                    </option>
                  ))}
                </select>
              </div>

              {/* Recipients */}
              <div>
                <div className="flex items-center justify-between gap-3">
                  <label className="block text-xs font-medium text-[var(--color-text-secondary)]">
                    {t("relay.recipients")}
                  </label>
                  <button
                    type="button"
                    className="text-xs underline text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                    onClick={() => setToTokens([])}
                    disabled={busy}
                  >
                    {t("common:reset")}
                  </button>
                </div>

                <div className={classNames("mt-2 flex flex-wrap gap-2", dstActorsBusy ? "opacity-60" : "")}>
                  {availableTokens.map((tok) => {
                    const active = toTokens.includes(tok);
                    return (
                      <button
                        key={tok}
                        type="button"
                        className={classNames(
                          "text-xs px-2.5 py-1.5 rounded-full border transition-colors",
                          active
                            ? "bg-[var(--glass-accent-bg)] border-[var(--glass-accent-border)] text-[var(--color-accent-primary)]"
                            : "bg-[var(--glass-tab-bg)] border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                        )}
                        onClick={() => toggleToken(tok)}
                        disabled={busy}
                        title={getRecipientDisplayLabel(tok, dstDisplayNameMap)}
                      >
                        {getRecipientDisplayLabel(tok, dstDisplayNameMap)}
                      </button>
                    );
                  })}
                </div>
                <div className="mt-2 text-xs text-[var(--color-text-muted)]">
                  {toTokens.length ? t("relay.selectedTokens", { tokens: formatRecipientList(toTokens, dstDisplayNameMap) }) : t("relay.selectedBroadcast")}
                </div>
              </div>

              {/* Optional note */}
              <div>
                <label className="block text-xs font-medium mb-2 text-[var(--color-text-secondary)]">
                  {t("relay.noteLabel")}
                </label>
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={3}
                  className="w-full rounded-xl px-3 py-2.5 text-sm transition-colors glass-input text-[var(--color-text-primary)]"
                  placeholder={t("relay.notePlaceholder")}
                  disabled={busy}
                />
              </div>
            </div>
          </div>

          <div className="shrink-0 flex gap-3 pt-1 flex-wrap justify-end">
            <button
              className="px-4 py-2.5 rounded-xl text-sm font-medium transition-colors min-h-[44px] glass-btn text-[var(--color-text-secondary)]"
              onClick={onCancel}
              disabled={busy}
            >
              {t("common:cancel")}
            </button>
            <button
              className="px-4 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold shadow-lg disabled:opacity-50 transition-all min-h-[44px]"
              onClick={() => onSubmit(dstGroupId, toTokens, note)}
              disabled={!canSubmit}
            >
              {busy ? t("relay.sending") : t("relay.relay")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
