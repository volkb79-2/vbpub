"""O1/O2/O3 — the coverage.py JSON parser, driven through the public
registry entry point (:func:`assay.coverage.load_coverage_profile`) so a
mutation that REMOVES this format from the registry, or that starts
selecting a parser from sniffed content instead of the declaration, breaks
these tests too (not only a direct call to the parser module).

Negative (O1): removing this format from the registry, or binding it to
Python specifically (e.g. rejecting a non-``.py`` path), changes a literal
normalized fixture. Negative (O2): ignoring a malformed record, or selecting
a parser from sniffed content rather than the declared one, makes a reject
fixture load. Negative (O3): collapsing ``excluded=frozenset()`` to ``None``
falsely claims this format cannot express exclusions.
"""

from __future__ import annotations

import json

import pytest

from assay.coverage import FileCoverage, load_coverage_profile
from assay.coverage_parsers import coverage_py_json
from assay.errors import AssayError, Outcome, ReasonCode

BASIC_ARTIFACT = json.dumps(
    {
        "meta": {"version": "7.15.2"},
        "files": {
            "pkg/mod.py": {
                "executed_lines": [1, 2, 5],
                "missing_lines": [3, 4],
                "excluded_lines": [6],
            },
            # a non-Python path proves this format is not bound to Python
            # (DESIGN-GUIDE §11: format and language are independent axes) —
            # nothing in this parser filters by extension.
            "web/app.ts.instrumented": {
                "executed_lines": [10],
                "missing_lines": [],
                "excluded_lines": [],
            },
        },
        "totals": {"percent_covered": 66.0},
    }
)


def test_basic_artifact_normalizes_to_the_expected_file_coverage():
    profile = load_coverage_profile(BASIC_ARTIFACT, declared_format="coverage-py-json")

    assert set(profile.files) == {"pkg/mod.py", "web/app.ts.instrumented"}
    assert profile.files["pkg/mod.py"] == FileCoverage(
        executed=frozenset({1, 2, 5}),
        missing=frozenset({3, 4}),
        excluded=frozenset({6}),
    )
    assert profile.files["web/app.ts.instrumented"] == FileCoverage(
        executed=frozenset({10}), missing=frozenset(), excluded=frozenset()
    )


def test_excluded_lines_reports_none_excluded_as_an_empty_frozenset_not_none():
    # O3's positive half for THIS format: coverage.py CAN express exclusions,
    # so reporting zero of them is `frozenset()`, never `None`.
    profile = load_coverage_profile(BASIC_ARTIFACT, declared_format="coverage-py-json")
    assert profile.files["web/app.ts.instrumented"].excluded == frozenset()
    assert profile.files["web/app.ts.instrumented"].excluded is not None


def test_missing_excluded_lines_key_defaults_to_the_empty_frozenset():
    # A minimal, hand-built fixture that omits `excluded_lines` entirely is
    # not itself malformed -- absence means the same thing `[]` would for a
    # format that CAN express exclusions.
    artifact = json.dumps(
        {"files": {"a.py": {"executed_lines": [1], "missing_lines": []}}}
    )
    profile = load_coverage_profile(artifact, declared_format="coverage-py-json")
    assert profile.files["a.py"].excluded == frozenset()


@pytest.mark.parametrize(
    "artifact",
    [
        pytest.param(
            json.dumps({"files": {"a.py": {"executed_lines": "oops", "missing_lines": []}}}),
            id="executed_lines-not-a-list",
        ),
        pytest.param(
            json.dumps({"files": {"a.py": {"executed_lines": [1], "missing_lines": ["x"]}}}),
            id="missing_lines-contains-a-non-int",
        ),
        pytest.param(
            json.dumps({"files": {"a.py": {"executed_lines": [1]}}}),
            id="missing_lines-key-absent-entirely",
        ),
        pytest.param(
            json.dumps({"files": {"a.py": "not-an-object"}}),
            id="file-record-is-not-an-object",
        ),
        pytest.param(
            # P15 (finding 4): a line claimed BOTH executed and missing --
            # sol's exact reproduction of a false PASS 100.0 that still
            # reports the same line missing.
            json.dumps({"files": {"a.py": {"executed_lines": [1], "missing_lines": [1]}}}),
            id="a-line-is-both-executed-and-missing",
        ),
        pytest.param(
            json.dumps(
                {"files": {"a.py": {"executed_lines": [1], "missing_lines": [], "excluded_lines": [1]}}}
            ),
            id="a-line-is-both-executed-and-excluded",
        ),
        pytest.param(
            json.dumps({"files": {"a.py": {"executed_lines": [0], "missing_lines": []}}}),
            id="a-line-number-is-zero",
        ),
        pytest.param(
            json.dumps({"files": {"a.py": {"executed_lines": [-1], "missing_lines": []}}}),
            id="a-line-number-is-negative",
        ),
    ],
)
def test_malformed_record_raises_unreadable_artifact(artifact: str):
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="coverage-py-json")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_no_files_key_fails_the_signature_cross_check_before_parsing():
    # `{"meta": {}}` contains no `"files"` substring at all, so it does not
    # even SNIFF as coverage.py JSON -- this is a format mismatch, not an
    # internal record defect, and is refused before `parse()` ever runs.
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(json.dumps({"meta": {}}), declared_format="coverage-py-json")
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


def test_no_files_key_still_raises_unreadable_artifact_when_parsed_directly():
    # A caller that bypasses the registry's sniff cross-check (as a test, or
    # a future direct caller, might) still gets a guarded, typed failure from
    # the parser module itself, not a bare KeyError/AttributeError.
    with pytest.raises(AssayError) as excinfo:
        coverage_py_json.parse(json.dumps({"meta": {}}), producer=None)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_not_valid_json_fails_the_signature_cross_check_before_parsing():
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile("{not valid json", declared_format="coverage-py-json")
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


def test_not_valid_json_still_raises_unreadable_artifact_when_parsed_directly():
    with pytest.raises(AssayError) as excinfo:
        coverage_py_json.parse("{not valid json", producer=None)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_top_level_not_an_object_raises_unreadable_artifact_when_parsed_directly():
    # A JSON array can never sniff as this format (sniff requires a leading
    # "{"), so this defect is only reachable through a direct parse() call.
    with pytest.raises(AssayError) as excinfo:
        coverage_py_json.parse("[1, 2, 3]", producer=None)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "expected object" in str(excinfo.value)


def test_bool_is_rejected_as_a_line_number_even_though_bool_is_an_int_subclass():
    # Python's `isinstance(True, int)` is True -- a naive `isinstance(x, int)`
    # check would silently accept `[true, false]` as line numbers. Catching
    # this here is what makes the type check in coverage_py_json.py's
    # `_int_list` genuinely strict rather than accidentally lenient.
    artifact = json.dumps({"files": {"a.py": {"executed_lines": [True], "missing_lines": []}}})
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="coverage-py-json")
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_declared_format_mismatch_is_refused_before_any_record_is_read():
    # Content that is actually an lcov tracefile, declared as coverage-py-json.
    lcov_text = "SF:a.c\nDA:1,1\nend_of_record\n"
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(lcov_text, declared_format="coverage-py-json")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


def test_sniff_is_a_cheap_signature_check_not_a_parse():
    assert coverage_py_json.sniff(BASIC_ARTIFACT) is True
    assert coverage_py_json.sniff("SF:a.c\nend_of_record\n") is False
    assert coverage_py_json.sniff("mode: set\n") is False
