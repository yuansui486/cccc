import type { WebBranding } from "../types";

export const DEFAULT_PRODUCT_NAME = "一号同事";
export const DEFAULT_LOGO_ICON_URL = "/ui/onecolleague-logo.svg";
export const DEFAULT_FAVICON_URL = "/ui/onecolleague-logo.svg";
export const DEFAULT_DOCUMENT_TITLE = "一号同事 - AI Agent Collaboration";

export const DEFAULT_WEB_BRANDING: WebBranding = {
  product_name: DEFAULT_PRODUCT_NAME,
  logo_icon_url: DEFAULT_LOGO_ICON_URL,
  favicon_url: DEFAULT_FAVICON_URL,
  has_custom_logo_icon: false,
  has_custom_favicon: false,
  updated_at: null,
};

export function normalizeWebBranding(value: Partial<WebBranding> | null | undefined): WebBranding {
  const productName = String(value?.product_name || "").trim() || DEFAULT_PRODUCT_NAME;
  const logoIconUrl = String(value?.logo_icon_url || "").trim() || DEFAULT_LOGO_ICON_URL;
  const faviconUrl = String(value?.favicon_url || "").trim() || logoIconUrl || DEFAULT_FAVICON_URL;
  return {
    product_name: productName,
    logo_icon_url: logoIconUrl,
    favicon_url: faviconUrl,
    has_custom_logo_icon: Boolean(value?.has_custom_logo_icon),
    has_custom_favicon: Boolean(value?.has_custom_favicon),
    updated_at: value?.updated_at ? String(value.updated_at) : null,
  };
}

export function resolveDocumentTitle(productName: string): string {
  const normalized = String(productName || "").trim() || DEFAULT_PRODUCT_NAME;
  return normalized === DEFAULT_PRODUCT_NAME ? DEFAULT_DOCUMENT_TITLE : normalized;
}

function ensureLink(rel: string): HTMLLinkElement {
  let el = document.querySelector(`link[rel="${rel}"]`) as HTMLLinkElement | null;
  if (!el) {
    el = document.createElement("link");
    el.rel = rel;
    document.head.appendChild(el);
  }
  return el;
}

export function applyBrandingToDocument(value: Partial<WebBranding> | null | undefined): WebBranding {
  const branding = normalizeWebBranding(value);
  document.title = resolveDocumentTitle(branding.product_name);
  const iconHref = String(branding.favicon_url || "").trim() || DEFAULT_FAVICON_URL;
  ensureLink("icon").href = iconHref;
  ensureLink("apple-touch-icon").href = iconHref;
  return branding;
}
