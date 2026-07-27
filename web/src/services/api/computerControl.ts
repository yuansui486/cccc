import { apiJson, type ApiResponse } from "./base";

export type ComputerSetup = {
  phase: string;
  version: string;
  fingerprint?: string;
  tool_count?: number;
  error?: { code?: string; message?: string } | null;
  logs?: string[];
  session_running?: boolean;
  in_progress?: boolean;
  detail?: string | null;
  failure?: { phase?: string; exit_code?: number | null; stderr_tail?: string[] } | null;
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

const root = "/api/v1/computer-control";
const groupRoot = (groupId: string) => `/api/v1/groups/${encodeURIComponent(groupId)}/computer-control`;

export const computerControlApi = {
  ensure: (force = false) => apiJson<ComputerSetup>(`${root}/setup/ensure?force=${force}`, { method: "POST" }),
  status: () => apiJson<ComputerSetup>(`${root}/setup/status`),
  repair: () => apiJson<ComputerSetup>(`${root}/setup/repair`, { method: "POST" }),
  upgrade: () => apiJson<ComputerSetup>(`${root}/setup/upgrade`, { method: "POST" }),
  catalog: (tool?: string) => apiJson<{ version?: string; fingerprint?: string; healthy?: boolean; tools?: Record<string, unknown>[]; tool?: Record<string, unknown> }>(`${root}/catalog${tool ? `?tool=${encodeURIComponent(tool)}` : ""}`),
  workflows: (groupId: string) => apiJson<{ workflows: WorkflowManifest[] }>(`${groupRoot(groupId)}/workflows`),
  settings: (groupId: string) => apiJson<ComputerControlSettings>(`${groupRoot(groupId)}/settings`),
  updateSettings: (groupId: string, autoPublishAndTrust: boolean, authorizeCurrentFingerprint = false) => apiJson<ComputerControlSettings>(`${groupRoot(groupId)}/settings`, { method: "PUT", body: JSON.stringify({ auto_publish_and_trust: autoPublishAndTrust, authorize_current_fingerprint: authorizeCurrentFingerprint }) }),
  workflow: (groupId: string, workflowId: string, version?: number) => apiJson<WorkflowRecord>(`${groupRoot(groupId)}/workflows/${encodeURIComponent(workflowId)}${version ? `?version=${version}` : ""}`),
  createWorkflow: (groupId: string, definition: Record<string, unknown>) => apiJson<WorkflowRecord>(`${groupRoot(groupId)}/workflows`, { method: "POST", body: JSON.stringify({ definition }) }),
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
};

export type ComputerControlApi = typeof computerControlApi;
export type ComputerControlResponse<T> = ApiResponse<T>;
