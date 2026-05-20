export function normalizeCapabilityIdList(raw: unknown): string[] {
  const items = Array.isArray(raw) ? raw : [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    const value = String(item || "").trim();
    if (!value) continue;
    if (seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

export function parseCapabilityIdInput(text: string): string[] {
  const chunks = String(text || "")
    .split(/[\n,;]/g)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
  return normalizeCapabilityIdList(chunks);
}

export function formatCapabilityIdInput(raw: unknown): string {
  return normalizeCapabilityIdList(raw).join("\n");
}

export function mergeInheritedCapabilityAutoload(raw: {
  own?: unknown;
  actorStateAutoload?: unknown;
  effectiveAutoload?: unknown;
  groupAutoload?: unknown;
  profileAutoload?: unknown;
  enabled?: unknown;
}): string[] {
  const ownSet = new Set(normalizeCapabilityIdList(raw.own));
  const actorStateSet = new Set(normalizeCapabilityIdList(raw.actorStateAutoload));
  const inheritedAutoload = normalizeCapabilityIdList(
    raw.effectiveAutoload ?? [
      ...normalizeCapabilityIdList(raw.groupAutoload),
      ...normalizeCapabilityIdList(raw.profileAutoload),
      ...normalizeCapabilityIdList(raw.actorStateAutoload),
    ]
  ).filter((capId) => !actorStateSet.has(capId));
  const groupAutoload = normalizeCapabilityIdList(raw.groupAutoload).filter(
    (capId) => !actorStateSet.has(capId)
  );
  const profileAutoload = normalizeCapabilityIdList(raw.profileAutoload).filter(
    (capId) => !actorStateSet.has(capId)
  );
  const groupEnabled = Array.isArray(raw.enabled)
    ? raw.enabled
        .filter((row) => typeof row === "object" && row !== null && String((row as { scope?: unknown }).scope || "").trim().toLowerCase() === "group")
        .map((row) => (row as { capability_id?: unknown }).capability_id)
    : [];
  return normalizeCapabilityIdList([...groupEnabled, ...groupAutoload, ...profileAutoload, ...inheritedAutoload]).filter((capId) => !ownSet.has(capId));
}
