import { describe, expect, it } from "vitest";

import {
  GROUP_CONTROL_STATE_SCENARIOS,
  evaluateGroupControlStateScenario,
  runGroupControlStateScenarios,
} from "../../src/utils/groupControlStateScenarios";

function codes(result: ReturnType<typeof evaluateGroupControlStateScenario>): string[] {
  return result.issues.map((issue) => issue.code);
}

describe("groupControlStateScenarios", () => {
  it("keeps every synthetic scenario aligned with its expected status and diagnostics", () => {
    expect(GROUP_CONTROL_STATE_SCENARIOS.length).toBeGreaterThanOrEqual(5);

    for (const scenario of GROUP_CONTROL_STATE_SCENARIOS) {
      const result = evaluateGroupControlStateScenario(scenario);
      expect(result.statusKey, scenario.id).toBe(scenario.expectedStatusKey);
      expect(codes(result), scenario.id).toEqual(expect.arrayContaining(scenario.expectedIssueCodes));
    }
  });

  it("exposes all scenarios as a runnable report", () => {
    const report = runGroupControlStateScenarios();
    expect(report.map((item) => item.id)).toEqual(GROUP_CONTROL_STATE_SCENARIOS.map((scenario) => scenario.id));
  });
});
