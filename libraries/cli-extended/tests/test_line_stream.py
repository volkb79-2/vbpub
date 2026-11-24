from __future__ import annotations

import io

import pytest

from cli_extended.line_stream import LineStreamRenderer

_INFO = "\033[32m[INFO]\033[0m"
_WARN = "\033[33m[WARN]\033[0m"
_ERROR = "\033[31m[ERROR]\033[0m"


class _TTYStream(io.StringIO):
    def isatty(self) -> bool:
        return True


class _FlushTrackingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flush_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()


def _render(
    chunks: tuple[str, ...],
    *,
    color: bool = False,
    json_mode: bool = False,
    tty: bool = False,
) -> str:
    target = _TTYStream() if tty else io.StringIO()
    renderer = LineStreamRenderer(target, color=color, json_mode=json_mode)
    for chunk in chunks:
        renderer.write(chunk)
    renderer.flush()
    return target.getvalue()


@pytest.mark.parametrize(
    ("chunks", "expected"),
    [
        (("[INFO] ready\n",), "[INFO] ready\n"),
        (("[WARN] waiting\n",), "[WARN] waiting\n"),
        (("[ERROR] failed\n",), "[ERROR] failed\n"),
        (
            ("[ERROR] first\n[WARN] second\n[INFO] third\n",),
            "[ERROR] first\n[WARN] second\n[INFO] third\n",
        ),
        (
            ("[ER", "ROR] first\n[WA", "RN] second\n[IN", "FO] third\n"),
            "[ERROR] first\n[WARN] second\n[INFO] third\n",
        ),
        (("ordinary\n[WARN] next",), "ordinary\n[WARN] next"),
        (("[IN", "FO] trailing"), "[INFO] trailing"),
        (("[INFO]\n[WARN]",), "[INFO]\n[WARN]"),
        (("ordinary\r\n[INFO]\rprogress\n",), "ordinary\r\n[INFO]\rprogress\n"),
        (("[INFO]no separator\n[DEBUG] unhandled\n",), "[INFO]no separator\n[DEBUG] unhandled\n"),
        (("building 50%",), "building 50%"),
        (("",), ""),
    ],
)
def test_plain_mode_matches_cmru_severity_stream_contract(chunks, expected):
    assert _render(chunks) == expected


@pytest.mark.parametrize(
    ("tty", "color", "json_mode", "expected"),
    [
        (
            True,
            True,
            False,
            f"{_ERROR} failed\n{_WARN} waiting\n{_INFO} ready\nplain\n"
            "[DEBUG] detail\nlast",
        ),
        (
            True,
            False,
            False,
            "[ERROR] failed\n[WARN] waiting\n[INFO] ready\nplain\n"
            "[DEBUG] detail\nlast",
        ),
        (
            False,
            True,
            False,
            f"{_ERROR} failed\n{_WARN} waiting\n{_INFO} ready\nplain\n"
            "[DEBUG] detail\nlast",
        ),
        (
            False,
            True,
            True,
            "[ERROR] failed\n[WARN] waiting\n[INFO] ready\nplain\n"
            "[DEBUG] detail\nlast",
        ),
    ],
)
def test_exact_policy_goldens_cover_colour_off_redirect_forced_and_json(
    tty, color, json_mode, expected,
):
    message = "[ERROR] failed\n[WARN] waiting\n[INFO] ready\nplain\n[DEBUG] detail\nlast"
    assert _render((message,), color=color, json_mode=json_mode, tty=tty) == expected


def test_severity_is_decorated_only_at_the_start_of_each_line():
    rendered = _render(
        ("[INFO] first\nmid [WARN] middle\n [ERROR] indented\n[ERROR] last\n",),
        color=True,
    )

    assert rendered == (
        f"{_INFO} first\n"
        "mid [WARN] middle\n"
        " [ERROR] indented\n"
        f"{_ERROR} last\n"
    )


def test_write_buffers_a_partial_line_until_newline_or_flush():
    target = io.StringIO()
    renderer = LineStreamRenderer(target, color=True, json_mode=False)

    assert renderer.write("[INFO] partial") == len("[INFO] partial")
    assert target.getvalue() == ""
    renderer.flush()
    assert target.getvalue() == f"{_INFO} partial"


def test_partial_severity_token_is_recognised_when_the_line_completes():
    target = io.StringIO()
    renderer = LineStreamRenderer(target, color=True, json_mode=False)

    assert renderer.write("[IN") == 3
    assert target.getvalue() == ""
    assert renderer.write("FO] ready\n") == len("FO] ready\n")
    assert target.getvalue() == f"{_INFO} ready\n"


def test_write_returns_input_length_after_a_partial_token_before_multiline_text():
    target = io.StringIO()
    renderer = LineStreamRenderer(target, color=True, json_mode=False)

    assert renderer.write("[WA") == len("[WA")
    message = "RN] second\n[ERROR] third"
    assert renderer.write(message) == len(message)
    renderer.flush()

    assert target.getvalue() == f"{_WARN} second\n{_ERROR} third"


def test_flush_discards_incomplete_escape_tails_without_losing_safe_prefixes():
    assert _render(("before\x1b",)) == "before"
    assert _render(("before\x1b[31",)) == "before"
    assert _render(("before\x1b]unterminated title",)) == "before"
    assert _render(("before\x9b31",)) == "before"
    assert _render(("before\x9dunterminated title",)) == "before"


@pytest.mark.parametrize(
    ("text", "plain", "colour"),
    [
        (
            "\x1b[31m[ERROR] forged\x1b[0m\n",
            "[ERROR] forged\n",
            f"{_ERROR} forged\n",
        ),
        (
            "\x1b]8;;https://evil.example\x07[INFO] link\x1b]8;;\x07\n",
            "[INFO] link\n",
            f"{_INFO} link\n",
        ),
        ("a\x00\x01\x02\x08\x0b\x0c\x0e\x1f\x7f\tb\r\n", "a\tb\r\n", "a\tb\r\n"),
        (
            "\x9b31m[WARN] forged\x9c\n\x9dtitle\x9cplain\n",
            "[WARN] forged\nplain\n",
            f"{_WARN} forged\nplain\n",
        ),
        ("\x1bc[INFO] not at start\n", "c[INFO] not at start\n", "c[INFO] not at start\n"),
        (
            "\x1b]0;title\npayload\x07[INFO] ready\n",
            "[INFO] ready\n",
            f"{_INFO} ready\n",
        ),
        (
            "\x1b[31\n[INFO] next\n",
            "\n[INFO] next\n",
            f"\n{_INFO} next\n",
        ),
        (
            "\x1b[31é[INFO] not at start\n",
            "é[INFO] not at start\n",
            "é[INFO] not at start\n",
        ),
        (
            "\x1b[31\x1b[32m[INFO] safe\n",
            "[INFO] safe\n",
            f"{_INFO} safe\n",
        ),
        (
            "\x1b]payload\x1bX\x1b\x1b\\[INFO] ok\n",
            "[INFO] ok\n",
            f"{_INFO} ok\n",
        ),
    ],
)
def test_hostile_input_sequences_are_removed_before_recognition_in_all_modes(
    text, plain, colour,
):
    assert _render((text,), color=False) == plain
    assert _render((text,), color=True) == colour
    assert _render((text,), color=True, json_mode=True) == plain
    assert "\x1b" not in plain
    assert "\x07" not in plain
    assert "\x9b" not in plain
    assert "\x9d" not in plain


def test_flush_renders_each_partial_line_once_and_flushes_the_destination():
    target = _FlushTrackingStream()
    renderer = LineStreamRenderer(target, color=True, json_mode=False)

    renderer.write("[INFO] partial")
    assert target.getvalue() == ""
    renderer.flush()
    assert target.getvalue() == f"{_INFO} partial"
    assert target.flush_count == 1
    renderer.flush()
    assert target.getvalue() == f"{_INFO} partial"
    assert target.flush_count == 2


def test_text_after_flush_continues_the_same_unterminated_logical_line():
    target = io.StringIO()
    renderer = LineStreamRenderer(target, color=True, json_mode=False)

    renderer.write("prefix")
    renderer.flush()
    renderer.write("[INFO] continuation\n")
    renderer.flush()

    assert target.getvalue() == "prefix[INFO] continuation\n"


_CHUNK_INPUT = (
    "plain\r\n"
    "\x1b[31m[ER\x1b[0mROR] bad\x1b]0;title\x07\n"
    "\x9b33m[WARN]\x9c continuing\t\n"
    "tail\x1b]8;;https://example.invalid\x1b\\cut"
)
_CHUNK_EXPECTED = (
    f"plain\r\n{_ERROR} bad\n{_WARN} continuing\t\ntailcut"
)


@pytest.mark.parametrize("split", range(1, len(_CHUNK_INPUT)))
def test_every_single_split_position_renders_identically(split):
    chunks = (_CHUNK_INPUT[:split], _CHUNK_INPUT[split:])

    assert _render(chunks, color=True) == _CHUNK_EXPECTED


@pytest.mark.parametrize(
    "chunk_size",
    [1, 5, 11],
)
def test_multi_split_partitions_render_identically(chunk_size):
    chunks = tuple(
        _CHUNK_INPUT[index : index + chunk_size]
        for index in range(0, len(_CHUNK_INPUT), chunk_size)
    )

    assert _render(chunks, color=True) == _CHUNK_EXPECTED
