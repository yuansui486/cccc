import React, { useMemo } from "react";
import { Plus, Trash2 } from "lucide-react";
import { optionLabel, parameterDescription, parameterLabel } from "./localization";
import type { ToolSchema } from "./types";

type Props = {
  schema?: ToolSchema;
  toolName?: string;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
};

type FieldProps = {
  name: string;
  schema: ToolSchema;
  value: unknown;
  required?: boolean;
  path?: string;
  onChange: (value: unknown) => void;
};

const inputClass =
  "mt-1 h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2.5 text-sm outline-none transition focus:border-[var(--color-accent-primary)]";
const compactInputClass =
  "h-8 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg-primary)] px-2 text-xs outline-none focus:border-[var(--color-accent-primary)]";

function defaultValue(schema: ToolSchema): unknown {
  if (schema.default !== undefined) return schema.default;
  if (schema.type === "boolean") return false;
  if (schema.type === "array") return [];
  if (schema.type === "object") return {};
  return "";
}

function formatSummary(value: unknown): string {
  if (value === undefined || value === null || value === "") return "未设置";
  if (typeof value === "boolean") return value ? "已开启" : "已关闭";
  if (Array.isArray(value)) return value.length ? `已填写 ${value.length} 项` : "未设置";
  if (typeof value === "object") return "已填写";
  const text = String(value);
  return text.length > 70 ? `${text.slice(0, 67)}…` : text;
}

function parseInput(raw: string, schema: ToolSchema): unknown {
  if (raw === "") return "";
  if (schema.type === "number") return Number(raw);
  if (schema.type === "integer") return Math.trunc(Number(raw));
  return raw;
}

function FieldEditor({ name, schema, value, required = false, path = name, onChange }: FieldProps) {
  const choices = schema.enum;
  const alternatives = schema.oneOf?.length ? schema.oneOf : schema.anyOf;
  const selectedAlternative = useMemo(() => {
    if (!alternatives?.length) return -1;
    const currentType = Array.isArray(value) ? "array" : value === null ? "null" : typeof value;
    const found = alternatives.findIndex((item) => item.type === currentType);
    return found >= 0 ? found : 0;
  }, [alternatives, value]);

  if (alternatives?.length) {
    const variant = alternatives[Math.max(0, selectedAlternative)] || alternatives[0];
    return (
      <div className="space-y-2 rounded-md border border-[var(--color-border)]/70 p-3">
        <label className="block text-xs font-medium">
          <span className="flex items-center gap-1">{parameterLabel(name, schema.title)}{required && <span className="text-red-500">*</span>}</span>
          <select
            className={inputClass}
            value={selectedAlternative < 0 ? 0 : selectedAlternative}
            onChange={(event) => onChange(defaultValue(alternatives[Number(event.target.value)] || variant))}
          >
            {alternatives.map((item, index) => <option key={index} value={index}>{item.title || item.type || `模式 ${index + 1}`}</option>)}
          </select>
        </label>
        <FieldEditor name={`${name}（模式）`} schema={variant} value={value} path={path} onChange={onChange} />
      </div>
    );
  }

  if (schema.type === "object" || schema.properties) {
    const objectValue = value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown>
      : {};
    const properties = schema.properties || {};
    const keys = Object.keys(properties);
    if (!keys.length) {
      return <DynamicObjectField name={name} schema={schema} value={objectValue} onChange={onChange} />;
    }
    return (
      <fieldset className="space-y-3 rounded-md border border-[var(--color-border)]/70 p-3">
        <legend className="px-1 text-xs font-medium">{parameterLabel(name, schema.title)}{required && <span className="ml-1 text-red-500">*</span>}</legend>
        {schema.description && <p className="-mt-1 text-xs leading-4 text-[var(--color-text-secondary)]">{parameterDescription(name, schema.description)}</p>}
        {keys.map((childName) => {
          const childSchema = properties[childName];
          return (
            <FieldEditor
              key={`${path}.${childName}`}
              name={childName}
              schema={childSchema}
              value={objectValue[childName] ?? childSchema.default}
              required={(schema.required || []).includes(childName)}
              path={`${path}.${childName}`}
              onChange={(next) => {
                const nextObject = { ...objectValue };
                if (next === "" || next === undefined) delete nextObject[childName];
                else nextObject[childName] = next;
                onChange(nextObject);
              }}
            />
          );
        })}
      </fieldset>
    );
  }

  if (schema.type === "array") {
    const items = Array.isArray(value) ? value : [];
    const itemSchema = schema.items || { type: "string" };
    return (
      <fieldset className="space-y-2 rounded-md border border-[var(--color-border)]/70 p-3">
        <legend className="px-1 text-xs font-medium">{parameterLabel(name, schema.title)}{required && <span className="ml-1 text-red-500">*</span>}</legend>
        {schema.description && <p className="-mt-1 text-xs leading-4 text-[var(--color-text-secondary)]">{parameterDescription(name, schema.description)}</p>}
        {items.map((item, index) => (
          <div className="flex items-start gap-2" key={`${path}[${index}]`}>
            <div className="min-w-0 flex-1">
              <FieldEditor name={`第 ${index + 1} 项`} schema={itemSchema} value={item} path={`${path}[${index}]`} onChange={(next) => {
                const nextItems = [...items];
                nextItems[index] = next;
                onChange(nextItems);
              }} />
            </div>
            <button type="button" title="删除这一项" aria-label="删除这一项" className="mt-1 rounded p-1.5 text-red-600 hover:bg-red-500/10" onClick={() => onChange(items.filter((_, itemIndex) => itemIndex !== index))}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        <button type="button" className="inline-flex items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1.5 text-xs hover:bg-black/5" onClick={() => onChange([...items, defaultValue(itemSchema)])}>
          <Plus size={13} /> 添加一项
        </button>
      </fieldset>
    );
  }

  const label = parameterLabel(name, schema.title);
  const description = parameterDescription(name, schema.description);
  const current = value === undefined ? schema.default : value;
  return (
    <label className="block text-xs font-medium">
      <span className="flex items-center gap-1">{label}{required && <span className="text-red-500">*</span>}</span>
      {choices ? (
        <select className={inputClass} value={current === undefined ? "" : String(current)} onChange={(event) => onChange(event.target.value === "" ? "" : parseInput(event.target.value, schema))}>
          {!required && <option value="">未设置</option>}
          {choices.map((choice) => <option key={String(choice)} value={String(choice)}>{optionLabel(choice)}</option>)}
        </select>
      ) : schema.type === "boolean" ? (
        <span className="mt-1 flex min-h-9 items-center justify-between rounded-md border border-[var(--color-border)] px-3 py-2">
          <span className="font-normal text-[var(--color-text-secondary)]">{current ? "已开启" : "已关闭"}</span>
          <input type="checkbox" checked={Boolean(current)} onChange={(event) => onChange(event.target.checked)} />
        </span>
      ) : schema.type === "number" || schema.type === "integer" ? (
        <input className={inputClass} type="number" min={schema.minimum} max={schema.maximum} value={current === undefined ? "" : String(current)} onChange={(event) => onChange(parseInput(event.target.value, schema))} />
      ) : (
        <input className={inputClass} type={schema.format === "password" || name.toLowerCase().includes("password") ? "password" : "text"} value={current === undefined ? "" : String(current)} onChange={(event) => onChange(event.target.value)} placeholder={`请输入${label}`} />
      )}
      {description && <span className="mt-1 block font-normal leading-4 text-[var(--color-text-secondary)]">{description}</span>}
    </label>
  );
}

function DynamicObjectField({ name, schema, value, onChange }: { name: string; schema: ToolSchema; value: Record<string, unknown>; onChange: (value: unknown) => void }) {
  const entries = Object.entries(value);
  return (
    <fieldset className="space-y-2 rounded-md border border-[var(--color-border)]/70 p-3">
      <legend className="px-1 text-xs font-medium">{parameterLabel(name, schema.title)}</legend>
      {schema.description && <p className="-mt-1 text-xs leading-4 text-[var(--color-text-secondary)]">{parameterDescription(name, schema.description)}</p>}
      {entries.map(([key, item]) => (
        <div className="flex items-center gap-2" key={key}>
          <input className={compactInputClass} value={key} aria-label="参数名称" onChange={(event) => {
            const next = { ...value };
            delete next[key];
            next[event.target.value] = item;
            onChange(next);
          }} />
          <input className={compactInputClass} value={String(item ?? "")} aria-label="参数值" onChange={(event) => onChange({ ...value, [key]: event.target.value })} />
          <button type="button" title="删除参数" aria-label="删除参数" className="rounded p-1.5 text-red-600 hover:bg-red-500/10" onClick={() => { const next = { ...value }; delete next[key]; onChange(next); }}><Trash2 size={14} /></button>
        </div>
      ))}
      <button type="button" className="inline-flex items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1.5 text-xs hover:bg-black/5" onClick={() => onChange({ ...value, 参数: "" })}><Plus size={13} /> 添加字段</button>
    </fieldset>
  );
}

export function SchemaForm({ schema, toolName, value, onChange }: Props) {
  const properties = schema?.properties || {};
  const required = new Set(schema?.required || []);
  if (!schema || Object.keys(properties).length === 0) {
    return <p className="text-xs text-[var(--color-text-secondary)]">此工具不需要额外参数。</p>;
  }

  const isElementTool = /^(click|type)$/i.test(toolName || "");
  function setField(name: string, next: unknown) {
    const updated = { ...value };
    if (next === "" || next === undefined) delete updated[name];
    else updated[name] = next;
    onChange(updated);
  }

  return (
    <div className="space-y-4">
      {isElementTool && <div className="rounded-md border border-sky-500/30 bg-sky-500/5 px-3 py-2 text-xs leading-4 text-sky-900 dark:text-sky-100">此工具优先使用已捕获的界面元素。屏幕坐标只作为明确允许的一次性兜底，不会保存为长期定位。</div>}
      {Object.entries(properties).map(([name, field]) => (
        <FieldEditor key={name} name={name} schema={field} value={value[name] ?? field.default} required={required.has(name)} onChange={(next) => setField(name, next)} />
      ))}
    </div>
  );
}
