import type { CapabilityOverviewItem } from "../../../types";

export const ONECOLLEAGUE_SKILL_SOURCE = "onecolleague_skill_library";
export const UNCATEGORIZED_KEY = "__uncategorized__";

function text(value: unknown): string {
  return String(value || "").trim();
}

function order(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function skillCategoryKey(row: CapabilityOverviewItem): string {
  return text(row.category_slug) || text(row.category_name) || UNCATEGORIZED_KEY;
}

export function marketplaceSkills(items: CapabilityOverviewItem[]): CapabilityOverviewItem[] {
  return items
    .filter((row) => text(row.kind).toLowerCase() === "skill")
    .filter((row) => text(row.source_id) === ONECOLLEAGUE_SKILL_SOURCE)
    .sort((a, b) =>
      order(a.category_order) - order(b.category_order)
      || order(a.display_order) - order(b.display_order)
      || (text(a.name) || text(a.capability_id)).localeCompare(text(b.name) || text(b.capability_id)),
    );
}

export function filterMarketplaceSkills(
  items: CapabilityOverviewItem[],
  query: string,
  categoryKey: string,
  addedOnly: boolean,
  enabled: Set<string>,
): CapabilityOverviewItem[] {
  const needle = query.trim().toLowerCase();
  return marketplaceSkills(items).filter((row) => {
    if (addedOnly && !enabled.has(text(row.capability_id))) return false;
    if (categoryKey && skillCategoryKey(row) !== categoryKey) return false;
    if (!needle) return true;
    return [row.name, row.description_short, row.category_name, row.capability_id, ...(row.tags || [])]
      .map((value) => text(value).toLowerCase())
      .join(" ")
      .includes(needle);
  });
}
