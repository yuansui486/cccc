import test from "node:test";
import assert from "node:assert/strict";

import {
  buildDesktopPetDaemonUrl,
  buildDesktopPetDownloadUrl,
  buildDesktopPetLaunchUrl,
  pickLaunchToken,
} from "./desktopPetLaunch.ts";

test("buildDesktopPetDaemonUrl trims trailing slash", () => {
  assert.equal(
    buildDesktopPetDaemonUrl("https://cccc.example.com/"),
    "https://cccc.example.com"
  );
});

test("buildDesktopPetLaunchUrl encodes query params", () => {
  assert.equal(
    buildDesktopPetLaunchUrl({
      daemonUrl: "https://cccc.example.com/ui",
      token: "tok en/with?chars",
      groupId: "g_demo",
    }),
    "cccc-pet://launch?daemon_url=https%3A%2F%2Fcccc.example.com%2Fui&token=tok%20en%2Fwith%3Fchars&group_id=g_demo"
  );
});

test("pickLaunchToken prefers scoped token before admin", () => {
  const token = pickLaunchToken(
    [
      { is_admin: false, allowed_groups: ["g_demo"], token_id: "scoped-1" },
      { is_admin: true, allowed_groups: [], token_id: "admin-1" },
    ],
    "g_demo"
  );
  assert.deepEqual(token, {
    is_admin: false,
    allowed_groups: ["g_demo"],
    token_id: "scoped-1",
  });
});

test("pickLaunchToken falls back to admin when no scoped token matches", () => {
  const token = pickLaunchToken(
    [
      { is_admin: false, allowed_groups: ["g_other"], token_id: "scoped-1" },
      { is_admin: true, allowed_groups: [], token_id: "admin-1" },
    ],
    "g_demo"
  );
  assert.deepEqual(token, {
    is_admin: true,
    allowed_groups: [],
    token_id: "admin-1",
  });
});

// --- buildDesktopPetDownloadUrl: stable filenames (no version) ---

function withNavigator(ua: string, uaData: any, fn: () => void) {
  const prev = globalThis.navigator;
  Object.defineProperty(globalThis, "navigator", {
    value: { userAgent: ua, userAgentData: uaData },
    writable: true,
    configurable: true,
  });
  try {
    fn();
  } finally {
    Object.defineProperty(globalThis, "navigator", {
      value: prev,
      writable: true,
      configurable: true,
    });
  }
}

test("buildDesktopPetDownloadUrl returns stable macOS aarch64 filename", () => {
  withNavigator("Macintosh; ARM Mac OS X", { architecture: "arm" }, () => {
    const result = buildDesktopPetDownloadUrl();
    assert.ok(result);
    assert.match(result.url, /cccc-desktop-pet_macos_aarch64\.dmg$/);
    assert.doesNotMatch(result.url, /\d+\.\d+\.\d+/);
    assert.equal(result.label, "macOS");
  });
});

test("buildDesktopPetDownloadUrl returns stable macOS x64 filename", () => {
  withNavigator("Macintosh; Intel Mac OS X", undefined, () => {
    const result = buildDesktopPetDownloadUrl();
    assert.ok(result);
    assert.match(result.url, /cccc-desktop-pet_macos_x64\.dmg$/);
    assert.doesNotMatch(result.url, /\d+\.\d+\.\d+/);
  });
});

test("buildDesktopPetDownloadUrl returns stable Windows x64 filename", () => {
  withNavigator("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", undefined, () => {
    const result = buildDesktopPetDownloadUrl();
    assert.ok(result);
    assert.match(result.url, /cccc-desktop-pet_windows_x64-setup\.exe$/);
    assert.doesNotMatch(result.url, /\d+\.\d+\.\d+/);
    assert.equal(result.label, "Windows");
  });
});

test("buildDesktopPetDownloadUrl returns stable Linux x64 filename", () => {
  withNavigator("Mozilla/5.0 (X11; Linux x86_64)", undefined, () => {
    const result = buildDesktopPetDownloadUrl();
    assert.ok(result);
    assert.match(result.url, /cccc-desktop-pet_linux_x64\.deb$/);
    assert.doesNotMatch(result.url, /\d+\.\d+\.\d+/);
    assert.equal(result.label, "Linux");
  });
});

test("buildDesktopPetDownloadUrl falls back to x64 for ARM Windows", () => {
  withNavigator("Mozilla/5.0 (Windows NT 10.0; ARM64)", { architecture: "arm" }, () => {
    const result = buildDesktopPetDownloadUrl();
    assert.ok(result);
    assert.match(result.url, /cccc-desktop-pet_windows_x64-setup\.exe$/);
  });
});

test("buildDesktopPetDownloadUrl falls back to x64 for ARM Linux", () => {
  withNavigator("Mozilla/5.0 (X11; Linux aarch64)", { architecture: "arm" }, () => {
    const result = buildDesktopPetDownloadUrl();
    assert.ok(result);
    assert.match(result.url, /cccc-desktop-pet_linux_x64\.deb$/);
  });
});
