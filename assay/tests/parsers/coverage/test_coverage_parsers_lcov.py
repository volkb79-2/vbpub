"""O1/O2/O3 — the lcov ``.info`` parser, driven through
:func:`assay.coverage.load_coverage_profile`.

lcov has no prior art in this estate (handoff item 6); every fixture here is
independently hand-written from the public ``geninfo`` spec (A-080).

Negative (O1): removing this format from the registry, or binding it to one
of the five languages that emit lcov, changes a literal normalized fixture.
Negative (O2): ignoring a malformed ``DA:``/``SF:`` structure, or selecting a
parser from sniffed content rather than the declared one, makes a reject
fixture load. Negative (O3): reporting ``excluded=frozenset()`` instead of
``None`` falsely claims this format can say "zero exclusions", which it
cannot say at all.
"""

from __future__ import annotations

import pytest

from assay.coverage import FileCoverage, load_coverage_profile
from assay.coverage_parsers import lcov
from assay.errors import AssayError, Outcome, ReasonCode

#: Two files; the second's line 2 has TWO `DA:` records (a legal lcov shape
#: after tracefile merging) whose hit counts are summed, and a non-`.c`
#: filename (`.rs` — Rust, one of the five languages lcov is emitted by
#: besides C/C++/PHP/TypeScript) proves no language binding.
MULTI_RECORD_ARTIFACT = """\
TN:
SF:src/foo.c
DA:1,1
DA:2,0
DA:2,3
DA:5,0
end_of_record
SF:src/bar.rs
DA:10,1
end_of_record
"""


def test_multi_file_multi_da_record_normalizes_with_summed_hit_counts():
    profile = load_coverage_profile(MULTI_RECORD_ARTIFACT, declared_format="lcov")

    assert set(profile.files) == {"src/foo.c", "src/bar.rs"}
    # line 2's two DA: records (0 + 3) sum to a nonzero count -> executed.
    assert profile.files["src/foo.c"] == FileCoverage(
        executed=frozenset({1, 2}), missing=frozenset({5}), excluded=None
    )
    assert profile.files["src/bar.rs"] == FileCoverage(
        executed=frozenset({10}), missing=frozenset(), excluded=None
    )


def test_excluded_is_always_none_for_this_format():
    # O3's negative half: lcov cannot say "zero exclusions" any more than it
    # can name one, so this must never collapse to `frozenset()`.
    profile = load_coverage_profile(MULTI_RECORD_ARTIFACT, declared_format="lcov")
    assert profile.files["src/foo.c"].excluded is None
    assert profile.files["src/bar.rs"].excluded is None


def test_blank_lines_inside_a_record_are_skipped():
    artifact = "SF:a.c\n\n   \nDA:1,1\n\nend_of_record\n"
    profile = load_coverage_profile(artifact, declared_format="lcov")
    assert profile.files["a.c"] == FileCoverage(
        executed=frozenset({1}), missing=frozenset(), excluded=None
    )


def test_unrecognised_but_legal_record_types_are_ignored_not_rejected():
    # BRDA:/BRF:/BRH: are deliberately EXCLUDED from this artifact: wave-1
    # (§3.3) made those three meaningful (see
    # test_coverage_parsers_lcov_branches.py), so this test now proves only
    # the record types that remain genuinely unused for either model.
    artifact = (
        "TN:my_test\n"
        "SF:a.c\n"
        "FN:1,my_func\n"
        "FNDA:3,my_func\n"
        "FNF:1\n"
        "FNH:1\n"
        "DA:1,3\n"
        "LF:1\n"
        "LH:1\n"
        "end_of_record\n"
    )
    profile = load_coverage_profile(artifact, declared_format="lcov")
    assert profile.files["a.c"] == FileCoverage(
        executed=frozenset({1}), missing=frozenset(), excluded=None
    )
    assert profile.files["a.c"].branches is None


@pytest.mark.parametrize(
    "artifact",
    [
        pytest.param("SF:a.c\nDA:x,1\nend_of_record\n", id="da-line-not-an-integer"),
        pytest.param("SF:a.c\nDA:1\nend_of_record\n", id="da-missing-hit-count-field"),
        pytest.param("SF:a.c\nDA:0,1\nend_of_record\n", id="da-line-number-not-positive"),
        pytest.param("SF:a.c\nDA:1,-1\nend_of_record\n", id="da-negative-hit-count"),
        pytest.param("SF:\nend_of_record\n", id="sf-names-no-path"),
        pytest.param("SF:a.c\nSF:b.c\nend_of_record\n", id="sf-opened-twice-without-closing"),
        # both of these keep one well-formed SF:/end_of_record pair earlier
        # in the text so the artifact still SNIFFS as lcov (a bare
        # "end_of_record"/"DA:" line alone contains no "SF:" line at all,
        # which would fail the signature cross-check before parsing ever
        # started, and prove a different thing than intended here).
        pytest.param(
            "SF:a.c\nend_of_record\nend_of_record\n", id="end-of-record-with-no-open-sf"
        ),
        pytest.param("SF:a.c\nend_of_record\nDA:1,1\n", id="da-outside-any-sf-record"),
        pytest.param("SF:a.c\nDA:1,1\n", id="sf-record-never-closed"),
    ],
)
def test_malformed_tracefile_raises_unreadable_artifact(artifact: str):
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="lcov")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_declared_format_mismatch_is_refused_before_any_record_is_read():
    coverage_py_json_text = '{"files": {"a.py": {"executed_lines": [1], "missing_lines": []}}}'
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(coverage_py_json_text, declared_format="lcov")
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


def test_sniff_is_a_cheap_signature_check_not_a_parse():
    assert lcov.sniff(MULTI_RECORD_ARTIFACT) is True
    assert lcov.sniff('{"files": {}}') is False
    assert lcov.sniff("mode: set\n") is False
