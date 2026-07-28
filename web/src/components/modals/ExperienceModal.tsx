import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { apiJson } from "../../services/api/base";
import type { ExperienceDocument, GroupSettings } from "../../types";
import { useModalA11y } from "../../hooks/useModalA11y";
import { classNames } from "../../utils/classNames";
import { CopyIcon, EditIcon, RefreshIcon } from "../Icons";
import { LazyMarkdownRenderer } from "../LazyMarkdownRenderer";
import { createContextModalUi } from "../ContextModal/ui";
import { settingsDialogBodyClass, settingsDialogPanelClass } from "./settings/types";

interface ExperienceModalProps {
  isOpen: boolean;
  onClose: () => void;
  isDark: boolean;
  groupId?: string;
  settings: GroupSettings | null;
  readOnly?: boolean;
  onUpdateSettings: (settings: Partial<GroupSettings>) => Promise<boolean | void>;
}

export function ExperienceModal({
  isOpen,
  onClose,
  isDark,
  groupId,
  settings,
  readOnly,
  onUpdateSettings,
}: ExperienceModalProps) {
  const { t } = useTranslation(["common", "layout"]);
  const { modalRef } = useModalA11y(isOpen, onClose);
  const ui = createContextModalUi(isDark);
  const [documentState, setDocumentState] = useState<ExperienceDocument | null>(null);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reminderEnabled, setReminderEnabled] = useState(true);
  const [reminderEvery, setReminderEvery] = useState(10);

  const loadDocument = useCallback(async () => {
    if (!groupId) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiJson<ExperienceDocument>(
        `/api/v1/groups/${encodeURIComponent(groupId)}/experience`,
      );
      if (!response.ok) {
        setError(response.error.message);
        return;
      }
      setDocumentState(response.result);
      setDraft(String(response.result.content || ""));
    } finally {
      setBusy(false);
    }
  }, [groupId]);

  useEffect(() => {
    if (!isOpen) return;
    setEditing(false);
    setReminderEnabled(settings?.experience_reminder_enabled ?? true);
    setReminderEvery(settings?.experience_reminder_every_user_messages ?? 10);
    void loadDocument();
  }, [isOpen, loadDocument, settings?.experience_reminder_enabled, settings?.experience_reminder_every_user_messages]);

  const saveDocument = async () => {
    if (!groupId || !documentState) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await apiJson<ExperienceDocument>(
        `/api/v1/groups/${encodeURIComponent(groupId)}/experience`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: draft, expected_revision: documentState.revision, by: "user" }),
        },
      );
      if (!response.ok) {
        setError(
          response.error.code === "revision_conflict"
            ? t("layout:experienceConflict")
            : response.error.message,
        );
        return;
      }
      setDocumentState(response.result);
      setDraft(response.result.content);
      setEditing(false);
      setNotice(t("layout:experienceSaved"));
    } finally {
      setBusy(false);
    }
  };

  const saveReminder = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const ok = await onUpdateSettings({
        experience_reminder_enabled: reminderEnabled,
        experience_reminder_every_user_messages: Math.max(1, Math.min(1000, Math.round(reminderEvery || 10))),
      });
      if (ok !== false) setNotice(t("layout:experienceReminderSaved"));
    } finally {
      setBusy(false);
    }
  };

  if (!isOpen || typeof document === "undefined") return null;

  return createPortal(
    <div className="fixed inset-0 z-[70] animate-fade-in" role="dialog" aria-modal="true">
      <div className="absolute inset-0 glass-overlay" onPointerDown={onClose} />
      <div ref={modalRef} className={settingsDialogPanelClass("xl")}>
        <div className="flex shrink-0 items-start justify-between gap-3 border-b border-[var(--glass-border-subtle)] px-3 py-3 sm:px-5 sm:py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-[var(--color-text-primary)] sm:text-lg">
              {t("layout:experience")}
            </h2>
            <p className="mt-1 text-xs text-[var(--color-text-muted)] sm:text-sm">
              {t("layout:experienceDescription")}
            </p>
          </div>
          <button type="button" className={ui.buttonSecondaryClass} onClick={onClose}>
            {t("common:close")}
          </button>
        </div>

        <div className={classNames(settingsDialogBodyClass, "space-y-5")}>
          {error ? (
            <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-600 dark:text-rose-300">
              {error}
            </div>
          ) : null}
          {notice ? (
            <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
              {notice}
            </div>
          ) : null}

          <section className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-[var(--color-text-primary)]">EXPERIENCE.md</div>
                <div className="mt-1 truncate text-xs text-[var(--color-text-muted)]">
                  {documentState?.path || t("layout:experienceLoading")}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button type="button" className={ui.buttonSecondaryClass} onClick={() => void loadDocument()} disabled={busy} title={t("common:refresh")}>
                  <RefreshIcon size={15} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className={ui.buttonSecondaryClass}
                  disabled={!documentState}
                  title={t("common:copy")}
                  onClick={() => void navigator.clipboard.writeText(documentState?.content || "")}
                >
                  <CopyIcon size={15} aria-hidden="true" />
                </button>
                {!readOnly ? (
                  <button
                    type="button"
                    className={editing ? ui.buttonSecondaryClass : ui.buttonPrimaryClass}
                    onClick={() => {
                      if (editing) {
                        setDraft(documentState?.content || "");
                        setEditing(false);
                      } else {
                        setEditing(true);
                      }
                    }}
                    disabled={!documentState || busy}
                  >
                    <EditIcon size={15} aria-hidden="true" />
                    <span>{editing ? t("common:cancel") : t("common:edit")}</span>
                  </button>
                ) : null}
              </div>
            </div>

            {editing ? (
              <div className="space-y-3">
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  className={classNames(ui.textareaClass, "min-h-[360px] font-mono text-xs")}
                  aria-label={t("layout:experienceEditor")}
                />
                <button type="button" className={ui.buttonPrimaryClass} disabled={busy} onClick={() => void saveDocument()}>
                  {busy ? t("common:saving") : t("layout:experienceSave")}
                </button>
              </div>
            ) : (
              <div className="max-h-[52vh] min-h-[260px] overflow-y-auto rounded-md border border-[var(--glass-border-subtle)] p-4">
                {busy && !documentState ? (
                  <div className="text-sm text-[var(--color-text-muted)]">{t("layout:experienceLoading")}</div>
                ) : (
                  <LazyMarkdownRenderer
                    content={documentState?.content || ""}
                    isDark={isDark}
                    className="text-sm"
                    fallback={<div className="whitespace-pre-wrap text-sm">{documentState?.content || ""}</div>}
                  />
                )}
              </div>
            )}
          </section>

          <section className="border-t border-[var(--glass-border-subtle)] pt-4">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("layout:experienceReminder")}</div>
            <div className="mt-3 flex flex-wrap items-end gap-4">
              <label className="flex min-h-9 items-center gap-2 text-sm text-[var(--color-text-primary)]">
                <input type="checkbox" checked={reminderEnabled} onChange={(event) => setReminderEnabled(event.target.checked)} disabled={readOnly} />
                {t("layout:experienceReminderEnabled")}
              </label>
              <label className="space-y-1 text-xs text-[var(--color-text-muted)]">
                <span className="block">{t("layout:experienceReminderEvery")}</span>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  step={1}
                  value={reminderEvery}
                  disabled={!reminderEnabled || readOnly}
                  onChange={(event) => setReminderEvery(Number(event.target.value))}
                  className={classNames(ui.inputClass, "w-28")}
                />
              </label>
              {!readOnly ? (
                <button type="button" className={ui.buttonSecondaryClass} disabled={busy} onClick={() => void saveReminder()}>
                  {t("layout:experienceReminderSave")}
                </button>
              ) : null}
            </div>
            <p className="mt-2 text-xs text-[var(--color-text-muted)]">{t("layout:experienceReminderHint")}</p>
          </section>
        </div>
      </div>
    </div>,
    document.body,
  );
}
