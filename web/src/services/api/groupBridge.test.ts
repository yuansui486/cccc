import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

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
});
