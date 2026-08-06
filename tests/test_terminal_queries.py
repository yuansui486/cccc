from no1.runners.terminal_queries import terminal_query_responses


def _feed(chunks: list[bytes], *, runtime: str = "opencode", active_writer: bool = False) -> list[bytes]:
    tail = b""
    responses: list[bytes] = []
    for chunk in chunks:
        tail, current = terminal_query_responses(
            tail,
            chunk,
            runtime=runtime,
            active_writer=active_writer,
        )
        responses.extend(current)
    return responses


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


def test_other_runtime_leaves_queries_to_active_writer() -> None:
    assert _feed([b"\x1b[6n\x1b[c"], runtime="codex", active_writer=True) == []


def test_pending_writer_is_not_treated_as_active() -> None:
    assert _feed([b"\x1b[6n"], runtime="codex", active_writer=False) == [b"\x1b[1;1R"]
