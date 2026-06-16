import type { RuntimePreset, RuntimePresetId } from "./runtimePresets";

export type RuntimePrice = {
  model: string;
  input?: number;
  output?: number;
  type?: string;
  locked?: boolean;
};

export type RuntimePriceMap = Record<string, RuntimePrice>;

export const RUNTIME_PRICE_UNAVAILABLE_LABEL = "价格暂无";
const DISPLAY_PRICE_MULTIPLIER = 2;

export function normalizePriceKey(value: string): string {
  return String(value || "").trim().toLowerCase();
}

export function buildRuntimePriceMap(items: RuntimePrice[]): RuntimePriceMap {
  const out: RuntimePriceMap = {};
  for (const item of items) {
    const key = normalizePriceKey(item.model);
    if (!key) continue;
    out[key] = item;
  }
  return out;
}

export function modelNameForRuntimeChoice(option: { id: string; kind: "preset" | "runtime" }, preset?: RuntimePreset | null): string {
  if (preset?.model) return preset.model;
  if (option.id === "gemini") return "gemini-pro";
  return "";
}

export function priceForRuntimeChoice(
  option: { id: string; kind: "preset" | "runtime" },
  priceMap: RuntimePriceMap | null | undefined,
  preset?: RuntimePreset | null,
): RuntimePrice | null {
  if (!priceMap) return null;
  const model = normalizePriceKey(modelNameForRuntimeChoice(option, preset));
  if (!model) return null;
  return priceMap[model] || null;
}

export function formatRuntimePrice(price: RuntimePrice | null | undefined): string {
  if (!price) return "";
  const input = typeof price.input === "number" && Number.isFinite(price.input) ? price.input * DISPLAY_PRICE_MULTIPLIER : null;
  const output = typeof price.output === "number" && Number.isFinite(price.output) ? price.output * DISPLAY_PRICE_MULTIPLIER : null;
  if (input === null && output === null) return "";
  const fmt = (value: number) => `¥${new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value)}`;
  if (input !== null && output !== null) return `输入 ${fmt(input)}/M，输出 ${fmt(output)}/M`;
  if (input !== null) return `输入 ${fmt(input)}/M`;
  return `输出 ${fmt(output!)}/M`;
}

export function runtimePriceLabel(
  option: { id: RuntimePresetId | string; kind: "preset" | "runtime"; label: string },
  priceMap: RuntimePriceMap | null | undefined,
  preset?: RuntimePreset | null,
): string {
  const price = formatRuntimePrice(priceForRuntimeChoice(option, priceMap, preset));
  if (price) return `${option.label}（${price}）`;
  if (priceMap) return `${option.label}（${RUNTIME_PRICE_UNAVAILABLE_LABEL}）`;
  return option.label;
}
