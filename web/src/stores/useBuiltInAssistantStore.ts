import { create } from "zustand";

export type BuiltInAssistantPanelTarget = "pet" | "voice_secretary";

type BuiltInAssistantOpenRequest = {
  target: BuiltInAssistantPanelTarget;
  nonce: number;
};

interface BuiltInAssistantState {
  openRequests: Record<string, BuiltInAssistantOpenRequest>;
  requestOpen: (groupId: string, target: BuiltInAssistantPanelTarget) => void;
}

export const useBuiltInAssistantStore = create<BuiltInAssistantState>((set) => ({
  openRequests: {},
  requestOpen: (groupId, target) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    set((state) => ({
      openRequests: {
        ...state.openRequests,
        [gid]: {
          target,
          nonce: Date.now(),
        },
      },
    }));
  },
}));
