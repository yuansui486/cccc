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


def terminal_query_responses(
    previous_tail: bytes,
    chunk: bytes,
    *,
    runtime: str,
    active_writer: bool,
) -> tuple[bytes, list[bytes]]:
    """Return a bounded overlap tail and terminal responses for newly seen queries."""

    prior = bytes(previous_tail or b"")[-_QUERY_TAIL_BYTES:]
    incoming = bytes(chunk or b"")
    if not incoming:
        return prior, []

    data = prior + incoming
    previous_length = len(prior)
    runtime_id = str(runtime or "").strip().lower()
    backend_handles_device_attributes = runtime_id in {"droid", "gemini", "neovate", "opencode"}
    responses: list[bytes] = []

    for query, response in _iter_new_fixed_queries(data, previous_length):
        is_cursor_report = query == b"\x1b[6n"
        if is_cursor_report and active_writer:
            continue
        if not is_cursor_report and active_writer and not backend_handles_device_attributes:
            continue
        responses.append(response)

    if runtime_id == "opencode":
        for match in _OSC_QUERY_RE.finditer(data):
            if match.end() <= previous_length:
                continue
            kind = match.group(1).decode("ascii", errors="ignore")
            if kind.startswith("4;"):
                index_text = (match.group(2) or b"0").decode("ascii", errors="ignore")
                try:
                    index = int(index_text)
                except ValueError:
                    index = -1
                color = _XTERM_PALETTE[index] if 0 <= index < len(_XTERM_PALETTE) else "000000"
                responses.append(f"\x1b]4;{index_text};rgb:{_osc_rgb(color)}\x07".encode("ascii"))
            elif kind == "10":
                responses.append(f"\x1b]10;rgb:{_osc_rgb(_DEFAULT_FOREGROUND)}\x07".encode("ascii"))
            elif kind == "11":
                responses.append(f"\x1b]11;rgb:{_osc_rgb(_DEFAULT_BACKGROUND)}\x07".encode("ascii"))

    return data[-_QUERY_TAIL_BYTES:], responses
