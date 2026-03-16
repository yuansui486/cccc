#!/usr/bin/env node
/**
 * Generate placeholder sprite sheet PNGs for the desktop pet.
 * Each sprite sheet is a horizontal strip of 32×32 frames.
 *
 * Usage: node scripts/generate-sprites.js
 * Output: assets/sprites/{napping,working,busy,needs_you}.png
 */

import { createCanvas } from "canvas";
import { writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SPRITES_DIR = join(__dirname, "..", "assets", "sprites");
const FRAME_SIZE = 32;

mkdirSync(SPRITES_DIR, { recursive: true });

// --- Color palette ---
const CAT_BODY = "#FF8C00";
const CAT_LIGHT = "#FFB366";
const CAT_DARK = "#E07800";
const BLACK = "#000000";
const WHITE = "#FFFFFF";
const SWEAT = "#87CEEB";
const RED = "#FF0000";
const GRAY = "#888888";

function drawCatBase(ctx, offsetY = 0) {
  // Body
  ctx.fillStyle = CAT_BODY;
  ctx.fillRect(8, 13 + offsetY, 16, 11);

  // Head
  ctx.fillRect(10, 7 + offsetY, 12, 8);

  // Ears
  ctx.fillRect(10, 4 + offsetY, 4, 4);
  ctx.fillRect(18, 4 + offsetY, 4, 4);

  // Inner ears
  ctx.fillStyle = CAT_LIGHT;
  ctx.fillRect(11, 5 + offsetY, 2, 2);
  ctx.fillRect(19, 5 + offsetY, 2, 2);
}

function drawTail(ctx, offsetY, wagX) {
  ctx.fillStyle = CAT_DARK;
  ctx.fillRect(24 + wagX, 12 + offsetY, 2, 5);
  ctx.fillRect(26 + wagX, 10 + offsetY, 2, 4);
}

// --- Napping: 6 frames, gentle breathing ---
function generateNapping() {
  const frames = 6;
  const canvas = createCanvas(FRAME_SIZE * frames, FRAME_SIZE);
  const ctx = canvas.getContext("2d");

  for (let f = 0; f < frames; f++) {
    const x = f * FRAME_SIZE;
    ctx.save();
    ctx.translate(x, 0);

    const breathe = Math.sin((f / frames) * Math.PI * 2) * 1;

    drawCatBase(ctx, breathe);

    // Closed eyes
    ctx.fillStyle = BLACK;
    ctx.fillRect(12, 11 + breathe, 3, 1);
    ctx.fillRect(17, 11 + breathe, 3, 1);

    // Zzz (alternate frames)
    if (f % 3 < 2) {
      ctx.fillStyle = GRAY;
      ctx.fillRect(26, 4, 2, 3);
      if (f % 3 === 0) {
        ctx.fillRect(28, 2, 3, 3);
      }
    }

    drawTail(ctx, breathe, 0);

    ctx.restore();
  }

  return canvas;
}

// --- Working: 4 frames, calm typing ---
function generateWorking() {
  const frames = 4;
  const canvas = createCanvas(FRAME_SIZE * frames, FRAME_SIZE);
  const ctx = canvas.getContext("2d");

  for (let f = 0; f < frames; f++) {
    const x = f * FRAME_SIZE;
    ctx.save();
    ctx.translate(x, 0);

    drawCatBase(ctx, 0);

    // Open eyes
    ctx.fillStyle = BLACK;
    ctx.fillRect(12, 10, 2, 2);
    ctx.fillRect(18, 10, 2, 2);
    ctx.fillStyle = WHITE;
    ctx.fillRect(13, 10, 1, 1);
    ctx.fillRect(19, 10, 1, 1);

    // Paws typing
    ctx.fillStyle = CAT_BODY;
    const pawL = f % 2 === 0 ? 0 : 1;
    const pawR = f % 2 === 0 ? 1 : 0;
    ctx.fillRect(10, 23 + pawL, 4, 2);
    ctx.fillRect(18, 23 + pawR, 4, 2);

    // Keyboard hint
    ctx.fillStyle = "#666";
    ctx.fillRect(8, 26, 16, 3);

    drawTail(ctx, 0, 0);

    ctx.restore();
  }

  return canvas;
}

// --- Busy: 6 frames, fast typing + sweat ---
function generateBusy() {
  const frames = 6;
  const canvas = createCanvas(FRAME_SIZE * frames, FRAME_SIZE);
  const ctx = canvas.getContext("2d");

  for (let f = 0; f < frames; f++) {
    const x = f * FRAME_SIZE;
    ctx.save();
    ctx.translate(x, 0);

    const shake = f % 2 === 0 ? 0 : 1;
    drawCatBase(ctx, shake);

    // Wide eyes
    ctx.fillStyle = BLACK;
    ctx.fillRect(12, 9 + shake, 3, 3);
    ctx.fillRect(17, 9 + shake, 3, 3);
    ctx.fillStyle = WHITE;
    ctx.fillRect(13, 9 + shake, 1, 1);
    ctx.fillRect(18, 9 + shake, 1, 1);

    // Fast paw alternation
    ctx.fillStyle = CAT_BODY;
    const pawL = f % 3 === 0 ? 0 : f % 3 === 1 ? 2 : 1;
    const pawR = 2 - pawL;
    ctx.fillRect(10, 23 + pawL, 4, 2);
    ctx.fillRect(18, 23 + pawR, 4, 2);

    // Keyboard
    ctx.fillStyle = "#666";
    ctx.fillRect(8, 26, 16, 3);

    // Sweat drop
    if (f % 3 < 2) {
      ctx.fillStyle = SWEAT;
      ctx.fillRect(23, 7 + shake, 2, 3);
      ctx.fillRect(23, 11 + shake, 1, 1);
    }

    drawTail(ctx, shake, f % 2);

    ctx.restore();
  }

  return canvas;
}

// --- Needs You: 8 frames, jumping + speech bubble ---
function generateNeedsYou() {
  const frames = 8;
  const canvas = createCanvas(FRAME_SIZE * frames, FRAME_SIZE);
  const ctx = canvas.getContext("2d");

  for (let f = 0; f < frames; f++) {
    const x = f * FRAME_SIZE;
    ctx.save();
    ctx.translate(x, 0);

    // Jump animation
    const jump = f < 4 ? -f * 2 : -(8 - f) * 2;

    drawCatBase(ctx, jump + 4);

    // Alert eyes
    ctx.fillStyle = BLACK;
    ctx.fillRect(12, 11 + jump + 4, 3, 3);
    ctx.fillRect(17, 11 + jump + 4, 3, 3);
    ctx.fillStyle = WHITE;
    ctx.fillRect(13, 11 + jump + 4, 2, 2);
    ctx.fillRect(18, 11 + jump + 4, 2, 2);

    // Speech bubble with "!"
    if (f % 2 === 0 || f < 6) {
      ctx.fillStyle = WHITE;
      ctx.fillRect(22, 1, 8, 7);
      // Border
      ctx.fillStyle = BLACK;
      ctx.fillRect(22, 0, 8, 1);
      ctx.fillRect(22, 8, 8, 1);
      ctx.fillRect(21, 1, 1, 7);
      ctx.fillRect(30, 1, 1, 7);
      // Tail of bubble
      ctx.fillStyle = WHITE;
      ctx.fillRect(24, 8, 2, 2);
      // "!"
      ctx.fillStyle = RED;
      ctx.fillRect(25, 2, 2, 3);
      ctx.fillRect(25, 6, 2, 1);
    }

    drawTail(ctx, jump + 4, Math.sin(f) > 0 ? 1 : -1);

    ctx.restore();
  }

  return canvas;
}

// --- Write PNGs ---

const generators = {
  napping: generateNapping,
  working: generateWorking,
  busy: generateBusy,
  needs_you: generateNeedsYou,
};

for (const [name, gen] of Object.entries(generators)) {
  const canvas = gen();
  const buf = canvas.toBuffer("image/png");
  const path = join(SPRITES_DIR, `${name}.png`);
  writeFileSync(path, buf);
  console.log(`Written: ${path} (${canvas.width}×${canvas.height}, ${buf.length} bytes)`);
}

console.log("Done! All sprite sheets generated.");
