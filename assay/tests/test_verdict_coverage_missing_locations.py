"""O3 (model half) — ``Coverage`` refuses a malformed ``missing_lines`` /
``files_missing_coverage`` payload (A-096), the same construction-time
discipline ``test_verdict_schema_rejects.py`` already applies to
``covered``/``changed_executable``/``pct``/``considered``.

Each rejection here is paired with the untouched form building successfully
in the SAME test, so a Coverage that refuses everything fails these tests
too (the same discipline ``test_verdict_schema_rejects.py``'s module
docstring names for the schema itself).
"""

from __future__ import annotations

import pytest

from assay.verdict import Coverage

BASE = {
    "covered": 1,
    "changed_executable": 2,
    "pct": 50.0,
    "considered": 1,
    "missing_lines": {"src/mod.py": frozenset({3})},
    # A file with NO coverage-artifact entry has every changed line recorded
    # as missing, so this summary is a subset of `missing_lines`' own keys
    # (P16 review) -- naming a path the detail never mentions is refused.
    "files_missing_coverage": ("src/mod.py",),
}


def test_the_untouched_form_builds():
    coverage = Coverage(**BASE)
    assert coverage.missing_lines == {"src/mod.py": frozenset({3})}
    assert coverage.files_missing_coverage == ("src/mod.py",)


def test_empty_missing_lines_and_files_missing_coverage_are_legal():
    """The A-096 pair is ALWAYS present, possibly empty -- never itself the
    defect."""
    coverage = Coverage(
        **{
            **BASE,
            "covered": 2,
            "pct": 100.0,
            "missing_lines": {},
            "files_missing_coverage": (),
        }
    )
    assert coverage.missing_lines == {}
    assert coverage.files_missing_coverage == ()


@pytest.mark.parametrize(
    "missing_lines,match",
    [
        (["not", "a", "mapping"], "must be a mapping"),
        ({1: frozenset({2})}, "non-empty string"),
        ({"": frozenset({2})}, "non-empty string"),
        ({"src/mod.py": []}, "non-empty"),
        ({"src/mod.py": frozenset()}, "non-empty"),
        ({"src/mod.py": frozenset({0})}, "positive line numbers"),
        ({"src/mod.py": frozenset({-1})}, "positive line numbers"),
        ({"src/mod.py": frozenset({True})}, "positive line numbers"),
        ({"src/mod.py": frozenset({"3"})}, "positive line numbers"),
    ],
)
def test_missing_lines_refuses_malformed_shapes(missing_lines, match):
    with pytest.raises(ValueError, match=match):
        Coverage(**{**BASE, "missing_lines": missing_lines})


@pytest.mark.parametrize(
    "files_missing_coverage,match",
    [
        (["src/other.py"], "must be a tuple"),
        ((1,), "non-empty strings"),
        (("",), "non-empty strings"),
        (("src/a.py", "src/a.py"), "duplicate"),
        (("src/b.py", "src/a.py"), "must be sorted"),
    ],
)
def test_files_missing_coverage_refuses_malformed_shapes(files_missing_coverage, match):
    with pytest.raises(ValueError, match=match):
        Coverage(**{**BASE, "files_missing_coverage": files_missing_coverage})
