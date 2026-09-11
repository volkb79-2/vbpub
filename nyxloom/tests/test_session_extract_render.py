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


def test_render_text_prefixes_operator_text_only():
    # 2026-09-10, operator feedback: timestamps and a per-block "## [ts]
    # LABEL" header were measured as pure bloat -- the one thing worth
    # keeping inline is which lines are the operator's own words. See
    # render.py's module docstring for the full rationale. Prefix word
    # chosen as "OPERATOR: " (not "USER: ") to match EventKind.OPERATOR_TEXT's
    # own name -- a later same-day correction.
    #
    # QA_PAIR is deliberately NOT blanket-prefixed here (a second later-
    # same-day correction, operator-caught against a real rendered session):
    # once claude_code.py's _format_qa_pairs started embedding its own
    # OPERATOR: label(s) directly in the text (correctly placed before each
    # answer, never before the question), render.py ALSO prefixing the kind
    # produced a duplicate, misplaced "OPERATOR: <question>" -- see
    # test_render_text_does_not_double_prefix_a_preformatted_qa_pair below
    # for the regression this exact bug shape is pinned against.
    events = [
        _ev(EventKind.OPERATOR_TEXT, "do the thing", marker="op1"),
        _ev(EventKind.QA_PAIR, "Q?\n\nOPERATOR: A", marker="qa1"),
        _ev(EventKind.LIFECYCLE_MARKER, "[compact boundary]", marker="lc1"),
        _ev(EventKind.ASSISTANT_TEXT, "checkpoint prose", marker="cp1", score=5.0),
        _ev(EventKind.ASSISTANT_TEXT, "plain prose", marker="as1", score=0.0),
    ]
    text = render_text(events, fmt="claude-code", last_marker=None)
    assert "OPERATOR: do the thing" in text
    # the QA_PAIR event's own embedded label survives verbatim...
    assert "OPERATOR: A" in text
    # ...but render.py added no SECOND prefix in front of the question
    assert "OPERATOR: Q?" not in text
    # everything else renders bare -- no header, no label, no OPERATOR: prefix
    assert "[compact boundary]" in text and "OPERATOR: [compact boundary]" not in text
    assert "checkpoint prose" in text and "OPERATOR: checkpoint prose" not in text
    assert "plain prose" in text and "OPERATOR: plain prose" not in text
    # no timestamp, no "##" header, no checkpoint/kind label anywhere
    assert _TS not in text
    assert "##" not in text
    assert "ASSISTANT" not in text
    assert "LIFECYCLE" not in text
    # exactly the two OPERATOR: occurrences (one render.py prefix, one the
    # QA_PAIR event's own embedded label), nothing else says "OPERATOR"
    assert text.count("OPERATOR") == 2


def test_render_text_does_not_double_prefix_a_preformatted_qa_pair():
    # Regression for the real bug this replaces: a QA_PAIR event's text is
    # ALREADY the fully-formatted question+bullets+"OPERATOR: <answer>"
    # block claude_code.py's _format_qa_pairs produces. render.py must
    # render it completely unprefixed -- adding its own blanket "OPERATOR: "
    # in front duplicated the label onto the QUESTION line instead of the
    # answer, and couldn't express more than one label for a multi-question
    # batch either way.
    formatted = "Pick one?\n- A\n- B\n\nOPERATOR: B"
    events = [_ev(EventKind.QA_PAIR, formatted, marker="qa1")]
    text = render_text(events, fmt="claude-code", last_marker=None)
    assert formatted in text
    assert "OPERATOR: Pick one?" not in text
    assert text.count("OPERATOR: ") == 1


def test_render_text_no_marker_omits_footer():
    events = [_ev(EventKind.OPERATOR_TEXT, "hi")]
    text = render_text(events, fmt="claude-code", last_marker=None)
    assert "nyxloom-extract" not in text
    assert MARKER_FOOTER_RE.search(text) is None


def test_render_text_embeds_marker_footer():
    events = [_ev(EventKind.OPERATOR_TEXT, "hi")]
    text = render_text(events, fmt="claude-code", last_marker="abc-123")
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


# --- gap_after / walk_stopped_because rendering (E-011) ------------------

def test_render_text_shows_gap_note_between_two_events():
    older = _ev(EventKind.OPERATOR_TEXT, "older text", marker="op0")
    older.meta["gap_after"] = "5"
    newer = _ev(EventKind.OPERATOR_TEXT, "newer text", marker="op1")
    text = render_text([older, newer], fmt="claude-code", last_marker=None)
    assert "[gap: 5 records omitted" in text
    # the gap note sits between the two real blocks, not before/after both
    assert text.index("older text") < text.index("[gap: 5") < text.index("newer text")


def test_render_text_gap_note_singular_unit_for_one():
    ev = _ev(EventKind.OPERATOR_TEXT, "x")
    ev.meta["gap_after"] = "1"
    text = render_text([ev], fmt="claude-code", last_marker=None, min_gap_to_annotate=1)
    assert "[gap: 1 record omitted" in text
    assert "1 records" not in text


def test_render_text_suppresses_gaps_below_the_threshold_by_default():
    # Real-data validation (dstdns 8ebff140) found the default (unfiltered)
    # behavior added dozens of 1-2-record notes, inflating output ~17% over
    # max_words with mostly-uninformative noise -- default threshold is 3.
    ev = _ev(EventKind.OPERATOR_TEXT, "x")
    ev.meta["gap_after"] = "2"
    text = render_text([ev], fmt="claude-code", last_marker=None)
    assert "[gap:" not in text


def test_render_text_min_gap_to_annotate_is_overridable():
    ev = _ev(EventKind.OPERATOR_TEXT, "x")
    ev.meta["gap_after"] = "2"
    text = render_text([ev], fmt="claude-code", last_marker=None, min_gap_to_annotate=2)
    assert "[gap: 2 records omitted" in text


def test_render_text_no_gap_note_when_meta_absent():
    events = [_ev(EventKind.OPERATOR_TEXT, "hi")]
    text = render_text(events, fmt="claude-code", last_marker=None)
    assert "[gap:" not in text


def test_render_text_shows_stop_reason_before_the_oldest_event():
    oldest = _ev(EventKind.OPERATOR_TEXT, "oldest kept text", marker="op0")
    oldest.meta["walk_stopped_because"] = "max_words"
    text = render_text([oldest], fmt="claude-code", last_marker=None)
    assert "older session content exists but was not included" in text
    assert "word budget reached" in text
    assert text.index("older session content exists") < text.index("oldest kept text")


def test_render_text_no_stop_reason_note_when_meta_absent():
    events = [_ev(EventKind.OPERATOR_TEXT, "hi")]
    text = render_text(events, fmt="claude-code", last_marker=None)
    assert "older session content exists" not in text


def test_render_text_stop_reason_handles_empty_event_list():
    text = render_text([], fmt="claude-code", last_marker=None)
    assert "older session content exists" not in text


def test_render_json_carries_gap_after_and_stop_reason():
    oldest = _ev(EventKind.OPERATOR_TEXT, "oldest", marker="op0")
    oldest.meta["walk_stopped_because"] = "max_checkpoints"
    oldest.meta["gap_after"] = "3"
    newer = _ev(EventKind.OPERATOR_TEXT, "newer", marker="op1")
    payload = json.loads(render_json([oldest, newer], checkpoint_threshold=3.0, fmt="claude-code", last_marker=None))
    assert payload["stop_reason"] == "max_checkpoints"
    by_marker = {e["marker"]: e for e in payload["events"]}
    assert by_marker["op0"]["gap_after"] == 3
    assert by_marker["op1"]["gap_after"] == 0


def test_render_json_stop_reason_null_when_no_events():
    payload = json.loads(render_json([], checkpoint_threshold=3.0, fmt="claude-code", last_marker=None))
    assert payload["stop_reason"] is None


# --- gap_note_show_marker (2026-09-10 operator feedback: default note was ---
# --- too verbose once it repeated dozens of times; opt into a marker      ---
# --- reference instead of a boilerplate explanation)                     ---

def test_render_text_default_gap_note_has_no_boilerplate_explanation():
    ev = _ev(EventKind.OPERATOR_TEXT, "x")
    ev.meta["gap_after"] = "5"
    text = render_text([ev], fmt="claude-code", last_marker=None)
    assert text.count("[gap: 5 records omitted]") == 1
    assert "short/procedural" not in text
    assert "not kept" not in text


def test_render_text_gap_note_show_marker_names_the_adapter_marker():
    ev = _ev(EventKind.OPERATOR_TEXT, "x", marker="op-uuid-123")
    ev.meta["gap_after"] = "20"
    text = render_text([ev], fmt="claude-code", last_marker=None, show_gap_marker=True)
    assert "[gap: 20 records omitted -- raw log continues after marker op-uuid-123]" in text


def test_render_text_gap_note_show_marker_off_by_default():
    ev = _ev(EventKind.OPERATOR_TEXT, "x", marker="op-uuid-123")
    ev.meta["gap_after"] = "20"
    text = render_text([ev], fmt="claude-code", last_marker=None)
    assert "op-uuid-123" not in text


def test_render_text_stop_reason_show_marker_names_the_oldest_events_marker():
    oldest = _ev(EventKind.OPERATOR_TEXT, "oldest kept text", marker="op-uuid-000")
    oldest.meta["walk_stopped_because"] = "max_words"
    text = render_text([oldest], fmt="claude-code", last_marker=None, show_gap_marker=True)
    assert "raw log continues before marker op-uuid-000" in text
    assert "word budget reached" in text  # the explanation is still present, just extended


def test_render_text_stop_reason_show_marker_off_by_default():
    oldest = _ev(EventKind.OPERATOR_TEXT, "oldest kept text", marker="op-uuid-000")
    oldest.meta["walk_stopped_because"] = "max_words"
    text = render_text([oldest], fmt="claude-code", last_marker=None)
    assert "op-uuid-000" not in text


# --- ledger param (E-012) -------------------------------------------------

def test_render_text_ledger_none_by_default_inserts_nothing():
    events = [_ev(EventKind.OPERATOR_TEXT, "do the thing", marker="op1")]
    text = render_text(events, fmt="claude-code", last_marker=None)
    assert "files read" not in text


def test_render_text_inserts_nonempty_ledger_entry_after_its_boundary():
    from nyxloom.session_extract.ledger import Ledger

    events = [
        _ev(EventKind.OPERATOR_TEXT, "do the thing", marker="op1"),
        _ev(EventKind.ASSISTANT_TEXT, "checkpoint prose", marker="cp1", score=5.0),
    ]
    entry = Ledger(files_read=["a.py"], files_edited=["b.py"])
    text = render_text(events, fmt="claude-code", last_marker=None, ledger={"op1": entry})
    assert "[files read: a.py]" in text
    assert "[files edited: b.py]" in text
    # Between the boundary's own text and the following kept content.
    assert text.index("do the thing") < text.index("[files read") < text.index("checkpoint prose")


def test_render_text_skips_empty_ledger_entries():
    from nyxloom.session_extract.ledger import Ledger

    events = [_ev(EventKind.OPERATOR_TEXT, "do the thing", marker="op1")]
    text = render_text(events, fmt="claude-code", last_marker=None, ledger={"op1": Ledger()})
    assert "[files" not in text
    # A PRESENT-but-empty Ledger's own render() is "" -- an empty string is
    # not a substring match for "[files" either way, so that check alone
    # can't tell "no extra block was appended" from "an empty block WAS
    # appended, it just renders as nothing visible". Blocks are joined with
    # "\n\n---\n\n"; a real single-block render has NO such separator at
    # all, which an erroneously-appended empty second block would add.
    assert "---" not in text


def test_render_text_ledger_ignores_non_boundary_kinds():
    from nyxloom.session_extract.ledger import Ledger

    events = [_ev(EventKind.ASSISTANT_TEXT, "plain prose", marker="as1", score=0.0)]
    # Even if a ledger entry happens to exist under this marker, only
    # OPERATOR_TEXT/QA_PAIR/LIFECYCLE_MARKER kinds are boundary rows.
    text = render_text(events, fmt="claude-code", last_marker=None, ledger={"as1": Ledger(files_read=["a.py"])})
    assert "files read" not in text
