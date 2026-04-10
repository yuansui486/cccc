// Canvas 2D sprite sheet animation engine for Web Pet.
// Pure Canvas API — no React dependency.
// Ported from desktop/src/cat.js with full TypeScript typing.

import type { CatState, CatEngine, CatEngineOptions, PetReaction } from "./types";
import { CAT_STATES } from "./types";
import { WEB_PET_CANVAS_SIZE } from "./constants";

const FRAME_SIZE = 32;
const RENDER_SIZE = WEB_PET_CANVAS_SIZE;
const TARGET_FPS = 8;
const FADE_MS = 200;
const PROCEDURAL_FRAMES = 8;
const REACTION_DURATION_MS = 2200;
const SPRITE_LOAD_TIMEOUT_MS = 10_000;

const LAUNCH_GUIDANCE_MARKERS = ["web ui", "launch", "启动"];

interface SpriteSheet {
  img: HTMLImageElement;
  frameCount: number;
}

type SpriteSheetMap = Record<CatState, SpriteSheet | null>;
type ReactionKind = NonNullable<PetReaction>["kind"];

const SPRITE_SHEET_CACHE = new Map<string, Promise<SpriteSheet | null>>();
const SPRITE_WARNING_KEYS = new Set<string>();

function warnSpriteOnce(key: string, message: string): void {
  if (SPRITE_WARNING_KEYS.has(key)) return;
  SPRITE_WARNING_KEYS.add(key);
  console.warn(message);
}

function loadSpriteSheet(state: CatState, url: string): Promise<SpriteSheet | null> {
  const cacheKey = `${state}:${url}`;
  const cached = SPRITE_SHEET_CACHE.get(cacheKey);
  if (cached) {
    return cached;
  }

  const promise = new Promise<SpriteSheet | null>((resolve) => {
    if (!url) {
      warnSpriteOnce(cacheKey, `Sprite URL missing: ${state}.png, using procedural fallback`);
      resolve(null);
      return;
    }

    let settled = false;
    const img = new Image();
    const finish = (sheet: SpriteSheet | null, warning?: string) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      img.onload = null;
      img.onerror = null;
      if (warning) {
        warnSpriteOnce(cacheKey, warning);
      }
      resolve(sheet);
    };
    img.onload = () => {
      const frameCount = Math.max(1, Math.floor(img.naturalWidth / FRAME_SIZE));
      finish({ img, frameCount });
    };
    img.onerror = () => {
      finish(null, `Sprite not found: ${state}.png, using procedural fallback`);
    };
    const timeout = window.setTimeout(() => {
      finish(null, `Sprite load timed out: ${state}.png, using procedural fallback`);
    }, SPRITE_LOAD_TIMEOUT_MS);
    img.src = url;
    if (img.complete && img.naturalWidth > 0) {
      const frameCount = Math.max(1, Math.floor(img.naturalWidth / FRAME_SIZE));
      finish({ img, frameCount });
    }
  });

  SPRITE_SHEET_CACHE.set(cacheKey, promise);
  return promise;
}

export function createCatEngine(options: CatEngineOptions): CatEngine {
  const { canvas, spriteUrls } = options;
  const ctxOrNull = canvas.getContext("2d");
  if (!ctxOrNull) {
    throw new Error("Failed to get 2d context from canvas");
  }
  // Non-null guaranteed by the throw above; reassign to satisfy TS in closures
  const ctx: CanvasRenderingContext2D = ctxOrNull;

  // Disable image smoothing for crisp pixel art
  ctx.imageSmoothingEnabled = false;

  const spriteSheets: Partial<SpriteSheetMap> = {};

  let currentState: CatState = "napping";
  let currentFrame = 0;
  let lastFrameTime = 0;
  let fadeOpacity = 1;
  let fadeStart = 0;
  let prevState: CatState | null = null;
  let animationId: number | null = null;
  let currentHintMessage = "";
  let destroyed = false;
  let activeReaction: ReactionKind | null = null;
  let reactionStartTime = 0;

  // --- Sprite Sheet Loader ---

  async function loadSprites(): Promise<void> {
    const promises = CAT_STATES.map(async (state) => {
      const sheet = await loadSpriteSheet(state, spriteUrls[state]);
      if (!destroyed) {
        spriteSheets[state] = sheet;
      }
    });
    await Promise.all(promises);
  }

  // --- Pixel Z drawing ---

  function drawPixelZ(x: number, y: number, size: number): void {
    if (size <= 4) {
      ctx.fillRect(x, y, 3, 1);
      ctx.fillRect(x + 2, y + 1, 1, 1);
      ctx.fillRect(x + 1, y + 2, 1, 1);
      ctx.fillRect(x, y + 2, 3, 1);
    } else if (size <= 6) {
      ctx.fillRect(x, y, 5, 1);
      ctx.fillRect(x + 3, y + 1, 1, 1);
      ctx.fillRect(x + 2, y + 2, 1, 1);
      ctx.fillRect(x + 1, y + 3, 1, 1);
      ctx.fillRect(x, y + 4, 5, 1);
    } else if (size <= 8) {
      ctx.fillRect(x, y, 7, 1);
      ctx.fillRect(x + 5, y + 1, 1, 1);
      ctx.fillRect(x + 4, y + 2, 1, 1);
      ctx.fillRect(x + 3, y + 3, 1, 1);
      ctx.fillRect(x + 2, y + 4, 1, 1);
      ctx.fillRect(x, y + 5, 7, 1);
    } else {
      ctx.fillRect(x, y, 9, 2);
      ctx.fillRect(x + 7, y + 2, 2, 1);
      ctx.fillRect(x + 5, y + 3, 2, 1);
      ctx.fillRect(x + 3, y + 4, 2, 1);
      ctx.fillRect(x + 1, y + 5, 2, 1);
      ctx.fillRect(x, y + 6, 9, 2);
    }
  }

  // --- Zzz Bubbles ---

  function drawZzzBubbles(frame: number, bounce: number): void {
    const cycle = 10;
    const zSpecs = [
      { baseX: 42, baseY: 14 + bounce, size: 5, offset: 0 },
      { baseX: 48, baseY: 6 + bounce, size: 7, offset: 3 },
      { baseX: 44, baseY: -2 + bounce, size: 9, offset: 6 },
    ];

    const savedAlpha = ctx.globalAlpha;
    const savedFill = ctx.fillStyle;
    for (const spec of zSpecs) {
      const t = (frame + spec.offset) % cycle;
      if (t >= 8) continue;

      const rise = t * 1.5;
      const alpha = 1 - (t / 8) * 0.6;
      ctx.globalAlpha = savedAlpha * alpha;

      ctx.fillStyle = "#333355";
      drawPixelZ(spec.baseX - 1, spec.baseY - rise - 1, spec.size + 2);

      ctx.fillStyle = "#FFFFFF";
      drawPixelZ(spec.baseX, spec.baseY - rise, spec.size);
    }
    ctx.globalAlpha = savedAlpha;
    ctx.fillStyle = savedFill;
  }

  // --- Procedural Fallback ---

  function drawProcedural(state: CatState, frame: number): void {
    const bounce = Math.sin((frame / 4) * Math.PI) * 2;

    // Body
    ctx.fillStyle = "#FF8C00";
    ctx.fillRect(16, 26 + bounce, 32, 22);

    // Head
    ctx.fillRect(20, 14 + bounce, 24, 16);

    // Ears
    ctx.fillRect(20, 8 + bounce, 8, 8);
    ctx.fillRect(36, 8 + bounce, 8, 8);

    // Inner ears
    ctx.fillStyle = "#FFB366";
    ctx.fillRect(22, 10 + bounce, 4, 4);
    ctx.fillRect(38, 10 + bounce, 4, 4);

    // Eyes
    ctx.fillStyle = "#000";
    switch (state) {
      case "napping":
        ctx.fillRect(24, 22 + bounce, 6, 2);
        ctx.fillRect(34, 22 + bounce, 6, 2);
        break;
      case "working":
        ctx.fillRect(25, 20 + bounce, 4, 4);
        ctx.fillRect(35, 20 + bounce, 4, 4);
        ctx.fillStyle = "#FFF";
        ctx.fillRect(27, 20 + bounce, 2, 2);
        ctx.fillRect(37, 20 + bounce, 2, 2);
        break;
      case "busy":
        ctx.fillRect(24, 19 + bounce, 6, 6);
        ctx.fillRect(34, 19 + bounce, 6, 6);
        ctx.fillStyle = "#FFF";
        ctx.fillRect(27, 20 + bounce, 2, 2);
        ctx.fillRect(37, 20 + bounce, 2, 2);
        if (frame % 2 === 0) {
          ctx.fillStyle = "#87CEEB";
          ctx.fillRect(44, 14 + bounce, 3, 5);
        }
        break;
    }

    // State-specific details
    ctx.fillStyle = "#000";
    switch (state) {
      case "napping":
        break;
      case "working":
      case "busy": {
        ctx.fillStyle = "#FF8C00";
        const pawOffset = frame % 2 === 0 ? 0 : 2;
        ctx.fillRect(20, 46 + bounce + pawOffset, 8, 4);
        ctx.fillRect(36, 46 + bounce + (2 - pawOffset), 8, 4);
        break;
      }
    }

    // Tail
    ctx.fillStyle = "#E07800";
    const tailWag = Math.sin((frame / 2) * Math.PI) * 3;
    ctx.fillRect(48, 24 + bounce + tailWag, 4, 10);
    ctx.fillRect(52, 20 + bounce + tailWag, 4, 8);
  }

  // --- State drawing ---

  function isLaunchGuidanceMessage(text: string): boolean {
    if (!text) return false;
    const normalized = text.toLowerCase();
    return LAUNCH_GUIDANCE_MARKERS.every((marker) =>
      normalized.includes(marker)
    );
  }

  function shouldSuppressNappingEffects(state: CatState): boolean {
    return state === "napping" && isLaunchGuidanceMessage(currentHintMessage);
  }

  function drawState(state: CatState, frame: number): void {
    const sheet = spriteSheets[state];
    if (sheet) {
      const sx = (frame % sheet.frameCount) * FRAME_SIZE;
      ctx.drawImage(
        sheet.img,
        sx,
        0,
        FRAME_SIZE,
        FRAME_SIZE,
        0,
        0,
        RENDER_SIZE,
        RENDER_SIZE
      );
    } else {
      drawProcedural(state, frame);
    }

    if (state === "napping" && !shouldSuppressNappingEffects(state)) {
      const bounce = Math.sin((frame / 4) * Math.PI) * 2;
      drawZzzBubbles(frame, bounce);
    }
  }

  function drawPixelBurst(x: number, y: number, size: number): void {
    ctx.fillRect(x, y - size, 1, size * 2 + 1);
    ctx.fillRect(x - size, y, size * 2 + 1, 1);
    ctx.fillRect(x - 1, y - 1, 3, 3);
  }

  function drawMentionOverlay(progress: number): void {
    const bounce = Math.sin(progress * Math.PI) * 4;
    const scale = 1 + Math.sin(progress * Math.PI) * 0.2;
    const x = 47;
    const y = 10 - bounce;
    const size = Math.max(3, Math.round(3 * scale));
    const savedFill = ctx.fillStyle;
    const savedAlpha = ctx.globalAlpha;

    ctx.globalAlpha = 1 - progress * 0.35;
    ctx.fillStyle = "#4FC3F7";
    ctx.fillRect(x, y, size, size * 3);
    ctx.fillRect(x, y + size * 4, size, size);

    ctx.globalAlpha = (1 - progress) * 0.5;
    ctx.strokeStyle = "#4FC3F7";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(32, 22, 10 + progress * 8, 0, Math.PI * 2);
    ctx.stroke();

    ctx.globalAlpha = savedAlpha;
    ctx.fillStyle = savedFill;
  }

  function drawSuccessOverlay(progress: number): void {
    const savedFill = ctx.fillStyle;
    const savedAlpha = ctx.globalAlpha;
    const stars = [
      { x: 17, y: 18, driftX: -6, driftY: -6, delay: 0 },
      { x: 45, y: 12, driftX: 5, driftY: -7, delay: 0.1 },
      { x: 15, y: 40, driftX: -5, driftY: 4, delay: 0.2 },
      { x: 48, y: 34, driftX: 6, driftY: 5, delay: 0.05 },
    ];

    ctx.fillStyle = "#FFD700";

    for (const star of stars) {
      const localProgress = Math.max(0, Math.min(1, (progress - star.delay) / (1 - star.delay)));
      if (localProgress <= 0) continue;
      ctx.globalAlpha = 1 - localProgress;
      const x = star.x + star.driftX * localProgress;
      const y = star.y + star.driftY * localProgress;
      drawPixelBurst(Math.round(x), Math.round(y), 2);
    }

    ctx.globalAlpha = savedAlpha;
    ctx.fillStyle = savedFill;
  }

  function drawErrorOverlay(progress: number): void {
    const savedFill = ctx.fillStyle;
    const savedAlpha = ctx.globalAlpha;
    const wobble = Math.sin(progress * Math.PI * 6) * 2;
    const alpha = 1 - progress * 0.25;

    ctx.globalAlpha = alpha;
    ctx.fillStyle = "#FF6B6B";
    ctx.fillRect(15 + wobble, 14, 3, 7);
    ctx.fillRect(15 + wobble, 23, 3, 3);

    ctx.fillStyle = "#FF8A80";
    ctx.fillRect(44 - wobble, 18, 2, 5);
    ctx.fillRect(46 - wobble, 20, 2, 5);
    ctx.fillRect(45 - wobble, 25, 2, 2);

    ctx.globalAlpha = savedAlpha;
    ctx.fillStyle = savedFill;
  }

  function drawReactionOverlay(timestamp: number): void {
    if (!activeReaction) return;

    const elapsed = timestamp - reactionStartTime;
    if (elapsed >= REACTION_DURATION_MS) {
      activeReaction = null;
      return;
    }

    const progress = Math.max(0, Math.min(1, elapsed / REACTION_DURATION_MS));
    switch (activeReaction) {
      case "mention":
        drawMentionOverlay(progress);
        break;
      case "success":
        drawSuccessOverlay(progress);
        break;
      case "error":
        drawErrorOverlay(progress);
        break;
    }
  }

  // --- Render loop ---

  function render(timestamp: number): void {
    if (destroyed) return;

    const frameDuration = 1000 / TARGET_FPS;
    if (timestamp - lastFrameTime >= frameDuration) {
      lastFrameTime += frameDuration;
      if (timestamp - lastFrameTime > frameDuration * 2) {
        lastFrameTime = timestamp;
      }
      const sheet = spriteSheets[currentState];
      const frameCount = sheet ? sheet.frameCount : PROCEDURAL_FRAMES;
      currentFrame = (currentFrame + 1) % frameCount;
    }

    let fadingOut = false;
    if (prevState !== null) {
      const elapsed = timestamp - fadeStart;
      fadeOpacity = Math.min(elapsed / FADE_MS, 1);
      if (fadeOpacity >= 1) {
        prevState = null;
        fadeOpacity = 1;
      } else {
        fadingOut = true;
      }
    }

    ctx.clearRect(0, 0, RENDER_SIZE, RENDER_SIZE);

    if (fadingOut && prevState !== null) {
      ctx.globalAlpha = 1 - fadeOpacity;
      drawState(prevState, currentFrame);
    }

    ctx.globalAlpha = fadingOut ? fadeOpacity : 1;
    drawState(currentState, currentFrame);
    drawReactionOverlay(timestamp);

    ctx.globalAlpha = 1;

    animationId = requestAnimationFrame(render);
  }

  // --- Public API ---

  return {
    async load(): Promise<void> {
      await loadSprites();
      if (!destroyed) {
        animationId = requestAnimationFrame(render);
      }
    },

    setState(state: CatState): void {
      if (state === currentState) return;
      if (!(CAT_STATES as readonly string[]).includes(state)) {
        console.warn(`Unknown cat state: ${state}`);
        return;
      }
      prevState = currentState;
      currentState = state;
      currentFrame = 0;
      fadeStart = performance.now();
      fadeOpacity = 0;
    },

    setHint(message: string): void {
      const text = typeof message === "string" ? message.trim() : "";
      currentHintMessage = isLaunchGuidanceMessage(text) ? text : "";
    },

    playReaction(kind: ReactionKind): void {
      activeReaction = kind;
      reactionStartTime = performance.now();
    },

    destroy(): void {
      destroyed = true;
      if (animationId !== null) {
        cancelAnimationFrame(animationId);
        animationId = null;
      }
      ctx.clearRect(0, 0, RENDER_SIZE, RENDER_SIZE);
      for (const state of CAT_STATES) {
        const sheet = spriteSheets[state];
        if (sheet?.img) {
          sheet.img.onload = null;
          sheet.img.onerror = null;
        }
        spriteSheets[state] = null;
      }
      currentState = "napping";
      currentFrame = 0;
      lastFrameTime = 0;
      fadeOpacity = 1;
      fadeStart = 0;
      prevState = null;
      currentHintMessage = "";
      activeReaction = null;
      reactionStartTime = 0;
    },
  };
}
