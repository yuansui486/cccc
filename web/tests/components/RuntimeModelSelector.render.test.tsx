import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RuntimeModelSelector } from "../../src/components/RuntimeModelSelector";

const labels = {
  runtime: "Runtime",
  model: "Model",
  runtimeSearch: "Search runtimes",
  modelSearch: "Search models",
  noResults: "No matching options",
  defaultModel: "Use runtime default",
  customModel: "Enter another model",
  customPlaceholder: "Model ID",
  apply: "Apply",
  notInstalled: "(not installed)",
  modelRequired: "Model required",
  modelInvalid: "Model invalid",
};

const runtimes = [
  {
    name: "openclaw",
    display_name: "OpenClaw",
    available: true,
    recommended_command: "openclaw",
  },
];

describe("RuntimeModelSelector fixed model catalog", () => {
  it("keeps the model chooser enabled without a remote catalog", () => {
    const html = renderToStaticMarkup(
      <RuntimeModelSelector
        runtime="openclaw"
        model=""
        runtimes={runtimes}
        onRuntimeChange={() => undefined}
        onModelChange={() => undefined}
        labels={labels}
      />,
    );

    expect(html).toMatch(/aria-label="Model"/);
    expect(html).not.toMatch(/aria-label="Model"[^>]*\sdisabled=""/);
  });

  it("shows a persisted model outside the fixed catalog as a custom model", () => {
    const html = renderToStaticMarkup(
      <RuntimeModelSelector
        runtime="openclaw"
        model="provider/legacy-model"
        runtimes={runtimes}
        onRuntimeChange={() => undefined}
        onModelChange={() => undefined}
        labels={labels}
      />,
    );

    expect(html).toContain('value="provider/legacy-model"');
    expect(html).toContain('placeholder="Model ID"');
  });
});
