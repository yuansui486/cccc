import { create } from "zustand";
import type { DoneHubSavedLogin, DoneHubSession, DoneHubStatus } from "../types";
import {
  DONE_HUB_BASE_URL,
  extractDoneHubSession,
  loginDoneHub,
  normalizeDoneHubBaseUrl,
  refreshDoneHubSession,
  sanitizeDoneHubErrorMessage,
} from "../services/doneHub";

const DONE_HUB_STORAGE_KEY = "onecolleague_done_hub_session";
const DONE_HUB_LOGIN_KEY = "onecolleague_done_hub_login";
const LEGACY_DONE_HUB_STORAGE_KEY = "cccc_done_hub_session";
const LEGACY_DONE_HUB_LOGIN_KEY = "cccc_done_hub_login";

const EMPTY_SAVED_LOGIN: DoneHubSavedLogin = {
  base_url: DONE_HUB_BASE_URL,
  tenant_code: "",
  username: "",
  password: "",
  remember_password: false,
};

let initializePromise: Promise<void> | null = null;

function isDoneHubHardAuthFailure(code: string | null | undefined, message: string | null | undefined): boolean {
  const normalizedCode = String(code || "").trim().toLowerCase();
  const normalizedMessage = String(message || "").trim().toLowerCase();
  if (
    /unauthorized|forbidden|auth[_-]?failed|invalid[_-]?token|token[_-]?(invalid|expired)|session[_-]?expired/.test(normalizedCode)
  ) {
    return true;
  }
  return /(401|403|unauthorized|forbidden|invalid token|token expired|session expired|login expired|authentication failed|auth failed|重新登录|登录失效|凭证失效|认证失效|未授权|未登录)/i.test(
    normalizedMessage,
  );
}

type DoneHubState = {
  status: DoneHubStatus;
  session: DoneHubSession | null;
  savedLogin: DoneHubSavedLogin;
  errorMessage: string;
  initialized: boolean;
  initialize: () => Promise<void>;
  connect: (
    username: string,
    password: string,
    rememberPassword?: boolean,
    tenantCode?: string,
  ) => Promise<boolean>;
  refresh: () => Promise<boolean>;
  disconnect: () => void;
  clearError: () => void;
};

export function getCurrentDoneHubAccessToken(): string {
  return String(useDoneHubStore.getState().session?.access_token || "").trim();
}

export function getCurrentDoneHubCodexApiKey(): string {
  return String(useDoneHubStore.getState().session?.codex_api_key || "").trim();
}

function loadStoredSession(): DoneHubSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(DONE_HUB_STORAGE_KEY) || sessionStorage.getItem(LEGACY_DONE_HUB_STORAGE_KEY);
    if (raw && !sessionStorage.getItem(DONE_HUB_STORAGE_KEY)) {
      sessionStorage.setItem(DONE_HUB_STORAGE_KEY, raw);
    }
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const record = parsed as Record<string, unknown>;
    const accessToken = String(record.access_token || "").trim();
    const baseUrl = normalizeDoneHubBaseUrl(String(record.base_url || "")) || DONE_HUB_BASE_URL;
    if (!accessToken || !baseUrl) return null;
    return {
      base_url: baseUrl,
      access_token: accessToken,
      codex_api_key: String(record.codex_api_key || "").trim() || undefined,
      codex_model: String(record.codex_model || "").trim() || undefined,
      username: String(record.username || "").trim(),
      display_name: String(record.display_name || "").trim(),
      group: String(record.group || "").trim(),
      quota: Number(record.quota || 0),
      used_quota: Number(record.used_quota || 0),
      role: Number(record.role || 0),
      status: Number(record.status || 0),
    };
  } catch {
    return null;
  }
}

function loadSavedLogin(): DoneHubSavedLogin {
  if (typeof window === "undefined") return EMPTY_SAVED_LOGIN;
  try {
    const raw = localStorage.getItem(DONE_HUB_LOGIN_KEY) || localStorage.getItem(LEGACY_DONE_HUB_LOGIN_KEY);
    if (raw && !localStorage.getItem(DONE_HUB_LOGIN_KEY)) {
      localStorage.setItem(DONE_HUB_LOGIN_KEY, raw);
    }
    if (!raw) return EMPTY_SAVED_LOGIN;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return EMPTY_SAVED_LOGIN;
    const record = parsed as Record<string, unknown>;
    const normalizedBaseUrl = normalizeDoneHubBaseUrl(String(record.base_url || "")) || DONE_HUB_BASE_URL;
    const username = String(record.username || "").trim();
    const tenantCode = String(record.tenant_code || "").trim();
    const rememberPassword = Boolean(record.remember_password);
    const password = rememberPassword ? String(record.password || "") : "";
    return {
      base_url: normalizedBaseUrl,
      tenant_code: tenantCode,
      username,
      password,
      remember_password: rememberPassword,
    };
  } catch {
    return EMPTY_SAVED_LOGIN;
  }
}

function persistSession(session: DoneHubSession | null): void {
  if (typeof window === "undefined") return;
  try {
    if (!session) {
      sessionStorage.removeItem(DONE_HUB_STORAGE_KEY);
      sessionStorage.removeItem(LEGACY_DONE_HUB_STORAGE_KEY);
      return;
    }
    sessionStorage.setItem(DONE_HUB_STORAGE_KEY, JSON.stringify(session));
  } catch {
    void 0;
  }
}

function persistSavedLogin(savedLogin: DoneHubSavedLogin): void {
  if (typeof window === "undefined") return;
  const normalizedBaseUrl = normalizeDoneHubBaseUrl(savedLogin.base_url);
  const username = String(savedLogin.username || "").trim();
  const tenantCode = String(savedLogin.tenant_code || "").trim();
  const rememberPassword = Boolean(savedLogin.remember_password);
  const password = rememberPassword ? String(savedLogin.password || "") : "";
  const nextValue: DoneHubSavedLogin = {
    base_url: normalizedBaseUrl,
    tenant_code: tenantCode,
    username,
    password,
    remember_password: rememberPassword,
  };
  try {
    if (!nextValue.tenant_code && !nextValue.username && !nextValue.password && !nextValue.remember_password) {
      localStorage.removeItem(DONE_HUB_LOGIN_KEY);
      localStorage.removeItem(LEGACY_DONE_HUB_LOGIN_KEY);
      return;
    }
    localStorage.setItem(DONE_HUB_LOGIN_KEY, JSON.stringify(nextValue));
  } catch {
    void 0;
  }
}

export const useDoneHubStore = create<DoneHubState>((set, get) => ({
  status: "idle",
  session: null,
  savedLogin: EMPTY_SAVED_LOGIN,
  errorMessage: "",
  initialized: false,

  initialize: async () => {
    if (get().initialized) return;
    if (initializePromise) return initializePromise;

    initializePromise = (async () => {
      const savedLogin = loadSavedLogin();
      const session = loadStoredSession();

      set((state) => ({
        ...state,
        savedLogin,
      }));

      if (session) {
        set({
          initialized: true,
          savedLogin,
          session,
          status: "refreshing",
          errorMessage: "",
        });
        const refreshed = await get().refresh();
        if (refreshed || get().session) return;
      }

      if (
        savedLogin.remember_password &&
        savedLogin.username &&
        savedLogin.password
      ) {
        set((state) => ({
          ...state,
          initialized: true,
          session: null,
          status: "authenticating",
          errorMessage: "",
        }));
        const connected = await get().connect(
          savedLogin.username,
          savedLogin.password,
          true,
          savedLogin.tenant_code || "",
        );
        if (connected) return;
      }

      set((state) => ({
        ...state,
        initialized: true,
        session: null,
        status: state.errorMessage ? "error" : "idle",
      }));
    })().finally(() => {
      initializePromise = null;
    });

    return initializePromise;
  },

  connect: async (username: string, password: string, rememberPassword = false, tenantCode = "") => {
    const normalizedBaseUrl = normalizeDoneHubBaseUrl(DONE_HUB_BASE_URL);
    const savedLogin: DoneHubSavedLogin = {
      base_url: normalizedBaseUrl,
      tenant_code: String(tenantCode || "").trim(),
      username: String(username || "").trim(),
      password: rememberPassword ? String(password || "") : "",
      remember_password: rememberPassword,
    };
    if (normalizedBaseUrl) {
      persistSavedLogin(savedLogin);
      set((state) => ({
        ...state,
        savedLogin,
      }));
    }
    set({ status: "authenticating", errorMessage: "" });
    const resp = await loginDoneHub(username, password, tenantCode);
    const session = extractDoneHubSession(resp);
    if (!resp.ok || !session) {
      persistSession(null);
      set({
        status: "error",
        session: null,
        initialized: true,
        errorMessage: sanitizeDoneHubErrorMessage(resp.ok ? "missing session" : resp.error.message),
      });
      return false;
    }
    persistSession(session);
    set({
      status: "connected",
      session,
      savedLogin,
      initialized: true,
      errorMessage: "",
    });
    return true;
  },

  refresh: async () => {
    const session = get().session;
    if (!session) {
      set({ status: "idle", errorMessage: "" });
      return false;
    }
    set((state) => ({
      ...state,
      status: "refreshing",
      errorMessage: "",
    }));
    const resp = await refreshDoneHubSession(session.access_token);
    const nextSession = extractDoneHubSession(resp);
    const latestSession = get().session;
    if (!latestSession || latestSession.access_token !== session.access_token) {
      return false;
    }
    if (!resp.ok || !nextSession) {
      const hardFailure = !resp.ok && isDoneHubHardAuthFailure(resp.error.code, resp.error.message);
      const errorMessage = sanitizeDoneHubErrorMessage(resp.ok ? "missing session" : resp.error.message);
      if (hardFailure) {
        persistSession(null);
        set((state) => ({
          ...state,
          status: "error",
          session: null,
          initialized: true,
          errorMessage,
        }));
        return false;
      }
      persistSession(latestSession);
      set((state) => ({
        ...state,
        status: "error",
        initialized: true,
        errorMessage,
      }));
      return false;
    }
    const mergedSession = {
      ...nextSession,
      codex_api_key: nextSession.codex_api_key || latestSession.codex_api_key,
      codex_model: nextSession.codex_model || latestSession.codex_model,
    };
    persistSession(mergedSession);
    set({
      status: "connected",
      session: mergedSession,
      errorMessage: "",
    });
    return true;
  },

  disconnect: () => {
    persistSession(null);
    persistSavedLogin(EMPTY_SAVED_LOGIN);
    set({
      status: "idle",
      session: null,
      savedLogin: EMPTY_SAVED_LOGIN,
      initialized: true,
      errorMessage: "",
    });
  },

  clearError: () => set({ errorMessage: "" }),
}));
