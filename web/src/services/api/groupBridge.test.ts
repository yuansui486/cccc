import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { GroupBridgeRegistration, GroupBridgeTrust } from "./groupBridge";

describe("groupBridgeApi", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("window", { location: { search: "" } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads management projections from the group-scoped endpoints", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { identity: { peer_id: "peer-1", public_key: "key", node_id: "node-1" } } }),
    });

    const { groupBridgeApi } = await import("./groupBridge");
    const response = await groupBridgeApi.identity("group-1");

    expect(response.ok).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/group-bridge/identity?group_id=group-1",
      expect.objectContaining({ headers: expect.objectContaining({ "content-type": "application/json" }) }),
    );
  });

  it("sends access changes with the server revision", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { trust: { trust_id: "trust-1", revision: 4 } } }),
    });

    const { groupBridgeApi } = await import("./groupBridge");
    await groupBridgeApi.setAccess("group-1", "trust-1", "read", 3);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/group-bridge/trusts/trust-1/access",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ group_id: "group-1", access_level: "read", expected_revision: 3 }),
      }),
    );
  });

  it("preserves the one-time pairing code from invite creation", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { invite: { invite_id: "invite-1", pairing_code: "ABCD-EFGH" } } }),
    });

    const { groupBridgeApi } = await import("./groupBridge");
    const response = await groupBridgeApi.invite({
      groupId: "group-1",
      expectedRemoteGroupId: "remote-group",
      expectedRemotePeerId: "remote-peer",
      multiaddrs: ["https://remote.example"],
      ttlSeconds: 600,
    });

    expect(response.ok).toBe(true);
    if (response.ok) expect(response.result.invite.pairing_code).toBe("ABCD-EFGH");
  });

  it("keeps Group Bridge reachable from the normal Settings navigation", () => {
    const settingsSource = readFileSync(resolve(process.cwd(), "src/components/SettingsModal.tsx"), "utf8");
    expect(settingsSource).toContain('id: "groupBridge"');
    expect(settingsSource).toContain('tab === "groupBridge" && groupId');
    expect(settingsSource).toContain('setScope("group")');
  });

  it("builds remote targets only from exact active registration and trust pairs", async () => {
    const { buildActiveGroupBridgeRemoteTargets } = await import("./groupBridge");
    const registrations: GroupBridgeRegistration[] = [
      { registration_id: "reg-1", group_id: "local", remote_group_id: "remote", remote_peer_id: "peer", transport: "group_bridge_session", status: "active" },
      { registration_id: "reg-2", group_id: "local", remote_group_id: "other", remote_peer_id: "peer", transport: "group_bridge_session", status: "revoked" },
    ];
    const trusts: GroupBridgeTrust[] = [
      { trust_id: "trust-1", registration_id: "reg-1", group_id: "local", remote_group_id: "remote", remote_peer_id: "peer", remote_endpoint: "https://remote", transport: "group_bridge_session", access_level: "full", status: "active", revision: 1 },
      { trust_id: "trust-2", registration_id: "reg-1", group_id: "local", remote_group_id: "other", remote_peer_id: "peer", remote_endpoint: "https://other", transport: "group_bridge_session", access_level: "messages", status: "active", revision: 1 },
    ];

    expect(buildActiveGroupBridgeRemoteTargets(registrations, trusts, "local")).toEqual([
      {
        registration_id: "reg-1",
        group_id: "local",
        remote_group_id: "remote",
        remote_peer_id: "peer",
      },
    ]);
  });

  it("sends and polls remote delivery with one registration-scoped idempotency key", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { queued: true, replayed: false, receipt: { status: "queued" } } }),
    });

    const { groupBridgeApi } = await import("./groupBridge");
    await groupBridgeApi.remoteSend("group-1", "registration-1", "gbs_0123456789abcdef0123456789abcdef", {
      text: "hello",
      format: "plain",
      priority: "normal",
      reply_required: false,
    });
    await groupBridgeApi.remoteStatus("group-1", "registration-1", "gbs_0123456789abcdef0123456789abcdef");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/group-bridge/remote/send",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          group_id: "group-1",
          registration_id: "registration-1",
          idempotency_key: "gbs_0123456789abcdef0123456789abcdef",
          payload: { text: "hello", format: "plain", priority: "normal", reply_required: false },
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/group-bridge/remote/status",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("forwards optional cancellation signals across remote orchestration requests", async () => {
    fetchMock.mockResolvedValue({
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ ok: true, result: { registrations: [], trusts: [], receipt: null } }),
    });
    const controller = new AbortController();
    const options = { signal: controller.signal };
    const { groupBridgeApi } = await import("./groupBridge");

    await groupBridgeApi.registrations("group-1", options);
    await groupBridgeApi.trusts("group-1", options);
    await groupBridgeApi.remoteSend(
      "group-1",
      "registration-1",
      "gbs_0123456789abcdef0123456789abcdef",
      { text: "hello" },
      options,
    );
    await groupBridgeApi.remoteStatus(
      "group-1",
      "registration-1",
      "gbs_0123456789abcdef0123456789abcdef",
      options,
    );

    for (const call of fetchMock.mock.calls) {
      expect(call[1]).toEqual(expect.objectContaining({ signal: controller.signal }));
    }
  });

  it("generates strict Web Crypto idempotency keys", async () => {
    vi.stubGlobal("crypto", { getRandomValues: (bytes: Uint8Array) => {
      bytes.fill(0xab);
      return bytes;
    } });
    const { createGroupBridgeIdempotencyKey } = await import("./groupBridge");
    const key = createGroupBridgeIdempotencyKey();
    expect(key).toMatch(/^gbs_[0-9a-f]{32}$/);
  });
});
