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

const DONE_HUB_STORAGE_KEY = "cccc_done_hub_session";
const DONE_HUB_LOGIN_KEY = "cccc_done_hub_login";

const EMPTY_SAVED_LOGIN: DoneHubSavedLogin = {
  base_url: DONE_HUB_BASE_URL,
  username: "",
  password: "",
  remember_password: false,
};

let initializePromise: Promise<void> | null = null;

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
  ) => Promise<boolean>;
  refresh: () => Promise<boolean>;
  disconnect: () => void;
  clearError: () => void;
};

function loadStoredSession(): DoneHubSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(DONE_HUB_STORAGE_KEY);
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
    const raw = localStorage.getItem(DONE_HUB_LOGIN_KEY);
    if (!raw) return EMPTY_SAVED_LOGIN;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return EMPTY_SAVED_LOGIN;
    const record = parsed as Record<string, unknown>;
    const normalizedBaseUrl = normalizeDoneHubBaseUrl(String(record.base_url || "")) || DONE_HUB_BASE_URL;
    const username = String(record.username || "").trim();
    const rememberPassword = Boolean(record.remember_password);
    const password = rememberPassword ? String(record.password || "") : "";
    return {
      base_url: normalizedBaseUrl,
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
  const rememberPassword = Boolean(savedLogin.remember_password);
  const password = rememberPassword ? String(savedLogin.password || "") : "";
  const nextValue: DoneHubSavedLogin = {
    base_url: normalizedBaseUrl,
    username,
    password,
    remember_password: rememberPassword,
  };
  try {
    if (!nextValue.username && !nextValue.password && !nextValue.remember_password) {
      localStorage.removeItem(DONE_HUB_LOGIN_KEY);
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
        if (refreshed) return;
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

  connect: async (username: string, password: string, rememberPassword = false) => {
    const normalizedBaseUrl = normalizeDoneHubBaseUrl(DONE_HUB_BASE_URL);
    const savedLogin: DoneHubSavedLogin = {
      base_url: normalizedBaseUrl,
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
    const resp = await loginDoneHub(username, password);
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
    if (!resp.ok || !nextSession) {
      persistSession(null);
      set((state) => ({
        ...state,
        status: "error",
        session: null,
        initialized: true,
        errorMessage: sanitizeDoneHubErrorMessage(resp.ok ? "missing session" : resp.error.message),
      }));
      return false;
    }
    persistSession(nextSession);
    set({
      status: "connected",
      session: nextSession,
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
