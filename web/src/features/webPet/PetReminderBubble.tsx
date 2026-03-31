import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { getReminderActionButtons } from "./reminderActions";
import { getPetReminderPrimaryText } from "./reminderText";
import type { PetReminder } from "./types";

interface PetReminderBubbleProps {
  reminder: PetReminder | null;
  message?: string;
  additionalCount?: number;
  onDismiss: (fingerprint: string, opts?: { outcome?: "dismissed" | null; cooldownMs?: number }) => void;
  onCloseMessage?: () => void;
  onAction?: (reminder: PetReminder) => void;
  onOpenPanel?: () => void;
}

export function PetReminderBubble({
  reminder,
  message,
  additionalCount = 0,
  onDismiss,
  onCloseMessage,
  onAction,
  onOpenPanel,
}: PetReminderBubbleProps) {
  const { t } = useTranslation("webPet");

  const handleDismiss = useCallback(() => {
    if (!reminder) return;
    onDismiss(reminder.fingerprint);
  }, [onDismiss, reminder]);

  const handleButtonAction = useCallback(
    () => {
      if (!reminder) return;
      onAction?.(reminder);
    },
    [onAction, reminder],
  );

  const displayAgent = reminder?.agent === "system"
    ? t("systemAgent", { defaultValue: "System" })
    : reminder?.agent || "";

  const label = useMemo(() => {
    if (!reminder) return "";
    if (reminder.kind === "suggestion") {
      return reminder.summary;
    }
    return reminder.agent === "system"
      ? reminder.summary
      : `${displayAgent}: ${reminder.summary}`;
  }, [displayAgent, reminder]);

  const actionButtons = useMemo(
    () =>
      reminder
        ? getReminderActionButtons(reminder).map((button) => ({
            label: String(
              t(`action.${button.labelKey}`, { defaultValue: button.fallback }),
            ),
            action: button.action,
          }))
        : [],
    [reminder, t],
  );

  const plainMessage = String(message || "").trim();

  if (!reminder && !plainMessage) {
    return null;
  }

  const kindLabel = reminder
    ? String(
        t(`kind.${reminder.kind}`, {
          defaultValue: reminder.kind.replace(/_/g, " "),
        }),
      )
    : "";
  const primaryReminderText = getPetReminderPrimaryText(reminder);
  const bodyText = plainMessage || primaryReminderText || label;
  const showMeta = !!reminder && !primaryReminderText;
  const isPlainMessage = !reminder && !!plainMessage;

  return (
    <div
      className={`pointer-events-auto absolute z-[1110] ${isPlainMessage ? "w-auto min-w-[220px] max-w-[min(360px,calc(100vw-32px))]" : "w-[min(280px,calc(100vw-32px))]"}`}
      style={{
        bottom: "calc(100% + 14px)",
        left: "50%",
        transform: "translateX(-50%)",
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
      }}
      aria-live={reminder?.ephemeral ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <div className={`glass-modal rounded-2xl border border-[var(--glass-border-subtle)] shadow-2xl ${isPlainMessage ? "px-4 py-3" : "relative px-3 py-2"}`}>
        {isPlainMessage ? (
          <div className="flex items-center gap-3">
            <div
              className="min-w-0 flex-1 text-sm leading-5 text-[var(--color-text-primary)]"
              title={bodyText}
            >
              {bodyText}
            </div>
            <button
              type="button"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)]"
              onPointerDown={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onCloseMessage?.();
              }}
              aria-label={t("dismissReminderAria", {
                defaultValue: "Dismiss reminder",
              })}
            >
              ×
            </button>
          </div>
        ) : (
          <>
            <button
              type="button"
              className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full text-sm text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)]"
              onPointerDown={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                handleDismiss();
              }}
              aria-label={t("dismissReminderAria", {
                defaultValue: "Dismiss reminder",
              })}
            >
              ×
            </button>
            <div className="pr-8">
              <div className="min-w-0">
                {showMeta ? (
                  <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
                    {kindLabel}
                  </div>
                ) : null}
                <div
                  className={`${showMeta ? "mt-1" : ""} cursor-pointer text-sm leading-5 text-[var(--color-text-primary)]`}
                  title={bodyText}
                  onClick={(event) => {
                    if (!onOpenPanel) return;
                    event.preventDefault();
                    event.stopPropagation();
                    onOpenPanel();
                  }}
                >
                  {bodyText}
                </div>
                {additionalCount > 0 ? (
                  <div className="mt-1 text-[11px] font-medium text-[var(--color-text-secondary)]">
                    {t("moreCount", {
                      defaultValue: "+{{count}} more",
                      count: additionalCount,
                    })}
                  </div>
                ) : null}
                <div className="mt-1.5 flex gap-1.5">
                  {actionButtons.map((btn) => (
                    <button
                      key={btn.action.type}
                      type="button"
                      className="rounded-md bg-white/10 px-2 py-0.5 text-xs font-medium text-[var(--color-text-primary)] transition hover:bg-white/20 active:bg-white/25"
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        handleButtonAction();
                      }}
                      onPointerDown={(event) => {
                        event.stopPropagation();
                      }}
                    >
                      {btn.label}
                    </button>
                  ))}
                  {primaryReminderText ? (
                    <button
                      type="button"
                      className="rounded-md bg-white/5 px-2 py-0.5 text-xs font-medium text-[var(--color-text-secondary)] transition hover:bg-white/10 hover:text-[var(--color-text-primary)] active:bg-white/15"
                      onClick={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        handleDismiss();
                      }}
                      onPointerDown={(event) => {
                        event.stopPropagation();
                      }}
                    >
                      {t("action.dismiss", { defaultValue: "Dismiss" })}
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
