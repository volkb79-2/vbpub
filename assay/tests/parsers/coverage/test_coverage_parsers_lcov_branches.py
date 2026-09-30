"""§3.1a/§3.3's lcov ``BRDA:``/``BRF:``/``BRH:`` grammar, driven directly
with hand-authored tracefiles -- the malformed shapes no real fixture
witnesses (A-080's convention).
"""

from __future__ import annotations

import pytest

from assay.coverage import load_coverage_profile
from assay.errors import AssayError, Outcome, ReasonCode


def _assert_unreadable(text: str) -> None:
    with pytest.raises(AssayError) as caught:
        load_coverage_profile(text, declared_format="lcov")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_brda_outside_any_sf_record_is_refused():
    # A well-formed SF:/end_of_record pair earlier keeps this sniffing as
    # lcov; the bare BRDA: line after it is what is under test.
    text = "SF:a.c\nDA:1,1\nend_of_record\nBRDA:1,0,0,1\n"
    _assert_unreadable(text)


def test_brda_with_too_few_fields_is_refused():
    text = "SF:a.c\nDA:1,1\nBRDA:1,0\nend_of_record\n"
    _assert_unreadable(text)


def test_brda_with_no_taken_field_is_refused():
    text = "SF:a.c\nDA:1,1\nBRDA:1,0,onlybranchid\nend_of_record\n"
    _assert_unreadable(text)


def test_brda_line_number_not_an_integer_is_refused():
    text = "SF:a.c\nDA:1,1\nBRDA:x,0,branch,1\nend_of_record\n"
    _assert_unreadable(text)


def test_brda_line_number_not_positive_is_refused():
    text = "SF:a.c\nDA:1,1\nBRDA:0,0,branch,1\nend_of_record\n"
    _assert_unreadable(text)


def test_brda_taken_neither_dash_nor_integer_is_refused():
    text = "SF:a.c\nDA:1,1\nBRDA:1,0,branch,notanumber\nend_of_record\n"
    _assert_unreadable(text)


def test_brf_value_not_an_integer_is_refused():
    text = "SF:a.c\nDA:1,1\nBRDA:1,0,branch,1\nBRF:notanumber\nend_of_record\n"
    _assert_unreadable(text)


def test_brh_value_not_an_integer_is_refused():
    text = "SF:a.c\nDA:1,1\nBRDA:1,0,branch,1\nBRH:notanumber\nend_of_record\n"
    _assert_unreadable(text)


def test_brf_mismatching_the_derived_total_is_refused():
    # One BRDA record -> derived total 1; BRF claims 5.
    text = "SF:a.c\nDA:1,1\nBRDA:1,0,branch,1\nBRF:5\nend_of_record\n"
    _assert_unreadable(text)


def test_brh_mismatching_the_derived_covered_count_is_refused():
    # One taken BRDA record -> derived covered 1; BRH claims 0.
    text = "SF:a.c\nDA:1,1\nBRDA:1,0,branch,1\nBRH:0\nend_of_record\n"
    _assert_unreadable(text)


def test_a_branch_line_with_no_corresponding_da_record_is_refused():
    # DA: and BRDA: are independent record streams: a BRDA-only line has no
    # DA: record naming it AT ALL, so it lands in neither `executed` nor
    # `missing` (model invariant 3), reached through THIS parser's own
    # wrapped FileCoverage construction.
    text = "SF:a.c\nDA:1,1\nBRDA:2,0,branch,1\nend_of_record\n"
    _assert_unreadable(text)


def test_a_branch_line_da_missing_but_brda_taken_is_refused():
    # DA:2,0 makes line 2 `missing`; BRDA:2,... reports it as taken (a
    # covered arc). Model invariant 5: a line that never ran cannot have
    # taken an arc.
    text = "SF:a.c\nDA:1,1\nDA:2,0\nBRDA:2,0,branch,1\nend_of_record\n"
    _assert_unreadable(text)


def test_the_control_still_parses_clean():
    text = "SF:a.c\nDA:1,1\nDA:2,0\nBRDA:1,0,branch,1\nBRDA:1,0,branch2,0\nBRF:2\nBRH:1\nend_of_record\n"
    profile = load_coverage_profile(text, declared_format="lcov")
    assert profile.files["a.c"].branches.by_line == {1: (1, 2)}
