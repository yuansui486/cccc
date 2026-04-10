import { describe, expect, it, vi, beforeEach } from "vitest";

import { createCatEngine } from "../../../src/features/webPet/catEngine";

type FakeImageRecord = {
  _src: string;
  naturalWidth: number;
  onload: null | (() => void);
  onerror: null | (() => void);
};

const imageRecords: FakeImageRecord[] = [];

class FakeImage {
  _src = "";
  naturalWidth = 256;
  onload: null | (() => void) = null;
  onerror: null | (() => void) = null;

  constructor() {
    imageRecords.push(this);
  }

  get src(): string {
    return this._src;
  }

  set src(value: string) {
    this._src = String(value || "");
    if (!this._src) return;
    queueMicrotask(() => {
      this.onload?.();
    });
  }

  get complete(): boolean {
    return !!this._src;
  }
}

function createFakeCanvas(): HTMLCanvasElement {
  const ctx = {
    imageSmoothingEnabled: false,
    fillStyle: "#000",
    globalAlpha: 1,
    strokeStyle: "#000",
    lineWidth: 1,
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    drawImage: vi.fn(),
    beginPath: vi.fn(),
    arc: vi.fn(),
    stroke: vi.fn(),
  };

  return {
    getContext: vi.fn(() => ctx),
  } as unknown as HTMLCanvasElement;
}

describe("createCatEngine", () => {
  beforeEach(() => {
    imageRecords.length = 0;
    vi.stubGlobal("Image", FakeImage);
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("window", {
      setTimeout,
      clearTimeout,
    });
  });

  it("does not invalidate shared sprite cache when one engine is destroyed", async () => {
    const spriteUrls = {
      napping: "/sprites/napping.png",
      working: "/sprites/working.png",
      busy: "/sprites/busy.png",
    };

    const first = createCatEngine({
      canvas: createFakeCanvas(),
      spriteUrls,
    });
    await first.load();

    expect(imageRecords.map((image) => image.src)).toEqual([
      "/sprites/napping.png",
      "/sprites/working.png",
      "/sprites/busy.png",
    ]);

    first.destroy();

    expect(imageRecords.map((image) => image.src)).toEqual([
      "/sprites/napping.png",
      "/sprites/working.png",
      "/sprites/busy.png",
    ]);

    const second = createCatEngine({
      canvas: createFakeCanvas(),
      spriteUrls,
    });
    await second.load();

    expect(imageRecords).toHaveLength(3);
    expect(imageRecords.map((image) => image.src)).toEqual([
      "/sprites/napping.png",
      "/sprites/working.png",
      "/sprites/busy.png",
    ]);
  });
});
