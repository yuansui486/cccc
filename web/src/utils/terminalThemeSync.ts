import { updateObservability } from "../services/api/webAccess";
import type { Theme } from "../types";

export type TerminalColorScheme = "light" | "dark";

let desiredScheme: TerminalColorScheme | null = null;
let lastSyncedScheme: TerminalColorScheme | null = null;
let drainPromise: Promise<boolean> | null = null;
let requestedForceVersion = 0;
let completedForceVersion = 0;

export function getSystemColorScheme(): TerminalColorScheme {
  if (typeof window === "undefined") return "dark";
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "dark";
  }
}

export function resolveTerminalColorScheme(
  theme: Theme,
  systemScheme: TerminalColorScheme = getSystemColorScheme(),
): TerminalColorScheme {
  return theme === "system" ? systemScheme : theme;
}

export function getStoredTerminalColorScheme(): TerminalColorScheme {
  if (typeof window === "undefined") return "dark";
  try {
    const stored = localStorage.getItem("onecolleague-theme") || localStorage.getItem("cccc-theme");
    const theme: Theme = stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
    return resolveTerminalColorScheme(theme);
  } catch {
    return getSystemColorScheme();
  }
}

async function drainThemeSync(): Promise<boolean> {
  let succeeded = true;
  while (
    desiredScheme &&
    (desiredScheme !== lastSyncedScheme || completedForceVersion < requestedForceVersion)
  ) {
    const target = desiredScheme;
    const forceVersion = requestedForceVersion;
    try {
      const response = await updateObservability({ terminalUiColorScheme: target });
      if (!response.ok) {
        succeeded = false;
        break;
      }
      lastSyncedScheme = target;
      completedForceVersion = forceVersion;
    } catch {
      succeeded = false;
      break;
    }
  }
  return succeeded;
}

export function syncTerminalColorScheme(
  scheme: TerminalColorScheme,
  options?: { force?: boolean },
): Promise<boolean> {
  desiredScheme = scheme;
  if (options?.force) requestedForceVersion += 1;
  if (
    lastSyncedScheme === scheme &&
    completedForceVersion >= requestedForceVersion &&
    !drainPromise
  ) return Promise.resolve(true);
  if (!drainPromise) {
    drainPromise = drainThemeSync().finally(() => {
      drainPromise = null;
    });
  }
  return drainPromise;
}

export function syncStoredTerminalColorScheme(options?: { force?: boolean }): Promise<boolean> {
  try {
    return syncTerminalColorScheme(getStoredTerminalColorScheme(), options);
  } catch {
    return Promise.resolve(false);
  }
}

export function resetTerminalThemeSyncForTests(): void {
  desiredScheme = null;
  lastSyncedScheme = null;
  drainPromise = null;
  requestedForceVersion = 0;
  completedForceVersion = 0;
}
