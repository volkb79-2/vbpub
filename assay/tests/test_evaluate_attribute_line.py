"""O1/O2 (pure function half) — ``assay.evaluate._attribute_line``, tested
directly with hand-built spans, per the handoff's own instruction: "Attribution
is a pure function; test it directly with hand-built spans as well as through
real Python source." Never derived from parsed source here — that is what
``test_adapters_python_statement_spans.py`` and
``test_evaluate_span_attribution.py`` do.

The negative this defends (O1's half): *line-number-only evaluation fails the
executed continuation or reports the continuation itself as an executable
start* — i.e. an interior line must resolve to its ENCLOSING statement's own
start line, never to itself and never to nothing when a real span contains it.

The negative this defends (O2's half): *choosing the first overlap or
dropping unattributed lines turns at least one ambiguity fixture green* — a
set of spans that does not nest cannot be silently resolved to whichever
sorts first; it must come back AMBIGUOUS.
"""

from __future__ import annotations

from assay.adapters.base import StatementSpan
from assay.evaluate import _Attribution, _attribute_line


def test_a_line_outside_every_span_is_not_code():
    spans = [StatementSpan(start_line=10, end_line=12)]
    assert _attribute_line(5, spans) is _Attribution.NOT_CODE


def test_an_empty_span_list_is_not_code():
    assert _attribute_line(1, []) is _Attribution.NOT_CODE


def test_a_line_inside_a_single_span_names_the_spans_own_start():
    """O1: an interior continuation line resolves to the STATEMENT's own
    start line — 'the statement start named' — never to itself, never to
    nothing."""
    spans = [StatementSpan(start_line=2, end_line=5)]
    assert _attribute_line(4, spans) == 2


def test_a_line_that_is_itself_a_spans_own_start_names_that_start():
    """The caller (evaluate_coverage) is the one that treats anchor == line
    as 'still non-executable' -- this function's own job is only to report
    the correct anchor, which for a single-line span is the line itself."""
    spans = [StatementSpan(start_line=7, end_line=7)]
    assert _attribute_line(7, spans) == 7


def test_properly_nested_spans_resolve_to_the_innermost():
    """A compound statement's own (trimmed) header span and a nested
    statement's own span both contain a line inside the nested statement --
    correct nesting is resolved deterministically by containment, the
    innermost (smallest) span wins, never flagged as ambiguous."""
    outer = StatementSpan(start_line=1, end_line=20)  # e.g. a function body
    middle = StatementSpan(start_line=5, end_line=15)  # a nested if-block
    inner = StatementSpan(start_line=8, end_line=8)  # one statement inside it
    # Order in the list must not matter -- proven by testing both orders.
    assert _attribute_line(8, [outer, middle, inner]) == 8
    assert _attribute_line(8, [inner, middle, outer]) == 8
    # A line only the two outer spans contain (not the innermost) resolves
    # to the next-smallest containing span.
    assert _attribute_line(6, [outer, middle, inner]) == 5


def test_duplicate_identical_spans_are_not_ambiguous():
    """Two spans with the exact same bounds trivially 'nest' in each other
    (containment holds both directions) -- harmless, not an adapter defect
    worth flagging."""
    spans = [StatementSpan(start_line=3, end_line=6), StatementSpan(start_line=3, end_line=6)]
    assert _attribute_line(4, spans) == 3


def test_non_nesting_overlap_is_ambiguous():
    """O2's defensive guard: two spans both contain the line, neither
    contains the other -- an adapter's own internally-inconsistent analysis,
    never a naturally-occurring case for correctly-nested real Python."""
    left = StatementSpan(start_line=5, end_line=9)
    right = StatementSpan(start_line=7, end_line=12)
    assert _attribute_line(8, [left, right]) is _Attribution.AMBIGUOUS
    # Order must not matter for the ambiguity to be detected.
    assert _attribute_line(8, [right, left]) is _Attribution.AMBIGUOUS


def test_two_disjoint_spans_of_equal_size_that_both_contain_the_line_are_ambiguous():
    """A same-size-but-different-position hazard: naive 'pick the smallest'
    tie-breaking (whichever sorts first) would silently choose one -- this
    function refuses instead."""
    left = StatementSpan(start_line=4, end_line=6)
    right = StatementSpan(start_line=5, end_line=7)
    assert _attribute_line(5, [left, right]) is _Attribution.AMBIGUOUS


def test_a_span_not_containing_the_line_never_causes_a_false_ambiguity():
    """A span elsewhere in the file that simply does not contain *line* is
    correctly excluded from consideration entirely -- it must never be
    compared against the (unrelated) spans that DO contain the line and
    accidentally trip the ambiguity guard."""
    unrelated = StatementSpan(start_line=100, end_line=105)
    containing = StatementSpan(start_line=2, end_line=5)
    assert _attribute_line(3, [unrelated, containing]) == 2
    assert _attribute_line(3, [containing, unrelated]) == 2
