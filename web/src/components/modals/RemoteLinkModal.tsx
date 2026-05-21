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
  const { t } = useTranslation(["common", "layout", "settings"]);
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
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--glass-border-subtle)] px-3 py-3 sm:px-5 sm:py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-[var(--color-text-primary)] sm:text-lg">
              {t("layout:remoteLink", { defaultValue: "连接手机" })}
            </h2>
            <p className="mt-1 text-xs text-[var(--color-text-muted)] sm:text-sm">
              {t("settings:imBridge.description")}
            </p>
          </div>
          <button type="button" className={ui.buttonSecondaryClass} onClick={onClose}>
            {t("common:close")}
          </button>
        </div>
        <div className={settingsDialogBodyClass}>
          <IMBridgeTab
            isDark={isDark}
            groupId={groupId}
            hideHeaderText
            {...imBridgeSettings}
          />
        </div>
      </div>
    </div>,
    document.body
  );
}
