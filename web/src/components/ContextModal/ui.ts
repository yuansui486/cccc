import { classNames } from "../../utils/classNames";

export interface ContextModalUi {
  surfaceClass: string;
  mutedTextClass: string;
  subtleTextClass: string;
  inputClass: string;
  textareaClass: string;
  buttonSecondaryClass: string;
  buttonPrimaryClass: string;
  buttonDangerClass: string;
  chipBaseClass: string;
  switchTrackClass: (active: boolean) => string;
  switchThumbClass: (active: boolean) => string;
}

export function createContextModalUi(isDark: boolean): ContextModalUi {
  const mutedTextClass = "text-[var(--color-text-muted)]";
  const subtleTextClass = "text-[var(--color-text-secondary)]";
  const inputClass = classNames(
    "w-full rounded-lg border px-3 py-2 text-sm outline-none transition-colors",
    "glass-input text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)]"
  );
  const textareaClass = classNames(inputClass, "min-h-[96px] resize-y");
  const buttonSecondaryClass = classNames(
    "rounded-lg px-3 py-2 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50",
    "glass-btn text-[var(--color-text-secondary)]"
  );

  return {
    surfaceClass: classNames("rounded-2xl border shadow-sm", "glass-card"),
    mutedTextClass,
    subtleTextClass,
    inputClass,
    textareaClass,
    buttonSecondaryClass,
    buttonPrimaryClass: "rounded-lg bg-blue-600 px-3 py-2 text-sm text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50",
    buttonDangerClass: "rounded-lg border border-rose-500/35 bg-rose-500/12 px-3 py-2 text-sm text-rose-700 transition-colors hover:bg-rose-500/18 dark:text-rose-300 disabled:cursor-not-allowed disabled:opacity-50",
    chipBaseClass: classNames(
      "rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
      "glass-card border-[var(--glass-border-subtle)] text-[var(--color-text-secondary)]"
    ),
    switchTrackClass: (active: boolean) => classNames(
      "relative inline-flex h-6 w-11 shrink-0 rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-50",
      active
        ? (isDark ? "border-blue-400 bg-blue-500/80" : "border-blue-500 bg-blue-500")
        : (isDark ? "border-slate-700 bg-slate-900" : "border-gray-300 bg-gray-200")
    ),
    switchThumbClass: (active: boolean) => classNames(
      "pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform",
      active ? "translate-x-5" : "translate-x-0"
    ),
  };
}
