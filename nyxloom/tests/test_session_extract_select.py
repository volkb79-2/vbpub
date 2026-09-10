"""select.select() windowing tests, against hand-built NormalizedEvent lists
with pre-set checkpoint_score (classifier.py is tested separately) so the
windowing rules themselves are pinned down precisely.
"""

from __future__ import annotations

from nyxloom.session_extract.config import ExtractConfig
from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.select import select

_TS = "2026-01-01T00:00:00Z"


def _op(seq, text="op"):
    return NormalizedEvent(seq, f"op{seq}", _TS, EventKind.OPERATOR_TEXT, text)


def _cp(seq, score=5.0, text=None):
    return NormalizedEvent(seq, f"cp{seq}", _TS, EventKind.ASSISTANT_TEXT, text or f"checkpoint {seq}",
                            checkpoint_score=score)


def _short(seq, text="short"):
    return NormalizedEvent(seq, f"sh{seq}", _TS, EventKind.ASSISTANT_TEXT, text, checkpoint_score=0.0)


def _long(seq, chars=300):
    return NormalizedEvent(seq, f"lg{seq}", _TS, EventKind.ASSISTANT_TEXT, "x" * chars, checkpoint_score=0.0)


def test_long_comment_survives_anywhere_in_the_span():
    # Chronological (oldest->newest): cp_a(5th=oldest), cp_b(4th), long_mid,
    # cp_c(3rd), cp_d(2nd), cp_e(1st=newest). A long comment clears
    # long_comment_chars regardless of how close to the newest checkpoint it
    # sits.
    events = [_cp(0), _cp(1), _long(2, chars=300), _cp(3), _cp(4), _cp(5)]
    kept = select(events, ExtractConfig(max_checkpoints=5, long_comment_chars=180))
    assert any(e.marker == "lg2" for e in kept)


def test_short_procedural_comment_dropped_even_right_next_to_the_newest_checkpoint():
    # Real-excerpt finding (a full, line-by-line replay of every
    # ASSISTANT_TEXT event in a real span against the operator's own
    # hand-curated excerpt, not a sample): short, no-finding-signal
    # procedural lines were dropped even in the very LAST turns of the span,
    # immediately next to the newest checkpoint -- there was no example of
    # recency alone ever rescuing one. This one-liner is a real example from
    # that span ("Real bug confirmed and it's a quick fix. Let me apply it
    # plus clean up the dead imports flagged."), positioned as the very next
    # ASSISTANT_TEXT after the newest checkpoint.
    events = [
        _cp(0), _cp(1), _cp(2), _cp(3), _cp(4),
        NormalizedEvent(5, "proc5", _TS, EventKind.ASSISTANT_TEXT,
                         "Real bug confirmed and it's a quick fix. Let me apply it "
                         "plus clean up the dead imports flagged.", checkpoint_score=0.0),
    ]
    kept = select(events, ExtractConfig(max_checkpoints=5, long_comment_chars=180))
    assert not any(e.marker == "proc5" for e in kept)


def test_short_comment_dropped_and_long_comment_kept_past_the_2nd_oldest_checkpoint():
    # Only 4 checkpoints total (< max_checkpoints=5) so the walk runs to
    # completion without an early max-checkpoints break. Chronological:
    # long_old, short_old, cp1, cp2, cp3, cp4.
    events = [_long(0), _short(1), _cp(2), _cp(3), _cp(4), _cp(5)]
    kept = select(events, ExtractConfig(max_checkpoints=5, long_comment_chars=180))
    kept_markers = {e.marker for e in kept}
    assert "lg0" in kept_markers  # long enough to survive the length filter
    assert "sh1" not in kept_markers  # too short, no finding signal


def test_stops_walk_once_max_checkpoints_reached():
    events = [_op(0), _cp(1), _cp(2), _cp(3), _cp(4), _cp(5), _op(6)]
    kept = select(events, ExtractConfig(max_checkpoints=3))
    kept_markers = {e.marker for e in kept}
    # only the 3 newest checkpoints (3, 4, 5) survive; op0 (older than the
    # walk's stopping point) and cp1/cp2 are never reached.
    assert kept_markers == {"cp3", "cp4", "cp5", "op6"}


def test_operator_and_qa_always_kept_within_the_walked_span():
    qa = NormalizedEvent(1, "qa1", _TS, EventKind.QA_PAIR, "Q=A")
    events = [_op(0), qa, _cp(2)]
    kept = select(events, ExtractConfig(max_checkpoints=5))
    markers = {e.marker for e in kept}
    assert {"op0", "qa1", "cp2"} <= markers


def test_lifecycle_marker_hard_stops_the_walk():
    marker = NormalizedEvent(1, "lc1", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    events = [_op(0), marker, _cp(2)]
    kept = select(events, ExtractConfig(max_checkpoints=5))
    markers = {e.marker for e in kept}
    assert markers == {"cp2", "lc1"}
    assert "op0" not in markers


def test_max_lifecycle_markers_zero_is_the_default_hard_stop():
    marker = NormalizedEvent(1, "lc1", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    events = [_op(0), marker, _cp(2)]
    kept = select(events, ExtractConfig(max_checkpoints=5, max_lifecycle_markers=0))
    markers = {e.marker for e in kept}
    assert markers == {"cp2", "lc1"}
    assert "op0" not in markers


def test_max_lifecycle_markers_one_walks_past_the_first_and_stops_at_the_second():
    # Chronological: op0, lc1 (1st marker), op2 (between the two markers),
    # lc3 (2nd marker), cp4 (newest). max_lifecycle_markers=1 permits
    # walking past lc1 but must stop AT lc3 -- op0 (before both markers)
    # never survives.
    lc1 = NormalizedEvent(1, "lc1", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    lc3 = NormalizedEvent(3, "lc3", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    events = [_op(0), lc1, _op(2, "between the boundaries"), lc3, _cp(4)]
    kept = select(events, ExtractConfig(max_checkpoints=5, max_lifecycle_markers=1))
    markers = {e.marker for e in kept}
    assert markers == {"cp4", "lc3", "op2", "lc1"}
    assert "op0" not in markers


def test_max_lifecycle_markers_negative_one_never_stops_on_a_marker():
    lc1 = NormalizedEvent(1, "lc1", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    lc3 = NormalizedEvent(3, "lc3", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    events = [_op(0), lc1, _op(2), lc3, _cp(4)]
    kept = select(events, ExtractConfig(max_checkpoints=5, max_lifecycle_markers=-1))
    markers = {e.marker for e in kept}
    assert markers == {"cp4", "lc3", "op2", "lc1", "op0"}


def test_max_lifecycle_markers_negative_one_is_still_bounded_by_max_words():
    # "ignore markers, just cap the word budget" -- the operator's stated
    # use case for a one-time manual extraction. max_lifecycle_markers=-1
    # lets the walk pass the marker, but max_words still eventually stops
    # it from reaching all the way back to the oldest event.
    too_old = NormalizedEvent(0, "old0", _TS, EventKind.ASSISTANT_TEXT,
                               "way too old to ever survive the budget", checkpoint_score=0.0)
    big = NormalizedEvent(1, "big1", _TS, EventKind.ASSISTANT_TEXT,
                           " ".join(f"w{i}" for i in range(150)), checkpoint_score=0.0)
    lc2 = NormalizedEvent(2, "lc2", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    newest = _short(3, "newest short")
    events = [too_old, big, lc2, newest]

    kept = select(events, ExtractConfig(max_lifecycle_markers=-1, max_words=100, long_comment_chars=1))
    markers = {e.marker for e in kept}
    assert markers == {"sh3", "lc2", "big1"}  # passed the marker, then the word budget stopped it
    assert "old0" not in markers


def test_short_finding_kept_but_short_procedural_line_dropped():
    # Same 4-checkpoint construction as the drops-short/keeps-long test
    # above, but both candidates are SHORT -- one reports a concrete finding
    # (survives via has_finding_signal even though it's under
    # long_comment_chars), one is purely procedural narration (dropped,
    # same length).
    finding = NormalizedEvent(0, "find0", _TS, EventKind.ASSISTANT_TEXT,
                               "Found it -- a `pgrep` pattern bug.", checkpoint_score=0.0)
    procedural = NormalizedEvent(1, "proc1", _TS, EventKind.ASSISTANT_TEXT,
                                  "Now let's fix that.", checkpoint_score=0.0)
    events = [finding, procedural, _cp(2), _cp(3), _cp(4), _cp(5)]
    kept = select(events, ExtractConfig(max_checkpoints=5, long_comment_chars=180))
    kept_markers = {e.marker for e in kept}
    assert "find0" in kept_markers
    assert "proc1" not in kept_markers


def test_word_budget_trims_the_oldest_end():
    long_text = "word " * 1000  # ~1000 words each
    events = [
        NormalizedEvent(i, f"e{i}", _TS, EventKind.OPERATOR_TEXT, long_text) for i in range(5)
    ]
    kept = select(events, ExtractConfig(max_checkpoints=5, max_words=2500))
    # walking backward from e4: e4, e3, e2 accepted (~3000 words > 2500 -> stop after e2)
    kept_markers = [e.marker for e in kept]
    assert kept_markers[-1] == "e4"  # newest always present
    assert "e0" not in kept_markers  # oldest is what got trimmed
