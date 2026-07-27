export const CHAT_TAB = "chat";
export const COMPUTER_CONTROL_TAB = "computer-control";

export function isWorkspaceTab(tab: string): boolean {
  return tab === CHAT_TAB || tab === COMPUTER_CONTROL_TAB;
}

export function computerControlGroupIdFromPath(pathname: string): string {
  const match = pathname.match(/^\/(?:ui\/)?computer-control(?:\/([^/]+))?\/?$/);
  if (!match) return "";
  try {
    return decodeURIComponent(match[1] || "");
  } catch {
    return "";
  }
}

export function isComputerControlPath(pathname: string): boolean {
  return /^\/(?:ui\/)?computer-control(?:\/|$)/.test(pathname);
}