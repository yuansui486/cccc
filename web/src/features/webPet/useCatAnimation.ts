// React hook wrapping catEngine for lifecycle management.
import { useEffect, useRef } from "react";
import { createCatEngine } from "./catEngine";
import type { CatState, CatEngine, PetReaction } from "./types";
import nappingSpriteUrl from "../../assets/webPet/napping.png";
import workingSpriteUrl from "../../assets/webPet/working.png";
import busySpriteUrl from "../../assets/webPet/busy.png";

const SPRITE_URLS = {
  napping: nappingSpriteUrl,
  working: workingSpriteUrl,
  busy: busySpriteUrl,
} as const;

interface UseCatAnimationOptions {
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  state: CatState;
  hint: string;
  reaction: PetReaction;
}

export function useCatAnimation({
  canvasRef,
  state,
  hint,
  reaction,
}: UseCatAnimationOptions): void {
  const engineRef = useRef<CatEngine | null>(null);

  // Mount: create engine + preload sprites; unmount: destroy
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const engine = createCatEngine({
      canvas,
      spriteUrls: SPRITE_URLS,
    });
    engineRef.current = engine;
    void engine.load();

    return () => {
      engine.destroy();
      engineRef.current = null;
    };
    // canvasRef is a RefObject — its identity is stable across renders
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync state changes to engine
  useEffect(() => {
    engineRef.current?.setState(state);
  }, [state]);

  // Sync hint changes to engine
  useEffect(() => {
    engineRef.current?.setHint(hint);
  }, [hint]);

  useEffect(() => {
    if (!reaction) return;
    engineRef.current?.playReaction(reaction.kind);
  }, [reaction]);
}
