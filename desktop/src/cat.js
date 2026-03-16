// Canvas 2D sprite sheet animation engine
// Renders pixel cat with 4 animation states driven by sprite sheets

const FRAME_SIZE = 32;        // Each frame in sprite sheet: 32×32 px
const RENDER_SIZE = 64;       // Rendered at 2x on canvas: 64×64 px
const TARGET_FPS = 8;         // Animation frame rate
const FADE_MS = 200;          // State transition fade duration
const PROCEDURAL_FRAMES = 8;  // Frame count for procedural fallback animation

const canvas = document.getElementById("cat-canvas");
const ctx = canvas.getContext("2d");
const petHint = document.getElementById("pet-hint");

const LAUNCH_GUIDANCE_MARKERS = ["web ui", "launch", "启动"];

// Disable image smoothing for crisp pixel art
ctx.imageSmoothingEnabled = false;

// --- Sprite Sheet Loader ---

const spriteSheets = {};
const STATES = ["napping", "working", "busy", "needs_you"];

/**
 * Load all sprite sheet PNGs from assets/sprites/.
 * Each PNG is a horizontal strip: frame 0 at x=0, frame 1 at x=32, etc.
 * @returns {Promise<void>}
 */
async function loadSprites() {
  const promises = STATES.map((state) => {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const frameCount = Math.floor(img.naturalWidth / FRAME_SIZE);
        spriteSheets[state] = { img, frameCount };
        resolve();
      };
      img.onerror = () => {
        // Fallback: generate a procedural sprite if PNG missing
        console.warn(`Sprite not found: ${state}.png, using procedural fallback`);
        spriteSheets[state] = null;
        resolve();
      };
      // Sprites are co-located in src/ so Tauri frontendDist can serve them
      img.src = `sprites/${state}.png`;
    });
  });
  await Promise.all(promises);
}

// --- Animation State Machine ---

let currentState = "napping";
let currentFrame = 0;
let lastFrameTime = 0;
let fadeOpacity = 1;
let fadeStart = 0;
let prevState = null;
let animationId = null;
let currentHintMessage = "";

/**
 * Main render loop driven by requestAnimationFrame.
 * Runs at TARGET_FPS for frame advancement, but renders every rAF for smooth fades.
 */
function render(timestamp) {
  // Advance frame at TARGET_FPS
  const frameDuration = 1000 / TARGET_FPS;
  if (timestamp - lastFrameTime >= frameDuration) {
    // Use additive timing to prevent drift (instead of lastFrameTime = timestamp)
    lastFrameTime += frameDuration;
    // Guard against spiral if tab was backgrounded — reset to now
    if (timestamp - lastFrameTime > frameDuration * 2) {
      lastFrameTime = timestamp;
    }
    const sheet = spriteSheets[currentState];
    const frameCount = sheet ? sheet.frameCount : PROCEDURAL_FRAMES;
    currentFrame = (currentFrame + 1) % frameCount;
  }

  // Handle fade transition
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

  // Clear canvas
  ctx.clearRect(0, 0, RENDER_SIZE, RENDER_SIZE);

  // Draw previous state fading out
  if (fadingOut && prevState !== null) {
    ctx.globalAlpha = 1 - fadeOpacity;
    drawState(prevState, currentFrame);
  }

  // Draw current state
  ctx.globalAlpha = fadingOut ? fadeOpacity : 1;
  drawState(currentState, currentFrame);

  ctx.globalAlpha = 1;

  animationId = requestAnimationFrame(render);
}

/**
 * Draw a single frame of the given state.
 * Falls back to procedural drawing if sprite sheet is missing.
 */
function drawState(state, frame) {
  const sheet = spriteSheets[state];
  if (sheet) {
    const sx = (frame % sheet.frameCount) * FRAME_SIZE;
    ctx.drawImage(sheet.img, sx, 0, FRAME_SIZE, FRAME_SIZE, 0, 0, RENDER_SIZE, RENDER_SIZE);
  } else {
    drawProcedural(state, frame);
  }

  // Zzz overlay for napping state (drawn at 64px render resolution for crisp Z shapes)
  if (state === "napping" && !shouldSuppressNappingEffects(state)) {
    const bounce = Math.sin((frame / 4) * Math.PI) * 2;
    drawZzzBubbles(frame, bounce);
  }
}

// --- Procedural Fallback (used when sprite PNGs are missing) ---

/**
 * Draw a pixel-art "z" at (x, y) with the given size (3px or 5px wide).
 * Uses fillRect only — no fillText — for crisp pixel rendering.
 */
function drawPixelZ(x, y, size) {
  if (size <= 4) {
    // Small z: 3×3
    ctx.fillRect(x, y, 3, 1);
    ctx.fillRect(x + 2, y + 1, 1, 1);
    ctx.fillRect(x + 1, y + 2, 1, 1);
    ctx.fillRect(x, y + 2, 3, 1);
  } else if (size <= 6) {
    // Medium Z: 5×5
    ctx.fillRect(x, y, 5, 1);
    ctx.fillRect(x + 3, y + 1, 1, 1);
    ctx.fillRect(x + 2, y + 2, 1, 1);
    ctx.fillRect(x + 1, y + 3, 1, 1);
    ctx.fillRect(x, y + 4, 5, 1);
  } else if (size <= 8) {
    // Large Z: 7×6
    ctx.fillRect(x, y, 7, 1);
    ctx.fillRect(x + 5, y + 1, 1, 1);
    ctx.fillRect(x + 4, y + 2, 1, 1);
    ctx.fillRect(x + 3, y + 3, 1, 1);
    ctx.fillRect(x + 2, y + 4, 1, 1);
    ctx.fillRect(x, y + 5, 7, 1);
  } else {
    // XL Z: 9×7
    ctx.fillRect(x, y, 9, 2);
    ctx.fillRect(x + 7, y + 2, 2, 1);
    ctx.fillRect(x + 5, y + 3, 2, 1);
    ctx.fillRect(x + 3, y + 4, 2, 1);
    ctx.fillRect(x + 1, y + 5, 2, 1);
    ctx.fillRect(x, y + 6, 9, 2);
  }
}

/**
 * Render three "Z" letters that float upward in a staggered cycle.
 * Each z has a lifespan of 8 frames, offset by ~3 frames from each other.
 * Uses white fill with dark outline for visibility on any background.
 */
function drawZzzBubbles(frame, bounce) {
  const cycle = 10; // total frames per z lifecycle
  const zSpecs = [
    { baseX: 42, baseY: 14 + bounce, size: 5, offset: 0 },
    { baseX: 48, baseY: 6 + bounce,  size: 7, offset: 3 },
    { baseX: 44, baseY: -2 + bounce, size: 9, offset: 6 },
  ];

  const savedAlpha = ctx.globalAlpha;
  const savedFill = ctx.fillStyle;
  for (const spec of zSpecs) {
    const t = (frame + spec.offset) % cycle;
    if (t >= 8) continue; // hidden for 2 frames between cycles

    const rise = t * 1.5;            // float upward
    const alpha = 1 - (t / 8) * 0.6; // fade from 1.0 → 0.4
    ctx.globalAlpha = savedAlpha * alpha;

    // Dark outline for contrast
    ctx.fillStyle = "#333355";
    drawPixelZ(spec.baseX - 1, spec.baseY - rise - 1, spec.size + 2);

    // White fill for visibility
    ctx.fillStyle = "#FFFFFF";
    drawPixelZ(spec.baseX, spec.baseY - rise, spec.size);
  }
  ctx.globalAlpha = savedAlpha;
  ctx.fillStyle = savedFill;
}

/**
 * Draws a simple pixel cat procedurally for each state.
 * This serves as a development placeholder until real sprite sheets are created.
 */
function drawProcedural(state, frame) {
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
      // Closed eyes (horizontal lines)
      ctx.fillRect(24, 22 + bounce, 6, 2);
      ctx.fillRect(34, 22 + bounce, 6, 2);
      break;
    case "working":
      // Normal open eyes
      ctx.fillRect(25, 20 + bounce, 4, 4);
      ctx.fillRect(35, 20 + bounce, 4, 4);
      // Pupils
      ctx.fillStyle = "#FFF";
      ctx.fillRect(27, 20 + bounce, 2, 2);
      ctx.fillRect(37, 20 + bounce, 2, 2);
      break;
    case "busy":
      // Wide eyes + sweat
      ctx.fillRect(24, 19 + bounce, 6, 6);
      ctx.fillRect(34, 19 + bounce, 6, 6);
      ctx.fillStyle = "#FFF";
      ctx.fillRect(27, 20 + bounce, 2, 2);
      ctx.fillRect(37, 20 + bounce, 2, 2);
      // Sweat drop
      if (frame % 2 === 0) {
        ctx.fillStyle = "#87CEEB";
        ctx.fillRect(44, 14 + bounce, 3, 5);
      }
      break;
    case "needs_you":
      // Alert eyes (big)
      ctx.fillRect(24, 18 + bounce, 6, 6);
      ctx.fillRect(34, 18 + bounce, 6, 6);
      ctx.fillStyle = "#FFF";
      ctx.fillRect(26, 19 + bounce, 3, 3);
      ctx.fillRect(36, 19 + bounce, 3, 3);
      break;
  }

  // State-specific details
  ctx.fillStyle = "#000";
  switch (state) {
    case "napping":
      // Zzz is now drawn in drawState() after sprite/procedural rendering
      break;
    case "working":
    case "busy":
      // Paws typing animation
      ctx.fillStyle = "#FF8C00";
      const pawOffset = frame % 2 === 0 ? 0 : 2;
      ctx.fillRect(20, 46 + bounce + pawOffset, 8, 4);
      ctx.fillRect(36, 46 + bounce + (2 - pawOffset), 8, 4);
      break;
    case "needs_you":
      // Speech bubble "!"
      ctx.fillStyle = "#FFF";
      ctx.fillRect(48, 6, 14, 14);
      ctx.fillStyle = "#000";
      ctx.fillRect(48, 4, 14, 2);   // top border
      ctx.fillRect(48, 20, 14, 2);  // bottom border
      ctx.fillRect(46, 6, 2, 14);   // left border
      ctx.fillRect(62, 6, 2, 14);   // right border
      ctx.fillRect(50, 20, 4, 4);   // tail
      // Exclamation mark
      ctx.fillStyle = "#FF0000";
      ctx.fillRect(54, 8, 2, 6);
      ctx.fillRect(54, 16, 2, 2);
      break;
  }

  // Tail
  ctx.fillStyle = "#E07800";
  const tailWag = Math.sin((frame / 2) * Math.PI) * 3;
  ctx.fillRect(48, 24 + bounce + tailWag, 4, 10);
  ctx.fillRect(52, 20 + bounce + tailWag, 4, 8);
}

// --- Public API ---

/**
 * Switch cat to a new animation state with fade transition.
 * @param {"napping"|"working"|"busy"|"needs_you"} stateId
 */
export function setCatState(stateId) {
  if (stateId === currentState) return;
  if (!STATES.includes(stateId)) {
    console.warn(`Unknown cat state: ${stateId}`);
    return;
  }
  prevState = currentState;
  currentState = stateId;
  currentFrame = 0;
  fadeStart = performance.now();
  fadeOpacity = 0;
}

/**
 * Get the current cat state.
 * @returns {string}
 */
export function getCatState() {
  return currentState;
}

export function setCatHint(message) {
  if (!petHint) {
    return;
  }

  const text = typeof message === "string" ? message.trim() : "";
  const visible = isLaunchGuidanceMessage(text);
  currentHintMessage = visible ? text : "";

  petHint.hidden = !visible;
  petHint.classList.toggle("visible", visible);
  petHint.textContent = visible ? text : "";
}

function isLaunchGuidanceMessage(text) {
  if (!text) {
    return false;
  }

  const normalized = text.toLowerCase();
  return LAUNCH_GUIDANCE_MARKERS.every((marker) => normalized.includes(marker));
}

function shouldSuppressNappingEffects(state) {
  return state === "napping" && isLaunchGuidanceMessage(currentHintMessage);
}

// --- Initialization ---

async function init() {
  await loadSprites();
  animationId = requestAnimationFrame(render);
}

init();
