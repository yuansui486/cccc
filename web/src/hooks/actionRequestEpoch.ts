export interface ActionRequestEpochRef {
  current: Map<string, number>;
}

export function beginActionRequestEpoch(ref: ActionRequestEpochRef, key: string): number {
  const normalizedKey = String(key || "").trim();
  if (!normalizedKey) return 0;
  const next = Number(ref.current.get(normalizedKey) || 0) + 1;
  ref.current.set(normalizedKey, next);
  return next;
}

export function isLatestActionRequestEpoch(
  ref: ActionRequestEpochRef,
  key: string,
  epoch: number,
): boolean {
  const normalizedKey = String(key || "").trim();
  if (!normalizedKey || epoch <= 0) return false;
  return Number(ref.current.get(normalizedKey) || 0) === epoch;
}
