import type { ApiResponse } from "../../services/api";

export async function reloadContextAfterWrite<T>(
  response: ApiResponse<T>,
  reloadContext: () => Promise<void>,
): Promise<ApiResponse<T>> {
  if (!response.ok) {
    return response;
  }
  await reloadContext();
  return response;
}
