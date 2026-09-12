"""mangle.py's two post-selection heuristics, against hand-built
NormalizedEvent lists -- mirrors test_session_extract_select.py's style.
"""

from __future__ import annotations

from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.mangle import redact_paragraphs, strip_stale_wakeup_tail

_TS = "2026-01-01T00:00:00Z"


def _at(seq, text, kind=EventKind.ASSISTANT_TEXT):
    return NormalizedEvent(seq, f"m{seq}", _TS, kind, text)


def _op(seq, text="real operator input"):
    return NormalizedEvent(seq, f"op{seq}", _TS, EventKind.OPERATOR_TEXT, text)


# --- strip_stale_wakeup_tail ------------------------------------------------

def test_trailing_run_of_two_or_more_is_collapsed_to_the_first():
    events = [
        _at(0, "Doing real work here."),
        _at(1, "This is the stale fallback wakeup I armed earlier -- everything it asks for "
                "already completed in the meantime. Quick confirmation, then done."),
        _at(2, "Confirmed consistent -- nothing stale or missing. No further action needed."),
        _at(3, "This is the same stale fallback message repeating -- already confirmed in the "
                "previous turn. No new action needed."),
    ]
    kept, removed = strip_stale_wakeup_tail(events)
    assert removed == 2
    assert [e.marker for e in kept] == ["m0", "m1"]


def test_a_lone_trailing_match_is_left_alone():
    # Run length 1 -- a single "confirmed, nothing to do" as the genuine
    # final status is still real signal, not repetition; must survive.
    events = [
        _at(0, "Doing real work here."),
        _at(1, "This is the stale fallback wakeup I armed earlier -- already completed. "
                "No further action needed."),
    ]
    kept, removed = strip_stale_wakeup_tail(events)
    assert removed == 0
    assert kept is events


def test_a_matching_block_earlier_in_the_span_is_never_touched():
    # Legitimate mid-session monitoring prose ("Confirmed alive, continuing
    # to wait") must never be stripped just because later, unrelated,
    # genuinely-informative content follows it.
    events = [
        _at(0, "This is a stale fallback wakeup -- everything it asks for has already happened "
                "via the Monitor-tracked path. No further action needed."),
        _at(1, "Merge landed as b3659192, gate now running."),
        _at(2, "Gate finished green across all lanes."),
    ]
    kept, removed = strip_stale_wakeup_tail(events)
    assert removed == 0
    assert kept is events


def test_non_assistant_text_at_the_tail_stops_the_walk():
    events = [
        _at(0, "This is the stale fallback wakeup -- already completed. No further action needed."),
        _at(1, "This is the same stale fallback message repeating. No new action needed."),
        _op(2, "A real operator message, not a wakeup."),
    ]
    kept, removed = strip_stale_wakeup_tail(events)
    assert removed == 0
    assert kept is events


def test_ordinary_checkpoints_never_match():
    events = [
        _at(0, "Confirmed alive -- the whole process tree is present. Continuing to wait."),
        _at(1, "test-runner lane finished (exit 0). Now in the assay lane."),
    ]
    kept, removed = strip_stale_wakeup_tail(events)
    assert removed == 0
    assert kept is events


# --- redact_paragraphs -------------------------------------------------------

def test_matching_paragraph_is_replaced_others_survive():
    text = (
        "[/compact] KEEP: standing /goal is active (set verbatim by the user via /goal "
        "command) -- continue autonomous per-component repair.\n\n"
        "KEEP per-package state:\n- P102: FULLY MERGED AND CLOSED."
    )
    events = [_at(0, text, kind=EventKind.LIFECYCLE_MARKER)]
    kept, redacted = redact_paragraphs(events, ["/goal"])
    assert redacted == 1
    assert "continue autonomous per-component repair" not in kept[0].text
    assert "KEEP per-package state:\n- P102: FULLY MERGED AND CLOSED." in kept[0].text
    assert "[redacted paragraph -- matched --redact-pattern '/goal']" in kept[0].text


def test_no_match_leaves_text_untouched():
    events = [_at(0, "Nothing sensitive in here.\n\nJust normal progress notes.")]
    original_text = events[0].text
    kept, redacted = redact_paragraphs(events, ["/goal"])
    assert redacted == 0
    assert kept[0].text == original_text


def test_multiple_patterns_and_multiple_matching_paragraphs_in_one_event():
    text = "Mentions /goal here.\n\nMentions SECRET_TOKEN here.\n\nNeither here."
    events = [_at(0, text)]
    kept, redacted = redact_paragraphs(events, ["/goal", "SECRET_TOKEN"])
    assert redacted == 2
    assert "Neither here." in kept[0].text
    assert "Mentions /goal here." not in kept[0].text
    assert "Mentions SECRET_TOKEN here." not in kept[0].text


def test_case_insensitive_match():
    events = [_at(0, "The GOAL directive is active.")]
    kept, redacted = redact_paragraphs(events, ["goal"])
    assert redacted == 1
