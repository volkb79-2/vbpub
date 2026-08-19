"""O4 — the tamper invariants bite, over the REAL coverage.py JSON fixture.

Every mutation here is a one-key edit of a COPY of
``tests/fixtures/coverage/coverage-py-json.branch.json`` -- never the fixture
itself (``json.loads`` on the fixture's own text always produces a fresh,
independently mutable ``dict``, so no test here can leave the committed file
touched). Each test is differential: the SAME test proves the unmodified
control still parses clean, in addition to proving the injected defect does
not (A-124/A-131) -- via :func:`_control`, called once per test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from assay.coverage import load_coverage_profile
from assay.errors import AssayError, Outcome, ReasonCode

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "coverage"
    / "coverage-py-json.branch.json"
)


def _document() -> dict[str, Any]:
    """A fresh, independently mutable copy of the real fixture's own parsed
    JSON -- reloaded per call so no test can see another test's mutation."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document)


def _control() -> None:
    """The unmodified fixture parses clean -- proven in the SAME test as
    every injected defect, never asserted once and assumed elsewhere."""
    profile = load_coverage_profile(_dump(_document()), declared_format="coverage-py-json")
    assert profile.files["sample.py"].branches.by_line == {
        5: (1, 2), 11: (1, 2), 12: (2, 2), 18: (0, 2),
    }


def _assert_unreadable(document: dict[str, Any]) -> None:
    with pytest.raises(AssayError) as caught:
        load_coverage_profile(_dump(document), declared_format="coverage-py-json")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_covered_branches_off_by_one_still_parses_clean():
    # A-272 withdraws this cross-check: `summary.covered_branches` is
    # coverage.py's OWN aggregate, not the cardinality of `executed_branches`,
    # and real coverage.py 7.15.4 output disagrees with it on ordinary files
    # (measured: `topos/.../banner.py`, `covered_branches=2` against an EMPTY
    # `executed_branches`). This used to be refused by the withdrawn
    # `_check_branch_summary`; the arc-identity invariants (`_check_arc_
    # identity`) do not fire here at all -- an isolated summary edit touches
    # neither array, so there is nothing for them to catch, and that is
    # exactly the point: this is not evidence of tampering. The mutation is
    # now inert, and the derived `by_line` (built only from the arrays) is
    # unchanged from `_control`'s.
    _control()
    document = _document()
    document["files"]["sample.py"]["summary"]["covered_branches"] += 1
    profile = load_coverage_profile(_dump(document), declared_format="coverage-py-json")
    assert profile.files["sample.py"].branches.by_line == {
        5: (1, 2), 11: (1, 2), 12: (2, 2), 18: (0, 2),
    }


def test_num_branches_off_by_one_still_parses_clean():
    # A-272, same reasoning as the sibling test above but for
    # `summary.num_branches` (measured false against `executed_branches`/
    # `missing_branches`' own combined length on real output).
    _control()
    document = _document()
    document["files"]["sample.py"]["summary"]["num_branches"] += 1
    profile = load_coverage_profile(_dump(document), declared_format="coverage-py-json")
    assert profile.files["sample.py"].branches.by_line == {
        5: (1, 2), 11: (1, 2), 12: (2, 2), 18: (0, 2),
    }


def test_an_arc_whose_source_line_is_not_in_executed_or_missing_is_refused():
    _control()
    document = _document()
    # line 999 is in neither sample.py's executed_lines nor missing_lines.
    document["files"]["sample.py"]["missing_branches"].append([999, 1000])
    _assert_unreadable(document)


def test_an_arc_whose_source_line_is_in_missing_but_appears_executed_is_refused():
    _control()
    document = _document()
    # line 7 is in sample.py's missing_lines and is NOT currently any
    # branch's source -- adding a covered arc there tampers a line that
    # never ran into having taken one (model invariant 5).
    document["files"]["sample.py"]["executed_branches"].append([7, 999])
    _assert_unreadable(document)


def test_meta_branch_coverage_flipped_to_false_with_arrays_left_is_refused():
    _control()
    document = _document()
    document["meta"]["branch_coverage"] = False
    _assert_unreadable(document)


def test_meta_deleted_entirely_with_arrays_left_still_parses_as_reported():
    # The load-bearing asymmetry (A-265): detail is authoritative, so an
    # absent meta must NOT refuse -- it must parse exactly as the control
    # does, branch arrays and all.
    _control()
    document = _document()
    del document["meta"]
    profile = load_coverage_profile(_dump(document), declared_format="coverage-py-json")
    assert profile.files["sample.py"].branches.by_line == {
        5: (1, 2), 11: (1, 2), 12: (2, 2), 18: (0, 2),
    }


def test_meta_true_with_every_arc_array_removed_is_refused():
    _control()
    document = _document()
    for record in document["files"].values():
        record.pop("executed_branches", None)
        record.pop("missing_branches", None)
    assert document["meta"]["branch_coverage"] is True
    _assert_unreadable(document)


def test_a_record_carrying_executed_branches_but_not_missing_branches_is_refused():
    _control()
    document = _document()
    del document["files"]["sample.py"]["missing_branches"]
    _assert_unreadable(document)


def test_a_duplicated_covered_arc_with_totals_tampered_to_match_is_refused():
    # The coherent-tamper case (A-265's whole reason for existing), STILL
    # refused after A-272 withdrew the summary cross-check -- because the
    # identity rule was always the thing catching it, not the withdrawn
    # check. Duplicating [5, 6] within executed_branches, under NAIVE
    # (no-identity) per-src aggregation, would derive total=9 (was 8) and
    # covered=5 (was 4) for the whole file; this tamper sets `summary` to
    # match those NAIVE numbers to show even a coherent-looking artifact
    # (one whose stated totals a since-withdrawn cross-check would have
    # accepted) is refused -- `summary` is not even consulted post-A-272, so
    # the duplicate arc itself, caught by `_check_arc_identity` before any
    # aggregation runs, is the ONLY thing doing the refusing here now.
    _control()
    document = _document()
    document["files"]["sample.py"]["executed_branches"].append([5, 6])
    document["files"]["sample.py"]["summary"]["num_branches"] = 9
    document["files"]["sample.py"]["summary"]["covered_branches"] = 5
    _assert_unreadable(document)


def test_the_same_arc_present_in_both_arrays_is_refused():
    _control()
    document = _document()
    document["files"]["sample.py"]["missing_branches"].append([5, 6])
    _assert_unreadable(document)
