from no1.runners.terminal_queries import filter_terminal_query_output, terminal_query_responses
from no1.daemon.terminal_theme import TERMINAL_COLOR_SCHEME_ENV, normalize_terminal_color_scheme, with_terminal_color_scheme


def _feed(
    chunks: list[bytes],
    *,
    runtime: str = "opencode",
    active_writer: bool = False,
    color_scheme: str = "dark",
) -> list[bytes]:
    tail = b""
    responses: list[bytes] = []
    for chunk in chunks:
        tail, current = terminal_query_responses(
            tail,
            chunk,
            runtime=runtime,
            active_writer=active_writer,
            color_scheme=color_scheme,
        )
        responses.extend(current)
    return responses


def test_terminal_color_scheme_is_session_scoped_and_defaults_dark() -> None:
    assert normalize_terminal_color_scheme("light") == "light"
    assert normalize_terminal_color_scheme("invalid") == "dark"
    assert with_terminal_color_scheme({"OTHER": "1"}, "light") == {
        "OTHER": "1",
        TERMINAL_COLOR_SCHEME_ENV: "light",
    }


def test_opencode_answers_osc_queries_across_chunks() -> None:
    responses = _feed(
        [
            b"before\x1b]4;12;?\x1b",
            b"\\\x1b]10;?\x07\x1b]11;?",
            b"\x1b\\after",
        ]
    )

    assert responses == [
        b"\x1b]4;12;rgb:5c5c/5c5c/ffff\x07",
        b"\x1b]10;rgb:d4d4/d4d4/d4d4\x07",
        b"\x1b]11;rgb:1e1e/1e1e/1e1e\x07",
    ]


def test_previous_overlap_does_not_repeat_complete_query() -> None:
    tail, first = terminal_query_responses(
        b"",
        b"\x1b]10;?\x07",
        runtime="opencode",
        active_writer=False,
    )
    _tail, second = terminal_query_responses(
        tail,
        b"ordinary output",
        runtime="opencode",
        active_writer=False,
    )

    assert len(first) == 1
    assert second == []


def test_opencode_leaves_cursor_query_to_active_writer_but_answers_da() -> None:
    assert _feed([b"\x1b[6n\x1b[>0c"], active_writer=True) == [
        b"\x1b[>0;0;0c",
    ]


def test_codex_answers_device_attributes_with_or_without_active_writer() -> None:
    expected = [b"\x1b[?1;2c", b"\x1b[>0;0;0c"]
    query = b"\x1b[c\x1b[>0c"

    assert _feed([query], runtime="codex", active_writer=False) == expected
    assert _feed([query], runtime="codex", active_writer=True) == expected


def test_codex_leaves_cursor_query_to_active_writer() -> None:
    assert _feed([b"\x1b[6n"], runtime="codex", active_writer=True) == []


def test_pending_writer_is_not_treated_as_active() -> None:
    assert _feed([b"\x1b[6n"], runtime="codex", active_writer=False) == [b"\x1b[1;1R"]


def test_codex_answers_light_and_dark_color_queries_with_active_writer() -> None:
    query = b"\x1b]10;?\x1b\\\x1b]11;?\x07"

    assert _feed([query], runtime="codex", active_writer=True, color_scheme="light") == [
        b"\x1b]10;rgb:1e1e/2929/3b3b\x1b\\",
        b"\x1b]11;rgb:fafa/fafa/fafa\x1b\\",
    ]
    assert _feed([query], runtime="codex", active_writer=True, color_scheme="dark") == [
        b"\x1b]10;rgb:e2e2/e8e8/f0f0\x1b\\",
        b"\x1b]11;rgb:0f0f/1717/2a2a\x1b\\",
    ]


def test_codex_answers_color_queries_without_active_writer() -> None:
    query = b"\x1b]10;?\x1b\\\x1b]11;?\x07"

    assert _feed([query], runtime="codex", active_writer=False, color_scheme="light") == [
        b"\x1b]10;rgb:1e1e/2929/3b3b\x1b\\",
        b"\x1b]11;rgb:fafa/fafa/fafa\x1b\\",
    ]


def test_codex_answers_fragmented_color_queries_once() -> None:
    responses = _feed(
        [b"\x1b]10;?\x1b", b"\\\x1b]11;", b"?\x07ordinary output"],
        runtime="codex",
        active_writer=True,
        color_scheme="light",
    )

    assert responses == [
        b"\x1b]10;rgb:1e1e/2929/3b3b\x1b\\",
        b"\x1b]11;rgb:fafa/fafa/fafa\x1b\\",
    ]


def test_codex_query_output_is_hidden_from_browser_across_chunks() -> None:
    pending = b""
    visible = []
    for chunk in (b"before\x1b]10;?\x1b", b"\\after\x1b[?1;", b"2cend"):
        output, pending = filter_terminal_query_output(pending, chunk, runtime="codex")
        visible.append(output)

    assert b"".join(visible) == b"beforeafterend"
    assert pending == b""


def test_non_query_terminal_output_is_preserved() -> None:
    output, pending = filter_terminal_query_output(b"", b"\x1b[Ahello\r", runtime="codex")
    assert output == b"\x1b[Ahello\r"
    assert pending == b""
