import { create } from "zustand";
import {
  WEB_PET_BUBBLE_SIZE,
  WEB_PET_VIEWPORT_MARGIN,
} from "../features/webPet/constants";

type WebPetPosition = {
  x: number;
  y: number;
};

interface WebPetState {
  positions: Record<string, WebPetPosition>;
  setPosition: (groupId: string, position: WebPetPosition) => void;
}

const POSITION_STORAGE_KEY = "cccc-web-pet-positions";

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function getDefaultPosition(stackIndex = 0): WebPetPosition {
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

  const verticalOffset = stackIndex * (WEB_PET_BUBBLE_SIZE + 12);
  return {
    x: maxX,
    y: clamp(maxY - verticalOffset, WEB_PET_VIEWPORT_MARGIN, maxY),
  };
}

function normalizePosition(position: Partial<WebPetPosition> | null | undefined): WebPetPosition {
  if (typeof window === "undefined") {
    return getDefaultPosition();
  }
  const fallback = getDefaultPosition();
  return {
    x: clamp(
      Number(position?.x ?? fallback.x),
      WEB_PET_VIEWPORT_MARGIN,
      Math.max(
        WEB_PET_VIEWPORT_MARGIN,
        window.innerWidth - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
      ),
    ),
    y: clamp(
      Number(position?.y ?? fallback.y),
      WEB_PET_VIEWPORT_MARGIN,
      Math.max(
        WEB_PET_VIEWPORT_MARGIN,
        window.innerHeight - WEB_PET_BUBBLE_SIZE - WEB_PET_VIEWPORT_MARGIN,
      ),
    ),
  };
}

function loadStoredPositions(): Record<string, WebPetPosition> {
  if (typeof window === "undefined") {
    return {};
  }

  try {
    const raw = window.localStorage.getItem(POSITION_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, Partial<WebPetPosition>>;
    const positions: Record<string, WebPetPosition> = {};
    for (const [groupId, position] of Object.entries(parsed || {})) {
      const gid = String(groupId || "").trim();
      if (!gid) continue;
      positions[gid] = normalizePosition(position);
    }
    return positions;
  } catch (error) {
    console.warn("Failed to read web pet positions from localStorage:", error);
    return {};
  }
}

function persistPositions(positions: Record<string, WebPetPosition>): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify(positions));
  } catch (error) {
    console.warn("Failed to persist web pet positions:", error);
  }
}

export const useWebPetStore = create<WebPetState>((set) => ({
  positions: loadStoredPositions(),
  setPosition: (groupId, position) => {
    const gid = String(groupId || "").trim();
    if (!gid) return;
    const normalized = normalizePosition(position);
    set((state) => {
      const positions = {
        ...state.positions,
        [gid]: normalized,
      };
      persistPositions(positions);
      return { positions };
    });
  },
}));

export function getWebPetPosition(
  groupId: string,
  positions: Record<string, WebPetPosition>,
  stackIndex = 0,
): WebPetPosition {
  const gid = String(groupId || "").trim();
  if (gid && positions[gid]) {
    return positions[gid];
  }
  return getDefaultPosition(stackIndex);
}
