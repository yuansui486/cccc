import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";

import * as api from "../../services/api";
import type { Actor, GroupSettings } from "../../types";
import { useModalA11y } from "../../hooks/useModalA11y";
import { createContextModalUi } from "../ContextModal/ui";
import { AutomationTab } from "./settings/AutomationTab";
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
  const { t } = useTranslation(["common"]);
  const { modalRef } = useModalA11y(isOpen, onClose);
  const ui = createContextModalUi(isDark);
  const [devActors, setDevActors] = useState<Actor[]>([]);
  const automationPolicyDraft = useAutomationPolicyDraft({
    active: isOpen,
    settings,
    onUpdateSettings,
  });

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
        <div className="flex shrink-0 justify-end border-b border-[var(--glass-border-subtle)] px-3 py-2 sm:px-4 sm:py-3">
          <button type="button" className={ui.buttonSecondaryClass} onClick={onClose}>
            {t("common:close")}
          </button>
        </div>
        <div className={settingsDialogBodyClass}>
          <AutomationTab
            isDark={isDark}
            groupId={groupId}
            devActors={devActors}
            busy={busy}
            {...automationPolicyDraft}
            onSavePolicies={automationPolicyDraft.savePolicies}
            onResetPolicies={automationPolicyDraft.resetPoliciesDraft}
          />
        </div>
      </div>
    </div>,
    document.body
  );
}
