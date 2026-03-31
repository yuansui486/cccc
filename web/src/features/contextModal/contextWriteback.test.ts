import { describe, expect, it, vi } from "vitest";
import type { ApiResponse } from "../../services/api";
import { reloadContextAfterWrite } from "./contextWriteback";

describe("contextWriteback", () => {
  it("reloads context after successful writes", async () => {
    const reloadContext = vi.fn().mockResolvedValue(undefined);
    const response: ApiResponse<{ updated: true }> = {
      ok: true,
      result: { updated: true },
    };

    const result = await reloadContextAfterWrite(response, reloadContext);

    expect(result).toBe(response);
    expect(reloadContext).toHaveBeenCalledTimes(1);
  });

  it("does not reload context after failed writes", async () => {
    const reloadContext = vi.fn().mockResolvedValue(undefined);
    const response: ApiResponse<{ updated: true }> = {
      ok: false,
      error: { code: "write_failed", message: "write failed" },
    };

    const result = await reloadContextAfterWrite(response, reloadContext);

    expect(result).toBe(response);
    expect(reloadContext).not.toHaveBeenCalled();
  });
});
