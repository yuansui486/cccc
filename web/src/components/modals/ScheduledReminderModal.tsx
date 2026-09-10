import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";

import * as api from "../../services/api";
import type { Actor, GroupSettings } from "../../types";
import { useModalA11y } from "../../hooks/useModalA11y";
import { createContextModalUi } from "../ContextModal/ui";
import { AutomationTab } from "./settings/AutomationTab";
import { inputClass, labelClass } from "./settings/types";
import { settingsDialogBodyClass, settingsDialogPanelClass } from "./settings/types";
import { useAutomationPolicyDraft } from "./settings/useAutomationPolicyDraft";

interface ScheduledReminderModalProps {
  isOpen: boolean;
  onClose: () => void;
  isDark: boolean;
  groupId?: string;
  settings: GroupSettings | null;
  busy: boolean;
  onUpdateSettings: (settings: Partial<GroupSettings>) => Promise<boolean | void>;
}

export function ScheduledReminderModal({
  isOpen,
  onClose,
  isDark,
  groupId,
  settings,
  busy,
  onUpdateSettings,
}: ScheduledReminderModalProps) {
  const { t } = useTranslation(["common", "settings"]);
  const { modalRef } = useModalA11y(isOpen, onClose);
  const ui = createContextModalUi(isDark);
  const [devActors, setDevActors] = useState<Actor[]>([]);
  const [rolePromptEveryMessages, setRolePromptEveryMessages] = useState(5);
  const automationPolicyDraft = useAutomationPolicyDraft({
    active: isOpen,
    settings,
    onUpdateSettings,
  });

  useEffect(() => {
    setRolePromptEveryMessages(Math.max(0, Math.min(1000, Number(settings?.role_prompt_every_messages ?? 5))));
  }, [settings]);

  const saveRolePromptFrequency = async (value: number) => {
    const next = Math.max(0, Math.min(1000, Math.trunc(value || 0)));
    setRolePromptEveryMessages(next);
    await onUpdateSettings({ role_prompt_every_messages: next });
  };

  useEffect(() => {
    if (!isOpen || !groupId) return;
    let cancelled = false;
    void (async () => {
      try {
        const resp = await api.fetchActors(groupId, false);
        if (!cancelled && resp.ok && resp.result?.actors) {
          setDevActors(Array.isArray(resp.result.actors) ? resp.result.actors : []);
        }
      } catch {
        if (!cancelled) setDevActors([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [groupId, isOpen]);

  if (!isOpen || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[70] animate-fade-in"
      role="dialog"
      aria-modal="true"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="absolute inset-0 glass-overlay" onPointerDown={onClose} />
      <div ref={modalRef} className={settingsDialogPanelClass("xl")}>
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--glass-border-subtle)] px-3 py-3 sm:px-5 sm:py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-[var(--color-text-primary)] sm:text-lg">
              {t("settings:automation.title")}
            </h2>
            <p className="mt-1 text-xs text-[var(--color-text-muted)] sm:text-sm">
              {t("settings:automation.description")}
            </p>
          </div>
          <button type="button" className={ui.buttonSecondaryClass} onClick={onClose}>
            {t("common:close")}
          </button>
        </div>
        <div className={settingsDialogBodyClass}>
          <div className="mb-4 rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--glass-bg-soft)] p-4">
            <label className={labelClass(isDark)}>{t("settings:messaging.rolePromptEveryMessages", { defaultValue: "角色提示频率" })}</label>
            <input type="number" min={0} max={1000} step={1} value={rolePromptEveryMessages}
              onChange={(e) => void saveRolePromptFrequency(Number(e.target.value))}
              className={inputClass(isDark)} />
            <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">{t("settings:messaging.rolePromptEveryMessagesHint", { defaultValue: "每 N 条成功消息混入一次角色提示；0 表示关闭。" })}</div>
          </div>
          <AutomationTab
            isDark={isDark}
            groupId={groupId}
            hideHeader
            devActors={devActors}
            busy={busy}
            {...automationPolicyDraft}
            onSaveBuiltinPolicies={automationPolicyDraft.saveBuiltinPolicies}
            onSaveTaskReminderSettings={automationPolicyDraft.saveTaskReminderSettings}
            onSaveTaskReminderEnabled={automationPolicyDraft.saveTaskReminderEnabled}
            onResetBuiltinPolicies={automationPolicyDraft.resetBuiltinPoliciesDraft}
            onResetTaskReminder={automationPolicyDraft.resetTaskReminderDraft}
          />
        </div>
      </div>
    </div>,
    document.body
  );
}
