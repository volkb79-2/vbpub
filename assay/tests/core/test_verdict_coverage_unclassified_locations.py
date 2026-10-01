"""O3 (model half) — ``Coverage`` refuses a malformed ``unclassified_lines``
/ ``files_with_unclassified_lines`` payload (P07's own additive THIRD pair,
A-096), the identical construction-time discipline
``test_verdict_coverage_missing_locations.py`` already applies to
``missing_lines``/``files_missing_coverage`` -- proven here to confirm the
shared ``_check_line_location_mapping``/``_check_file_tuple`` helpers hold
the new fields to the same standard, not merely the old ones.

Each rejection is paired with the untouched form building successfully in
the SAME test, so a ``Coverage`` that refuses everything fails these tests
too.
"""

from __future__ import annotations

import pytest

from assay.verdict import Coverage

BASE = {
    "covered": 2,
    "executable": 2,
    "pct": 100.0,
    "considered": 1,
    # P21: required, never defaulted -- see `Coverage.exclusion_capability`.
    "exclusion_capability": "reported",
    "missing_lines": {},
    "files_missing_coverage": (),
    "unclassified_lines": {"src/mod.py": frozenset({3})},
    "files_with_unclassified_lines": ("src/mod.py",),
}


def test_the_untouched_form_builds():
    coverage = Coverage(**BASE)
    assert coverage.unclassified_lines == {"src/mod.py": frozenset({3})}
    assert coverage.files_with_unclassified_lines == ("src/mod.py",)


def test_the_new_fields_default_to_empty():
    """P07's own pair defaults to empty: a producer that does not pass them
    still builds a valid ``Coverage``.

    P21 note: ``exclusion_capability`` is deliberately NOT in that group and
    is passed explicitly here. Defaulting it would mean a producer that
    forgot it silently claimed a capability, which is the shadowing default
    AGENTS.md 4.2a forbids -- and it is exactly the collapse A-008/A-O16
    spent two packages keeping open.
    """
    coverage = Coverage(
        covered=1,
        executable=1,
        pct=100.0,
        considered=1,
        exclusion_capability="reported",
        missing_lines={},
        files_missing_coverage=(),
    )
    assert coverage.unclassified_lines == {}
    assert coverage.files_with_unclassified_lines == ()
    assert coverage.to_dict()["unclassified_lines"] == {}
    assert coverage.to_dict()["files_with_unclassified_lines"] == []


def test_empty_unclassified_lines_and_files_with_unclassified_lines_are_legal():
    coverage = Coverage(
        **{**BASE, "unclassified_lines": {}, "files_with_unclassified_lines": ()}
    )
    assert coverage.unclassified_lines == {}
    assert coverage.files_with_unclassified_lines == ()


@pytest.mark.parametrize(
    "unclassified_lines,match",
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
def test_unclassified_lines_refuses_malformed_shapes(unclassified_lines, match):
    with pytest.raises(ValueError, match=match):
        Coverage(**{**BASE, "unclassified_lines": unclassified_lines})


@pytest.mark.parametrize(
    "files_with_unclassified_lines,match",
    [
        (["src/mod.py"], "must be a tuple"),
        ((1,), "non-empty strings"),
        (("",), "non-empty strings"),
        (("src/a.py", "src/a.py"), "duplicate"),
        (("src/b.py", "src/a.py"), "must be sorted"),
    ],
)
def test_files_with_unclassified_lines_refuses_malformed_shapes(
    files_with_unclassified_lines, match
):
    with pytest.raises(ValueError, match=match):
        Coverage(
            **{
                **BASE,
                "files_with_unclassified_lines": files_with_unclassified_lines,
            }
        )


def test_to_dict_always_emits_both_keys_even_when_empty():
    """'Always present, possibly empty, never conditionally omitted' --
    the same discipline A-025 applies one level up to the coverage block as
    a whole, applied here to P07's own pair."""
    coverage = Coverage(
        covered=0,
        executable=0,
        pct=100.0,
        considered=0,
        exclusion_capability="reported",
        missing_lines={},
        files_missing_coverage=(),
    )
    payload = coverage.to_dict()
    assert "unclassified_lines" in payload
    assert "files_with_unclassified_lines" in payload
    assert payload["unclassified_lines"] == {}
    assert payload["files_with_unclassified_lines"] == []


def test_to_dict_sorts_unclassified_lines_by_path_and_line():
    coverage = Coverage(
        **{
            **BASE,
            "unclassified_lines": {
                "src/b.py": frozenset({9, 1}),
                "src/a.py": frozenset({4}),
            },
            "files_with_unclassified_lines": ("src/a.py", "src/b.py"),
        }
    )
    payload = coverage.to_dict()
    assert list(payload["unclassified_lines"].items()) == [
        ("src/a.py", [4]),
        ("src/b.py", [1, 9]),
    ]
