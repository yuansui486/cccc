import { describe, expect, it } from "vitest";
import type { CapabilityOverviewItem } from "../../../src/types";
import {
  filterMarketplaceSkills,
  marketplaceSkills,
  skillCategoryKey,
  UNCATEGORIZED_KEY,
} from "../../../src/components/ContextModal/skills/skillsModel";

function row(overrides: Partial<CapabilityOverviewItem>): CapabilityOverviewItem {
  return {
    capability_id: "skill:onecolleague_skill_library:demo",
    kind: "skill",
    source_id: "onecolleague_skill_library",
    name: "Demo",
    ...overrides,
  };
}

describe("skill marketplace model", () => {
  it("keeps only OneColleague skills and sorts category then skill order", () => {
    const result = marketplaceSkills([
      row({ capability_id: "skill:onecolleague_skill_library:b", category_order: 2, display_order: 1 }),
      row({ capability_id: "skill:onecolleague_skill_library:c", category_order: 1, display_order: 2 }),
      row({ capability_id: "skill:onecolleague_skill_library:a", category_order: 1, display_order: 1 }),
      row({ capability_id: "skill:other:hidden", source_id: "other" }),
    ]);
    expect(result.map((item) => item.capability_id)).toEqual([
      "skill:onecolleague_skill_library:a",
      "skill:onecolleague_skill_library:c",
      "skill:onecolleague_skill_library:b",
    ]);
  });

  it("filters category, search text, and added state", () => {
    const enabled = new Set(["skill:onecolleague_skill_library:docs"]);
    const items = [
      row({ capability_id: "skill:onecolleague_skill_library:docs", name: "Writer", category_slug: "office", category_name: "Office", tags: ["documents"] }),
      row({ capability_id: "skill:onecolleague_skill_library:web", name: "Browser", category_slug: "web", category_name: "Web" }),
    ];
    expect(filterMarketplaceSkills(items, "documents", "office", true, enabled).map((item) => item.name)).toEqual(["Writer"]);
    expect(filterMarketplaceSkills(items, "browser", "office", false, enabled)).toEqual([]);
  });

  it("provides a stable uncategorized key", () => {
    expect(skillCategoryKey(row({ category_slug: "", category_name: "" }))).toBe(UNCATEGORIZED_KEY);
  });
});
