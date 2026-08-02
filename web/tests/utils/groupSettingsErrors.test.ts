import { describe, expect, it } from "vitest";

import { formatGroupSettingsUpdateError } from "../../src/utils/groupSettingsErrors";

const translations: Record<string, string> = {
  "modals:context.failedToUpdateSettingsWithCause": "Failed to update group settings: {{cause}}",
  "modals:context.settingsPermissionDenied": "You do not have permission to update these settings.",
};

function t(key: string, fallbackOrOptions?: unknown, maybeOptions?: unknown): string {
  let template = translations[key] || key;
  let options: Record<string, unknown> = {};

  if (typeof fallbackOrOptions === "string") {
    template = translations[key] || fallbackOrOptions;
    if (maybeOptions && typeof maybeOptions === "object") options = maybeOptions as Record<string, unknown>;
  } else if (fallbackOrOptions && typeof fallbackOrOptions === "object") {
    options = fallbackOrOptions as Record<string, unknown>;
    template = translations[key] || String(options.defaultValue || key);
  }

  return template.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options[name] ?? ""));
}

describe("formatGroupSettingsUpdateError", () => {
  it("uses the structured cause for a failed settings update", () => {
    const result = formatGroupSettingsUpdateError(t as never, {
      code: "group_settings_update_failed",
      message: "settings validation failed",
      details: { cause: "invalid feature flag" },
    });

    expect(result).toBe("Failed to update group settings: invalid feature flag");
  });

  it("uses the raw message when structured cause details are missing", () => {
    const result = formatGroupSettingsUpdateError(t as never, {
      code: "group_settings_update_failed",
      message: "settings validation failed",
    });

    expect(result).toBe("Failed to update group settings: settings validation failed");
  });

  it("uses localized code-level fallbacks for common settings errors", () => {
    const result = formatGroupSettingsUpdateError(t as never, {
      code: "permission_denied",
      message: "permission denied",
    });

    expect(result).toBe("You do not have permission to update these settings.");
  });
});
