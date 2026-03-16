import { useTranslation } from "react-i18next";
import { InfoIcon } from "../../Icons";
import { ScrollFade } from "../../ScrollFade";
import type { SettingsScope } from "./types";
import { ScopeTooltip } from "./ScopeTooltip";

interface SettingsTabOption {
  id: string;
  label: string;
}

interface SettingsNavigationProps {
  isDark: boolean;
  groupId?: string;
  scope: SettingsScope;
  scopeRootUrl: string;
  globalEnabled: boolean;
  tabs: SettingsTabOption[];
  activeTab: string;
  onScopeChange: (scope: SettingsScope) => void;
  onTabChange: (tabId: string) => void;
}

export function SettingsNavigation({
  isDark,
  groupId,
  scope,
  scopeRootUrl,
  globalEnabled,
  tabs,
  activeTab,
  onScopeChange,
  onTabChange,
}: SettingsNavigationProps) {
  const { t } = useTranslation("settings");
  const globalScopeTitle = globalEnabled ? t("navigation.globalScopeTitle") : t("navigation.globalLockedTitle");
  const globalScopeContent = globalEnabled ? t("navigation.globalScopeContent") : t("navigation.globalLockedContent");
  return (
    <>
      <aside className="hidden sm:flex sm:flex-col w-48 border-r flex-shrink-0 bg-[var(--glass-panel-bg)] border-[var(--glass-border-subtle)]">
        <div className="p-3 space-y-3">
          <div className="px-3 text-[10px] font-bold uppercase tracking-wider opacity-30 text-[var(--color-text-tertiary)]">
            {t("navigation.targetScope")}
          </div>
          <div className="flex flex-col gap-1">
            <button
              type="button"
              onClick={() => onScopeChange("group")}
              disabled={!groupId}
              className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm text-left font-semibold transition-colors ${
                scope === "group"
                  ? "bg-sky-500/15 text-sky-700 dark:text-sky-300 border border-sky-500/30"
                  : "hover:bg-[var(--glass-tab-bg-hover)] text-[var(--color-text-tertiary)]"
              } disabled:opacity-40`}
            >
              <span>{t("navigation.thisGroup")}</span>
              <ScopeTooltip
                isDark={isDark}
                title={t("navigation.groupScopeTitle")}
                content={
                  <>
                    {t("navigation.groupScopeContent", { scopeRoot: scopeRootUrl || groupId })}
                  </>
                }
              >
                {(getReferenceProps, setReference) => (
                  <div
                    ref={setReference}
                    {...getReferenceProps({
                      onClick: (e) => e.stopPropagation(),
                    })}
                    className="p-1 -mr-1 hover:bg-black/5 dark:hover:bg-white/5 rounded-full transition-colors opacity-50"
                  >
                    <InfoIcon size={12} />
                  </div>
                )}
              </ScopeTooltip>
            </button>

            <button
              type="button"
              onClick={() => onScopeChange("global")}
              disabled={!globalEnabled}
              className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm text-left font-semibold transition-colors ${
                scope === "global"
                  ? "bg-sky-500/15 text-sky-700 dark:text-sky-300 border border-sky-500/30"
                  : "hover:bg-[var(--glass-tab-bg-hover)] text-[var(--color-text-tertiary)]"
              } disabled:opacity-40`}
            >
              <span>{t("navigation.global")}</span>
              <ScopeTooltip
                isDark={isDark}
                title={globalScopeTitle}
                content={<>{globalScopeContent}</>}
              >
                {(getReferenceProps, setReference) => (
                  <div
                    ref={setReference}
                    {...getReferenceProps({
                      onClick: (e) => e.stopPropagation(),
                    })}
                    className="p-1 -mr-1 hover:bg-black/5 dark:hover:bg-white/5 rounded-full transition-colors opacity-50"
                  >
                    <InfoIcon size={12} />
                  </div>
                )}
              </ScopeTooltip>
            </button>
          </div>
        </div>

        <div className="mx-3 border-b border-[var(--glass-border-subtle)]" />

        <nav className="flex-1 overflow-y-auto scrollbar-subtle p-3 pb-4 space-y-1 [scrollbar-gutter:stable]">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`w-full flex items-center px-3 py-2 text-xs font-medium rounded-lg transition-colors ${
                activeTab === tab.id
                  ? "glass-tab-active text-sky-700 dark:text-sky-300"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--glass-tab-bg-hover)]"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </aside>

      <div className="sm:hidden flex flex-col flex-shrink-0">
        <div className="px-5 py-3 border-b border-[var(--glass-border-subtle)]">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onScopeChange("group")}
              disabled={!groupId}
              className={`flex-1 relative flex items-center justify-center px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                scope === "group"
                  ? "bg-sky-500/15 text-sky-700 dark:text-sky-300 border border-sky-500/30"
                  : "glass-btn text-[var(--color-text-secondary)]"
              } disabled:opacity-40`}
            >
              <span>{t("navigation.thisGroup")}</span>
              <div className="absolute right-1 top-1/2 -translate-y-1/2">
                <ScopeTooltip
                  isDark={isDark}
                  title={t("navigation.groupScopeTitle")}
                  content={
                    <>
                      {t("navigation.groupScopeContent", { scopeRoot: scopeRootUrl || groupId })}
                    </>
                  }
                >
                  {(getReferenceProps, setReference) => (
                    <div
                      ref={setReference}
                      {...getReferenceProps({
                        onClick: (e) => e.stopPropagation(),
                      })}
                      className="p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-full transition-colors opacity-50"
                    >
                      <InfoIcon size={14} />
                    </div>
                  )}
                </ScopeTooltip>
              </div>
            </button>

            <button
              type="button"
              onClick={() => onScopeChange("global")}
              disabled={!globalEnabled}
              className={`flex-1 relative flex items-center justify-center px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors ${
                scope === "global"
                  ? "bg-sky-500/15 text-sky-700 dark:text-sky-300 border border-sky-500/30"
                  : "glass-btn text-[var(--color-text-secondary)]"
              } disabled:opacity-40`}
            >
              <span>{t("navigation.global")}</span>
              <div className="absolute right-1 top-1/2 -translate-y-1/2">
                <ScopeTooltip
                  isDark={isDark}
                  title={globalScopeTitle}
                  content={<>{globalScopeContent}</>}
                >
                  {(getReferenceProps, setReference) => (
                    <div
                      ref={setReference}
                      {...getReferenceProps({
                        onClick: (e) => e.stopPropagation(),
                      })}
                      className="p-1.5 hover:bg-black/5 dark:hover:bg-white/5 rounded-full transition-colors opacity-50"
                    >
                      <InfoIcon size={14} />
                    </div>
                  )}
                </ScopeTooltip>
              </div>
            </button>
          </div>
        </div>

        <ScrollFade
          className="flex-shrink-0 w-full border-b border-[var(--glass-border-subtle)]"
          innerClassName="flex min-h-[48px]"
          fadeWidth={20}
        >
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`flex-shrink-0 px-4 py-2.5 text-xs font-medium transition-colors whitespace-nowrap ${
                activeTab === tab.id
                  ? "text-sky-700 dark:text-sky-300 border-b-2 border-sky-600 dark:border-sky-300"
                  : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </ScrollFade>
      </div>
    </>
  );
}
