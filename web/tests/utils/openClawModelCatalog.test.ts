import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import actorsEn from "../../src/i18n/locales/en/actors.json";
import actorsJa from "../../src/i18n/locales/ja/actors.json";
import actorsZh from "../../src/i18n/locales/zh/actors.json";
import settingsEn from "../../src/i18n/locales/en/settings.json";
import settingsJa from "../../src/i18n/locales/ja/settings.json";
import settingsZh from "../../src/i18n/locales/zh/settings.json";
import { buildRuntimeSelectorOptions } from "../../src/utils/runtimeChoiceGroups";

describe("fixed peer model catalog wiring", () => {
  it("does not fetch or pass a remote model catalog in any actor editor", () => {
    const sources = [
      "../../src/components/modals/AddActorModal.tsx",
      "../../src/components/modals/EditActorModal.tsx",
      "../../src/components/modals/settings/ActorProfilesTab.tsx",
    ].map((path) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8"));

    for (const source of sources) {
      expect(source).not.toContain("fetchDoneHubModels");
      expect(source).not.toContain("modelCatalog=");
      expect(source).not.toContain("opencodeModels");
    }
  });

  it("uses a localized OpenClaw runtime description", () => {
    const options = buildRuntimeSelectorOptions(
      [{ name: "openclaw", display_name: "OpenClaw", available: true }],
      ["openclaw"],
      { openclaw: "Localized Gateway description" },
    );

    expect(options[0]?.description).toBe("Localized Gateway description");
  });

  it("provides the OpenClaw runtime description in every locale", () => {
    const actorLocales = [actorsEn, actorsZh, actorsJa];
    const settingsLocales = [settingsEn, settingsZh, settingsJa];
    for (const locale of actorLocales) {
      expect(locale.openClawRuntimeDescription.trim()).not.toBe("");
    }
    for (const locale of settingsLocales) {
      expect(locale.actorProfiles.openClawRuntimeDescription.trim()).not.toBe("");
    }
  });
});
