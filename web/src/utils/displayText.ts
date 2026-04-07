import i18n from "../i18n";

const APP_LOGO_PATH = `${import.meta.env.BASE_URL}onecolleague-logo.svg`;

function tr(key: string, fallback: string): string {
  return String(i18n.t(key, { defaultValue: fallback }) || fallback);
}

export function getAppBrandName(): string {
  return tr("common:appName", "一号同事");
}

export function getAppLogoPath(): string {
  return APP_LOGO_PATH;
}

export function getUserDisplayName(): string {
  return tr("common:userLabel", "用户");
}

export function getRecipientTokenLabel(token: string): string {
  switch (String(token || "").trim()) {
    case "@all":
      return tr("common:recipientAll", "@所有人");
    case "@foreman":
      return tr("common:recipientForeman", "@负责人");
    case "@peers":
      return tr("common:recipientPeers", "@协作者");
    case "user":
      return getUserDisplayName();
    default:
      return String(token || "").trim();
  }
}

export function getRecipientDisplayLabel(
  token: string,
  displayNameMap?: Map<string, string>,
): string {
  const normalized = String(token || "").trim();
  if (!normalized) return "";
  if (normalized.startsWith("@") || normalized === "user") {
    return getRecipientTokenLabel(normalized);
  }
  return displayNameMap?.get(normalized) || normalized;
}

export function formatRecipientList(
  tokens: string[] | undefined | null,
  displayNameMap?: Map<string, string>,
): string {
  const normalized = (tokens || [])
    .map((token) => getRecipientDisplayLabel(String(token || "").trim(), displayNameMap))
    .filter((token) => token);
  if (normalized.length === 0) return getRecipientTokenLabel("@all");
  return normalized.join(", ");
}

export function getGroupStatusLabel(key: "run" | "paused" | "idle" | "stop"): string {
  switch (key) {
    case "run":
      return tr("common:groupStatusRun", "运行中");
    case "paused":
      return tr("common:groupStatusPaused", "暂停中");
    case "idle":
      return tr("common:groupStatusIdle", "空闲中");
    case "stop":
      return tr("common:groupStatusStop", "已停止");
    default:
      return key;
  }
}
