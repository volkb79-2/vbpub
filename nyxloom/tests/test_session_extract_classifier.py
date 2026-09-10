"""classifier.score_events tests, covering the four content families mined
from real historical checkpoint messages plus the "followed by a pause"
structural signal.
"""

from __future__ import annotations

from nyxloom.session_extract.classifier import has_finding_signal, score_events
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


def test_long_shapeless_text_does_not_score_as_a_checkpoint_on_length_alone():
    # Adversarial-review finding: an earlier revision had an undocumented
    # length-based floor that could push a long message over the
    # checkpoint threshold with zero header/closure/meta/direct-address
    # signal -- directly contradicting this module's own thesis. A long
    # narration with no checkpoint shape must score near zero, same as a
    # short one.
    events = [_asst(0, "blah " * 1000)]  # ~5000 chars, no shape signal at all
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


def test_followed_by_more_assistant_text_gets_no_pause_bonus():
    # Two assistant turns back to back (no real pause in between) --
    # the lookahead must stop at the next ASSISTANT_TEXT without granting
    # the "followed_by_pause" bonus, since nothing paused for input.
    bare = [_asst(0, "some assistant text with no shape signal at all")]
    score_events(bare)
    events = [
        _asst(0, "some assistant text with no shape signal at all"),
        _asst(1, "more assistant text"),
    ]
    score_events(events)
    assert events[0].checkpoint_score == bare[0].checkpoint_score


def test_followed_by_lifecycle_marker_gets_no_pause_bonus():
    marker = NormalizedEvent(1, "lc1", _TS, EventKind.LIFECYCLE_MARKER, "[compact boundary]")
    bare = [_asst(0, "some assistant text with no shape signal at all")]
    score_events(bare)
    events = [_asst(0, "some assistant text with no shape signal at all"), marker]
    score_events(events)
    assert events[0].checkpoint_score == bare[0].checkpoint_score


# has_finding_signal: real examples mined by comparing tool output against
# the operator's own hand-curated excerpt of a real session -- these two
# short one-liners were, respectively, kept and dropped from that excerpt.
def test_finding_opener_is_a_signal():
    assert has_finding_signal(
        "Found it -- a pgrep -f '/apt-cacher-ng ' pattern bug: a bare-command "
        "invocation doesn't put a leading / in argv[0]."
    )


def test_purely_procedural_line_is_not_a_signal():
    assert not has_finding_signal("Now let's fix the detection to match on process name instead.")


def test_code_reference_is_a_signal():
    assert has_finding_signal("Let me check `config.py` for the existing pattern.")


def test_bare_backtick_word_is_not_a_signal():
    assert not has_finding_signal("Let me check `config` for the existing pattern.")


def test_filename_mention_is_a_signal():
    assert has_finding_signal("Fixed in ensure_apt_cache.py, verified.")
