import { create } from "zustand";
import {
  WEB_PET_BUBBLE_SIZE,
  WEB_PET_VIEWPORT_MARGIN,
} from "../features/webPet/constants";

type WebPetPosition = {
  x: number;
  y: number;
};

export type PetIntent =
  | { kind: "task"; taskId: string }
  | null;

interface WebPetState {
  panelOpen: boolean;
  position: WebPetPosition;
  pendingIntent: PetIntent;
  togglePanel: () => void;
  setPosition: (position: WebPetPosition) => void;
  setPendingIntent: (intent: PetIntent) => void;
}

const POSITION_STORAGE_KEY = "cccc-web-pet-position";

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function getDefaultPosition(): WebPetPosition {
  if (typeof window === "undefined") {
    return { x: WEB_PET_VIEWPORT_MARGIN, y: WEB_PET_VIEWPORT_MARGIN };
  }

  const maxX = Math.max(
    WEB_PET_VIEWPORT_MARGIN,
    window.innerWidth - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
  );
  const maxY = Math.max(
    WEB_PET_VIEWPORT_MARGIN,
    window.innerHeight - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
  );

  return {
    x: maxX,
    y: maxY,
  };
}

function loadStoredPosition(): WebPetPosition {
  if (typeof window === "undefined") {
    return getDefaultPosition();
  }

  try {
    const raw = window.localStorage.getItem(POSITION_STORAGE_KEY);
    if (!raw) {
      return getDefaultPosition();
    }

    const parsed = JSON.parse(raw) as Partial<WebPetPosition>;
    const fallback = getDefaultPosition();

    return {
      x: clamp(
        Number(parsed.x ?? fallback.x),
        WEB_PET_VIEWPORT_MARGIN,
        Math.max(
          WEB_PET_VIEWPORT_MARGIN,
          window.innerWidth - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
        ),
      ),
      y: clamp(
        Number(parsed.y ?? fallback.y),
        WEB_PET_VIEWPORT_MARGIN,
        Math.max(
          WEB_PET_VIEWPORT_MARGIN,
          window.innerHeight - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
        ),
      ),
    };
  } catch (error) {
    console.warn("Failed to read web pet position from localStorage:", error);
    return getDefaultPosition();
  }
}

function persistPosition(position: WebPetPosition): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(position));
  } catch (error) {
    console.warn("Failed to persist web pet position:", error);
  }
}

export const useWebPetStore = create<WebPetState>((set) => ({
  panelOpen: false,
  position: loadStoredPosition(),
  pendingIntent: null,
  togglePanel: () =>
    set((state) => ({
      panelOpen: !state.panelOpen,
    })),
  setPosition: (position) => {
    persistPosition(position);
    set({ position });
  },
  setPendingIntent: (intent) => set({ pendingIntent: intent }),
}));
