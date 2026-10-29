import type { SettingsScope } from "../components/modals/settings/types";

// Product presentation only: upstream components, permissions and stored data remain intact.
export const onecolleagueSettings = {
  isTabVisible(scope: SettingsScope, tab: string): boolean {
    return scope === "global"
      ? !["account", "branding", "developer"].includes(tab)
      : tab !== "guidance";
  },
  showGlobalScopeExplanation: false,
};

export function visibleSettingsTabs<T extends { id: string }>(
  scope: SettingsScope,
  tabs: T[],
): T[] {
  return tabs.filter((tab) => onecolleagueSettings.isTabVisible(scope, tab.id));
}

export function resolveSettingsTab<T extends string>(requested: T, permitted: { id: T }[]): T {
  return permitted.find((tab) => tab.id === requested)?.id ?? permitted[0]?.id ?? requested;
}
