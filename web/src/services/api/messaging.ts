import type { LedgerEvent } from "../../types";
import { apiJson } from "./base";

export async function fetchLedgerTail(groupId: string, lines = 120, init?: RequestInit & { noCache?: boolean }) {
  const params = new URLSearchParams({
    kind: "chat",
    limit: String(lines),
    with_read_status: "true",
    with_ack_status: "true",
    with_obligation_status: "true",
  });
  return apiJson<{ events: LedgerEvent[]; has_more: boolean; count: number }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/search?${params.toString()}`,
    init,
  );
}

export async function fetchOlderMessages(groupId: string, beforeEventId: string, limit = 50) {
  const params = new URLSearchParams({
    kind: "chat",
    before: beforeEventId,
    limit: String(limit),
    with_read_status: "true",
    with_ack_status: "true",
    with_obligation_status: "true",
  });
  return apiJson<{ events: LedgerEvent[]; has_more: boolean; count: number }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/search?${params.toString()}`,
  );
}

export async function fetchMessageWindow(
  groupId: string,
  centerEventId: string,
  opts?: { before?: number; after?: number },
) {
  const params = new URLSearchParams({
    kind: "chat",
    center: centerEventId,
    before: String(opts?.before ?? 30),
    after: String(opts?.after ?? 30),
    with_read_status: "true",
    with_ack_status: "true",
    with_obligation_status: "true",
  });
  return apiJson<{
    center_id: string;
    center_index: number;
    events: LedgerEvent[];
    has_more_before: boolean;
    has_more_after: boolean;
    count: number;
  }>(`/api/v1/groups/${encodeURIComponent(groupId)}/ledger/window?${params.toString()}`);
}

export async function searchChatMessages(
  groupId: string,
  q: string,
  opts?: { limit?: number; before?: string; after?: string },
) {
  const params = new URLSearchParams({
    kind: "chat",
    q: q || "",
    limit: String(opts?.limit ?? 50),
    with_read_status: "true",
    with_ack_status: "true",
    with_obligation_status: "true",
  });
  if (opts?.before) params.set("before", opts.before);
  if (opts?.after) params.set("after", opts.after);
  return apiJson<{ events: LedgerEvent[]; has_more: boolean; count: number }>(
    `/api/v1/groups/${encodeURIComponent(groupId)}/ledger/search?${params.toString()}`,
  );
}
