import { describe, expect, it } from "vitest";

import { filterTerminalInputChunk } from "../../src/utils/terminalInputFilter";
import { encodeTerminalInputFrame, parseTerminalBinaryFrame } from "../../src/utils/terminalConnection";

const da1 = "\x1b[?1;2c";
const color10 = "\x1b]10;rgb:1e1e/2929/3b3b\x1b\\";
const color11 = "\x1b]11;rgb:fafa/fafa/fafa\x1b\\";
const color4 = "\x1b]4;12;rgb:5c5c/5c5c/ffff\x07";

describe("filterTerminalInputChunk", () => {
  it("filters concatenated device and color replies", () => {
    expect(filterTerminalInputChunk("", `${da1}${color10}${color11}`, "gemini")).toEqual({ data: "", pending: "" });
  });

  it("filters replies split across chunks", () => {
    const first = filterTerminalInputChunk("", "\x1b[?1;", "droid");
    expect(first).toEqual({ data: "", pending: "\x1b[?1;" });
    expect(filterTerminalInputChunk(first.pending, `2c${color10.slice(0, 12)}`, "droid")).toEqual({
      data: "",
      pending: color10.slice(0, 12),
    });
    expect(filterTerminalInputChunk(color10.slice(0, 12), `${color10.slice(12)}hello\r`, "droid")).toEqual({
      data: "hello\r",
      pending: "",
    });
  });

  it("filters responses around real user input", () => {
    expect(filterTerminalInputChunk("", `${da1}hello\r${color11}`, "neovate")).toEqual({ data: "hello\r", pending: "" });
  });

  it("preserves ordinary escape input and non-filtered runtimes", () => {
    expect(filterTerminalInputChunk("", "\x1b[A", "gemini")).toEqual({ data: "\x1b[A", pending: "" });
    expect(filterTerminalInputChunk("", da1, "bash")).toEqual({ data: da1, pending: "" });
  });

  it("filters OpenCode device and palette replies but preserves focus events", () => {
    expect(filterTerminalInputChunk("", `${da1}${color4}`, "opencode")).toEqual({ data: "", pending: "" });
    expect(filterTerminalInputChunk("", "\x1b[I", "opencode")).toEqual({ data: "\x1b[I", pending: "" });
  });

  it("preserves browser CPR and modified function keys for OpenCode", () => {
    expect(filterTerminalInputChunk("", "\x1b[24;80R", "opencode")).toEqual({ data: "\x1b[24;80R", pending: "" });
    expect(filterTerminalInputChunk("", "\x1b[1;2R", "opencode")).toEqual({ data: "\x1b[1;2R", pending: "" });
  });

  it("preserves Codex terminal replies but filters focus events", () => {
    expect(filterTerminalInputChunk("", `${da1}${color10}${color11}\x1b[O`, "codex")).toEqual({
      data: `${da1}${color10}${color11}`,
      pending: "",
    });
  });

  it("preserves fragmented Codex color replies", () => {
    const first = filterTerminalInputChunk("", color11.slice(0, 12), "codex");
    expect(first).toEqual({ data: color11.slice(0, 12), pending: "" });
    expect(filterTerminalInputChunk(first.pending, color11.slice(12), "codex")).toEqual({
      data: color11.slice(12),
      pending: "",
    });
  });

  it("filters fragmented Codex focus events without swallowing ordinary escape input", () => {
    const focusPrefix = filterTerminalInputChunk("", "\x1b[", "codex");
    expect(focusPrefix).toEqual({ data: "", pending: "\x1b[" });
    expect(filterTerminalInputChunk(focusPrefix.pending, "I", "codex")).toEqual({ data: "", pending: "" });

    const arrowPrefix = filterTerminalInputChunk("", "\x1b[", "codex");
    expect(filterTerminalInputChunk(arrowPrefix.pending, "A", "codex")).toEqual({ data: "\x1b[A", pending: "" });
  });

  it("frames only bytes left after fragmented provider response filtering", () => {
    const first = filterTerminalInputChunk("", "\x1b[?1;", "neovate");
    const second = filterTerminalInputChunk(first.pending, "2chello\r", "neovate");
    const encoded = encodeTerminalInputFrame(second.data);
    const buffer = encoded.buffer.slice(encoded.byteOffset, encoded.byteOffset + encoded.byteLength) as ArrayBuffer;
    const frame = parseTerminalBinaryFrame(buffer);

    expect(frame?.type).toBe("input");
    expect(new TextDecoder().decode(frame?.payload)).toBe("hello\r");
  });
});
