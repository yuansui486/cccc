import { apiJson } from "./base";

export type GroupBridgeAccessLevel = "messages" | "read" | "full";

export interface GroupBridgeIdentity {
  peer_id: string;
  public_key: string;
  node_id: string;
}

export interface GroupBridgeRegistration {
  registration_id: string;
  registration_fingerprint?: string;
  group_id: string;
  url: string;
  transport: string;
  remote_group_id: string;
  remote_peer_id: string;
  multiaddrs?: string[];
  status: string;
  created_at?: string;
  updated_at?: string;
  last_sync_at?: string;
}

export interface GroupBridgeTrust {
  trust_id: string;
  request_id?: string;
  registration_id?: string;
  group_id: string;
  remote_group_id: string;
  remote_group_title?: string;
  remote_endpoint: string;
  remote_peer_id: string;
  remote_multiaddrs?: string[];
  transport: string;
  access_level: GroupBridgeAccessLevel;
  status: string;
  approved_by?: string;
  access_updated_by?: string;
  revoked_by?: string;
  revision: number;
  created_at?: string;
  updated_at?: string;
}

export interface GroupBridgePairingRequest {
  request_id: string;
  invite_id?: string;
  group_id: string;
  remote_group_id: string;
  remote_group_title?: string;
  remote_endpoint: string;
  remote_peer_id: string;
  transport: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  expires_at?: string;
  approved_by?: string;
  rejected_by?: string;
  rejection_reason?: string;
  registration_id?: string;
  trust_id?: string;
}

export interface GroupBridgePairingInvite {
  invite_id: string;
  group_id: string;
  expected_remote_group_id: string;
  expected_remote_peer_id: string;
  local_multiaddrs?: string[];
  transport: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  expires_at?: string;
  request_id?: string;
  pairing_code?: string;
}

export type GroupBridgeRemoteSendStatus = "queued" | "sending" | "retrying" | "sent" | "failed";

export interface GroupBridgeSessionMessage {
  text: string;
  format?: "plain" | "markdown";
  priority?: "normal" | "attention";
  reply_required?: boolean;
}

export interface GroupBridgeRemoteReceipt {
  status: GroupBridgeRemoteSendStatus;
  remote_event_id?: string | null;
  idempotency_key?: string;
  registration_id?: string;
  attempt?: number;
  error?: { code?: string; message?: string } | null;
  [key: string]: unknown;
}

export interface GroupBridgeRemoteTarget {
  registration_id: string;
  group_id: string;
  remote_group_id: string;
  remote_peer_id: string;
  remote_group_title?: string;
}

export function createGroupBridgeIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.getRandomValues) {
    throw new Error("Web Crypto is required for Group Bridge remote sends.");
  }
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  return `gbs_${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

export function buildActiveGroupBridgeRemoteTargets(
  registrations: GroupBridgeRegistration[],
  trusts: GroupBridgeTrust[],
  groupId: string,
): GroupBridgeRemoteTarget[] {
  const localGroupId = String(groupId || "").trim();
  if (!localGroupId) return [];

  const targets: GroupBridgeRemoteTarget[] = [];
  for (const registration of registrations) {
    const registrationId = String(registration.registration_id || "").trim();
    const remoteGroupId = String(registration.remote_group_id || "").trim();
    const remotePeerId = String(registration.remote_peer_id || "").trim();
    if (
      !registrationId ||
      registration.status !== "active" ||
      registration.transport !== "group_bridge_session" ||
      String(registration.group_id || "").trim() !== localGroupId ||
      !remoteGroupId ||
      !remotePeerId
    ) continue;

    const trust = trusts.find((candidate) => (
      candidate.status === "active" &&
      candidate.transport === "group_bridge_session" &&
      String(candidate.registration_id || "").trim() === registrationId &&
      String(candidate.group_id || "").trim() === localGroupId &&
      String(candidate.remote_group_id || "").trim() === remoteGroupId &&
      String(candidate.remote_peer_id || "").trim() === remotePeerId
    ));
    if (!trust) continue;

    targets.push({
      registration_id: registrationId,
      group_id: localGroupId,
      remote_group_id: remoteGroupId,
      remote_peer_id: remotePeerId,
      remote_group_title: String(trust.remote_group_title || "").trim() || undefined,
    });
  }
  return targets;
}

const groupBridgePath = (suffix: string, groupId: string) =>
  `/api/group-bridge/${suffix}?group_id=${encodeURIComponent(groupId)}`;

export const groupBridgeApi = {
  identity: (groupId: string) => apiJson<{ identity: GroupBridgeIdentity }>(groupBridgePath("identity", groupId)),
  registrations: (groupId: string) => apiJson<{ registrations: GroupBridgeRegistration[] }>(groupBridgePath("registrations", groupId)),
  trusts: (groupId: string) => apiJson<{ trusts: GroupBridgeTrust[] }>(groupBridgePath("trusts", groupId)),
  pairingRequests: (groupId: string) => apiJson<{ requests: GroupBridgePairingRequest[] }>(groupBridgePath("pairing/requests", groupId)),
  invite: (args: {
    groupId: string;
    expectedRemoteGroupId: string;
    expectedRemotePeerId: string;
    multiaddrs: string[];
    ttlSeconds: number;
  }) => apiJson<{ invite: GroupBridgePairingInvite }>("/api/group-bridge/pairing/invites", {
    method: "POST",
    body: JSON.stringify({
      group_id: args.groupId,
      expected_remote_group_id: args.expectedRemoteGroupId,
      expected_remote_peer_id: args.expectedRemotePeerId,
      multiaddrs: args.multiaddrs,
      ttl_seconds: args.ttlSeconds,
    }),
  }),
  approve: (groupId: string, requestId: string) => apiJson<{ request: GroupBridgePairingRequest; trust: GroupBridgeTrust }>(
    `/api/group-bridge/pairing/requests/${encodeURIComponent(requestId)}/approve`,
    { method: "POST", body: JSON.stringify({ group_id: groupId }) },
  ),
  reject: (groupId: string, requestId: string, reason = "") => apiJson<{ request: GroupBridgePairingRequest }>(
    `/api/group-bridge/pairing/requests/${encodeURIComponent(requestId)}/reject`,
    { method: "POST", body: JSON.stringify({ group_id: groupId, reason }) },
  ),
  setAccess: (groupId: string, trustId: string, accessLevel: GroupBridgeAccessLevel, expectedRevision: number) => apiJson<{ trust: GroupBridgeTrust }>(
    `/api/group-bridge/trusts/${encodeURIComponent(trustId)}/access`,
    { method: "POST", body: JSON.stringify({ group_id: groupId, access_level: accessLevel, expected_revision: expectedRevision }) },
  ),
  revoke: (groupId: string, trustId: string, expectedRevision: number) => apiJson<{ trust: GroupBridgeTrust }>(
    `/api/group-bridge/trusts/${encodeURIComponent(trustId)}/revoke`,
    { method: "POST", body: JSON.stringify({ group_id: groupId, expected_revision: expectedRevision }) },
  ),
  remoteSend: (
    groupId: string,
    registrationId: string,
    idempotencyKey: string,
    payload: GroupBridgeSessionMessage,
  ) => apiJson<{ queued: boolean; replayed: boolean; receipt: GroupBridgeRemoteReceipt }>(
    "/api/group-bridge/remote/send",
    {
      method: "POST",
      body: JSON.stringify({
        group_id: groupId,
        registration_id: registrationId,
        idempotency_key: idempotencyKey,
        payload,
      }),
    },
  ),
  remoteStatus: (
    groupId: string,
    registrationId: string,
    idempotencyKey: string,
    options?: { signal?: AbortSignal },
  ) => apiJson<{ receipt: GroupBridgeRemoteReceipt | null }>(
    "/api/group-bridge/remote/status",
    {
      method: "POST",
      signal: options?.signal,
      body: JSON.stringify({ group_id: groupId, registration_id: registrationId, idempotency_key: idempotencyKey }),
    },
  ),
};
