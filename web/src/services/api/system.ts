import type { DirItem, DirSuggestion, OpenClawModel, RuntimeInfo } from "../../types";
import {
  apiJson,
  pingRequestKey,
  RECENT_BOOTSTRAP_READ_TTL_MS,
  reuseRecentReadRequest,
} from "./base";

export async function fetchPing(options?: { includeHome?: boolean }) {
  const includeHome = Boolean(options?.includeHome);
  const suffix = includeHome ? "?include_home=1" : "";
  return reuseRecentReadRequest(
    pingRequestKey(includeHome),
    RECENT_BOOTSTRAP_READ_TTL_MS,
    () =>
      apiJson<{ home?: string; daemon: unknown; version: string; web?: { mode?: string; read_only?: boolean } }>(
        `/api/v1/ping${suffix}`,
      ),
  );
}

export async function fetchRuntimes() {
  return apiJson<{ runtimes: RuntimeInfo[]; available: string[] }>("/api/v1/runtimes");
}

export async function fetchOpenClawModels() {
  return apiJson<{ models: OpenClawModel[] }>("/api/v1/runtimes/openclaw/models");
}

export async function fetchDirSuggestions() {
  return apiJson<{ suggestions: DirSuggestion[] }>("/api/v1/fs/recent");
}

export async function fetchDirContents(path: string) {
  return apiJson<{ path: string; parent: string | null; items: DirItem[] }>(
    `/api/v1/fs/list?path=${encodeURIComponent(path)}`,
  );
}

export async function pickDirectory(initialPath: string, title: string) {
  return apiJson<{ path: string; cancelled: boolean }>("/api/v1/fs/pick-directory", {
    method: "POST",
    body: JSON.stringify({
      initial_path: initialPath,
      title,
    }),
  });
}

export async function resolveScopeRoot(path: string) {
  return apiJson<{ path: string; scope_root: string; scope_key: string; git_remote: string }>(
    `/api/v1/fs/scope_root?path=${encodeURIComponent(path)}`,
  );
}
