"""O2 (construction half) — ``StatementSpan`` refuses a malformed span at
construction time, the same discipline ``Coverage``'s own
``covered``/``changed_executable``/``considered`` fields already apply
(A-092).

This is the "malformed spans" leg of O2's negative: a span whose own numbers
could never describe a real physical line range (non-integer, non-positive,
or backwards) is refused immediately, so it can never enter the pure
attribution function at all — the same fail-fast discipline that stops a
malformed ``Coverage`` payload from ever being built. This is deliberately
NOT the same test as ``test_evaluate_attribute_line.py``'s own "overlapping"
coverage: THIS module proves a single span can never be nonsensical on its
own; that module proves a SET of individually well-formed spans can still be
mutually ambiguous. Both are real, distinct readings of O2's "malformed ...
spans" text, and both are exercised.
"""

from __future__ import annotations

import pytest

from assay.adapters.base import StatementSpan


def test_the_untouched_form_builds():
    span = StatementSpan(start_line=2, end_line=5)
    assert span.start_line == 2
    assert span.end_line == 5


def test_a_single_line_span_is_legal():
    """start_line == end_line describes one physical line — legal, not the
    same as end_line < start_line."""
    span = StatementSpan(start_line=7, end_line=7)
    assert span.start_line == span.end_line == 7


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"start_line": 0, "end_line": 1}, ">= 1"),
        ({"start_line": -1, "end_line": 1}, ">= 1"),
        ({"start_line": 1, "end_line": 0}, ">= 1"),
        ({"start_line": True, "end_line": 2}, "must be an integer"),
        ({"start_line": 1, "end_line": True}, "must be an integer"),
        ({"start_line": "1", "end_line": 2}, "must be an integer"),
        ({"start_line": 1.0, "end_line": 2}, "must be an integer"),
        ({"start_line": 5, "end_line": 2}, "end_line \\(2\\) must be >="),
    ],
)
def test_a_malformed_span_is_refused_at_construction(kwargs, match):
    """The 'malformed spans' leg of O2: a span that could never describe a
    real statement is refused before it can enter attribution, never
    surfaced downstream as an ambiguity to classify."""
    with pytest.raises(ValueError, match=match):
        StatementSpan(**kwargs)


def test_the_dataclass_is_frozen():
    span = StatementSpan(start_line=1, end_line=1)
    with pytest.raises(Exception):
        span.start_line = 2  # type: ignore[misc]
