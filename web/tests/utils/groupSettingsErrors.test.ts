import { describe, expect, it } from "vitest";

import { formatGroupSettingsUpdateError } from "../../src/utils/groupSettingsErrors";

const translations: Record<string, string> = {
  "modals:context.desktopPetRequiresForeman": "Enable a foreman actor before turning on Web Pet.",
  "modals:context.desktopPetStartFailedWithCause": "Failed to start the Web Pet runtime: {{cause}}",
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
  it("maps the desktop pet foreman requirement to a localized message", () => {
    const result = formatGroupSettingsUpdateError(t as never, {
      code: "group_settings_update_failed",
      message: "desktop pet requires an enabled foreman actor",
      details: { reason: "desktop_pet_requires_enabled_foreman" },
    });

    expect(result).toBe("Enable a foreman actor before turning on Web Pet.");
  });

  it("derives legacy pet start failures from the raw message when details are missing", () => {
    const result = formatGroupSettingsUpdateError(t as never, {
      code: "group_settings_update_failed",
      message: "failed to start pet actor: playwright missing",
    });

    expect(result).toBe("Failed to start the Web Pet runtime: playwright missing");
  });

  it("uses localized code-level fallbacks for common settings errors", () => {
    const result = formatGroupSettingsUpdateError(t as never, {
      code: "permission_denied",
      message: "permission denied",
    });

    expect(result).toBe("You do not have permission to update these settings.");
  });
});
