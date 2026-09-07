import { apiBlob, apiForm, apiJson, type ApiResponse } from "./base";

export type Entity = {
  id?: string;
  type: string;
  type_name?: string;
  text?: string;
  start: number;
  end: number;
  score?: number;
  selected?: boolean;
  page?: number;
  bbox?: number[];
  box_ids?: string[];
};

export type OcrBox = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  id?: string;
  text?: string;
  selected?: boolean;
  page?: number;
  x1?: number;
  y1?: number;
  x2?: number;
  y2?: number;
};

export type Analysis = {
  embedded_images?: EmbeddedImage[];
  analysis_id: string;
  task_id?: string;
  filename: string;
  kind: string;
  text?: string;
  entities?: Entity[];
  boxes?: OcrBox[];
  warnings?: Array<{ code?: string; message?: string }>;
  page_count?: number;
  image_width?: number;
  image_height?: number;
  source_preview?: string;
  source_view?: PreviewView;
};

export type EmbeddedImage = {
  image_id: string;
  status: string;
  locations?: string[];
  image_width?: number;
  image_height?: number;
  entities: Entity[];
  boxes: OcrBox[];
  source_preview?: string;
};

export type PreviewView = {
  type?: "text" | "document" | "spreadsheet" | "pdf" | "image" | string;
  kind?: "text" | "document" | "spreadsheet" | "pdf" | "image" | string;
  url?: string;
  page_url_template?: string;
  text?: string;
  blocks?: Array<Record<string, unknown>>;
  sheets?: Array<Record<string, unknown>>;
  page_count?: number;
  truncated?: boolean;
};

export type Task = {
  id: string;
  batch_id?: string;
  original_name: string;
  relative_path?: string;
  kind: string;
  status: string;
  artifact_path?: string;
  report_path?: string;
  restored_path?: string;
  reversible?: boolean;
  created: number;
  updated: number;
  error_message?: string;
};

export type BatchItem = {
  sequence: number;
  relative_path: string;
  task_id?: string;
  status: string;
  error_code?: string;
  error_message?: string;
  analysis?: Analysis | null;
};

export type Batch = {
  id: string;
  batch_id?: string;
  status: string;
  mode: "auto" | "review";
  total: number;
  completed: number;
  failed: number;
  output_path?: string;
  items?: BatchItem[];
  created?: number;
  updated?: number;
  root_name?: string;
};

export type ModelStatus = {
  status: string;
  service_phase?: "stopped" | "installing" | "starting" | "ready" | "failed";
  install_progress?: number;
  install_step?: string;
  install_log_tail?: string[];
  last_log_line?: string;
  service_started_at?: number;
  service_updated_at?: number;
  service_error_code?: string;
  service_error_message?: string;
  service_retryable?: boolean;
  ner_error?: string;
  ocr_error?: string;
  phase?: string;
  progress?: number;
  message?: string;
  enabled?: boolean;
  available?: boolean;
  initialized?: boolean;
  ner_available?: boolean;
  ocr_available?: boolean;
  retryable?: boolean;
  missing_dependencies?: string[];
  model_dir?: string;
  error?: string;
};

export type Rule = {
  id: string;
  name: string;
  kind: string;
  pattern?: string;
  entity_type?: string;
  replacement?: string;
  enabled: boolean;
  category?: string;
  scope?: string;
  editable?: boolean;
  builtin?: boolean;
};

export type Policy = {
  text_action?: string;
  image_action?: string;
  replacement?: string;
  color?: string;
  blur_radius?: number;
  pixel_size?: number;
  show_replacement?: boolean;
};

export type RuleCategory = {
  id: string;
  name: string;
  builtin?: boolean;
  count?: number;
};

export type WorkspaceMetadata = {
  group_id: string;
  output_dir?: string;
  state_dir?: string;
  output_path?: string;
  scope_key?: string;
  scope_root?: string;
  capabilities?: Record<string, unknown>;
};

function root(groupId: string): string {
  return `/api/v1/groups/${encodeURIComponent(groupId)}/desensitization`;
}

function unwrap<T>(response: ApiResponse<T>): T {
  if (!response.ok) {
    const code = String(response.error.code || "UNKNOWN_ERROR").trim();
    const message = String(response.error.message || "Request failed").trim();
    const error = new Error(code ? `${code}: ${message}` : message) as Error & {
      code?: string;
      details?: unknown;
    };
    error.code = code;
    error.details = response.error.details;
    throw error;
  }
  return response.result;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  return unwrap(await apiJson<T>(path, init));
}

export function batchManifest(files: File[], mode: "auto" | "review", options: Record<string, unknown>) {
  const paths = files.map((file) => file.webkitRelativePath || file.name);
  return {
    version: 1,
    mode,
    root_name: paths[0]?.split("/")[0] || "",
    entries: paths.map((relative_path) => ({ relative_path })),
    options,
    idempotency_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`,
  };
}

export const desensitizationApi = {
  modelStatus: () => json<ModelStatus>("/api/v1/desensitization/models/status"),
  retryService: () => json<ModelStatus>("/api/v1/desensitization/service/retry", { method: "POST" }),
  initializeModels: () => json<{ job_id: string }>("/api/v1/desensitization/models/initialize", { method: "POST" }),
  modelJob: (jobId: string) => json<ModelStatus>(`/api/v1/desensitization/models/jobs/${encodeURIComponent(jobId)}`),
  metadata: (groupId: string) => json<WorkspaceMetadata>(`${root(groupId)}/metadata`),
  entities: (groupId: string) => json<Record<string, unknown>>(`${root(groupId)}/entities`),
  tasks: (groupId: string, offset = 0) => json<{ items: Task[]; total: number }>(`${root(groupId)}/tasks?limit=100&offset=${offset}`),
  taskAnalysis: (groupId: string, taskId: string) => json<Analysis>(`${root(groupId)}/tasks/${encodeURIComponent(taskId)}/analysis`),
  deleteTask: (groupId: string, taskId: string) => json(`${root(groupId)}/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" }),
  textAnalyze: (groupId: string, text: string) => json<{ text: string; entities: Entity[] }>(`${root(groupId)}/text/analyze`, {
    method: "POST", body: JSON.stringify({ text }),
  }),
  textMask: (groupId: string, text: string, entities: Entity[], policies: Record<string, Policy> = {}) => json<Record<string, unknown>>(`${root(groupId)}/text/mask`, {
    method: "POST", body: JSON.stringify({ text, entities, policies }),
  }),
  textUnmask: (groupId: string, taskId: string, maskedText?: string) => json<{ task_id: string; text: string }>(`${root(groupId)}/text/unmask`, {
    method: "POST", body: JSON.stringify({ task_id: taskId, ...(maskedText === undefined ? {} : { masked_text: maskedText }) }),
  }),
  textRestore: (groupId: string, taskId: string, maskedText?: string) => json<{ task_id: string; text: string }>(`${root(groupId)}/text/restore`, {
    method: "POST", body: JSON.stringify({ task_id: taskId, ...(maskedText === undefined ? {} : { masked_text: maskedText }) }),
  }),
  analyzeFile: async (groupId: string, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return unwrap(await apiForm<Analysis>(`${root(groupId)}/files/analyze`, form));
  },
  maskFile: (groupId: string, payload: Record<string, unknown>) => json<Record<string, unknown>>(`${root(groupId)}/files/mask`, {
    method: "POST", body: JSON.stringify(payload),
  }),
  previewFile: (groupId: string, taskId: string, payload: Record<string, unknown>) => json<Record<string, unknown>>(`${root(groupId)}/tasks/${encodeURIComponent(taskId)}/preview`, {
    method: "POST", body: JSON.stringify(payload),
  }),
  restore: (groupId: string, taskId: string, password: string) => json(`${root(groupId)}/tasks/${encodeURIComponent(taskId)}/restore`, {
    method: "POST", body: JSON.stringify({ password }),
  }),
  createBatch: async (groupId: string, files: File[], mode: "auto" | "review", options: Record<string, unknown>) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    form.append("manifest", JSON.stringify(batchManifest(files, mode, options)));
    return unwrap(await apiForm<Batch>(`${root(groupId)}/batches`, form));
  },
  batches: (groupId: string) => json<{ items: Batch[]; total: number }>(`${root(groupId)}/batches?limit=100`),
  batch: (groupId: string, batchId: string) => json<Batch>(`${root(groupId)}/batches/${encodeURIComponent(batchId)}`),
  cancelBatch: (groupId: string, batchId: string) => json<Batch>(`${root(groupId)}/batches/${encodeURIComponent(batchId)}/cancel`, { method: "POST" }),
  executeBatch: (groupId: string, batchId: string, payload: Record<string, unknown>) => json<Batch>(`${root(groupId)}/batches/${encodeURIComponent(batchId)}/mask`, {
    method: "POST", body: JSON.stringify(payload),
  }),
  rules: (groupId: string) => json<{ rules: Rule[]; builtin_rules: Rule[]; ner_enabled?: boolean }>(`${root(groupId)}/rules`),
  customSensitive: (groupId: string) => json<{ items: Rule[] }>(`${root(groupId)}/custom-sensitive`),
  createCustomSensitive: (groupId: string, payload: Record<string, unknown>) => json(`${root(groupId)}/custom-sensitive`, { method: "POST", body: JSON.stringify(payload) }),
  updateCustomSensitive: (groupId: string, ruleId: string, payload: Record<string, unknown>) => json(`${root(groupId)}/custom-sensitive/${encodeURIComponent(ruleId)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteCustomSensitive: (groupId: string, ruleId: string) => json(`${root(groupId)}/custom-sensitive/${encodeURIComponent(ruleId)}`, { method: "DELETE" }),
  createRule: (groupId: string, payload: Record<string, unknown>) => json(`${root(groupId)}/rules`, { method: "POST", body: JSON.stringify(payload) }),
  updateRule: (groupId: string, ruleId: string, payload: Record<string, unknown>) => json(`${root(groupId)}/rules/${encodeURIComponent(ruleId)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteRule: (groupId: string, ruleId: string) => json(`${root(groupId)}/rules/${encodeURIComponent(ruleId)}`, { method: "DELETE" }),
  toggleBuiltinRule: (groupId: string, ruleId: string, enabled: boolean) => json(`${root(groupId)}/rules/builtin/${encodeURIComponent(ruleId)}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  toggleModelRule: (groupId: string, enabled: boolean) => json(`${root(groupId)}/rules/model`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  ruleCategories: (groupId: string) => json<{ categories: RuleCategory[] }>(`${root(groupId)}/rule-categories`),
  createRuleCategory: (groupId: string, name: string) => json<RuleCategory>(`${root(groupId)}/rule-categories`, {
    method: "POST", body: JSON.stringify({ name }),
  }),
  renameRuleCategory: (groupId: string, categoryId: string, name: string) => json<RuleCategory>(`${root(groupId)}/rule-categories/${encodeURIComponent(categoryId)}`, {
    method: "PATCH", body: JSON.stringify({ name }),
  }),
  deleteRuleCategory: (groupId: string, categoryId: string) => json(`${root(groupId)}/rule-categories/${encodeURIComponent(categoryId)}`, { method: "DELETE" }),
  policies: (groupId: string) => json<{ policies: Record<string, Policy> }>(`${root(groupId)}/policies`),
  putPolicies: (groupId: string, policies: Record<string, Policy>) => json(`${root(groupId)}/policies`, { method: "PUT", body: JSON.stringify(policies) }),
  resetPolicies: (groupId: string) => json(`${root(groupId)}/policies/reset`, { method: "POST" }),
  blob: async (path: string, fallbackName = "preview.bin") => unwrap(await apiBlob(path, fallbackName)),
  download: async (groupId: string, path: string, fallbackName: string) => {
    const response = await apiBlob(path.startsWith("/") ? path : `${root(groupId)}/${path}`, fallbackName);
    const result = unwrap(response);
    const href = URL.createObjectURL(result.blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = result.filename;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  },
  taskUrl: (groupId: string, taskId: string, kind: "source" | "artifact" | "report" | "restored") => `${root(groupId)}/tasks/${encodeURIComponent(taskId)}/${kind}`,
  batchUrl: (groupId: string, batchId: string, kind: "download" | "report") => `${root(groupId)}/batches/${encodeURIComponent(batchId)}/${kind}`,
};
