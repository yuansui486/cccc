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
export type GlobalTabId = "capabilities" | "actorProfiles" | "myProfiles" | "branding" | "webAccess" | "developer";

// Shared style class helpers — glass design system
export const inputClass = (_isDark?: boolean) =>
  `glass-input w-full rounded-xl px-4 py-3 pr-5 text-[var(--color-text-primary)] text-sm leading-6 min-h-[44px] placeholder:text-[var(--color-text-muted)]`;

export const labelClass = (_isDark?: boolean) =>
  `block text-xs mb-1 text-[var(--color-text-secondary)]`;

export const primaryButtonClass = (_busy?: boolean) =>
  `px-4 py-2 bg-sky-600 hover:bg-sky-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors font-medium`;

export const secondaryButtonClass = (size: "sm" | "md" = "md") =>
  `inline-flex items-center justify-center gap-2 rounded-xl border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] hover:bg-[var(--glass-bg-hover)] active:bg-[var(--glass-bg-active)] ${
    size === "sm" ? "px-2.5 py-1.5 text-xs min-h-[36px]" : "px-3.5 py-2.5 text-sm min-h-[44px]"
  } cursor-pointer font-medium text-[var(--color-text-secondary)] shadow-sm transition-[background-color,border-color,box-shadow] disabled:opacity-50 disabled:cursor-not-allowed`;

export const dangerButtonClass = (size: "sm" | "md" = "md") =>
  `inline-flex items-center justify-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/15 hover:bg-rose-500/22 active:bg-rose-500/28 ${
    size === "sm" ? "px-2.5 py-1.5 text-xs min-h-[36px]" : "px-3.5 py-2.5 text-sm min-h-[44px]"
  } cursor-pointer font-medium text-rose-700 dark:text-rose-300 shadow-sm transition-[background-color,border-color,box-shadow] disabled:opacity-50 disabled:cursor-not-allowed`;

export const settingsDialogPanelClass = (size: "lg" | "xl" = "lg") =>
  `glass-modal absolute inset-0 sm:inset-auto sm:left-1/2 sm:top-1/2 ${
    size === "xl"
      ? "sm:w-[min(980px,calc(100vw-2rem))] sm:h-[min(88vh,860px)]"
      : "sm:w-[min(920px,calc(100vw-2rem))] sm:h-[min(86vh,820px)]"
  } sm:-translate-x-1/2 sm:-translate-y-1/2 rounded-none sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden`;

export const settingsDialogHeaderClass =
  `flex shrink-0 items-start gap-3 border-b border-[var(--glass-border-subtle)] px-4 py-3 sm:px-5 sm:py-4`;

export const settingsDialogBodyClass =
  `min-h-0 flex-1 overflow-y-auto scrollbar-subtle p-4 sm:p-5 [scrollbar-gutter:stable]`;

export const settingsDialogFooterClass =
  `flex shrink-0 items-center justify-end gap-2 border-t border-[var(--glass-border-subtle)] px-4 py-3 sm:px-5 sm:py-4 safe-area-bottom-compact`;

export const cardClass = (_isDark?: boolean) =>
  `glass-panel rounded-lg p-3`;

export const preClass = (_isDark?: boolean) =>
  `mt-2 p-2 rounded overflow-x-auto whitespace-pre text-[11px] bg-[var(--color-bg-secondary)] text-[var(--color-text-primary)] border border-[var(--glass-border-subtle)]`;
