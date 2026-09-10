"""render.py: output formatting + the embedded end-of-session marker that
delta extraction (--since-file) reads back. classifier/select are exercised
via hand-built NormalizedEvent lists, same style as test_session_extract_select.py.
"""

from __future__ import annotations

import json

from nyxloom.session_extract.events import EventKind, NormalizedEvent
from nyxloom.session_extract.render import MARKER_FOOTER_RE, render_json, render_text

_TS = "2026-01-01T00:00:00Z"


def _ev(kind, text, seq=0, marker="m0", score=None):
    return NormalizedEvent(seq, marker, _TS, kind, text, checkpoint_score=score)


def test_render_text_labels_each_kind():
    events = [
        _ev(EventKind.OPERATOR_TEXT, "do the thing", marker="op1"),
        _ev(EventKind.QA_PAIR, "Q=A", marker="qa1"),
        _ev(EventKind.LIFECYCLE_MARKER, "[compact boundary]", marker="lc1"),
        _ev(EventKind.ASSISTANT_TEXT, "checkpoint prose", marker="cp1", score=5.0),
        _ev(EventKind.ASSISTANT_TEXT, "plain prose", marker="as1", score=0.0),
    ]
    text = render_text(events, checkpoint_threshold=3.0, fmt="claude-code", last_marker=None)
    assert "OPERATOR" in text and "do the thing" in text
    assert "Q&A" in text and "Q=A" in text
    assert "LIFECYCLE" in text and "[compact boundary]" in text
    assert "ASSISTANT (checkpoint)" in text and "checkpoint prose" in text
    # a non-checkpoint ASSISTANT_TEXT is labeled plain "ASSISTANT", not "(checkpoint)"
    plain_block = next(block for block in text.split("---") if "plain prose" in block)
    assert "ASSISTANT (checkpoint)" not in plain_block
    assert "ASSISTANT" in plain_block


def test_render_text_no_marker_omits_footer():
    events = [_ev(EventKind.OPERATOR_TEXT, "hi")]
    text = render_text(events, checkpoint_threshold=3.0, fmt="claude-code", last_marker=None)
    assert "nyxloom-extract" not in text
    assert MARKER_FOOTER_RE.search(text) is None


def test_render_text_embeds_marker_footer():
    events = [_ev(EventKind.OPERATOR_TEXT, "hi")]
    text = render_text(events, checkpoint_threshold=3.0, fmt="claude-code", last_marker="abc-123")
    m = MARKER_FOOTER_RE.search(text)
    assert m is not None
    assert m.group(1) == "claude-code"
    assert m.group(2) == "abc-123"


def test_render_json_shape_and_checkpoint_flag():
    events = [
        _ev(EventKind.ASSISTANT_TEXT, "checkpoint prose", marker="cp1", score=5.0),
        _ev(EventKind.ASSISTANT_TEXT, "plain prose", marker="as1", score=0.0),
        _ev(EventKind.OPERATOR_TEXT, "hi", marker="op1"),
    ]
    payload = json.loads(render_json(events, checkpoint_threshold=3.0, fmt="codex", last_marker="xyz"))
    assert payload["format"] == "codex"
    assert payload["last_marker"] == "xyz"
    by_marker = {e["marker"]: e for e in payload["events"]}
    assert by_marker["cp1"]["checkpoint"] is True
    assert by_marker["as1"]["checkpoint"] is False
    # checkpoint is always False for non-ASSISTANT_TEXT kinds, even if a
    # score were somehow set
    assert by_marker["op1"]["checkpoint"] is False


def test_render_json_last_marker_none_is_null():
    events = [_ev(EventKind.OPERATOR_TEXT, "hi")]
    payload = json.loads(render_json(events, checkpoint_threshold=3.0, fmt="claude-code", last_marker=None))
    assert payload["last_marker"] is None
