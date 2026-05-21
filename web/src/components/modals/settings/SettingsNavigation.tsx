import { useTranslation } from "react-i18next";
import { ScrollFade } from "../../ScrollFade";
import { FolderIcon } from "../../Icons";
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
  account?: {
    label: string;
    title: string;
    status: "idle" | "authenticating" | "connected" | "refreshing" | "error";
    isPro: boolean;
    onClick: () => void;
  };
}

export function SettingsNavigation({
  isDark,
  tabs,
  activeTab,
  onTabChange,
  account,
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
        {account ? (
          <div className="border-t border-[var(--glass-border-subtle)] px-4 py-4">
            <button
              type="button"
              onClick={account.onClick}
              className={`flex min-h-[48px] w-full items-center gap-3 rounded-[16px] border border-[var(--glass-border-subtle)] bg-transparent px-3.5 py-3 text-left text-sm font-medium transition-all hover:bg-[var(--glass-tab-bg-hover)] ${
                account.status === "error"
                  ? "text-rose-700 dark:text-rose-300"
                  : account.status === "connected" || account.status === "refreshing"
                    ? "text-sky-700 dark:text-sky-300"
                    : "text-[var(--color-text-primary)]"
              }`}
              title={account.title}
            >
              <span className="flex h-5 min-w-5 items-center justify-center">
                {account.isPro ? (
                  <span className="rounded-full border border-amber-300/65 bg-amber-400/18 px-1.5 py-0.5 text-[9px] font-semibold leading-none text-amber-700 dark:border-amber-300/30 dark:bg-amber-300/14 dark:text-amber-200">
                    PRO
                  </span>
                ) : (
                  <FolderIcon size={17} />
                )}
              </span>
              <span className="min-w-0 truncate">{account.label}</span>
            </button>
          </div>
        ) : null}
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
