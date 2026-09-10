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


def test_recent_window_keeps_short_comments_newer_than_second_oldest_checkpoint():
    # Chronological (oldest->newest): cp_a(5th=oldest), cp_b(4th=2nd-oldest),
    # short_recent, cp_c(3rd), cp_d(2nd), cp_e(1st=newest). short_recent is
    # NEWER than the 2nd-oldest checkpoint (cp_b) -> still "recent history":
    # kept regardless of length.
    events = [_cp(0), _cp(1), _short(2), _cp(3), _cp(4), _cp(5)]
    kept = select(events, ExtractConfig(max_checkpoints=5, long_comment_chars=180))
    assert any(e.marker == "sh2" for e in kept)


def test_older_window_drops_short_but_keeps_long_past_second_oldest_checkpoint():
    # Only 4 checkpoints total (< max_checkpoints=5) so the walk runs to
    # completion without an early max-checkpoints break. Chronological:
    # long_old, short_old, cp1, cp2, cp3, cp4. Walking backward, cp4..cp1
    # are found first (checkpoints_found reaches 4 == second_oldest_rank),
    # flipping into the older window BEFORE short_old/long_old are reached.
    events = [_long(0), _short(1), _cp(2), _cp(3), _cp(4), _cp(5)]
    kept = select(events, ExtractConfig(max_checkpoints=5, long_comment_chars=180))
    kept_markers = {e.marker for e in kept}
    assert "lg0" in kept_markers  # long enough to survive the older-window filter
    assert "sh1" not in kept_markers  # too short once past the 2nd-oldest checkpoint


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
