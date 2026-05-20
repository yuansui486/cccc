import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import {
  CapabilitiesTab,
  capabilityStoreInvalidLabel,
  capabilityStoreInvalidReason,
  shouldAutoRefreshOneColleagueStore,
} from "../../src/components/modals/settings/CapabilitiesTab";
import enSettings from "../../src/i18n/locales/en/settings.json";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

describe("CapabilitiesTab rendering", () => {
  it("keeps the global capabilities tab focused on the OneColleague skill store and links browsing to Capability Center", () => {
    const html = renderToStaticMarkup(
      <CapabilitiesTab isDark={false} isActive={false} groupId="g-demo" />,
    );

    expect(html).toContain("capabilities.openCenter");
    expect(html).toContain("capabilities.store.title");
    expect(html).toContain("capabilities.libraryTitle");
    expect(html).not.toContain("capabilities.selfProposedTitle");
    expect(html).not.toContain("capabilities.safetyModeTitle");
    expect(enSettings.capabilities.subtitle).toContain("OneColleague skill store");
    expect(enSettings.capabilities.pageGuide).toContain("OneColleague skill store");
    expect(enSettings.capabilities.pageGuide).not.toContain("Configure external policy");
  });

  it("keeps the self-evolving surface available for self-proposed skills", () => {
    const html = renderToStaticMarkup(
      <CapabilitiesTab isDark={false} isActive={false} groupId="g-demo" surface="selfEvolving" />,
    );

    expect(html).toContain("capabilities.selfProposedTitle");
    expect(html).toContain("capabilities.selfEvolvingGroupNoCandidates");
    expect(html).not.toContain("capabilities.store.title");
    expect(html).not.toContain("capabilities.libraryTitle");
  });

  it("uses lightweight capability overview requests for item-only surfaces", () => {
    const pickerSource = readFileSync(resolve(process.cwd(), "src/components/CapabilityPicker.tsx"), "utf8");
    const tabSource = readFileSync(resolve(process.cwd(), "src/components/modals/settings/CapabilitiesTab.tsx"), "utf8");

    expect(pickerSource).toContain("includeSourceInstances: false");
    expect(tabSource).toContain("includeSourceInstances: false");
  });

  it("auto-refreshes the OneColleague store only for an enabled empty local catalog", () => {
    expect(shouldAutoRefreshOneColleagueStore({
      isActive: true,
      selfEvolvingSurface: false,
      sourceEnabled: true,
      importedCount: 0,
      activePendingCount: 0,
      busyKey: "",
      attempted: false,
    })).toBe(true);
    expect(shouldAutoRefreshOneColleagueStore({
      isActive: true,
      selfEvolvingSurface: false,
      sourceEnabled: true,
      importedCount: 1,
      activePendingCount: 0,
      busyKey: "",
      attempted: false,
    })).toBe(false);
    expect(shouldAutoRefreshOneColleagueStore({
      isActive: true,
      selfEvolvingSurface: false,
      sourceEnabled: true,
      importedCount: 0,
      activePendingCount: 0,
      busyKey: "store:refresh",
      attempted: false,
    })).toBe(false);
    expect(shouldAutoRefreshOneColleagueStore({
      isActive: true,
      selfEvolvingSurface: false,
      sourceEnabled: true,
      importedCount: 0,
      activePendingCount: 0,
      busyKey: "",
      attempted: true,
    })).toBe(false);
  });

  it("extracts invalid OneColleague store labels and nested failure reasons", () => {
    const row = {
      record: {
        capability_id: "skill:onecolleague_skill_library:creat-ppt",
      },
      import_result: {
        error: {
          message: "skill package has too many files",
        },
      },
    };

    expect(capabilityStoreInvalidLabel(row)).toBe("skill:onecolleague_skill_library:creat-ppt");
    expect(capabilityStoreInvalidReason(row)).toBe("skill package has too many files");
  });
});
