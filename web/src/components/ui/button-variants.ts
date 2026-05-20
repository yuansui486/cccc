import { cva } from "class-variance-authority";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)]/45 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "border border-blue-600 bg-blue-600 text-white shadow-[var(--glass-accent-shadow)] hover:border-blue-700 hover:bg-blue-700 dark:border-blue-400 dark:bg-blue-500 dark:text-white dark:hover:border-blue-300 dark:hover:bg-blue-400",
        secondary: "glass-btn text-[var(--color-text-secondary)]",
        destructive:
          "border border-rose-500/30 bg-rose-500/15 text-rose-600 hover:bg-rose-500/25 dark:text-rose-400",
        outline:
          "border border-[var(--glass-border-subtle)] bg-transparent text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg)]",
        ghost: "text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg)]",
      },
      size: {
        default: "min-h-[44px] px-4 py-2.5 text-sm",
        sm: "min-h-[36px] rounded-lg px-3 py-1.5 text-xs",
        lg: "min-h-[52px] px-5 py-3 text-base",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);
