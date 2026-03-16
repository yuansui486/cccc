function getCurrentOrigin(): string {
  if (typeof window === "undefined") return "";
  return String(window.location.origin || "");
}

const DESKTOP_PET_RELEASED = true;

export function buildDesktopPetDaemonUrl(origin: string = getCurrentOrigin()): string {
  return String(origin || "").trim().replace(/\/+$/, "");
}

export function buildDesktopPetLaunchUrl(args: {
  daemonUrl: string;
  token: string;
  groupId: string;
}): string {
  const daemonUrl = String(args.daemonUrl || "").trim();
  const token = String(args.token || "").trim();
  const groupId = String(args.groupId || "").trim();
  const query = [
    `daemon_url=${encodeURIComponent(daemonUrl)}`,
    `token=${encodeURIComponent(token)}`,
    `group_id=${encodeURIComponent(groupId)}`,
  ].join("&");
  return `cccc-pet://launch?${query}`;
}

type LaunchTokenLike = {
  is_admin?: boolean;
  allowed_groups?: string[];
};

export function pickLaunchToken<T extends LaunchTokenLike>(tokens: T[], groupId: string): T | null {
  const normalizedGroupId = String(groupId || "").trim();
  if (!Array.isArray(tokens) || tokens.length === 0 || !normalizedGroupId) return null;

  const scopedToken =
    tokens.find((token) =>
      Array.isArray(token?.allowed_groups) &&
      token.allowed_groups.some((item) => String(item || "").trim() === normalizedGroupId)
    ) || null;
  if (scopedToken) return scopedToken;

  return tokens.find((token) => token?.is_admin) || null;
}

// ---------------------------------------------------------------------------
// Platform-aware download URL
// ---------------------------------------------------------------------------

const RELEASES_BASE = "https://github.com/ChesterRa/cccc/releases/latest/download";

type PlatformInfo = {
  os: "macos" | "windows" | "linux" | "unknown";
  arch: "aarch64" | "x64" | "unknown";
  label: string;
};

export function detectPlatform(): PlatformInfo {
  if (typeof navigator === "undefined") return { os: "unknown", arch: "unknown", label: "" };

  const ua = navigator.userAgent.toLowerCase();
  const uaData = (navigator as any).userAgentData;

  let os: PlatformInfo["os"] = "unknown";
  if (ua.includes("mac")) os = "macos";
  else if (ua.includes("win")) os = "windows";
  else if (ua.includes("linux")) os = "linux";

  let arch: PlatformInfo["arch"] = "x64";
  if (uaData?.architecture === "arm") {
    arch = "aarch64";
  } else if (ua.includes("arm64") || ua.includes("aarch64")) {
    arch = "aarch64";
  }

  const labels: Record<string, string> = {
    macos: "macOS",
    windows: "Windows",
    linux: "Linux",
  };

  return { os, arch, label: labels[os] || "" };
}

export function buildDesktopPetDownloadUrl(): { url: string; label: string } | null {
  if (!DESKTOP_PET_RELEASED) return null;

  const { os, arch, label } = detectPlatform();

  const nameBase = "cccc-desktop-pet";
  let filename = "";

  switch (os) {
    case "macos":
      filename = `${nameBase}_macos_${arch}.dmg`;
      break;
    case "windows":
      filename = `${nameBase}_windows_x64-setup.exe`;
      break;
    case "linux":
      filename = `${nameBase}_linux_x64.deb`;
      break;
    default:
      return null;
  }

  return {
    url: `${RELEASES_BASE}/${filename}`,
    label,
  };
}
