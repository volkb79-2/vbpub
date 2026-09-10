"""debug_diff.py: the `extract-debug` colored diff between lossless.py's
own dump and a given extract() render. Tests the pure `render_debug`
function directly against small synthetic lossless/extract text pairs --
matches the style of test_session_extract_render.py's hand-built events.
"""

from __future__ import annotations

from nyxloom.session_extract.debug_diff import _note_color, render_debug

_RESET = "\x1b[0m"
_GREY = "\x1b[90m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"


def _lossless(*blocks):
    return "\n\n".join(f"===[{i} | 2026-01-01T00:00:00Z | USER]===\n{b}" for i, b in enumerate(blocks)) + "\n"


def test_identical_content_renders_plain_with_no_color_codes():
    lossless_text = _lossless("hello world")
    extract_text = "OPERATOR: hello world\n"
    text = render_debug(lossless_text, extract_text, use_color=False)
    assert "hello world" in text
    assert _RESET not in text and _CYAN not in text and _GREY not in text


def test_identical_content_gets_no_color_even_when_color_is_on():
    # White == "no color code," not a specific color -- equal blocks are
    # rendered verbatim, un-wrapped, regardless of use_color.
    lossless_text = _lossless("hello world")
    extract_text = "OPERATOR: hello world\n"
    text = render_debug(lossless_text, extract_text, use_color=True)
    assert "\x1b[" not in text.split("\n\n")[0]  # the first (equal) block carries no ANSI codes


def test_dropped_lossless_block_is_wrapped_in_a_grey_gap_note():
    lossless_text = _lossless("kept text", "dropped text")
    extract_text = "OPERATOR: kept text\n"
    text = render_debug(lossless_text, extract_text, use_color=False)
    assert "kept text" in text
    assert ">>> [gap: 1 lossless block dropped]" in text
    assert "dropped text" in text
    assert "<<<" in text
    assert "---" in text


def test_dropped_run_gets_one_gap_note_not_one_per_block():
    lossless_text = _lossless("kept text", "dropped 1", "dropped 2", "dropped 3")
    extract_text = "OPERATOR: kept text\n"
    text = render_debug(lossless_text, extract_text, use_color=False)
    assert text.count(">>> [gap:") == 1
    assert "3 lossless blocks dropped" in text
    assert "dropped 1" in text and "dropped 2" in text and "dropped 3" in text


def test_color_wraps_dropped_content_in_grey_and_gap_note_in_cyan():
    lossless_text = _lossless("kept text", "dropped text")
    extract_text = "OPERATOR: kept text\n"
    text = render_debug(lossless_text, extract_text, use_color=True)
    assert f"{_CYAN}>>> [gap: 1 lossless block dropped]{_RESET}" in text
    assert f"{_GREY}" in text and "dropped text" in text
    assert f"{_CYAN}<<<{_RESET}" in text


def test_render_only_gap_note_colored_cyan():
    lossless_text = _lossless("older text", "kept text")
    extract_text = "[gap: 5 records omitted]\n\n---\n\nOPERATOR: kept text\n"
    text = render_debug(lossless_text, extract_text, use_color=True)
    assert f"{_CYAN}[gap: 5 records omitted]{_RESET}" in text


def test_render_ledger_line_colored_green():
    lossless_text = _lossless("kept text")
    extract_text = "OPERATOR: kept text\n\n---\n\n[files read: a.py] [files edited: b.py]\n"
    text = render_debug(lossless_text, extract_text, use_color=True)
    assert f"{_GREEN}[files read: a.py] [files edited: b.py]{_RESET}" in text


def test_render_ledger_line_plain_when_color_off():
    lossless_text = _lossless("kept text")
    extract_text = "OPERATOR: kept text\n\n---\n\n[files read: a.py]\n"
    text = render_debug(lossless_text, extract_text, use_color=False)
    assert "[files read: a.py]" in text
    assert _GREEN not in text


def test_note_color_returns_none_not_a_falsy_placeholder_for_plain_text():
    # render_debug's own call site only ever checks truthiness (`if
    # color:`), but _note_color's documented return type is `str | None` --
    # worth pinning directly, not only through that one truthiness use.
    assert _note_color("ordinary kept text, no bracket prefix at all") is None
    assert _note_color("[gap: 3 lossless blocks dropped]") == _CYAN
    assert _note_color("[files read: a.py]") == _GREEN
