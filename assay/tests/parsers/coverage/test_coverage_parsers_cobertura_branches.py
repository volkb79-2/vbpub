"""§3.3's Cobertura branch rules, driven directly with hand-authored XML
(A-080's own convention for this format -- no real fixture witnesses these
malformed shapes, so a fixture invented to match them would be exactly the
failure PROVENANCE.md's directory exists to prevent for the REAL artifacts;
these are deliberately NOT claimed as real-artifact evidence).
"""

from __future__ import annotations

import pytest

from assay.coverage import load_coverage_profile
from assay.errors import AssayError, Outcome, ReasonCode


def _assert_unreadable(text: str) -> None:
    with pytest.raises(AssayError) as caught:
        load_coverage_profile(text, declared_format="cobertura")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def _doc(lines_xml: str, root_attrs: str = "") -> str:
    return (
        f"<coverage {root_attrs}><packages><package name='p'><classes>"
        f"<class name='c' filename='a.py'>"
        f"<lines>{lines_xml}</lines>"
        f"</class></classes></package></packages></coverage>"
    )


def test_branch_true_line_missing_condition_coverage_is_refused():
    text = _doc('<line number="1" hits="1" branch="true"/>')
    _assert_unreadable(text)


def test_branch_true_line_unparsable_condition_coverage_is_refused():
    text = _doc(
        '<line number="1" hits="1" branch="true" condition-coverage="oops"/>'
    )
    _assert_unreadable(text)


def test_root_branches_valid_value_mismatches_the_derived_total_is_refused():
    # per-line detail says total=2 (a single branch="true" line with (1/2)),
    # but the root claims 5 -- both are consistent with "detail present", so
    # the presence/absence disagreement check passes, and only the VALUE
    # cross-check catches this.
    text = _doc(
        '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>',
        root_attrs='branches-valid="5" branches-covered="1"',
    )
    _assert_unreadable(text)


def test_root_branches_covered_value_mismatches_the_derived_covered_is_refused():
    text = _doc(
        '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>',
        root_attrs='branches-valid="2" branches-covered="9"',
    )
    _assert_unreadable(text)


def test_root_branches_valid_non_integer_is_refused():
    text = _doc(
        '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>',
        root_attrs='branches-valid="not-a-number"',
    )
    _assert_unreadable(text)


def test_root_branches_covered_non_integer_is_refused():
    text = _doc(
        '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>',
        root_attrs='branches-valid="2" branches-covered="not-a-number"',
    )
    _assert_unreadable(text)


def test_root_branches_valid_absent_skips_the_cross_check_entirely():
    # The differential control for the two mismatch tests above: with NO
    # root attribute at all, a real per-line detail block still parses
    # clean -- this is the shape the existing (pre-wave) BASIC_ARTIFACT
    # fixture in test_coverage_parsers_cobertura.py already relies on.
    text = _doc(
        '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/>'
    )
    profile = load_coverage_profile(text, declared_format="cobertura")
    assert profile.files["a.py"].branches.by_line == {1: (1, 2)}


def test_a_branch_line_reported_missing_with_a_covered_arc_is_refused():
    # The model's own invariant 5, reached through THIS parser's own wrapped
    # ValueError path (not model.py's own direct unit test): line 1 has
    # hits="0" (missing) but its condition-coverage claims 1 covered arc.
    text = _doc(
        '<line number="1" hits="0" branch="true" condition-coverage="50% (1/2)"/>'
    )
    _assert_unreadable(text)
