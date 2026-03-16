#!/usr/bin/env node
/**
 * Generate Desktop Pet application icons from pixel cat design.
 * Produces all sizes required by Tauri for macOS + Windows builds.
 *
 * Usage: node scripts/generate-icons.js
 * Requires: npm install canvas png-to-ico (canvas already installed)
 */

import { createCanvas } from "canvas";
import { writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ICONS_DIR = join(__dirname, "..", "src-tauri", "icons");

mkdirSync(ICONS_DIR, { recursive: true });

// --- Color palette ---
const CAT_BODY = "#FF8C00";
const CAT_LIGHT = "#FFB366";
const CAT_DARK = "#E07800";
const BLACK = "#000000";
const WHITE = "#FFFFFF";
const BG_LIGHT = "#FFF8EE";
const BG_RING = "#FFE0B2";

/**
 * Draw pixel cat icon at a given size.
 * The cat is drawn on a 32×32 "logical" grid, scaled to the target size.
 */
function drawCatIcon(size) {
  const canvas = createCanvas(size, size);
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;

  const s = size / 32; // scale factor

  function px(x, y, w, h) {
    ctx.fillRect(Math.round(x * s), Math.round(y * s), Math.round(w * s), Math.round(h * s));
  }

  // Background circle (warm tint)
  ctx.fillStyle = BG_LIGHT;
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
  ctx.fill();

  // Subtle ring
  ctx.strokeStyle = BG_RING;
  ctx.lineWidth = Math.max(1, s * 0.8);
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - s * 0.5, 0, Math.PI * 2);
  ctx.stroke();

  // Body
  ctx.fillStyle = CAT_BODY;
  px(8, 14, 16, 12);

  // Head
  px(10, 7, 12, 9);

  // Ears
  px(10, 3, 4, 5);
  px(18, 3, 4, 5);

  // Inner ears
  ctx.fillStyle = CAT_LIGHT;
  px(11, 4, 2, 3);
  px(19, 4, 2, 3);

  // Eyes (friendly, slightly large)
  ctx.fillStyle = BLACK;
  px(12, 11, 3, 3);
  px(17, 11, 3, 3);

  // Eye highlights
  ctx.fillStyle = WHITE;
  px(13, 11, 1, 1);
  px(18, 11, 1, 1);

  // Nose
  ctx.fillStyle = "#FF6B6B";
  px(15, 14, 2, 1);

  // Mouth
  ctx.fillStyle = BLACK;
  px(14, 15, 1, 1);
  px(17, 15, 1, 1);

  // Paws
  ctx.fillStyle = CAT_LIGHT;
  px(9, 25, 4, 2);
  px(19, 25, 4, 2);

  // Tail
  ctx.fillStyle = CAT_DARK;
  px(24, 16, 2, 6);
  px(26, 14, 2, 4);
  px(27, 12, 2, 3);

  return canvas;
}

// --- Generate all required sizes ---

const SIZES = {
  "32x32.png": 32,
  "128x128.png": 128,
  "128x128@2x.png": 256,
  "icon.png": 512,
  // Windows Store logos
  "Square30x30Logo.png": 30,
  "Square44x44Logo.png": 44,
  "Square71x71Logo.png": 71,
  "Square89x89Logo.png": 89,
  "Square107x107Logo.png": 107,
  "Square142x142Logo.png": 142,
  "Square150x150Logo.png": 150,
  "Square284x284Logo.png": 284,
  "Square310x310Logo.png": 310,
  "StoreLogo.png": 50,
};

for (const [filename, size] of Object.entries(SIZES)) {
  const canvas = drawCatIcon(size);
  const buf = canvas.toBuffer("image/png");
  const path = join(ICONS_DIR, filename);
  writeFileSync(path, buf);
  console.log(`Written: ${filename} (${size}×${size}, ${buf.length} bytes)`);
}

// --- Generate .ico (multi-size) ---
// .ico needs 16, 32, 48, 256 px sizes packed together
// Use a simple BMP-based ICO structure

function createIco(sizes) {
  const images = sizes.map((size) => {
    const canvas = drawCatIcon(size);
    return canvas.toBuffer("image/png");
  });

  // ICO header: 6 bytes
  const numImages = images.length;
  const headerSize = 6 + numImages * 16;
  let dataOffset = headerSize;

  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);        // reserved
  header.writeUInt16LE(1, 2);        // type: ICO
  header.writeUInt16LE(numImages, 4); // count

  const entries = [];
  const dataBuffers = [];

  for (let i = 0; i < numImages; i++) {
    const size = sizes[i];
    const imgData = images[i];
    const entry = Buffer.alloc(16);

    entry.writeUInt8(size >= 256 ? 0 : size, 0);  // width (0 = 256)
    entry.writeUInt8(size >= 256 ? 0 : size, 1);  // height
    entry.writeUInt8(0, 2);                         // color palette
    entry.writeUInt8(0, 3);                         // reserved
    entry.writeUInt16LE(1, 4);                      // color planes
    entry.writeUInt16LE(32, 6);                     // bits per pixel
    entry.writeUInt32LE(imgData.length, 8);         // data size
    entry.writeUInt32LE(dataOffset, 12);            // data offset

    entries.push(entry);
    dataBuffers.push(imgData);
    dataOffset += imgData.length;
  }

  return Buffer.concat([header, ...entries, ...dataBuffers]);
}

const icoBuffer = createIco([16, 32, 48, 256]);
writeFileSync(join(ICONS_DIR, "icon.ico"), icoBuffer);
console.log(`Written: icon.ico (${icoBuffer.length} bytes)`);

// --- Generate .icns (macOS) ---
// Simplified: just use the 512px PNG as icon.icns placeholder
// A proper .icns would need multiple sizes in Apple's format
// For now, copy the 512px PNG — Tauri's bundler will handle conversion

const icon512 = drawCatIcon(512).toBuffer("image/png");

// Build a minimal .icns with ic09 (512×512 PNG)
function createIcns(pngData) {
  const type = Buffer.from("ic09"); // 512×512 PNG
  const entrySize = Buffer.alloc(4);
  entrySize.writeUInt32BE(8 + pngData.length);
  const entry = Buffer.concat([type, entrySize, pngData]);

  const magic = Buffer.from("icns");
  const totalSize = Buffer.alloc(4);
  totalSize.writeUInt32BE(8 + entry.length);

  return Buffer.concat([magic, totalSize, entry]);
}

const icnsBuffer = createIcns(icon512);
writeFileSync(join(ICONS_DIR, "icon.icns"), icnsBuffer);
console.log(`Written: icon.icns (${icnsBuffer.length} bytes)`);

console.log("\nDone! All icons generated.");
