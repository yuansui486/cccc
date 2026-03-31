const BACKGROUND_REFRESH_MS = 30_000;
const BACKGROUND_REFRESH_MAX_MS = 5 * 60 * 1000;

export function getBackgroundRefreshDelayMs(failureCount: number): number {
  if (failureCount <= 0) return BACKGROUND_REFRESH_MS;
  return Math.min(BACKGROUND_REFRESH_MAX_MS, BACKGROUND_REFRESH_MS * (2 ** failureCount));
}
