export function getMessageInsight(data: unknown): string {
  if (!data || typeof data !== "object") return "";
  const value = (data as { insight?: unknown }).insight;
  return typeof value === "string" ? value.trim() : "";
}
