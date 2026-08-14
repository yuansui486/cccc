"""Small terminal-query responder used by PTY runtimes."""

from __future__ import annotations

import re
from typing import Iterable, Tuple


_QUERY_TAIL_BYTES = 256
_FIXED_QUERY_RESPONSES: Tuple[Tuple[bytes, bytes], ...] = (
    (b"\x1b[6n", b"\x1b[1;1R"),
    (b"\x1b[c", b"\x1b[?1;2c"),
    (b"\x1b[0c", b"\x1b[?1;2c"),
    (b"\x1b[>c", b"\x1b[>0;0;0c"),
    (b"\x1b[>0c", b"\x1b[>0;0;0c"),
)
_OSC_QUERY_RE = re.compile(rb"\x1b\](4;(\d+)|10|11);\?(?:\x07|\x1b\\)")
_OSC_RESPONSE_RE = re.compile(
    rb"\x1b\](?:10|11);rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\)"
)

_XTERM_PALETTE = (
    "000000",
    "cd0000",
    "00cd00",
    "cdcd00",
    "0000ee",
    "cd00cd",
    "00cdcd",
    "e5e5e5",
    "7f7f7f",
    "ff0000",
    "00ff00",
    "ffff00",
    "5c5cff",
    "ff00ff",
    "00ffff",
    "ffffff",
)
_DEFAULT_FOREGROUND = "d4d4d4"
_DEFAULT_BACKGROUND = "1e1e1e"
_TERMINAL_COLORS = {
    "light": ("1e293b", "fafafa"),
    "dark": ("e2e8f0", "0f172a"),
}
_QUERY_FILTER_RUNTIMES = {"codex", "droid", "gemini", "neovate", "opencode"}
_FIXED_TERMINAL_RESPONSES = (
    b"\x1b[1;1R",
    b"\x1b[?1;2c",
    b"\x1b[>0;0;0c",
)


def _osc_rgb(hex_color: str) -> str:
    value = str(hex_color or "000000").strip().lower().lstrip("#")
    if len(value) != 6:
        value = "000000"
    return "/".join(value[offset : offset + 2] * 2 for offset in (0, 2, 4))


def _iter_new_fixed_queries(data: bytes, previous_length: int) -> Iterable[Tuple[bytes, bytes]]:
    for query, response in _FIXED_QUERY_RESPONSES:
        offset = 0
        while True:
            index = data.find(query, offset)
            if index < 0:
                break
            end = index + len(query)
            if end > previous_length:
                yield query, response
            offset = index + 1


def _is_terminal_query_prefix(value: bytes, *, runtime: str) -> bool:
    if runtime not in _QUERY_FILTER_RUNTIMES or not value:
        return False
    fixed = tuple(query for query, _response in _FIXED_QUERY_RESPONSES) + _FIXED_TERMINAL_RESPONSES
    if any(candidate.startswith(value) for candidate in fixed):
        return True
    if value.startswith(b"\x1b]"):
        prefixes = (b"\x1b]4;", b"\x1b]10;", b"\x1b]11;")
        return any(prefix.startswith(value) or value.startswith(prefix) for prefix in prefixes)
    return False


def filter_terminal_query_output(
    previous_pending: bytes,
    chunk: bytes,
    *,
    runtime: str,
) -> tuple[bytes, bytes]:
    """Remove terminal queries/replies from the browser-visible PTY stream.

    Query bytes must still reach ``terminal_query_responses`` so the child can
    receive an immediate answer, but they should never be rendered by xterm.
    ``previous_pending`` contains only an incomplete candidate from the prior
    chunk and is therefore safe to hold until the sequence is complete.
    """

    runtime_id = str(runtime or "").strip().lower()
    if runtime_id not in _QUERY_FILTER_RUNTIMES:
        return bytes(chunk or b""), b""

    data = bytes(previous_pending or b"") + bytes(chunk or b"")
    visible = bytearray()
    index = 0
    fixed = tuple(query for query, _response in _FIXED_QUERY_RESPONSES) + _FIXED_TERMINAL_RESPONSES
    while index < len(data):
        if data[index] != 0x1B:
            visible.append(data[index])
            index += 1
            continue
        remaining = data[index:]
        matched = False
        for sequence in fixed:
            if remaining.startswith(sequence):
                index += len(sequence)
                matched = True
                break
        if matched:
            continue
        osc_match = _OSC_QUERY_RE.match(remaining) or _OSC_RESPONSE_RE.match(remaining)
        if osc_match:
            index += osc_match.end()
            continue
        if _is_terminal_query_prefix(remaining, runtime=runtime_id):
            return bytes(visible), remaining
        visible.append(data[index])
        index += 1
    return bytes(visible), b""


def terminal_query_responses(
    previous_tail: bytes,
    chunk: bytes,
    *,
    runtime: str,
    active_writer: bool,
    color_scheme: str = "dark",
) -> tuple[bytes, list[bytes]]:
    """Return a bounded overlap tail and terminal responses for newly seen queries."""

    prior = bytes(previous_tail or b"")[-_QUERY_TAIL_BYTES:]
    incoming = bytes(chunk or b"")
    if not incoming:
        return prior, []

    data = prior + incoming
    previous_length = len(prior)
    runtime_id = str(runtime or "").strip().lower()
    backend_handles_device_attributes = runtime_id in {"codex", "droid", "gemini", "neovate", "opencode"}
    responses: list[bytes] = []

    for query, response in _iter_new_fixed_queries(data, previous_length):
        is_cursor_report = query == b"\x1b[6n"
        if is_cursor_report and active_writer:
            continue
        if not is_cursor_report and active_writer and not backend_handles_device_attributes:
            continue
        responses.append(response)

    if runtime_id in {"codex", "opencode"}:
        if runtime_id == "codex":
            foreground, background = _TERMINAL_COLORS.get(
                str(color_scheme or "").strip().lower(),
                _TERMINAL_COLORS["dark"],
            )
        else:
            foreground, background = _DEFAULT_FOREGROUND, _DEFAULT_BACKGROUND
        for match in _OSC_QUERY_RE.finditer(data):
            if match.end() <= previous_length:
                continue
            kind = match.group(1).decode("ascii", errors="ignore")
            if kind.startswith("4;"):
                if runtime_id != "opencode":
                    continue
                index_text = (match.group(2) or b"0").decode("ascii", errors="ignore")
                try:
                    index = int(index_text)
                except ValueError:
                    index = -1
                color = _XTERM_PALETTE[index] if 0 <= index < len(_XTERM_PALETTE) else "000000"
                responses.append(f"\x1b]4;{index_text};rgb:{_osc_rgb(color)}\x07".encode("ascii"))
            elif kind == "10":
                terminator = "\x1b\\" if runtime_id == "codex" else "\x07"
                responses.append(f"\x1b]10;rgb:{_osc_rgb(foreground)}{terminator}".encode("ascii"))
            elif kind == "11":
                terminator = "\x1b\\" if runtime_id == "codex" else "\x07"
                responses.append(f"\x1b]11;rgb:{_osc_rgb(background)}{terminator}".encode("ascii"))

    return data[-_QUERY_TAIL_BYTES:], responses
