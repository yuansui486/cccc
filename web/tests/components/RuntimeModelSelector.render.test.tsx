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
  loadingModels: "Loading model catalog",
  modelCatalogError: "Model catalog unavailable",
  retryModelCatalog: "Retry model catalog",
};

const runtimes = [
  {
    name: "openclaw",
    display_name: "OpenClaw",
    available: true,
    recommended_command: "openclaw",
  },
];

describe("RuntimeModelSelector model catalog state", () => {
  it("announces loading and disables the model chooser", () => {
    const html = renderToStaticMarkup(
      <RuntimeModelSelector
        runtime="openclaw"
        model=""
        runtimes={runtimes}
        onRuntimeChange={() => undefined}
        onModelChange={() => undefined}
        modelCatalogLoading
        labels={labels}
      />,
    );

    expect(html).toContain('role="status"');
    expect(html).toContain("Loading model catalog");
    expect(html).toMatch(/aria-label="Model"[^>]*disabled/);
  });

  it("shows a localized error, diagnostic detail, and retry command", () => {
    const html = renderToStaticMarkup(
      <RuntimeModelSelector
        runtime="openclaw"
        model=""
        runtimes={runtimes}
        onRuntimeChange={() => undefined}
        onModelChange={() => undefined}
        modelCatalogError="Gateway unavailable"
        onRetryModelCatalog={() => undefined}
        labels={labels}
      />,
    );

    expect(html).toContain('role="alert"');
    expect(html).toContain("Model catalog unavailable");
    expect(html).toContain("Gateway unavailable");
    expect(html).toContain('aria-label="Retry model catalog"');
  });
});
