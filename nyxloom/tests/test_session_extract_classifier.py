"""classifier.score_events tests, covering the four content families mined
from real historical checkpoint messages plus the "followed by a pause"
structural signal.
"""

from __future__ import annotations

from nyxloom.session_extract.classifier import score_events
from nyxloom.session_extract.events import EventKind, NormalizedEvent

_TS = "2026-01-01T00:00:00Z"


def _asst(seq, text):
    return NormalizedEvent(seq, f"e{seq}", _TS, EventKind.ASSISTANT_TEXT, text)


def _op(seq, text="proceed"):
    return NormalizedEvent(seq, f"e{seq}", _TS, EventKind.OPERATOR_TEXT, text)


def test_markdown_header_scores_high():
    events = [_asst(0, "## Where things actually stand\n\nsome prose")]
    score_events(events)
    assert events[0].checkpoint_score >= 3.0


def test_closure_declarative_scores():
    events = [_asst(0, "Done -- everything landed, main clean at `abc123`.")]
    score_events(events)
    assert events[0].checkpoint_score >= 2.0


def test_meta_compaction_marker_scores():
    events = [_asst(0, "Given we're at 67% context, here's a self-contained compaction prompt: /compact ...")]
    score_events(events)
    assert events[0].checkpoint_score >= 2.0


def test_direct_address_opener_scores():
    events = [_asst(0, "Short answer: yes, and here's why.")]
    score_events(events)
    assert events[0].checkpoint_score >= 1.0


def test_plain_narration_scores_near_zero():
    events = [_asst(0, "Let me check that.")]
    score_events(events)
    assert events[0].checkpoint_score < 1.0


def test_followed_by_operator_pause_adds_score():
    bare = [_asst(0, "some assistant text with no shape signal at all")]
    score_events(bare)
    followed = [_asst(0, "some assistant text with no shape signal at all"), _op(1)]
    score_events(followed)
    assert followed[0].checkpoint_score > bare[0].checkpoint_score


def test_only_assistant_text_events_get_scored():
    events = [_op(0)]
    score_events(events)
    assert events[0].checkpoint_score is None
