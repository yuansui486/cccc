import { afterEach, describe, expect, it, vi } from "vitest";

import { apiJson, formatApiErrorMessage } from "./api";

describe("formatApiErrorMessage", () => {
  it("keeps regular API errors unchanged", () => {
    expect(
      formatApiErrorMessage({
        code: "permission_denied",
        message: "permission denied",
      })
    ).toBe("permission denied");
  });

  it("summarizes daemon transport diagnostics into the message", () => {
    expect(
      formatApiErrorMessage({
        code: "daemon_unavailable",
        message: "ccccd unavailable",
        details: {
          transport: "tcp",
          endpoint: { host: "127.0.0.1", port: 9001 },
          phase: "connect",
          reason: "os_error",
        },
      })
    ).toBe("ccccd unavailable · tcp 127.0.0.1:9001 · connect os error");
  });
});

describe("apiJson", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("normalizes daemon_unavailable messages from response bodies", async () => {
    vi.stubGlobal("window", { location: { search: "" } });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ok: false,
            error: {
              code: "daemon_unavailable",
              message: "ccccd unavailable",
              details: {
                transport: "unix",
                endpoint: { path: "/tmp/ccccd.sock" },
                phase: "read",
                reason: "timeout",
              },
            },
          }),
          {
            status: 503,
            headers: { "content-type": "application/json" },
          }
        )
      )
    );

    const resp = await apiJson("/api/v1/ping");

    expect(resp.ok).toBe(false);
    if (resp.ok) {
      throw new Error("expected error response");
    }
    expect(resp.error.message).toBe("ccccd unavailable · unix /tmp/ccccd.sock · read timeout");
    expect(resp.error.details).toEqual({
      transport: "unix",
      endpoint: { path: "/tmp/ccccd.sock" },
      phase: "read",
      reason: "timeout",
    });
  });
});
