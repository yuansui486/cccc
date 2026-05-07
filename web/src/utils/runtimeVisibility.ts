import type { Actor } from "../types";

export type RuntimeVisibilityMode = "hidden" | "visible";

export type RuntimeVisibilityState = {
  peerRuntimeVisibility: RuntimeVisibilityMode;
  petRuntimeVisibility: RuntimeVisibilityMode;
};

export const DEFAULT_PEER_RUNTIME_VISIBILITY: RuntimeVisibilityMode = "visible";
export const DEFAULT_PET_RUNTIME_VISIBILITY: RuntimeVisibilityMode = "hidden";

export function normalizeRuntimeVisibilityMode(
  value: unknown,
  fallback: RuntimeVisibilityMode,
): RuntimeVisibilityMode {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized === "hidden" || normalized === "visible" ? normalized : fallback;
}

export function isPetRuntimeActor(actor: Actor | null | undefined): boolean {
  const internalKind = String(actor?.internal_kind || "").trim().toLowerCase();
  const id = String(actor?.id || "").trim();
  return internalKind === "pet" || id === "pet-peer" || internalKind === "voice_secretary" || id === "voice-secretary";
}

export function isRuntimeSurfaceActorVisible(
  actor: Actor | null | undefined,
  options: Partial<RuntimeVisibilityState>,
): boolean {
  if (!actor) return false;
  const peerRuntimeVisibility = normalizeRuntimeVisibilityMode(
    options.peerRuntimeVisibility,
    DEFAULT_PEER_RUNTIME_VISIBILITY,
  );
  const petRuntimeVisibility = normalizeRuntimeVisibilityMode(
    options.petRuntimeVisibility,
    DEFAULT_PET_RUNTIME_VISIBILITY,
  );
  return isPetRuntimeActor(actor)
    ? petRuntimeVisibility === "visible"
    : peerRuntimeVisibility === "visible";
}

export function filterVisibleRuntimeActors(
  actors: Actor[],
  options: Partial<RuntimeVisibilityState>,
): Actor[] {
  return actors.filter((actor) => isRuntimeSurfaceActorVisible(actor, options));
}
