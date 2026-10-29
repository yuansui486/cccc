// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vite-plus/test";
import { SettingsModal } from "./SettingsModal";
import { useModalStore } from "../stores";
import {
  readSettingsLastLocation,
  writeSettingsLastLocation,
} from "./modals/settings/settingsLastLocation";
import type { GlobalTabId } from "./modals/settings/types";
import { GroupConnectionsPanel } from "../features/connect/GroupConnectionsControl";

const fixture = vi.hoisted(() => ({ admin: true, signedIn: false, hiddenRender: vi.fn() }));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t, i18n: { language: "en" } }) };
});
vi.mock("../services/api/base", () => ({
  apiJson: async () => ({ ok: true, result: { status: "not_linked", links: [] } }),
}));
vi.mock("../services/api", async (original) => ({
  ...(await original<typeof import("../services/api")>()),
  fetchWebAccessSession: async () => ({
    ok: true,
    result: {
      web_access_session: {
        can_access_global_settings: fixture.admin,
        current_browser_signed_in: fixture.signedIn,
      },
    },
  }),
  fetchActors: async () => ({ ok: true, result: { actors: [] } }),
}));
vi.mock("./modals/settings/AutomationTab", () => ({
  AutomationTab: () => <div>Automation page</div>,
}));
vi.mock("./modals/settings/CapabilitiesTab", () => ({
  CapabilitiesTab: () => <div>Capabilities page</div>,
}));
vi.mock("./modals/settings/ActorProfilesTab", () => ({
  ActorProfilesTab: () => <div>Profiles page</div>,
}));
vi.mock("./modals/settings/AccountTab", () => ({
  AccountTab: () => {
    fixture.hiddenRender();
    return <div>Hidden account</div>;
  },
}));
vi.mock("./modals/settings/BrandingTab", () => ({
  BrandingTab: () => {
    fixture.hiddenRender();
    return <div>Hidden branding</div>;
  },
}));
vi.mock("./modals/settings/DeveloperTab", () => ({
  DeveloperTab: () => {
    fixture.hiddenRender();
    return <div>Hidden developer</div>;
  },
}));
vi.mock("./modals/settings/GuidanceTab", () => ({
  GuidanceTab: () => {
    fixture.hiddenRender();
    return <div>Hidden guidance</div>;
  },
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  HTMLElement.prototype.scrollIntoView = vi.fn();
  fixture.admin = true;
  fixture.signedIn = false;
  fixture.hiddenRender.mockClear();
  localStorage.clear();
  useModalStore.setState({ settingsTarget: null });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  localStorage.clear();
  useModalStore.setState({ settingsTarget: null });
  vi.unstubAllGlobals();
});
async function render(groupId: string | undefined = "g") {
  await act(async () =>
    root.render(
      <SettingsModal
        codexVoice={{} as never}
        isOpen
        onClose={() => {}}
        settings={null}
        onUpdateSettings={async () => {}}
        busy={false}
        isDark={false}
        groupId={groupId}
      />,
    ),
  );
}
async function expectPage(text: string) {
  const deadline = Date.now() + 3000;
  while (!host.textContent?.includes(text) && Date.now() < deadline) {
    await act(async () => new Promise((resolve) => setTimeout(resolve, 10)));
  }
  expect(host.textContent).toContain(text);
  expect(fixture.hiddenRender).not.toHaveBeenCalled();
  for (const tab of ["account", "branding", "developer", "guidance"]) {
    expect(host.textContent).not.toContain(`tabs.${tab}`);
  }
}

it("opens the first visible group page by default and retains group scope help", async () => {
  await render();
  await expectPage("Automation page");
  expect(host.textContent).toContain("navigation.appliesToGroup");
  expect(readSettingsLastLocation(true).groupTab).toBe("automation");
});

it.each(["account", "branding", "developer"] as GlobalTabId[])(
  "normalizes persisted hidden %s before mounting a page",
  async (globalTab) => {
    writeSettingsLastLocation({ scope: "global", globalTab, groupTab: "guidance" });
    await render();
    await expectPage("Capabilities page");
    expect(readSettingsLastLocation(true)).toEqual({
      scope: "global",
      globalTab: "capabilities",
      groupTab: "automation",
    });
    expect(host.textContent).not.toContain("navigation.appliesToGlobal");
    expect(host.textContent).not.toContain("navigation.globalScopeContent");
    const globalButtons = [...host.querySelectorAll("button")].filter(
      (button) => button.textContent === "navigation.global",
    );
    expect(globalButtons).toHaveLength(2);
    expect(globalButtons.every((button) => !button.querySelector("svg"))).toBe(true);
  },
);

it("normalizes external targets while open and before first open without rendering hidden pages", async () => {
  useModalStore.getState().openSettingsTarget({ scope: "global", tab: "branding" });
  await render();
  await expectPage("Capabilities page");
  for (const tab of ["account", "developer"] as const) {
    await act(async () => useModalStore.getState().openSettingsTarget({ scope: "global", tab }));
    await expectPage("Capabilities page");
  }
  await act(async () =>
    useModalStore.getState().openSettingsTarget({ scope: "group", tab: "guidance" }),
  );
  await expectPage("Automation page");
  expect(useModalStore.getState().settingsTarget).toBeNull();
});

it("falls back only within permitted tabs for signed-in non-admin users", async () => {
  fixture.admin = false;
  fixture.signedIn = true;
  writeSettingsLastLocation({ scope: "global", globalTab: "account", groupTab: "guidance" });
  await render();
  await expectPage("Profiles page");
  expect(host.textContent).not.toContain("tabs.capabilities");
  expect(readSettingsLastLocation(true).globalTab).toBe("myProfiles");
});

it("does not grant restricted users global access when normalizing a hidden target", async () => {
  fixture.admin = false;
  useModalStore.getState().openSettingsTarget({ scope: "global", tab: "account" });
  await render();
  await expectPage("Automation page");
  expect(host.querySelectorAll('button[aria-pressed="false"][disabled]').length).toBeGreaterThan(0);
  expect(host.textContent).not.toContain("Capabilities page");
  expect(fixture.hiddenRender).not.toHaveBeenCalled();
});

it("removes the secondary account navigation from group connections", async () => {
  await act(async () => root.render(<GroupConnectionsPanel groupId="g" onOpenAccount={vi.fn()} />));
  expect(host.textContent).not.toContain("groupConnections.linkAccount");
  expect(host.textContent).toContain("direct.title");
});
