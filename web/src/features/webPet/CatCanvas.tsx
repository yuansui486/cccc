import { useRef } from "react";
import { useCatAnimation } from "./useCatAnimation";
import type { CatState, PetReaction } from "./types";
import { WEB_PET_CANVAS_SIZE } from "./constants";

interface CatCanvasProps {
  state: CatState;
  hint: string;
  reaction: PetReaction;
}

export function CatCanvas({ state, hint, reaction }: CatCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useCatAnimation({
    canvasRef,
    state,
    hint,
    reaction,
  });

  return (
    <canvas
      ref={canvasRef}
      width={WEB_PET_CANVAS_SIZE}
      height={WEB_PET_CANVAS_SIZE}
      className="block"
      style={{
        width: WEB_PET_CANVAS_SIZE,
        height: WEB_PET_CANVAS_SIZE,
        imageRendering: "pixelated",
      }}
      aria-hidden="true"
    />
  );
}
