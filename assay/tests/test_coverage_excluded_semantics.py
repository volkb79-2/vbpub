"""O3 — ``FileCoverage.excluded`` preserves ``None`` (the format cannot
report exclusions) as distinct from ``frozenset()`` (the format can, and
reports none).

Negative: collapsing ``None`` to ``frozenset()`` makes a Go fixture and a
coverage.py fixture that otherwise measured the identical lines compare
EQUAL, and falsely claims the Go lane's exclusions were verified as zero when
the format never had a way to say that at all.
"""

from __future__ import annotations

import dataclasses

import pytest

from assay.coverage import FileCoverage, load_coverage_profile


def test_go_and_coverage_py_fixtures_with_identical_line_sets_are_not_equal():
    # Same executed/missing sets on both sides -- the ONLY thing that can
    # legitimately differ is `excluded`.
    go_profile = load_coverage_profile(
        "mode: set\npkg/x.go:1.1,1.1 1 1\n", declared_format="go-cover"
    )
    py_profile = load_coverage_profile(
        '{"files": {"pkg/x.go": {"executed_lines": [1], "missing_lines": [], '
        '"excluded_lines": []}}}',
        declared_format="coverage-py-json",
    )
    go_fc = go_profile.files["pkg/x.go"]
    py_fc = py_profile.files["pkg/x.go"]

    assert go_fc.executed == py_fc.executed == frozenset({1})
    assert go_fc.missing == py_fc.missing == frozenset()
    assert go_fc.excluded is None
    assert py_fc.excluded == frozenset()
    assert py_fc.excluded is not None
    assert go_fc != py_fc, (
        "a Go fixture (excluded=None) and a coverage.py fixture "
        "(excluded=frozenset()) that measured the SAME lines must not "
        "compare equal -- collapsing None to frozenset() is exactly the "
        "regression O3 exists to catch"
    )


@pytest.mark.parametrize("declared_format,text", [
    ("go-cover", "mode: set\npkg/x.go:1.1,1.1 1 1\n"),
    ("lcov", "SF:x.c\nDA:1,1\nend_of_record\n"),
    ("cobertura", "<coverage><packages><package name='p'><classes>"
     "<class name='c' filename='x.py'><lines><line number='1' hits='1'/>"
     "</lines></class></classes></package></packages></coverage>"),
])
def test_formats_without_exclusion_support_always_report_none(
    declared_format: str, text: str
):
    profile = load_coverage_profile(text, declared_format=declared_format)
    for file_coverage in profile.files.values():
        assert file_coverage.excluded is None


def test_file_coverage_is_a_frozen_kw_only_dataclass():
    fc = FileCoverage(executed=frozenset({1}), missing=frozenset(), excluded=None)
    assert dataclasses.is_dataclass(fc)

    with pytest.raises(dataclasses.FrozenInstanceError):
        fc.executed = frozenset()  # type: ignore[misc]

    with pytest.raises(TypeError):
        FileCoverage(frozenset(), frozenset(), None)  # type: ignore[misc]  # kw_only


def test_file_coverage_executable_is_the_union_of_executed_and_missing():
    fc = FileCoverage(
        executed=frozenset({1, 2}), missing=frozenset({3}), excluded=frozenset()
    )
    assert fc.executable == frozenset({1, 2, 3})
