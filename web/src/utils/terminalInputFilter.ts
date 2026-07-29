/* eslint-disable no-control-regex */

const DEVICE_ATTRIBUTES_RE = /^\x1b\[(?:\?|>)\d+(?:;\d+)*c/;
const FOCUS_EVENT_RE = /^\x1b\[[IO]/;
const OSC_COLOR_RE = /^\x1b\](?:10|11);rgb:[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)/;

const FILTERED_RUNTIMES = new Set(["droid", "gemini", "neovate"]);

function isFilteredRuntime(runtime: string | undefined): boolean {
  return FILTERED_RUNTIMES.has(String(runtime || "").trim().toLowerCase());
}

function isPotentialDevicePrefix(value: string): boolean {
  if (!value.startsWith("\x1b[")) return false;
  const body = value.slice(2);
  if (!body) return true;
  if (body === "I" || body === "O") return true;
  return /^[?>][0-9;]*$/.test(body);
}

function isPotentialOscPrefix(value: string): boolean {
  if (!value.startsWith("\x1b]")) return false;
  const body = value.slice(2);
  if (!body) return true;
  if (!(body.startsWith("1") || body.startsWith("10") || body.startsWith("11"))) return false;
  if (body === "1" || body === "10" || body === "11" || body === "10;" || body === "11;") return true;
  if (!body.startsWith("10;rgb:") && !body.startsWith("11;rgb:")) return false;
  return /^[0-9a-fA-F]{0,4}(?:\/[0-9a-fA-F]{0,4}){0,2}(?:\x1b)?$/.test(body.slice(7));
}

function isPotentialResponsePrefix(value: string): boolean {
  return isPotentialDevicePrefix(value) || isPotentialOscPrefix(value);
}

export type TerminalInputFilterResult = {
  data: string;
  pending: string;
};

/** Removes terminal-generated replies while preserving ordinary keyboard input. */
export function filterTerminalInputChunk(
  previousPending: string,
  chunk: string,
  runtime?: string,
): TerminalInputFilterResult {
  if (!isFilteredRuntime(runtime)) return { data: chunk, pending: "" };

  const combined = `${previousPending}${chunk || ""}`;
  let data = "";
  let index = 0;

  while (index < combined.length) {
    if (combined[index] !== "\x1b") {
      data += combined[index++];
      continue;
    }

    const remaining = combined.slice(index);
    const deviceMatch = remaining.match(DEVICE_ATTRIBUTES_RE);
    const focusMatch = remaining.match(FOCUS_EVENT_RE);
    const oscMatch = remaining.match(OSC_COLOR_RE);
    const match = deviceMatch || focusMatch || oscMatch;
    if (match) {
      index += match[0].length;
      continue;
    }

    if (isPotentialResponsePrefix(remaining)) {
      return { data, pending: remaining };
    }

    data += combined[index++];
  }

  return { data, pending: "" };
}
