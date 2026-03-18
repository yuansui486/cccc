import { useCallback, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { PetReminder, ReminderAction } from "./types";

const AUTO_HIDE_MS = 6000;

interface PetReminderBubbleProps {
  reminder: PetReminder | null;
  onDismiss: (fingerprint: string) => void;
  onAction?: (action: ReminderAction) => void;
}

function getActionButtons(
  reminder: PetReminder,
  t: (key: string, opts?: Record<string, unknown>) => string,
): { label: string; action: ReminderAction }[] {
  const buttons: { label: string; action: ReminderAction }[] = [];

  if (reminder.kind === "waiting_user" && reminder.source.taskId) {
    buttons.push({
      label: String(t("action.complete", { defaultValue: "Done" })),
      action: {
        type: "complete_task",
        groupId: reminder.action.groupId,
        taskId: reminder.source.taskId,
      },
    });
    buttons.push({
      label: String(t("action.view", { defaultValue: "View" })),
      action: reminder.action,
    });
  } else if (reminder.kind === "reply_required") {
    buttons.push({
      label: String(t("action.reply", { defaultValue: "Reply" })),
      action: reminder.action,
    });
  } else {
    buttons.push({
      label: String(t("action.view", { defaultValue: "View" })),
      action: reminder.action,
    });
  }

  return buttons;
}

export function PetReminderBubble({
  reminder,
  onDismiss,
  onAction,
}: PetReminderBubbleProps) {
  const { t } = useTranslation("webPet");
  const autoHideTimeoutRef = useRef<number | null>(null);

  const clearAutoHideTimer = useCallback(() => {
    if (autoHideTimeoutRef.current === null) return;
    window.clearTimeout(autoHideTimeoutRef.current);
    autoHideTimeoutRef.current = null;
  }, []);

  useEffect(() => {
    clearAutoHideTimer();

    if (!reminder) {
      return;
    }

    autoHideTimeoutRef.current = window.setTimeout(() => {
      autoHideTimeoutRef.current = null;
      onDismiss(reminder.fingerprint);
    }, AUTO_HIDE_MS);

    return () => {
      clearAutoHideTimer();
    };
  }, [clearAutoHideTimer, onDismiss, reminder]);

  const handleDismiss = useCallback(() => {
    if (!reminder) return;
    clearAutoHideTimer();
    onDismiss(reminder.fingerprint);
  }, [clearAutoHideTimer, onDismiss, reminder]);

  const handleButtonAction = useCallback(
    (action: ReminderAction) => {
      if (!reminder) return;
      clearAutoHideTimer();
      onAction?.(action);
      onDismiss(reminder.fingerprint);
    },
    [clearAutoHideTimer, onAction, onDismiss, reminder],
  );

  const displayAgent = reminder?.agent === "system"
    ? t("systemAgent", { defaultValue: "System" })
    : reminder?.agent || "";

  const label = useMemo(() => {
    if (!reminder) return "";
    return reminder.agent === "system"
      ? reminder.summary
      : `${displayAgent}: ${reminder.summary}`;
  }, [displayAgent, reminder]);

  const actionButtons = useMemo(
    () => (reminder ? getActionButtons(reminder, t) : []),
    [reminder, t],
  );

  if (!reminder) {
    return null;
  }

  const kindLabel = String(
    t(`kind.${reminder.kind}`, {
      defaultValue: reminder.kind.replace(/_/g, " "),
    }),
  );

  return (
    <div
      className="pointer-events-auto absolute z-[1110] w-[min(280px,calc(100vw-32px))]"
      style={{
        bottom: "calc(100% + 14px)",
        left: "50%",
        transform: "translateX(-50%)",
      }}
      onPointerDown={(event) => {
        event.stopPropagation();
      }}
      aria-live={reminder.ephemeral ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <div className="glass-modal relative rounded-2xl border border-[var(--glass-border-subtle)] px-3 py-2 shadow-2xl">
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
            <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--color-text-secondary)]">
              {kindLabel}
            </div>
            <div
              className="mt-1 text-sm leading-5 text-[var(--color-text-primary)]"
              title={label}
            >
              {label}
            </div>
            <div className="mt-1.5 flex gap-1.5">
              {actionButtons.map((btn) => (
                <button
                  key={btn.action.type}
                  type="button"
                  className="rounded-md bg-white/10 px-2 py-0.5 text-xs font-medium text-[var(--color-text-primary)] transition hover:bg-white/20 active:bg-white/25"
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    handleButtonAction(btn.action);
                  }}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                  }}
                >
                  {btn.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
