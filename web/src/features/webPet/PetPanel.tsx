import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { getReminderActionButtons } from "./reminderActions";
import {
  getPetReminderActionPreviewText,
  getPetReminderPreviewLabel,
  getPetReminderPrimaryText,
  getPetReminderRouteInfo,
} from "./reminderText";
import type { PetCompanionProfile, PetReminder } from "./types";

interface PetPanelProps {
  reminder: PetReminder | null;
  reminders: PetReminder[];
  companion: PetCompanionProfile;
  taskSummaries?: string[];
  reviewInFlight?: boolean;
  onDismiss: (fingerprint: string, opts?: { outcome?: "dismissed" | null; cooldownMs?: number }) => void;
  onAction?: (reminder: PetReminder) => void;
  onReviewNow: () => void;
  onSelectReminder: (fingerprint: string) => void;
}

function buildReminderBody(reminder: PetReminder | null): string {
  if (!reminder) return "";
  return getPetReminderPrimaryText(reminder);
}

export function PetPanel({
  reminder,
  reminders,
  companion,
  taskSummaries = [],
  reviewInFlight = false,
  onDismiss,
  onAction,
  onReviewNow,
  onSelectReminder,
}: PetPanelProps) {
  const { t } = useTranslation("webPet");

  const actionButtons = useMemo(
    () =>
      reminder
        ? getReminderActionButtons(reminder).map((button) => ({
            key: button.action.type,
            label: String(t(`action.${button.labelKey}`, { defaultValue: button.fallback })),
          }))
        : [],
    [reminder, t],
  );

  const bodyText = buildReminderBody(reminder);
  const previewText = reminder ? getPetReminderActionPreviewText(reminder) : "";
  const routeInfo = reminder ? getPetReminderRouteInfo(reminder) : { toText: "", replyInThread: false };
  const showPreview = !!previewText && previewText !== bodyText;
  const otherReminders = reminder
    ? reminders.filter((item) => item.fingerprint !== reminder.fingerprint)
    : reminders;

  return (
    <div
      className="pointer-events-auto absolute z-[1112] w-[min(360px,calc(100vw-32px))]"
      style={{
        bottom: "calc(100% + 14px)",
        left: "50%",
        transform: "translateX(-50%)",
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
      }}
      aria-label={t("panelAria", { defaultValue: "Web Pet panel" })}
    >
      <div className="glass-modal overflow-hidden rounded-3xl border border-[var(--glass-border-subtle)] shadow-2xl">
        <div className="flex items-center gap-3 border-b border-[var(--glass-border-subtle)] px-4 py-3">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">
              {t("panelTitleNamed", {
                defaultValue: "{{name}}",
                name: companion.name,
              })}
            </div>
            {reminders.length > 0 ? (
              <div className="text-[11px] text-[var(--color-text-secondary)]">
              {reminders.length > 0
                ? t("reminderCount", {
                    defaultValue: "{{count}} reminders",
                    count: reminders.length,
                  })
                : null}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            className="rounded-full bg-white/8 px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] transition hover:bg-white/14 disabled:cursor-not-allowed disabled:opacity-55"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onReviewNow();
            }}
            disabled={reviewInFlight}
          >
            {reviewInFlight
              ? t("reviewing", { defaultValue: "Reviewing…" })
              : t("reviewNow", { defaultValue: "Refresh now" })}
          </button>
        </div>

        <div className={`px-4 ${reminder ? "py-4" : "py-6"}`}>
          {reminder ? (
            <>
              <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                {t(`kind.${reminder.kind}`, {
                  defaultValue: reminder.kind.replace(/_/g, " "),
                })}
              </div>
              <div className="mt-2 text-sm leading-6 text-[var(--color-text-primary)]">
                {bodyText}
              </div>
              {showPreview ? (
                <div className="mt-3 rounded-2xl bg-white/6 px-3 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                    {getPetReminderPreviewLabel(reminder, companion) ||
                      t("previewLabel", { defaultValue: "Prepared message" })}
                  </div>
                  {routeInfo.toText || routeInfo.replyInThread ? (
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-[var(--color-text-secondary)]">
                      {routeInfo.toText ? (
                        <span className="rounded-full bg-white/8 px-2 py-1">
                          {t("routeTo", {
                            defaultValue: "To: {{value}}",
                            value: routeInfo.toText,
                          })}
                        </span>
                      ) : null}
                      {routeInfo.replyInThread ? (
                        <span className="rounded-full bg-white/8 px-2 py-1">
                          {t("routeReply", { defaultValue: "Reply in thread" })}
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                  <div className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-[var(--color-text-primary)]">
                    {previewText}
                  </div>
                </div>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                {actionButtons.map((button) => (
                  <button
                    key={button.key}
                    type="button"
                    className="rounded-xl bg-white/10 px-3 py-1.5 text-xs font-medium text-[var(--color-text-primary)] transition hover:bg-white/16"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      onAction?.(reminder);
                    }}
                  >
                    {button.label}
                  </button>
                ))}
                <button
                  type="button"
                  className="rounded-xl bg-white/6 px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition hover:bg-white/12 hover:text-[var(--color-text-primary)]"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onDismiss(reminder.fingerprint);
                  }}
                >
                  {t("action.dismiss", { defaultValue: "Dismiss" })}
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="text-sm leading-6 text-[var(--color-text-secondary)]">
                {t("panelIdle", { defaultValue: "No current reminders." })}
              </div>
              {taskSummaries.length > 0 ? (
                <div className="mt-4 rounded-2xl border border-[var(--glass-border-subtle)] bg-white/5 px-3 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                    {t("currentTasks", { defaultValue: "Current tasks" })}
                  </div>
                  <div className="mt-2 space-y-1.5">
                    {taskSummaries.map((summary) => (
                      <div
                        key={summary}
                        className="rounded-xl bg-white/6 px-3 py-2 text-xs leading-5 text-[var(--color-text-primary)]"
                      >
                        {summary}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>

        {otherReminders.length > 0 ? (
          <div className="border-t border-[var(--glass-border-subtle)] px-3 py-3">
            <div className="px-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
              {t("moreReminders", { defaultValue: "More reminders" })}
            </div>
            <div className="mt-2 space-y-1.5">
              {otherReminders.map((item) => (
                <button
                  key={item.fingerprint}
                  type="button"
                  className="flex w-full items-start gap-2 rounded-2xl px-3 py-2 text-left transition hover:bg-white/8"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onSelectReminder(item.fingerprint);
                  }}
                >
                  <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent)] opacity-80" />
                  <span className="min-w-0 flex-1 text-xs leading-5 text-[var(--color-text-secondary)]">
                    {buildReminderBody(item)}
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
