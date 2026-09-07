import { describe, expect, it } from "vitest";

import { entityNames, groupEntities, selectEntityGroup } from "../src/pages/desensitization/entityPresentation";

describe("desensitization entity presentation", () => {
  it("uses localized entity names with catalog fallback", () => {
    expect(entityNames({
      entity_catalog: { PHONE: "电话号码" },
      entities: [{ code: "CUSTOMER", name: "客户编号" }],
    })).toEqual({ PHONE: "电话号码", CUSTOMER: "客户编号" });
  });

  it("groups duplicate entities by type and text", () => {
    const groups = groupEntities([
      { id: "1", type: "PHONE", text: "13800138000", start: 0, end: 11, selected: true },
      { id: "2", type: "PHONE", text: "13800138000", start: 20, end: 31, selected: false },
      { id: "3", type: "PHONE", text: "13900139000", start: 40, end: 51, selected: true },
    ], { PHONE: "电话号码" });

    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({ type: "PHONE", name: "电话号码", text: "13800138000", selected: 1 });
    expect(groups[0].entities).toHaveLength(2);
    expect(selectEntityGroup(groups.flatMap((group) => group.entities), groups[0], false))
      .toMatchObject([{ selected: false }, { selected: false }, { selected: true }]);
  });
});
