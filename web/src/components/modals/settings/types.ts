// Shared types/helpers for the Settings modal.

export type SettingsScope = "group" | "global";
export type GroupTabId =
  | "automation"
  | "delivery"
  | "guidance"
  | "space"
  | "messaging"
  | "im"
  | "transcript"
  | "blueprint";
export type GlobalTabId = "capabilities" | "actorProfiles" | "myProfiles" | "webAccess" | "developer";

// Shared style class helpers — glass design system
export const inputClass = (_isDark?: boolean) =>
  `glass-input w-full text-[var(--color-text-primary)] text-sm min-h-[44px]`;

export const labelClass = (_isDark?: boolean) =>
  `block text-xs mb-1 text-[var(--color-text-secondary)]`;

export const primaryButtonClass = (_busy?: boolean) =>
  `px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors font-medium`;

export const cardClass = (_isDark?: boolean) =>
  `glass-panel rounded-lg p-3`;

export const preClass = (_isDark?: boolean) =>
  `mt-2 p-2 rounded overflow-x-auto whitespace-pre text-[11px] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--glass-border-subtle)]`;
