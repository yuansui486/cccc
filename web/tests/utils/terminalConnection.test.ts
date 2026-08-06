import { describe, expect, it } from "vitest";

import {
  buildTerminalWebSocketUrl,
  buildTerminalConnectionKey,
  decodeTerminalJsonFrame,
  encodeTerminalInputFrame,
  encodeTerminalResizeFrame,
  isTerminalAttachNonRetryableErrorCode,
  isTerminalAttachStartupRaceErrorCode,
  parseTerminalBinaryFrame,
  seedTerminalReplayCursor,
  shouldMaintainTerminalConnection,
  shouldSuppressTerminalAttachErrorOutput,
  terminalAttachRetryDelayMs,
} from "../../src/utils/terminalConnection";

describe("buildTerminalConnectionKey", () => {
  it("changes when terminal control becomes available", () => {
    const base = {
      activated: true,
      isRunning: true,
      isHeadless: false,
      groupId: "g1",
      actorId: "peer1",
      reconnectTrigger: 0,
    };

    expect(buildTerminalConnectionKey({ ...base, canControl: false })).not.toBe(
      buildTerminalConnectionKey({ ...base, canControl: true }),
    );
  });

  it("treats runner-mismatch attach errors as non-retryable but keeps startup races retryable", () => {
    expect(isTerminalAttachNonRetryableErrorCode("not_pty_actor")).toBe(true);
    expect(isTerminalAttachNonRetryableErrorCode("actor_not_running")).toBe(false);
    expect(isTerminalAttachNonRetryableErrorCode("actor_not_found")).toBe(true);
    expect(isTerminalAttachNonRetryableErrorCode("daemon_unavailable")).toBe(false);
  });

  it("classifies transient terminal attach startup races", () => {
    expect(isTerminalAttachStartupRaceErrorCode("not_pty_actor")).toBe(false);
    expect(isTerminalAttachStartupRaceErrorCode("actor_not_running")).toBe(true);
    expect(isTerminalAttachStartupRaceErrorCode("actor_not_found")).toBe(false);
    expect(isTerminalAttachStartupRaceErrorCode("daemon_unavailable")).toBe(false);
    expect(isTerminalAttachStartupRaceErrorCode("terminal_attach_busy")).toBe(true);
  });

  it("suppresses noisy terminal attach state-transition errors in the terminal buffer", () => {
    expect(shouldSuppressTerminalAttachErrorOutput("not_pty_actor")).toBe(true);
    expect(shouldSuppressTerminalAttachErrorOutput("actor_not_running")).toBe(true);
    expect(shouldSuppressTerminalAttachErrorOutput("actor_not_found")).toBe(false);
    expect(shouldSuppressTerminalAttachErrorOutput("daemon_unavailable")).toBe(false);
    expect(shouldSuppressTerminalAttachErrorOutput("terminal_attach_busy")).toBe(true);
  });

  it("keeps an activated running terminal connected independently of tab visibility", () => {
    expect(
      shouldMaintainTerminalConnection({
        activated: true,
        isRunning: true,
        isHeadless: false,
        hasTerminal: true,
      }),
    ).toBe(true);
  });

  it("resumes from a delivered cursor and resets only when replay falls out of the ring", () => {
    expect(seedTerminalReplayCursor(null, 10)).toEqual({ cursor: 10, resetTerminal: false });
    expect(seedTerminalReplayCursor(42, 42)).toEqual({ cursor: 42, resetTerminal: false });
    expect(seedTerminalReplayCursor(42, 80)).toEqual({ cursor: 80, resetTerminal: true });
  });

  it("bounds startup-race and busy retries", () => {
    expect(terminalAttachRetryDelayMs({ code: "actor_not_running", attempt: 100, startupElapsedMs: 59999 })).toBe(750);
    expect(terminalAttachRetryDelayMs({ code: "actor_not_running", attempt: 0, startupElapsedMs: 60000 })).toBeNull();
    expect(terminalAttachRetryDelayMs({ code: "terminal_attach_busy", attempt: 0, startupElapsedMs: 0 })).toBe(1000);
    expect(terminalAttachRetryDelayMs({ code: "terminal_attach_busy", attempt: 10, startupElapsedMs: 0 })).toBeNull();
  });
});

describe("terminal binary protocol", () => {
  const asArrayBuffer = (value: Uint8Array): ArrayBuffer =>
    value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength) as ArrayBuffer;

  it("builds viewer and resumed control URLs", () => {
    expect(
      buildTerminalWebSocketUrl({
        protocol: "https:",
        host: "example.test",
        groupId: "group one",
        actorId: "peer/1",
        mode: "viewer",
      }),
    ).toBe("wss://example.test/api/v1/groups/group%20one/actors/peer%2F1/term?mode=viewer");
    expect(
      buildTerminalWebSocketUrl({
        protocol: "http:",
        host: "localhost:8848",
        groupId: "g1",
        actorId: "a1",
        mode: "control",
        takeover: true,
        since: 42,
      }),
    ).toContain("?mode=control&takeover=true&since=42");
  });

  it("encodes input and resize frames", () => {
    const input = parseTerminalBinaryFrame(asArrayBuffer(encodeTerminalInputFrame("hello")));
    expect(input?.type).toBe("input");
    expect(new TextDecoder().decode(input?.payload)).toBe("hello");

    const resize = parseTerminalBinaryFrame(asArrayBuffer(encodeTerminalResizeFrame(120, 40)));
    expect(resize?.type).toBe("resize");
    expect(decodeTerminalJsonFrame(resize?.payload || new Uint8Array())).toEqual({ cols: 120, rows: 40 });
  });

  it("parses attach, output, and input acknowledgement frames", () => {
    const encoder = new TextEncoder();
    const frame = (opcode: number, payload: Uint8Array): ArrayBuffer => {
      const value = new Uint8Array(payload.byteLength + 1);
      value[0] = opcode;
      value.set(payload, 1);
      return asArrayBuffer(value);
    };

    expect(parseTerminalBinaryFrame(frame("1".charCodeAt(0), encoder.encode("out")))?.type).toBe("output");
    const attach = parseTerminalBinaryFrame(
      frame("3".charCodeAt(0), encoder.encode('{"terminal_writable":true,"replay_cursor":7}')),
    );
    expect(attach?.type).toBe("attach");
    expect(decodeTerminalJsonFrame(attach?.payload || new Uint8Array())).toEqual({
      terminal_writable: true,
      replay_cursor: 7,
    });
    expect(parseTerminalBinaryFrame(frame("4".charCodeAt(0), encoder.encode('{"ok":false}')))?.type).toBe(
      "input_ack",
    );
  });
});
