"""O1/O2 — the real coverage artifacts under ``tests/fixtures/coverage/``
parse to the real numbers ``PROVENANCE.md`` documents.

These EIGHT coverage.py files are carver-owned evidence (see
``PROVENANCE.md``; the two ``coverage-istanbul-json.*`` documents that joined
that directory in B036 belong to
``test_coverage_istanbul_real_fixtures.py``) and are never edited, never
hand-authored, and never referenced by a path outside this directory's own
``FIXTURES`` constant. Every number asserted here is
independently re-derived in this module's own comments from the artifact's
own bytes (grep the raw file, not this test, to re-verify one) -- "the one
the tool printed", not a hand-computed value (O1's own wording).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.coverage import derive_branch_capability, load_coverage_profile
from assay.errors import AssayError, Outcome, ReasonCode

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "coverage"


def _load(name: str, declared_format: str):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return load_coverage_profile(text, declared_format=declared_format)


# ---------------------------------------------------------------------------
# O1 — derived branch totals equal the artifact's own stated totals, and the
# combined percentage equals coverage.py's own summary.percent_covered.
# ---------------------------------------------------------------------------


def _derived_totals(by_line: dict) -> tuple[int, int]:
    covered = sum(c for c, _t in by_line.values())
    total = sum(t for _c, t in by_line.values())
    return covered, total


def test_coverage_py_json_branch_derives_the_documented_totals():
    profile = _load("coverage-py-json.branch.json", "coverage-py-json")
    sample = profile.files["sample.py"]
    assert derive_branch_capability(profile) == "reported"
    # PROVENANCE.md: summary.num_branches=8, covered_branches=4.
    covered, total = _derived_totals(sample.branches.by_line)
    assert (covered, total) == (4, 8)
    # combined = 100 * (covered_lines + covered_branches) / (num_statements +
    # num_branches) = 100 * 12 / 21 = 57.142857142857146 -- coverage.py's own
    # summary.percent_covered, not a hand-computed number. The multiply-then-
    # divide order matters for float equality: `(8+4)/(13+8)*100` rounds to a
    # different last bit than `100*(8+4)/(13+8)` in IEEE 754 binary64, and
    # coverage.py's own `coverage.results.Numbers._percent` computes exactly
    # `(100.0 * numerator) / denominator` (verified against the installed
    # `coverage` package's real source, not assumed).
    executable = len(sample.executed | sample.missing)
    combined_pct = 100 * (len(sample.executed) + covered) / (executable + total)
    assert combined_pct == 57.142857142857146


def test_coverage_py_json_nobranch_is_unavailable():
    profile = _load("coverage-py-json.nobranch.json", "coverage-py-json")
    assert derive_branch_capability(profile) == "unavailable"
    for file_coverage in profile.files.values():
        assert file_coverage.branches is None


def test_coverage_py_json_exitarc_parses_without_error():
    # The negative-dst and free-text-branch-id proof: this must simply not
    # raise, and the derived totals must match summary's own claim.
    profile = _load("coverage-py-json.exitarc.json", "coverage-py-json")
    exits = profile.files["exits.py"]
    covered, total = _derived_totals(exits.branches.by_line)
    assert (covered, total) == (2, 4)


def test_lcov_branch_derives_the_documented_totals():
    profile = _load("lcov.branch.info", "lcov")
    sample = profile.files["sample.py"]
    covered, total = _derived_totals(sample.branches.by_line)
    # BRF:8 / BRH:4 in the raw file.
    assert (covered, total) == (4, 8)


def test_lcov_nobranch_is_unavailable():
    profile = _load("lcov.nobranch.info", "lcov")
    assert derive_branch_capability(profile) == "unavailable"
    for file_coverage in profile.files.values():
        assert file_coverage.branches is None


def test_lcov_exitarc_parses_without_error():
    profile = _load("lcov.exitarc.info", "lcov")
    exits = profile.files["exits.py"]
    covered, total = _derived_totals(exits.branches.by_line)
    # BRF:4 / BRH:2 in the raw file.
    assert (covered, total) == (2, 4)


def test_cobertura_branch_derives_the_documented_totals():
    profile = _load("cobertura.branch.xml", "cobertura")
    sample = profile.files["sample.py"]
    covered, total = _derived_totals(sample.branches.by_line)
    # root branches-valid="8" branches-covered="4" in the raw file.
    assert (covered, total) == (4, 8)


def test_cobertura_nobranch_is_unavailable():
    profile = _load("cobertura.nobranch.xml", "cobertura")
    assert derive_branch_capability(profile) == "unavailable"
    for file_coverage in profile.files.values():
        assert file_coverage.branches is None


# ---------------------------------------------------------------------------
# O2 — capability is not inferred from emptiness: the branch-free file in a
# branch-tracking artifact is "reported" with an EMPTY by_line, not None, and
# the whole artifact is not "mixed".
# ---------------------------------------------------------------------------


def test_coverage_py_json_branch_free_file_is_reported_with_empty_by_line():
    profile = _load("coverage-py-json.branch.json", "coverage-py-json")
    check_sample = profile.files["check_sample.py"]
    assert check_sample.branches is not None
    assert check_sample.branches.by_line == {}
    assert derive_branch_capability(profile) == "reported"


def test_lcov_branch_free_file_is_reported_with_empty_by_line():
    # lcov.branch.info's own witness: check_sample.py carries no BRF/BRH at
    # all while sample.py does -- a PER-FILE capability rule would call this
    # single real artifact "mixed" and refuse it.
    profile = _load("lcov.branch.info", "lcov")
    check_sample = profile.files["check_sample.py"]
    assert check_sample.branches is not None
    assert check_sample.branches.by_line == {}
    assert derive_branch_capability(profile) == "reported"


def test_cobertura_branch_free_file_is_reported_with_empty_by_line():
    profile = _load("cobertura.branch.xml", "cobertura")
    check_sample = profile.files["check_sample.py"]
    assert check_sample.branches is not None
    assert check_sample.branches.by_line == {}
    assert derive_branch_capability(profile) == "reported"


# ---------------------------------------------------------------------------
# A never-entered branch line reports zero covered arcs in every format
# (PROVENANCE.md's last row -- the invariant FileCoverage enforces once).
# ---------------------------------------------------------------------------


def test_a_never_entered_branch_line_reports_zero_covered_in_every_format():
    # line 18 in probe/sample.py (`never_called`): coverage.py [18,19]/
    # [18,20] both missing; lcov two `-`; Cobertura condition-coverage
    # "0% (0/2)".
    for name, fmt in (
        ("coverage-py-json.branch.json", "coverage-py-json"),
        ("lcov.branch.info", "lcov"),
        ("cobertura.branch.xml", "cobertura"),
    ):
        profile = _load(name, fmt)
        sample = profile.files["sample.py"]
        covered, total = sample.branches.by_line[18]
        assert (covered, total) == (0, 2), f"{fmt}: line 18 must be (0, 2)"
        assert 18 in sample.missing, f"{fmt}: line 18 must itself be missing"


# ---------------------------------------------------------------------------
# A-272 — coverage.py's own per-file `summary.num_branches`/
# `covered_branches` are NOT the cardinality of its own arc arrays, so the
# equality cross-check wave 1 shipped in `_check_branch_summary` is
# WITHDRAWN. This is a NEW, hand-built witness (never one of the eight
# frozen artifacts under this directory, which PROVENANCE.md forbids
# editing) reproducing the exact measured shape that made the withdrawn
# check a release-blocking false refusal: `covered_branches` GREATER than
# `len(executed_branches)`, and `num_branches` GREATER than the arcs
# actually listed across both arrays -- the real coverage.py 7.15.4 shape
# measured on `topos/src/topos/ui/banner.py` (`num_branches=108` vs 106
# arcs listed, `covered_branches=2` vs an EMPTY `executed_branches`) and
# `topos/src/topos/ui/hostmem.py` (`48` vs 46, `2` vs empty). The numbers
# below are scaled down for a unit test but preserve the identical
# property on purpose (`covered_branches=2` against an EMPTY
# `executed_branches`, `num_branches=5` against 3 arcs listed).
# ---------------------------------------------------------------------------


def _banner_shaped_document() -> dict:
    """One file, `shaped_like_banner.py`, whose `summary` disagrees with its
    own arc arrays exactly the way real coverage.py 7.15.4 output does
    (A-272). Lines 1 and 4 are both `executed` (branch source lines must be
    considered lines, never `missing` or `excluded` -- `FileCoverage`'s own
    invariant), each carrying only MISSING arcs -- `executed_branches` is
    empty, matching banner.py's/hostmem.py's own witnessed shape exactly.
    """
    return {
        "meta": {"branch_coverage": True},
        "files": {
            "shaped_like_banner.py": {
                "executed_lines": [1, 2, 3, 4],
                "missing_lines": [5],
                "excluded_lines": [],
                "executed_branches": [],
                "missing_branches": [[1, 2], [1, 3], [4, 5]],
                "summary": {"num_branches": 5, "covered_branches": 2},
            }
        },
    }


def test_a_real_shaped_summary_arc_mismatch_parses_clean():
    """The MUST-SUCCEED control: this is a real-shaped, untampered artifact
    (its shape is exactly what a real coverage.py 7.15.4 run produces) and
    R1 must be able to run against it, not refuse ERROR/UNREADABLE_ARTIFACT
    -- the false refusal A-272 exists to withdraw. The arc arrays, not the
    disagreeing `summary`, are authoritative: derived totals come from
    `missing_branches`/`executed_branches` alone.
    """
    document = _banner_shaped_document()
    profile = load_coverage_profile(json.dumps(document), declared_format="coverage-py-json")
    shaped = profile.files["shaped_like_banner.py"]
    assert shaped.branches.by_line == {1: (0, 2), 4: (0, 1)}
    covered, total = _derived_totals(shaped.branches.by_line)
    # The point of A-272: derived (0, 3) from the arrays disagrees with the
    # artifact's own stated summary (covered_branches=2, num_branches=5),
    # and that disagreement is exactly what must NOT refuse.
    assert (covered, total) == (0, 3)
    assert document["files"]["shaped_like_banner.py"]["summary"] == {
        "num_branches": 5,
        "covered_branches": 2,
    }


def test_a_tampered_variant_of_the_same_shape_still_refuses():
    """The paired TAMPERED case: on this exact real-shaped artifact -- the
    one the withdrawn cross-check would have refused for a legitimate
    reason -- an actual tamper (a repeated arc identity within
    `missing_branches`) still refuses, via `_check_arc_identity`, completely
    independent of whatever `summary` states. This is the proof that A-272
    did not weaken the invariant that actually catches a tampered artifact.
    """
    document = _banner_shaped_document()
    document["files"]["shaped_like_banner.py"]["missing_branches"].append([1, 2])
    with pytest.raises(AssayError) as caught:
        load_coverage_profile(json.dumps(document), declared_format="coverage-py-json")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "repeats an arc identity" in str(caught.value)
