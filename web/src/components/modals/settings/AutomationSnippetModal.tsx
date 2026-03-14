import React from "react";
import { useTranslation } from "react-i18next";

import { getAutomationVarHelp } from "./automationUtils";
import { cardClass, inputClass } from "./types";

interface AutomationSnippetModalProps {
  open: boolean;
  isDark: boolean;
  templateErr: string;
  newSnippetId: string;
  supportedVars: string[];
  snippetIds: string[];
  snippets: Record<string, string>;
  onClose: () => void;
  onNewSnippetIdChange: (next: string) => void;
  onAddSnippet: () => void;
  onDeleteSnippet: (snippetId: string) => void;
  onUpdateSnippet: (snippetId: string, content: string) => void;
}

export function AutomationSnippetModal(props: AutomationSnippetModalProps) {
  const {
    open,
    isDark,
    templateErr,
    newSnippetId,
    supportedVars,
    snippetIds,
    snippets,
    onClose,
    onNewSnippetIdChange,
    onAddSnippet,
    onDeleteSnippet,
    onUpdateSnippet,
  } = props;

  const { t } = useTranslation("settings");
  const automationVarHelp = getAutomationVarHelp(t);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[1000]"
      role="dialog"
      aria-modal="true"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="absolute inset-0 bg-black/50" />
      <div
        className="glass-modal absolute inset-2 sm:inset-auto sm:left-1/2 sm:top-1/2 sm:w-[min(820px,calc(100vw-20px))] sm:h-[min(74vh,700px)] sm:-translate-x-1/2 sm:-translate-y-1/2 rounded-xl sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden"
      >
        <div className="px-4 py-3 border-b border-[var(--glass-border-subtle)] flex items-start gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("snippetModal.title")}</div>
            <div className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
              {t("snippetModal.description")}
            </div>
          </div>
          <button
            type="button"
            className="glass-btn ml-auto px-3 py-2 rounded-lg text-sm min-h-[44px] transition-colors text-[var(--color-text-secondary)]"
            onClick={onClose}
          >
            {t("common:close")}
          </button>
        </div>

        <div className="p-3 sm:p-4 flex-1 overflow-auto space-y-3">
          {templateErr ? <div className="text-xs text-rose-600 dark:text-rose-300">{templateErr}</div> : null}
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2">
            <input
              value={newSnippetId}
              onChange={(e) => onNewSnippetIdChange(e.target.value)}
              className={`${inputClass(isDark)} font-mono`}
              placeholder="snippet_name"
              spellCheck={false}
            />
            <button
              type="button"
              className="glass-btn px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors text-[var(--color-text-primary)]"
              onClick={onAddSnippet}
            >
              {t("snippetModal.addSnippet")}
            </button>
          </div>

          {supportedVars.length > 0 ? (
            <div className="rounded-lg border border-[var(--glass-border-subtle)] p-2.5 text-[11px] bg-[var(--glass-panel-bg)] text-[var(--color-text-tertiary)]">
              <div className="font-semibold mb-1 text-[var(--color-text-secondary)]">{t("snippetModal.availablePlaceholders")}</div>
              <div className="space-y-1">
                {supportedVars.map((v) => {
                  const help = automationVarHelp[v];
                  return (
                    <div key={v}>
                      <span className="font-mono">{`{{${v}}}`}</span>
                      <span>{` - ${help?.description || t("automation.placeholderBuiltIn")}`}</span>
                      <span className="text-[var(--color-text-muted)]">{` (${t("automation.exampleLabel")}: ${help?.example || "-"})`}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}

          {snippetIds.length === 0 ? <div className="text-sm text-[var(--color-text-tertiary)]">{t("snippetModal.noSnippets")}</div> : null}

          <div className="space-y-3">
            {snippetIds.map((snippetId) => (
              <div key={snippetId} className={cardClass(isDark)}>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <div className="text-xs font-semibold font-mono text-[var(--color-text-primary)]">{snippetId}</div>
                  <button
                    type="button"
                    className="glass-btn px-2 py-1.5 rounded-lg text-xs min-h-[36px] transition-colors text-[var(--color-text-secondary)]"
                    onClick={() => onDeleteSnippet(snippetId)}
                    title={t("snippetModal.deleteSnippet")}
                  >
                    {t("common:delete")}
                  </button>
                </div>
                <textarea
                  value={snippets[snippetId] || ""}
                  onChange={(e) => onUpdateSnippet(snippetId, e.target.value)}
                  className={`${inputClass(isDark)} font-mono text-[12px]`}
                  style={{ minHeight: 140 }}
                  spellCheck={false}
                />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
