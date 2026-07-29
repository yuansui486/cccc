import { apiJson, type ApiResponse } from "./base";

export type ComputerSetup = {
  phase: string;
  setup_phase?: string;
  version: string;
  fingerprint?: string;
  tool_count?: number;
  error?: { code?: string; message?: string } | null;
  logs?: string[];
  session_running?: boolean;
  session_started_at?: number | string | null;
  transport_restarts?: number;
  session?: {
    running?: boolean;
    started_at?: number | string | null;
    transport_restarts?: number;
  } | null;
  observation?: ComputerObservation | null;
  last_observation?: ComputerObservation | null;
  observation_updated_at?: number | string | null;
  observation_provider?: string;
  observation_target_window?: string;
  observation_element_count?: number;
  in_progress?: boolean;
  detail?: string | null;
  python_candidates?: string[];
  attempt_id?: string;
  operation?: string;
  step?: string;
  started_at?: number | string | null;
  step_started_at?: number | string | null;
  last_activity_at?: number | string | null;
  updated_at?: number | string | null;
  finished_at?: number | string | null;
  uv_version?: string | null;
  python_path?: string | null;
  python_source?: "system" | "uv_managed" | string | null;
  package_index?: string | null;
  package_index_fallback?: boolean;
  package_index_fallback_allowed?: boolean;
  process_id?: number | null;
  current_command?: string | null;
  can_cancel?: boolean;
  log_truncated?: boolean;
  failure?: { phase?: string; exit_code?: number | null; stderr_tail?: string[] } | null;
};

export type ComputerObservation = {
  updated_at?: number | string | null;
  captured_at?: number | string | null;
  provider?: string;
  source?: string;
  target_window?: string;
  window_name?: string;
  element_count?: number;
  count?: number;
};

export type WorkflowManifest = {
  workflow_id: string;
  name: string;
  description?: string;
  revision: number;
  current_version: number;
  published_version?: number | null;
  effective_version?: number | null;
  trusted?: Record<string, { fingerprint?: string; permissions?: string[] }>;
  archived?: boolean;
};

export type ComputerControlSettings = {
  auto_publish_and_trust: boolean;
  approved_fingerprint?: string;
  current_fingerprint?: string;
  reauthorization_required?: boolean;
};

export type WorkflowRecord = {
  manifest: WorkflowManifest;
  version: number;
  definition: Record<string, unknown>;
};

export type WorkflowVersion = { version: number; name?: string; change_note?: string };
export type OptimizationProposal = {
  proposal_id: string;
  base_version: number;
  summary?: string;
  status: "pending" | "accepted" | "rejected";
  created_at?: number;
  accepted_version?: number;
};
export type ComputerControlRequest = {
  request_id: string;
  text?: string;
  actor_id?: string;
  workflow_id?: string;
  status?: string;
  risk?: { level?: string; high_risk_nodes?: Array<{ title?: string; tool?: string }> };
};
export type ComputerControlLease = {
  active: boolean;
  lease?: { group_id?: string; actor_id?: string; run_id?: string; acquired_at?: number; heartbeat_at?: number; expires_at?: number; observe_only?: boolean };
  run?: { run_id?: string; group_id?: string; workflow_id?: string; actor_id?: string; status?: string; current_node_id?: string | null; started_at?: number; updated_at?: number };
  workflow?: { workflow_id?: string; name?: string; version?: number };
};

export type ElementPickerElement = {
  element_id?: string;
  mcp_label?: number;
  window_name?: string;
  process_name?: string;
  control_type?: string;
  name?: string;
  text?: string;
  automation_id?: string;
  class_name?: string;
  framework_id?: string;
  parent_name?: string;
  bounds?: { x?: number; y?: number; width?: number; height?: number; left?: number; top?: number };
  monitor?: number;
  stability?: string;
  stability_score?: number;
  locator?: Record<string, unknown>;
  [key: string]: unknown;
};

export type ElementPickerSession = {
  session_id: string;
  status: "starting" | "observing" | "locked" | "confirmed" | "ended" | "failed" | string;
  hotkey?: string;
  element?: ElementPickerElement | null;
  candidates?: ElementPickerElement[];
  warnings?: string[];
  warning?: string;
  stability?: string | Record<string, unknown> | null;
  created_at?: number;
  updated_at?: number;
};

export type ElementPickerEvent = {
  type: "started" | "hover" | "locked" | "warning" | "ended" | string;
  session_id?: string;
  element?: ElementPickerElement | null;
  candidates?: ElementPickerElement[];
  message?: string;
  [key: string]: unknown;
};

export type RecoveryContext = {
  run_id: string;
  status?: string;
  recovery_id?: string;
  node_id?: string;
  tool?: string;
  arguments?: Record<string, unknown>;
  error?: { code?: string; layer?: string; message?: string; next_action?: string; retryable?: boolean; field_errors?: Record<string, string> };
  candidates?: ElementPickerElement[];
  actions?: string[];
  [key: string]: unknown;
};

const root = "/api/v1/computer-control";
const groupRoot = (groupId: string) => `/api/v1/groups/${encodeURIComponent(groupId)}/computer-control`;

export const computerControlApi = {
  ensure: (force = false) => apiJson<ComputerSetup>(`${root}/setup/ensure?force=${force}`, { method: "POST" }),
  status: () => apiJson<ComputerSetup>(`${root}/setup/status`),
  repair: () => apiJson<ComputerSetup>(`${root}/setup/repair`, { method: "POST" }),
  upgrade: () => apiJson<ComputerSetup>(`${root}/setup/upgrade`, { method: "POST" }),
  cancelSetup: () => apiJson<ComputerSetup>(`${root}/setup/cancel`, { method: "POST" }),
  restartSession: () => apiJson<ComputerSetup>(`${root}/setup/restart-session`, { method: "POST" }),
  catalog: (tool?: string) => apiJson<{ version?: string; fingerprint?: string; healthy?: boolean; tools?: Record<string, unknown>[]; tool?: Record<string, unknown> }>(`${root}/catalog${tool ? `?tool=${encodeURIComponent(tool)}` : ""}`),
  leaseStatus: () => apiJson<ComputerControlLease>(`${root}/lease/status`),
  interruptLease: (runId: string, emergency = false) => apiJson<{ interrupted: boolean; emergency?: boolean; run?: Record<string, unknown> }>(`${root}/lease/interrupt`, { method: "POST", body: JSON.stringify({ run_id: runId, emergency }) }),
  workflows: (groupId: string) => apiJson<{ workflows: WorkflowManifest[] }>(`${groupRoot(groupId)}/workflows`),
  settings: (groupId: string) => apiJson<ComputerControlSettings>(`${groupRoot(groupId)}/settings`),
  updateSettings: (groupId: string, autoPublishAndTrust: boolean, authorizeCurrentFingerprint = false) => apiJson<ComputerControlSettings>(`${groupRoot(groupId)}/settings`, { method: "PUT", body: JSON.stringify({ auto_publish_and_trust: autoPublishAndTrust, authorize_current_fingerprint: authorizeCurrentFingerprint }) }),
  workflow: (groupId: string, workflowId: string, version?: number) => apiJson<WorkflowRecord>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}${version ? `?version=${version}` : ""}`),
  createWorkflow: (groupId: string, definition: Record<string, unknown>) => apiJson<WorkflowRecord>(`${groupRoot(groupId)}/workflows`, { method: "POST", body: JSON.stringify({ definition }) }),
  compileWorkflow: (groupId: string, definition: Record<string, unknown>) => apiJson<{ valid: boolean; definition?: Record<string, unknown>; diagnostics: Array<{ code: string; path?: string; message: string; severity?: string; next_action?: string }> }>(`${groupRoot(groupId)}/workflows/compile`, { method: "POST", body: JSON.stringify({ definition }) }),
  updateWorkflow: (groupId: string, workflowId: string, definition: Record<string, unknown>, expected_revision: number) => apiJson<WorkflowRecord>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}`, { method: "PUT", body: JSON.stringify({ definition, expected_revision }) }),
  deleteWorkflow: (groupId: string, workflowId: string) => apiJson<{ deleted: boolean }>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}`, { method: "DELETE" }),
  versions: (groupId: string, workflowId: string) => apiJson<{ versions: WorkflowVersion[] }>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}/versions`),
  rollback: (groupId: string, workflowId: string, version: number) => apiJson<WorkflowRecord>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}/versions/${version}/rollback`, { method: "POST" }),
  proposals: (groupId: string, workflowId: string) => apiJson<{ proposals: OptimizationProposal[] }>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}/optimization-proposals`),
  decideProposal: (groupId: string, workflowId: string, proposalId: string, decision: "accept" | "reject") => apiJson<OptimizationProposal>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}/optimization-proposals/${encodeURIComponent(proposalId)}/${decision}`, { method: "POST" }),
  publish: (groupId: string, workflowId: string, version: number) => apiJson<WorkflowManifest>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}/publish`, { method: "POST", body: JSON.stringify({ version }) }),
  trust: (groupId: string, workflowId: string, version: number) => apiJson<WorkflowManifest>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}/trust`, { method: "POST", body: JSON.stringify({ version, permissions: ["all_windows_mcp_tools"] }) }),
  run: (groupId: string, workflowId: string, actorId: string, version?: number) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}/runs`, { method: "POST", body: JSON.stringify({ actor_id: actorId, version }) }),
  runs: (groupId: string) => apiJson<{ runs: Record<string, unknown>[] }>(`${groupRoot(groupId)}/runs`),
  cancel: (groupId: string, runId: string, emergency = false) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST", body: JSON.stringify({ emergency }) }),
  verify: (groupId: string, runId: string, passed: boolean, summary: string, evidenceIds: string[] = []) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/runs/${encodeURIComponent(runId)}/verify`, { method: "POST", body: JSON.stringify({ passed, summary, evidence_ids: evidenceIds }) }),
  decideRunApproval: (groupId: string, runId: string, nodeId: string, approved: boolean) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(nodeId)}`, { method: "POST", body: JSON.stringify({ approved }) }),
  request: (groupId: string, text: string, workflowId = "", actorId = "foreman", inputs: Record<string, unknown> = {}) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/requests`, { method: "POST", body: JSON.stringify({ text, workflow_id: workflowId, actor_id: actorId, inputs }) }),
  requests: (groupId: string) => apiJson<{ requests: ComputerControlRequest[] }>(`${groupRoot(groupId)}/requests`),
  approveRequest: (groupId: string, requestId: string) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/requests/${encodeURIComponent(requestId)}/approve`, { method: "POST" }),
  rejectRequest: (groupId: string, requestId: string) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/requests/${encodeURIComponent(requestId)}/reject`, { method: "POST" }),
  captureElements: (groupId: string) => apiJson<{ capture_id: string; elements: Array<Record<string, unknown>>; warnings?: string[]; image_url?: string | null }>(`${groupRoot(groupId)}/element-picker/capture`, { method: "POST" }),
  validateElement: (groupId: string, locator: Record<string, unknown>) => apiJson<{ valid: boolean; status?: string; confidence?: string; match_count?: number; matches?: Array<Record<string, unknown>>; warnings?: string[] }>(`${groupRoot(groupId)}/element-picker/validate`, { method: "POST", body: JSON.stringify(locator) }),
  pickerStart: (groupId: string, options: { hotkey?: string; node_id?: string } = {}) => apiJson<ElementPickerSession>(`${groupRoot(groupId)}/element-picker/sessions`, { method: "POST", body: JSON.stringify(options) }),
  pickerSession: (groupId: string, sessionId: string) => apiJson<ElementPickerSession>(`${groupRoot(groupId)}/element-picker/sessions/${encodeURIComponent(sessionId)}`),
  pickerLock: (groupId: string, sessionId: string) => apiJson<ElementPickerSession>(`${groupRoot(groupId)}/element-picker/sessions/${encodeURIComponent(sessionId)}/lock`, { method: "POST" }),
  pickerConfirm: (groupId: string, sessionId: string, locator?: Record<string, unknown>) => apiJson<ElementPickerSession>(`${groupRoot(groupId)}/element-picker/sessions/${encodeURIComponent(sessionId)}/confirm`, { method: "POST", body: JSON.stringify(locator ? { locator } : {}) }),
  pickerStop: (groupId: string, sessionId: string) => apiJson<{ ended: boolean }>(`${groupRoot(groupId)}/element-picker/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  recovery: (groupId: string, runId: string) => apiJson<RecoveryContext>(`${groupRoot(groupId)}/runs/${encodeURIComponent(runId)}/recovery`),
  resolveRecovery: (groupId: string, runId: string, payload: { resolution: "reobserve" | "retry" | "repair_step" | "replace_target" | "skip" | "cancel"; node_id?: string; target?: Record<string, unknown>; arguments?: Record<string, unknown>; idempotency_key?: string }) => apiJson<Record<string, unknown>>(`${groupRoot(groupId)}/runs/${encodeURIComponent(runId)}/recovery/resolve`, { method: "POST", body: JSON.stringify(payload) }),
};

export type ComputerControlApi = typeof computerControlApi;
export type ComputerControlResponse<T> = ApiResponse<T>;
