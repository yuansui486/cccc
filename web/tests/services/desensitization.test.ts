import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const storage = new Map<string, string>();
const sessionStorageMock = {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
  removeItem: (key: string) => storage.delete(key),
  clear: () => storage.clear(),
};

describe("desensitization API", () => {
  beforeEach(() => {
    vi.resetModules();
    storage.clear();
    vi.stubGlobal("sessionStorage", sessionStorageMock);
    vi.stubGlobal("window", { location: { search: "" }, setTimeout });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("preserves folder relative paths in the multipart manifest", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true, result: { id: "batch-1", status: "queued", mode: "review", total: 2, completed: 0, failed: 0 },
    }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const first = new File(["a"], "a.txt", { type: "text/plain" });
    const second = new File(["b"], "b.txt", { type: "text/plain" });
    Object.defineProperty(first, "webkitRelativePath", { value: "客户资料/华东/a.txt" });
    Object.defineProperty(second, "webkitRelativePath", { value: "客户资料/华南/b.txt" });

    const { desensitizationApi } = await import("../../src/services/api/desensitization");
    await desensitizationApi.createBatch("group one", [first, second], "review", { reversible: false });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/groups/group%20one/desensitization/batches");
    expect(init.body).toBeInstanceOf(FormData);
    expect(new Headers(init.headers).has("content-type")).toBe(false);
    const manifest = JSON.parse(String((init.body as FormData).get("manifest")));
    expect(manifest.root_name).toBe("客户资料");
    expect(manifest.mode).toBe("review");
    expect(manifest.entries.map((item: { relative_path: string }) => item.relative_path)).toEqual([
      "客户资料/华东/a.txt", "客户资料/华南/b.txt",
    ]);
  });

  it("uses the authorization header for blob downloads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("masked", {
      status: 200,
      headers: { "content-disposition": "attachment; filename*=UTF-8''result.txt" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const base = await import("../../src/services/api/base");
    base.setAuthToken("test-token");
    const { desensitizationApi } = await import("../../src/services/api/desensitization");
    const result = await desensitizationApi.blob("/api/v1/groups/g/tasks/t/artifact");

    expect(result.filename).toBe("result.txt");
    expect(await result.blob.text()).toBe("masked");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer test-token");
  });
});
