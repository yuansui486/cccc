import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";

import { useModalA11y } from "../../hooks/useModalA11y";
import { createContextModalUi } from "../ContextModal/ui";
import { IMBridgeTab } from "./settings/IMBridgeTab";
import { settingsDialogBodyClass, settingsDialogPanelClass } from "./settings/types";
import { useIMBridgeSettings } from "./settings/useIMBridgeSettings";

interface RemoteLinkModalProps {
  isOpen: boolean;
  onClose: () => void;
  isDark: boolean;
  groupId?: string;
}

export function RemoteLinkModal({
  isOpen,
  onClose,
  isDark,
  groupId,
}: RemoteLinkModalProps) {
  const { t } = useTranslation(["common"]);
  const { modalRef } = useModalA11y(isOpen, onClose);
  const ui = createContextModalUi(isDark);
  const imBridgeSettings = useIMBridgeSettings({
    active: isOpen,
    groupId,
  });

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
          <IMBridgeTab
            isDark={isDark}
            groupId={groupId}
            {...imBridgeSettings}
          />
        </div>
      </div>
    </div>,
    document.body
  );
}
