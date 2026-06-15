import { useTranslation } from "react-i18next";
import type { ClaudeReasoningEffort, CodexReasoningEffort } from "../utils/runtimePresets";
import { Button } from "./ui/button";

type ReasoningEffortOption<T extends string> = {
  value: T;
  labelKey: string;
};

type ReasoningEffortLayout = "stacked" | "inline";

const CODEX_REASONING_OPTIONS: Array<ReasoningEffortOption<CodexReasoningEffort>> = [
  { value: "xhigh", labelKey: "reasoningXhigh" },
  { value: "high", labelKey: "reasoningHigh" },
  { value: "medium", labelKey: "reasoningMedium" },
  { value: "low", labelKey: "reasoningLow" },
  { value: "minimal", labelKey: "reasoningMinimal" },
];

const CLAUDE_REASONING_OPTIONS: Array<ReasoningEffortOption<ClaudeReasoningEffort>> = [
  { value: "max", labelKey: "reasoningXhigh" },
  { value: "xhigh", labelKey: "reasoningHigh" },
  { value: "high", labelKey: "reasoningMedium" },
  { value: "medium", labelKey: "reasoningLow" },
  { value: "low", labelKey: "reasoningMinimal" },
];

function modeButtonClass(selected: boolean): string {
  return [
    "px-3 py-2.5 rounded-xl border text-sm min-h-[44px] font-medium transition-colors",
    selected
      ? "border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg)] text-[var(--color-accent-primary)] dark:border-[var(--glass-accent-border)] dark:bg-white/[0.06] dark:text-white"
      : "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]",
  ].join(" ");
}

function ReasoningEffortButtonGrid<T extends string>({
  options,
  value,
  disabled,
  onChange,
  layout = "stacked",
}: {
  options: Array<ReasoningEffortOption<T>>;
  value: T;
  disabled?: boolean;
  onChange: (value: T) => void;
  layout?: ReasoningEffortLayout;
}) {
  const { t } = useTranslation("actors");
  const label = (
    <label
      className={
        layout === "inline"
          ? "text-xs font-medium text-[var(--color-text-muted)] sm:w-20 sm:shrink-0"
          : "block text-xs font-medium mb-2 text-[var(--color-text-muted)]"
      }
    >
      {t("reasoningEffort")}
    </label>
  );
  const buttons = (
    <div className={layout === "inline" ? "grid min-w-0 flex-1 grid-cols-5 gap-2" : "grid grid-cols-5 gap-2"}>
      {options.map((option) => (
        <Button
          key={option.value}
          type="button"
          variant="outline"
          className={modeButtonClass(value === option.value)}
          onClick={() => onChange(option.value)}
          disabled={disabled}
        >
          {t(option.labelKey)}
        </Button>
      ))}
    </div>
  );
  if (layout === "inline") {
    return (
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        {label}
        {buttons}
      </div>
    );
  }
  return (
    <div>
      {label}
      {buttons}
    </div>
  );
}

export function CodexReasoningEffortSelector({
  value,
  disabled,
  onChange,
  labelPlacement = "stacked",
}: {
  value: CodexReasoningEffort;
  disabled?: boolean;
  onChange: (value: CodexReasoningEffort) => void;
  labelPlacement?: ReasoningEffortLayout;
}) {
  return <ReasoningEffortButtonGrid options={CODEX_REASONING_OPTIONS} value={value} disabled={disabled} onChange={onChange} layout={labelPlacement} />;
}

export function ClaudeReasoningEffortSelector({
  value,
  disabled,
  onChange,
  labelPlacement = "stacked",
}: {
  value: ClaudeReasoningEffort;
  disabled?: boolean;
  onChange: (value: ClaudeReasoningEffort) => void;
  labelPlacement?: ReasoningEffortLayout;
}) {
  return <ReasoningEffortButtonGrid options={CLAUDE_REASONING_OPTIONS} value={value} disabled={disabled} onChange={onChange} layout={labelPlacement} />;
}
