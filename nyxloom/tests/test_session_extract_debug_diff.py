"""debug_diff.py: the `extract-debug` colored diff between lossless.py's
own dump and a given extract() render. Tests the pure `render_debug`
function directly against small synthetic lossless/extract text pairs --
matches the style of test_session_extract_render.py's hand-built events.
"""

from __future__ import annotations

from nyxloom.session_extract.config import ExtractConfig
from nyxloom.session_extract.debug_diff import _note_color, render_debug
from nyxloom.session_extract.events import EventKind, NormalizedEvent

_RESET = "\x1b[0m"
_GREY = "\x1b[90m"
_CYAN = "\x1b[36m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"


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


def test_autojunk_false_still_matches_a_rare_repeated_block_past_the_200_element_threshold():
    # `autojunk=False` on the SequenceMatcher call is a real, load-bearing
    # keyword distinct from the leading `None` (isjunk) argument's own
    # proven-equivalent None->[] mutant above: difflib.SequenceMatcher's
    # autojunk heuristic only activates when len(b) >= 200, at which point
    # any element appearing more than ~1% of the time is dropped as
    # "popular" and can no longer seed a match -- so this needs >=200
    # extract blocks with one value repeated often enough to be junked.
    #
    # Fixture: 200 lossless blocks (L0..L99, one "DUP", L100..L198) vs 200
    # extract blocks (N0..N99, "DUP" x10, N100..N189) -- L{i}/N{i} never
    # overlap by construction, so the single lossless "DUP" can ONLY be
    # matched to one of the extract "DUP"s via SequenceMatcher's own
    # popularity-based b2j index, not via any adjacent equal run bleeding
    # into it (verified directly against difflib's own find_longest_match:
    # with autojunk=True this exact fixture collapses to ONE all-200
    # "replace" with zero equal blocks; with autojunk=False the single
    # "DUP" survives as its own equal block between two 100/99-block
    # replaces).
    lossless_blocks = [f"L{i}" for i in range(100)] + ["DUP"] + [f"L{i}" for i in range(100, 199)]
    extract_blocks = [f"N{i}" for i in range(100)] + ["DUP"] * 10 + [f"N{i}" for i in range(100, 190)]
    lossless_text = _lossless(*lossless_blocks)
    extract_text = "\n\n---\n\n".join(f"OPERATOR: {b}" for b in extract_blocks) + "\n"

    text = render_debug(lossless_text, extract_text, use_color=False)

    assert "[gap: 200 lossless blocks dropped]" not in text
    assert "[gap: 100 lossless blocks dropped]" in text
    assert "[gap: 99 lossless blocks dropped]" in text


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


# --- per-block yellow "why was this dropped" reasons (2026-09-11, operator
# direction: "which part of the algorithm made the decision") -- only
# emitted when `config` is passed; every test above (config defaults to
# None) proves that omitting it reproduces this module's pre-2026-09-11
# output byte-for-byte.

def test_no_config_means_no_reason_lines_at_all():
    lossless_text = _lossless("kept text", "short drop")
    extract_text = "OPERATOR: kept text\n"
    text = render_debug(lossless_text, extract_text, use_color=False)
    assert "reason:" not in text


def test_short_drop_with_no_all_events_gets_an_honest_approximate_reason():
    # Not at the leading (i1==0) position -- "kept text" occupies index 0 --
    # so this is a genuine per-content rejection, not a walk-stop. Without
    # `all_events`, marker lookup can't happen at all -- this is the ONE
    # remaining honestly-approximate branch (2026-09-11: no more bare
    # "unclear" -- it names WHERE the loss happened, adapter-vs-selector,
    # even when it can't pin the exact clause, and labels the shape-score
    # re-derivation explicitly as a secondary cross-check).
    lossless_text = _lossless("kept text", "short drop, no signal here")
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig(long_comment_chars=180, checkpoint_score_threshold=3.0)
    text = render_debug(lossless_text, extract_text, use_color=False, config=config)
    assert "unclear" not in text
    assert "no scored event exists for this exact record" in text
    assert "APPROXIMATE cross-check only" in text


def test_short_drop_resolved_via_marker_lookup_gets_a_certain_reason():
    # Same shape as above, but with `all_events` supplied (cmd_extract_debug
    # always does this) -- marker "1" resolves to the real ASSISTANT_TEXT
    # event, so the length verdict is now CERTAIN (computed against the
    # real event's own text/score, not re-derived from lossless's text).
    lossless_text = (
        "===[0 | 2026-01-01T00:00:00Z | USER]===\nkept text\n\n"
        "===[1 | 2026-01-01T00:00:01Z | ASSISTANT]===\nshort drop, no signal here\n"
    )
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig(long_comment_chars=180, checkpoint_score_threshold=3.0)
    all_events = [
        NormalizedEvent(0, "0", "2026-01-01T00:00:00Z", EventKind.OPERATOR_TEXT, "kept text"),
        NormalizedEvent(
            1, "1", "2026-01-01T00:00:01Z", EventKind.ASSISTANT_TEXT, "short drop, no signal here",
            checkpoint_score=0.0,
        ),
    ]
    text = render_debug(
        lossless_text, extract_text, use_color=False, config=config, fmt="claude-code", all_events=all_events,
    )
    assert "reason: the real event for this exact record is 26 chars" in text
    assert "select() correctly dropped it for length" in text
    assert "unclear" not in text


def test_ismeta_header_flag_gets_a_certain_adapter_level_reason():
    lossless_text = (
        "===[0 | 2026-01-01T00:00:00Z | USER]===\nkept text\n\n"
        "===[1 | 2026-01-01T00:00:01Z | USER isMeta]===\nStop hook feedback: [Resume]\n"
    )
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig()
    text = render_debug(lossless_text, extract_text, use_color=False, config=config, fmt="claude-code")
    assert "reason: adapters/claude_code.py's parse() drops this record unconditionally" in text
    assert "isMeta=true" in text


def test_thinking_block_without_include_thinking_gets_a_certain_reason():
    lossless_text = (
        "===[0 | 2026-01-01T00:00:00Z | USER]===\nkept text\n\n"
        "===[1 | 2026-01-01T00:00:01Z | ASSISTANT thinking]===\npondering...\n"
    )
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig(include_thinking=False)
    text = render_debug(lossless_text, extract_text, use_color=False, config=config, fmt="claude-code")
    assert "reason: THINKING content" in text
    assert "--include-thinking" in text


def test_marker_present_in_kept_set_is_a_formatting_mismatch_not_a_drop():
    # The real event for marker "1" DID survive select() (it's an
    # OPERATOR_TEXT event, always kept) but lossless's raw rendering
    # doesn't textually match extract's rendering closely enough for
    # SequenceMatcher to call it "equal" -- this must be reported as a
    # rendering artifact, never as a selection decision.
    lossless_text = (
        "===[0 | 2026-01-01T00:00:00Z | USER]===\nkept text\n\n"
        "===[1 | 2026-01-01T00:00:01Z | USER]===\nsomewhat different rendering of the same turn\n"
    )
    extract_text = "OPERATOR: kept text\n\n---\n\nOPERATOR: totally reformatted turn text\n"
    config = ExtractConfig()
    all_events = [
        NormalizedEvent(0, "0", "2026-01-01T00:00:00Z", EventKind.OPERATOR_TEXT, "kept text"),
        NormalizedEvent(1, "1", "2026-01-01T00:00:01Z", EventKind.OPERATOR_TEXT, "totally reformatted turn text"),
    ]
    text = render_debug(
        lossless_text, extract_text, use_color=False, config=config, fmt="claude-code", all_events=all_events,
    )
    assert "reason: NOT actually a selection drop" in text
    assert "formatting mismatch, not a content decision" in text


def test_detected_checkpoint_gets_a_blue_tag_on_a_kept_block():
    lossless_text = "===[1 | 2026-01-01T00:00:01Z | ASSISTANT]===\n## Where things stand\nDone.\n"
    extract_text = "## Where things stand\nDone.\n"
    config = ExtractConfig(checkpoint_score_threshold=3.0)
    all_events = [
        NormalizedEvent(
            1, "1", "2026-01-01T00:00:01Z", EventKind.ASSISTANT_TEXT, "## Where things stand\nDone.",
            checkpoint_score=5.0,
        ),
    ]
    text = render_debug(
        lossless_text, extract_text, use_color=False, config=config, fmt="claude-code", all_events=all_events,
    )
    assert "[checkpoint detected: score 5.0 >= threshold 3.0]" in text


def test_detected_checkpoint_never_reached_still_gets_a_blue_tag_on_a_grey_block():
    lossless_text = (
        "===[1 | 2026-01-01T00:00:01Z | ASSISTANT]===\n## Old checkpoint\nDone.\n\n"
        "===[2 | 2026-01-01T00:00:02Z | USER]===\nkept text\n"
    )
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig(checkpoint_score_threshold=3.0)
    all_events = [
        NormalizedEvent(
            1, "1", "2026-01-01T00:00:01Z", EventKind.ASSISTANT_TEXT, "## Old checkpoint\nDone.",
            checkpoint_score=5.0,
        ),
        NormalizedEvent(2, "2", "2026-01-01T00:00:02Z", EventKind.OPERATOR_TEXT, "kept text"),
    ]
    text = render_debug(
        lossless_text, extract_text, use_color=False, config=config, fmt="claude-code", all_events=all_events,
    )
    assert "reason: never reached by the walk" in text
    assert "[checkpoint detected: score 5.0 >= threshold 3.0]" in text


def test_reason_line_colored_yellow_when_color_is_on():
    lossless_text = _lossless("kept text", "short drop")
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig()
    text = render_debug(lossless_text, extract_text, use_color=True, config=config)
    assert f"{_YELLOW}reason:" in text
    assert f"{_RESET}" in text


def test_api_error_drop_gets_its_own_reason():
    lossless_text = _lossless("kept text", "[API ERROR: rate_limit] You've hit your session limit")
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig(hide_api_errors=True)
    text = render_debug(lossless_text, extract_text, use_color=False, config=config)
    assert "reason: matched adapters/claude_code.py's own [API ERROR: ...] tag" in text


def test_leading_gap_gets_one_reason_line_not_one_per_block():
    # Nothing kept before this run -- extract_text has no equal content at
    # all before the dropped span, so i1==0 on the very first opcode.
    lossless_text = _lossless("old drop 1", "old drop 2", "old drop 3")
    extract_text = "OPERATOR: something else entirely, unrelated to the drops above\n"
    config = ExtractConfig()
    text = render_debug(lossless_text, extract_text, use_color=False, config=config)
    assert text.count("reason: never reached by the walk") == 1
    assert "old drop 1" in text and "old drop 2" in text and "old drop 3" in text


def test_claude_code_harness_tag_only_block_gets_the_precise_reason(tmp_path):
    lossless_text = _lossless(
        "kept text",
        "<task-notification><task-id>x</task-id><summary>bg event</summary></task-notification>",
    )
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig()
    text = render_debug(lossless_text, extract_text, use_color=False, config=config, fmt="claude-code")
    assert "reason: pure harness-tag framing" in text
    assert "unclear" not in text.split("reason: pure harness-tag framing")[0].split("\n")[-1]


def test_harness_tag_check_is_skipped_for_a_non_claude_code_format():
    # Same content as above, but fmt="codex" -- the claude-code-specific
    # harness-tag check must not fire for a format it doesn't apply to.
    lossless_text = _lossless(
        "kept text",
        "<task-notification><task-id>x</task-id></task-notification>",
    )
    extract_text = "OPERATOR: kept text\n"
    config = ExtractConfig()
    text = render_debug(lossless_text, extract_text, use_color=False, config=config, fmt="codex")
    assert "pure harness-tag framing" not in text
    assert "reason:" in text  # still gets SOME reason, just not the claude-code-specific one


def test_note_color_returns_none_not_a_falsy_placeholder_for_plain_text():
    # render_debug's own call site only ever checks truthiness (`if
    # color:`), but _note_color's documented return type is `str | None` --
    # worth pinning directly, not only through that one truthiness use.
    assert _note_color("ordinary kept text, no bracket prefix at all") is None
    assert _note_color("[gap: 3 lossless blocks dropped]") == _CYAN
    assert _note_color("[files read: a.py]") == _GREEN
