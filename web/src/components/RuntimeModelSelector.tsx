import { useMemo, useState } from "react";

import type { RuntimeInfo, SupportedRuntime } from "../types";
import {
  ACTOR_RUNTIME_CHOICES,
  buildRuntimeModelOptions,
  buildRuntimeSelectorOptions,
} from "../utils/runtimeChoiceGroups";
import { runtimeSupportsModelSelection, validateRuntimeModelId } from "../utils/runtimePresets";
import type { RuntimePriceMap } from "../utils/runtimePrices";
import { SelectCombobox, type SelectComboboxItem } from "./SelectCombobox";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

const DEFAULT_MODEL = "__runtime_default__";
const CUSTOM_MODEL = "__custom_model__";

export interface RuntimeModelSelectorProps {
  runtime: SupportedRuntime;
  model: string;
  runtimes: RuntimeInfo[];
  onRuntimeChange: (runtime: SupportedRuntime) => void;
  onModelChange: (model: string) => void;
  priceMap?: RuntimePriceMap | null;
  opencodeModels?: string[];
  allowedRuntimes?: readonly SupportedRuntime[];
  disabled?: boolean;
  labels: {
    runtime: string;
    model: string;
    runtimeSearch: string;
    modelSearch: string;
    noResults: string;
    defaultModel: string;
    customModel: string;
    customPlaceholder: string;
    apply: string;
    notInstalled: string;
    modelRequired: string;
    modelInvalid: string;
  };
}

export function RuntimeModelSelector({
  runtime,
  model,
  runtimes,
  onRuntimeChange,
  onModelChange,
  priceMap,
  opencodeModels = [],
  allowedRuntimes = ACTOR_RUNTIME_CHOICES,
  disabled = false,
  labels,
}: RuntimeModelSelectorProps) {
  const discoveredRuntimeOptions = useMemo<SelectComboboxItem[]>(
    () => buildRuntimeSelectorOptions(runtimes, allowedRuntimes).map((item) => ({
      value: item.value,
      label: item.disabled ? `${item.label} ${labels.notInstalled}` : item.label,
      description: item.description,
      disabled: item.disabled,
      keywords: [item.value],
    })),
    [allowedRuntimes, labels.notInstalled, runtimes],
  );
  const modelOptions = useMemo(
    () => buildRuntimeModelOptions(runtime, priceMap, opencodeModels),
    [runtime, priceMap, opencodeModels],
  );
  const presetModels = useMemo(() => new Set(modelOptions.map((item) => item.value)), [modelOptions]);
  const supportsModels = runtimeSupportsModelSelection(runtime);
  const manualModel = Boolean(model) && !presetModels.has(model);
  const [customOpen, setCustomOpen] = useState(manualModel);
  const [customValue, setCustomValue] = useState(manualModel ? model : "");
  const [customError, setCustomError] = useState("");

  const runtimeOptions = discoveredRuntimeOptions.some((item) => item.value === runtime)
    ? discoveredRuntimeOptions
    : [...discoveredRuntimeOptions, { value: runtime, label: runtime, disabled: false }];

  const modelItems: SelectComboboxItem[] = [
    { value: DEFAULT_MODEL, label: labels.defaultModel },
    ...modelOptions.map((item) => ({ value: item.value, label: item.label, keywords: [item.preset.label, item.value] })),
    { value: CUSTOM_MODEL, label: labels.customModel },
  ];
  const showCustomInput = customOpen || manualModel;
  const selectedValue = showCustomInput ? CUSTOM_MODEL : model || DEFAULT_MODEL;
  const applyCustom = () => {
    const normalized = (customOpen ? customValue : model).trim();
    const validationError = validateRuntimeModelId(normalized);
    const error = validationError ? (normalized ? labels.modelInvalid : labels.modelRequired) : "";
    setCustomError(error);
    if (validationError) return;
    onModelChange(normalized);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <label className="text-sm font-medium text-[var(--color-text-muted)] sm:w-16 sm:shrink-0">{labels.runtime}</label>
        <div className="min-w-0 flex-1">
          <SelectCombobox
            items={runtimeOptions}
            value={runtime}
            onChange={(value) => {
              setCustomOpen(false);
              setCustomValue("");
              setCustomError("");
              onRuntimeChange(value as SupportedRuntime);
            }}
            ariaLabel={labels.runtime}
            placeholder={labels.runtime}
            searchPlaceholder={labels.runtimeSearch}
            emptyText={labels.noResults}
            searchable
            disabled={disabled}
            className="onecolleague-runtime-select min-h-[44px] w-full border px-4 py-2.5 text-sm glass-input text-[var(--color-text-primary)]"
          />
        </div>
      </div>
      {supportsModels ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <label className="pt-3 text-sm font-medium text-[var(--color-text-muted)] sm:w-16 sm:shrink-0">{labels.model}</label>
          <div className="min-w-0 flex-1 space-y-2">
            <SelectCombobox
              items={modelItems}
              value={selectedValue}
              onChange={(value) => {
                if (value === CUSTOM_MODEL) {
                  setCustomOpen(true);
                  setCustomValue(model);
                  setCustomError("");
                  return;
                }
                setCustomOpen(false);
                setCustomError("");
                onModelChange(value === DEFAULT_MODEL ? "" : value);
              }}
              ariaLabel={labels.model}
              placeholder={labels.model}
              searchPlaceholder={labels.modelSearch}
              emptyText={labels.noResults}
              searchable
              disabled={disabled}
              className="min-h-[44px] w-full border px-4 py-2.5 text-sm glass-input text-[var(--color-text-primary)]"
            />
            {showCustomInput ? (
              <div className="flex gap-2">
                <div className="min-w-0 flex-1">
                  <Input
                    value={customOpen ? customValue : model}
                    onChange={(event) => {
                      setCustomOpen(true);
                      setCustomValue(event.target.value);
                      setCustomError("");
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        applyCustom();
                      }
                    }}
                    placeholder={labels.customPlaceholder}
                    aria-invalid={Boolean(customError)}
                    className="font-mono"
                  />
                  {customError ? <div className="mt-1 text-xs text-rose-600 dark:text-rose-300">{customError}</div> : null}
                </div>
                <Button type="button" variant="secondary" onClick={applyCustom} disabled={disabled}>{labels.apply}</Button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
