import { useTranslation } from "react-i18next";
import { ScrollFade } from "../../ScrollFade";
import {
  settingsWorkspaceSoftPanelClass,
} from "./types";

interface SettingsTabOption {
  id: string;
  label: string;
}

interface SettingsNavigationProps {
  isDark: boolean;
  tabs: SettingsTabOption[];
  activeTab: string;
  onTabChange: (tabId: string) => void;
}

export function SettingsNavigation({
  isDark,
  tabs,
  activeTab,
  onTabChange,
}: SettingsNavigationProps) {
  const { t } = useTranslation("settings");
  const tabButtonClass = (active: boolean) =>
    `w-full flex items-center rounded-[14px] px-3 py-2.5 text-sm font-medium transition-[background-color,border-color,color,box-shadow] ${
      active
        ? "border border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg-active)] text-[var(--color-text-primary)] shadow-[0_8px_22px_rgba(15,23,42,0.05)]"
        : "border border-transparent text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--glass-tab-bg-hover)]"
    }`;

  return (
    <>
      <aside
        className={`hidden border-r border-[var(--glass-border-subtle)] sm:flex sm:w-60 lg:w-[16.5rem] sm:flex-col shrink-0 ${
          isDark
            ? "bg-[linear-gradient(180deg,rgba(20,22,26,0.96),rgba(14,15,18,0.92))]"
            : "bg-[linear-gradient(180deg,rgba(255,255,255,0.99),rgba(247,249,252,0.94))]"
        }`}
      >
        <nav className="flex-1 overflow-y-auto scrollbar-hide px-4 pb-4 pt-3 lg:px-4">
          <div className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--color-text-muted)]">
            {t("navigation.sections", { defaultValue: "Sections" })}
          </div>
          <div className="space-y-1">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={tabButtonClass(activeTab === tab.id)}
            >
              {tab.label}
            </button>
          ))}
          </div>
        </nav>
      </aside>

      <div className="sm:hidden flex flex-col flex-shrink-0">
        <ScrollFade
          className="flex-shrink-0 w-full border-b border-[var(--glass-border-subtle)]"
          innerClassName="flex min-h-[54px] px-4 py-2"
          fadeWidth={20}
        >
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`${settingsWorkspaceSoftPanelClass(isDark)} flex-shrink-0 px-4 py-2.5 text-xs font-medium whitespace-nowrap ${
                activeTab === tab.id
                  ? "!border-[var(--glass-border-subtle)] !bg-[var(--glass-tab-bg-active)] text-[var(--color-text-primary)]"
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
