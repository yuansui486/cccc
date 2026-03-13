import type { DoneHubSession } from "../types";

export type DoneHubApiResponse<T> =
  | { ok: true; result: T; error?: null }
  | { ok: false; result?: unknown; error: { code: string; message: string; details?: unknown } };

type DoneHubSessionResult = {
  session?: DoneHubSession | null;
};

export const DONE_HUB_BASE_URL = "https://peer.shierkeji.com";
const DONE_HUB_BASE_URL_ERROR = "Login service URL is invalid.";

function makeError<T>(code: string, message: string): DoneHubApiResponse<T> {
  return { ok: false, error: { code, message } };
}

export function normalizeDoneHubBaseUrl(raw: string): string {
  const trimmed = String(raw || "").trim();
  if (!trimmed) return "";
  try {
    const url = new URL(trimmed);
    if (!/^https?:$/.test(url.protocol)) return "";
    url.hash = "";
    url.search = "";
    let normalized = url.toString();
    if (normalized.endsWith("/")) normalized = normalized.slice(0, -1);
    return normalized;
  } catch {
    return "";
  }
}

function getFixedDoneHubBaseUrl(): string {
  return normalizeDoneHubBaseUrl(DONE_HUB_BASE_URL);
}

function normalizeSession(value: unknown): DoneHubSession | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const accessToken = String(record.access_token || "").trim();
  const baseUrl = normalizeDoneHubBaseUrl(String(record.base_url || ""));
  if (!accessToken || !baseUrl) return null;
  return {
    base_url: baseUrl,
    access_token: accessToken,
    username: String(record.username || "").trim(),
    display_name: String(record.display_name || "").trim(),
    group: String(record.group || "").trim(),
    quota: Number(record.quota || 0),
    used_quota: Number(record.used_quota || 0),
    role: Number(record.role || 0),
    status: Number(record.status || 0),
  };
}

async function request<T>(path: string, body: Record<string, unknown>): Promise<DoneHubApiResponse<T>> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    });
  } catch (error) {
    return makeError("NETWORK_ERROR", error instanceof Error ? error.message : "Network request failed");
  }

  const text = await resp.text();
  if (!text) {
    if (resp.ok) return { ok: true, result: {} as T };
    return makeError("EMPTY_RESPONSE", `Server returned ${resp.status} with empty body`);
  }

  try {
    return JSON.parse(text) as DoneHubApiResponse<T>;
  } catch {
    return makeError("PARSE_ERROR", `Invalid JSON response: ${text.slice(0, 100)}`);
  }
}

export async function loginDoneHub(
  username: string,
  password: string,
): Promise<DoneHubApiResponse<DoneHubSessionResult>> {
  const normalizedBaseUrl = getFixedDoneHubBaseUrl();
  if (!normalizedBaseUrl) return makeError("invalid_base_url", DONE_HUB_BASE_URL_ERROR);
  return request<DoneHubSessionResult>("/api/v1/done_hub/login", {
    base_url: normalizedBaseUrl,
    username: String(username || "").trim(),
    password: String(password || ""),
  });
}

export async function refreshDoneHubSession(
  accessToken: string,
): Promise<DoneHubApiResponse<DoneHubSessionResult>> {
  const normalizedBaseUrl = getFixedDoneHubBaseUrl();
  if (!normalizedBaseUrl) return makeError("invalid_base_url", DONE_HUB_BASE_URL_ERROR);
  return request<DoneHubSessionResult>("/api/v1/done_hub/self", {
    base_url: normalizedBaseUrl,
    access_token: String(accessToken || "").trim(),
  });
}

export function extractDoneHubSession(resp: DoneHubApiResponse<DoneHubSessionResult>): DoneHubSession | null {
  if (!resp.ok) return null;
  return normalizeSession(resp.result?.session);
}

export function formatDoneHubQuota(value: number | null | undefined): string {
  const amount = Number(value ?? 0);
  if (!Number.isFinite(amount)) return "0元";
  const rmb = amount / 500000;
  const formatted = new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 4,
  }).format(rmb);
  return `${formatted}元`;
}

export function sanitizeDoneHubErrorMessage(message: string | null | undefined): string {
  const raw = String(message || "").trim();
  if (!raw) return "登录失败，请稍后重试。";
  if (/missing session/i.test(raw)) return "登录信息已失效，请重新登录。";
  return raw.replace(/done-hub/gi, "一号同事");
}
