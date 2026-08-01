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
};
