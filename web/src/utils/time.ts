import i18n from "../i18n";

function getResolvedLocale(): string {
  const language = String(i18n.resolvedLanguage || i18n.language || "en").toLowerCase();
  if (language.startsWith("zh")) return "zh-CN";
  if (language.startsWith("ja")) return "ja-JP";
  return "en-US";
}

// Format ISO timestamp to friendly relative/absolute time
export function formatTime(isoStr: string | undefined): string {
  if (!isoStr) return "—";
  try {
    const date = new Date(isoStr);
    if (isNaN(date.getTime())) return isoStr;
    const now = new Date();
    const locale = getResolvedLocale();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);
    if (locale === "zh-CN") {
      if (diffSec < 60) return String(i18n.t("common:justNow", { defaultValue: "刚刚" }));
      if (diffMin < 60) return `${diffMin}分钟前`;
      if (diffHour < 24) return `${diffHour}小时前`;
      if (diffDay < 7) return `${diffDay}天前`;
      const month = date.getMonth() + 1;
      const day = date.getDate();
      const year = date.getFullYear();
      const currentYear = now.getFullYear();
      if (year === currentYear) return `${month}月${day}日`;
      return `${year}年${month}月${day}日`;
    }
    if (diffSec < 60) return String(i18n.t("common:justNow", { defaultValue: "just now" }));
    if (diffMin < 60) return new Intl.RelativeTimeFormat(locale, { numeric: "always" }).format(-diffMin, "minute");
    if (diffHour < 24) return new Intl.RelativeTimeFormat(locale, { numeric: "always" }).format(-diffHour, "hour");
    if (diffDay < 7) return new Intl.RelativeTimeFormat(locale, { numeric: "always" }).format(-diffDay, "day");
    const year = date.getFullYear();
    const currentYear = now.getFullYear();
    if (year === currentYear) {
      return new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(date);
    }
    return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric" }).format(date);
  } catch {
    return isoStr;
  }
}

export function formatFullTime(isoStr: string | undefined): string {
  if (!isoStr) return "";
  try {
    const date = new Date(isoStr);
    if (isNaN(date.getTime())) return isoStr;
    return date.toLocaleString(getResolvedLocale());
  } catch {
    return isoStr;
  }
}
