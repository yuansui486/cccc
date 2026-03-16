import { useTranslation } from 'react-i18next';
import { Theme } from "../types";
import { classNames } from "../utils/classNames";
import { SunIcon, MoonIcon, TerminalIcon } from "./Icons";

interface ThemeToggleProps {
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
  isDark: boolean;
  className?: string;
}

export function ThemeToggle({ theme, onThemeChange, isDark: _isDark }: ThemeToggleProps) {
  const { t } = useTranslation('layout');
  const themes: { value: Theme; label: string; Icon: React.FC<{ size?: number }> }[] = [
    { value: "light", label: t('themeLight'), Icon: SunIcon },
    { value: "dark", label: t('themeDark'), Icon: MoonIcon },
    { value: "system", label: t('themeSystem'), Icon: TerminalIcon },
  ];

  return (
    <div className="flex items-center gap-1 p-1 rounded-xl glass-btn">
      {themes.map((th) => (
        <button
          key={th.value}
          onClick={() => onThemeChange(th.value)}
          className={classNames(
            "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all min-h-[36px]",
            theme === th.value
              ? "bg-black/5 text-gray-900 shadow-sm dark:bg-white/10 dark:text-white"
              : "text-gray-500 hover:text-gray-700 dark:text-slate-400 dark:hover:text-slate-200"
          )}
          aria-label={t('switchToTheme', { theme: th.label })}
          aria-pressed={theme === th.value}
        >
          <th.Icon size={14} />
          <span className="hidden sm:inline">{th.label}</span>
        </button>
      ))}
    </div>
  );
}

// Compact version for header
export function ThemeToggleCompact({ theme, onThemeChange, isDark: _isDark, className }: ThemeToggleProps) {
  const { t } = useTranslation('layout');
  const nextTheme = (): Theme => {
    if (theme === "light") return "dark";
    if (theme === "dark") return "system";
    return "light";
  };

  const Icon = theme === "light" ? SunIcon : theme === "dark" ? MoonIcon : TerminalIcon;
  const label = theme === "light" ? t('themeLight') : theme === "dark" ? t('themeDark') : t('themeSystem');

  return (
    <button
      onClick={() => onThemeChange(nextTheme())}
      className={classNames(
        "flex items-center justify-center w-11 h-11 rounded-xl transition-all min-h-[44px] min-w-[44px] shrink-0 glass-btn",
        "text-[var(--color-text-secondary)]",
        className
      )}
      title={t('themeClickToChange', { theme: label })}
      aria-label={t('currentTheme', { theme: label })}
    >
      <Icon size={18} />
    </button>
  );
}
