/* eslint-disable no-control-regex */

const DEVICE_ATTRIBUTES_RE = /^\x1b\[(?:\?|>)\d+(?:;\d+)*c/;
const FOCUS_EVENT_RE = /^\x1b\[[IO]/;
const OSC_COLOR_RE = /^\x1b\](?:4;\d+|10|11);rgb:[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}\/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)/;

const RESPONSE_FILTERED_RUNTIMES = new Set(["droid", "gemini", "neovate", "opencode"]);
const FOCUS_FILTERED_RUNTIMES = new Set(["codex", "droid", "gemini", "neovate"]);

function normalizedRuntime(runtime: string | undefined): string {
  return String(runtime || "").trim().toLowerCase();
}

function isPotentialDevicePrefix(value: string, filterResponses: boolean, filterFocus: boolean): boolean {
  if (!value.startsWith("\x1b[")) return false;
  const body = value.slice(2);
  if (!body) return filterResponses || filterFocus;
  if (filterFocus && (body === "I" || body === "O")) return true;
  return filterResponses && /^[?>][0-9;]*$/.test(body);
}

function isPotentialOscPrefix(value: string): boolean {
  if (!value.startsWith("\x1b]")) return false;
  const body = value.slice(2);
  if (!body) return true;
  if (/^(?:1|10|11|4(?:;\d*)?)$/.test(body)) return true;
  const colorPrefix = /^(?:4;\d+|10|11);rgb:/.exec(body);
  if (!colorPrefix) return false;
  return /^[0-9a-fA-F]{0,4}(?:\/[0-9a-fA-F]{0,4}){0,2}(?:\x1b)?$/.test(
    body.slice(colorPrefix[0].length),
  );
}

function isPotentialResponsePrefix(value: string, filterResponses: boolean, filterFocus: boolean): boolean {
  return (
    isPotentialDevicePrefix(value, filterResponses, filterFocus) ||
    (filterResponses && isPotentialOscPrefix(value))
  );
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
  const runtimeId = normalizedRuntime(runtime);
  const filterResponses = RESPONSE_FILTERED_RUNTIMES.has(runtimeId);
  const filterFocus = FOCUS_FILTERED_RUNTIMES.has(runtimeId);
  if (!filterResponses && !filterFocus) return { data: chunk, pending: "" };

  const combined = `${previousPending}${chunk || ""}`;
  let data = "";
  let index = 0;

  while (index < combined.length) {
    if (combined[index] !== "\x1b") {
      data += combined[index++];
      continue;
    }

    const remaining = combined.slice(index);
    const deviceMatch = filterResponses ? remaining.match(DEVICE_ATTRIBUTES_RE) : null;
    const focusMatch = filterFocus ? remaining.match(FOCUS_EVENT_RE) : null;
    const oscMatch = filterResponses ? remaining.match(OSC_COLOR_RE) : null;
    const match = deviceMatch || focusMatch || oscMatch;
    if (match) {
      index += match[0].length;
      continue;
    }

    if (isPotentialResponsePrefix(remaining, filterResponses, filterFocus)) {
      return { data, pending: remaining };
    }

    data += combined[index++];
  }

  return { data, pending: "" };
}
