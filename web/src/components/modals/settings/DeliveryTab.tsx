// DeliveryTab configures PTY delivery read-cursor behavior.
import React from "react";
import { useTranslation } from "react-i18next";

import { ClockIcon } from "../../Icons";
import {
  labelClass,
  primaryButtonClass,
  settingsWorkspaceActionBarClass,
  settingsWorkspaceBodyClass,
  settingsWorkspaceHeaderClass,
  settingsWorkspaceShellClass,
  settingsWorkspaceSoftPanelClass,
} from "./types";

interface DeliveryTabProps {
  isDark: boolean;
  busy: boolean;
  autoMarkOnDelivery: boolean;
  setAutoMarkOnDelivery: (v: boolean) => void;
  onSave: () => void;
  onAutoSave?: (field: string, value: boolean) => void;
}

const ToggleRow = ({
  label,
  checked,
  onChange,
  isDark,
  helperText,
  onAutoSave,
}: {
  label: string;
  checked: boolean;
  onChange: (val: boolean) => void;
  isDark: boolean;
  helperText?: React.ReactNode;
  onAutoSave?: (newValue: boolean) => void;
}) => (
  <div className={settingsWorkspaceSoftPanelClass(isDark)}>
    <label className="flex items-center justify-between cursor-pointer">
      <span className={`pr-4 ${labelClass(isDark)}`}>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => {
          const newValue = !checked;
          onChange(newValue);
          onAutoSave?.(newValue);
        }}
        className={`
          relative inline-flex h-6 w-11 items-center rounded-full transition-colors
          focus:outline-none focus:ring-2 focus:ring-offset-2
          ${checked
            ? "bg-sky-500 focus:ring-sky-500"
            : "bg-gray-300 dark:bg-slate-600 focus:ring-gray-400 dark:focus:ring-slate-500"
          }
          focus:ring-offset-white dark:focus:ring-offset-slate-900
        `}
      >
        <span
          className={`
            inline-block h-4 w-4 rounded-full bg-white shadow-sm transform transition-transform
            ${checked ? "translate-x-6" : "translate-x-1"}
          `}
        />
      </button>
    </label>
    {helperText && (
      <div className="mt-1.5 text-[11px] leading-snug text-[var(--color-text-muted)]">
        {helperText}
      </div>
    )}
  </div>
);

export function DeliveryTab(props: DeliveryTabProps) {
  const { isDark, busy, onSave, onAutoSave } = props;
  const { t } = useTranslation("settings");

  const autoSave = (field: string, getValue: () => boolean) => {
    if (!onAutoSave) return;
    setTimeout(() => onAutoSave(field, getValue()), 0);
  };

  return (
    <div className="animate-in fade-in slide-in-from-bottom-2 duration-300">
      <div className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("delivery.title")}</h3>
            <p className="mt-1 text-xs text-[var(--color-text-muted)]">
              {t("delivery.description")}
            </p>
          </div>
        </div>

        <div className={settingsWorkspaceBodyClass}>
          <ToggleRow
            isDark={isDark}
            label={t("delivery.autoMarkRead")}
            checked={props.autoMarkOnDelivery}
            onChange={props.setAutoMarkOnDelivery}
            helperText={t("delivery.autoMarkReadHelp")}
            onAutoSave={(newValue) => autoSave("auto_mark_on_delivery", () => newValue)}
          />
        </div>

        <div className={settingsWorkspaceActionBarClass(isDark)}>
          <button onClick={onSave} disabled={busy} className={primaryButtonClass(busy)}>
            {busy ? (
              t("common:saving")
            ) : (
              <span className="flex items-center gap-2">
                <ClockIcon className="w-4 h-4" /> {t("delivery.saveDelivery")}
              </span>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
