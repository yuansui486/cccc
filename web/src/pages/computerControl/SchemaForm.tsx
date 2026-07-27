import React from "react";
import { optionLabel, parameterDescription, parameterLabel } from "./localization";
import type { ToolSchema } from "./types";

type Props = {
  schema?: ToolSchema;
  toolName?: string;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
};

function inputClass(): string {
  return "mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-transparent px-2.5 text-sm outline-none focus:border-[var(--color-accent-primary)]";
}

function isComplexSchema(field: ToolSchema): boolean {
  return field.type === "object" || Boolean(field.oneOf?.length || field.anyOf?.length)
    || (field.type === "array" && field.items?.type === "object");
}

export function SchemaForm({ schema, value, onChange }: Props) {
  const properties = schema?.properties || {};
  const required = new Set(schema?.required || []);

  if (Object.keys(properties).length === 0) {
    return <p className="text-xs text-[var(--color-text-secondary)]">此工具不需要额外参数。</p>;
  }

  function setField(name: string, next: unknown) {
    const updated = { ...value };
    if (next === "" || next === undefined) delete updated[name];
    else updated[name] = next;
    onChange(updated);
  }

  return (
    <div className="space-y-4">
      {Object.entries(properties).map(([name, field]) => {
        const current = value[name] ?? field.default ?? "";
        const label = parameterLabel(name, field.title);
        const description = parameterDescription(name, field.description);
        const choices = field.enum || (name === "clicks" ? [0, 1, 2] : undefined);
        return (
          <label key={name} className="block text-xs font-medium">
            <span className="flex items-center gap-1">
              {label}
              {required.has(name) && <span className="text-red-500">*</span>}
            </span>
            {choices ? (
              <select className={inputClass()} value={String(current)} onChange={(event) => setField(name, field.type === "number" || field.type === "integer" ? Number(event.target.value) : event.target.value)}>
                {!required.has(name) && <option value="">未设置</option>}
                {choices.map((option) => <option key={String(option)} value={String(option)}>{optionLabel(option)}</option>)}
              </select>
            ) : field.type === "boolean" ? (
              <span className="mt-2 flex items-center justify-between rounded-md border border-[var(--color-border)] px-3 py-2">
                <span className="font-normal text-[var(--color-text-secondary)]">{current ? "已开启" : "已关闭"}</span>
                <input type="checkbox" checked={Boolean(current)} onChange={(event) => setField(name, event.target.checked)} />
              </span>
            ) : field.type === "number" || field.type === "integer" ? (
              <input className={inputClass()} type="number" min={field.minimum} max={field.maximum} value={String(current)} onChange={(event) => setField(name, event.target.value === "" ? "" : Number(event.target.value))} />
            ) : isComplexSchema(field) ? (
              <textarea
                className={`${inputClass()} min-h-20 resize-y py-2 font-mono text-xs`}
                value={typeof current === "string" ? current : JSON.stringify(current, null, 2)}
                onChange={(event) => {
                  const raw = event.target.value;
                  try { setField(name, JSON.parse(raw)); } catch { setField(name, raw); }
                }}
                placeholder="请输入 JSON 参数"
              />
            ) : field.type === "array" ? (
              <input className={inputClass()} value={Array.isArray(current) ? current.join(", ") : String(current)} onChange={(event) => setField(name, event.target.value.split(",").map((item) => { const itemValue = item.trim(); if (!itemValue) return ""; if (field.items?.type === "number" || field.items?.type === "integer") return Number(itemValue); return itemValue; }).filter((item) => item !== ""))} placeholder="多个值用逗号分隔" />
            ) : (
              <input className={inputClass()} value={String(current)} onChange={(event) => setField(name, event.target.value)} placeholder={`请输入${label}`} />
            )}
            {description && <span className="mt-1 block font-normal leading-4 text-[var(--color-text-secondary)]">{description}</span>}
          </label>
        );
      })}
    </div>
  );
}