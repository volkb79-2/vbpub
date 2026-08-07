"""P16 — the resolved judge policy (:class:`~assay.verdict.Judgment` and
its ``r1``/``r2``/``r3`` payloads), the new ``Coverage`` arithmetic
invariants, and :class:`~assay.verdict.Verdict`'s own ``scope``/
``enforcement``/``judgment`` construction-time rules.

The claim this module defends, restated from sol finding 2: *schema v2 did
not record the policy that decided an R1 status, so an independent
consumer could not re-derive whether it was correct from the payload
alone.* Every rejection here is the construction-time half of closing that
gap — the artifact-level half lives in ``assay.verify`` and is proven in
``test_verdict_conformance.py``.
"""

from __future__ import annotations

import pytest

from assay.verdict import (
    Claim,
    Coverage,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR3,
    Verdict,
)
from assay.errors import Outcome, ReasonCode

BASE_R1_POLICY = dict(
    language="python",
    source_roots=("src",),
    coverage_format="coverage-py-json",
    coverage_artifact="cov.json",
    fail_under=100.0,
    allow_excluded=False,
    base="a" * 40,
)

BASE_VERDICT = {
    "lane": "package",
    "commit": "b" * 40,
    "started": "2026-08-07T09:00:00+00:00",
    "ended": "2026-08-07T09:00:01+00:00",
    "assay_version": "0.1.0",
    "declared_evidence": (),
    "argv_declared": ("pytest", "-q"),
    "argv_appended": (),
    "argv_effective": ("pytest", "-q"),
    "env_declared": {},
    "env_effective": {},
    "scope": "S1",
    "enforcement": "gate",
}


def r0_pass() -> Claim:
    return Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)


def r1_pass_claim() -> Claim:
    coverage = Coverage(
        covered=2, changed_executable=2, pct=100.0, considered=1,
        missing_lines={}, files_missing_coverage=(),
    )
    return Claim(
        rigor="R1", source="computed", status=Outcome.PASS,
        verified_by_assay=True, coverage=coverage,
    )


# --- Coverage: the new arithmetic invariants (P16) ---------------------------


def test_coverage_refuses_pct_disagreeing_with_covered_and_changed_executable():
    Coverage(covered=1, changed_executable=2, pct=50.0, considered=1,
              missing_lines={"a.py": frozenset({1})}, files_missing_coverage=())  # untouched form

    with pytest.raises(ValueError, match="does not agree with"):
        Coverage(covered=1, changed_executable=2, pct=75.0, considered=1,
                  missing_lines={"a.py": frozenset({1})}, files_missing_coverage=())


def test_coverage_refuses_a_zero_over_zero_pct_that_is_not_100():
    with pytest.raises(ValueError, match="does not agree with"):
        Coverage(covered=0, changed_executable=0, pct=0.0, considered=0,
                  missing_lines={}, files_missing_coverage=())


def test_coverage_refuses_missing_lines_total_disagreeing_with_the_summary():
    with pytest.raises(ValueError, match="must sum to the summary"):
        Coverage(covered=1, changed_executable=2, pct=50.0, considered=1,
                  missing_lines={}, files_missing_coverage=())


def test_coverage_refuses_overlapping_missing_and_excluded_lines():
    with pytest.raises(ValueError, match="exactly one classification"):
        Coverage(
            covered=0, changed_executable=1, pct=0.0, considered=1,
            missing_lines={"a.py": frozenset({1})}, files_missing_coverage=(),
            excluded_lines={"a.py": frozenset({1})}, files_with_excluded_lines=("a.py",),
        )


def test_coverage_refuses_overlapping_missing_and_unclassified_lines():
    with pytest.raises(ValueError, match="exactly one classification"):
        Coverage(
            covered=0, changed_executable=1, pct=0.0, considered=1,
            missing_lines={"a.py": frozenset({1})}, files_missing_coverage=(),
            unclassified_lines={"a.py": frozenset({1})},
            files_with_unclassified_lines=("a.py",),
        )


def test_coverage_refuses_overlapping_excluded_and_unclassified_lines():
    with pytest.raises(ValueError, match="exactly one classification"):
        Coverage(
            covered=0, changed_executable=0, pct=100.0, considered=1,
            missing_lines={}, files_missing_coverage=(),
            excluded_lines={"a.py": frozenset({1})}, files_with_excluded_lines=("a.py",),
            unclassified_lines={"a.py": frozenset({1})},
            files_with_unclassified_lines=("a.py",),
        )


# --- JudgmentR1 --------------------------------------------------------------


def test_judgment_r1_untouched_form_builds():
    policy = JudgmentR1(**BASE_R1_POLICY)
    assert policy.language == "python"
    assert policy.to_dict()["base"] == "a" * 40


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"language": ""}, "language"),
        ({"source_roots": []}, "non-empty tuple"),
        ({"source_roots": ()}, "non-empty tuple"),
        ({"source_roots": ("",)}, "source_roots entry"),
        ({"coverage_format": ""}, "coverage_format"),
        ({"coverage_artifact": ""}, "coverage_artifact"),
        ({"fail_under": "100"}, "must be a number"),
        ({"fail_under": True}, "must be a number"),
        ({"fail_under": 101.0}, "between 0 and 100"),
        ({"fail_under": -1.0}, "between 0 and 100"),
        ({"allow_excluded": "false"}, "must be a boolean"),
        ({"base": ""}, "base"),
    ],
)
def test_judgment_r1_refuses_malformed_fields(overrides: dict, match: str):
    with pytest.raises(ValueError, match=match):
        JudgmentR1(**{**BASE_R1_POLICY, **overrides})


# --- JudgmentR2 (reserved) ----------------------------------------------------


def test_judgment_r2_untouched_form_builds():
    r2 = JudgmentR2(jobs=4, operators=("compare-swap", "boolop-swap"))
    assert r2.to_dict() == {"jobs": 4, "operators": ["compare-swap", "boolop-swap"]}


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"jobs": "4", "operators": ("compare-swap",)}, "must be an integer"),
        ({"jobs": True, "operators": ("compare-swap",)}, "must be an integer"),
        ({"jobs": 0, "operators": ("compare-swap",)}, ">= 1"),
        ({"jobs": 4, "operators": []}, "non-empty tuple"),
        ({"jobs": 4, "operators": ()}, "non-empty tuple"),
        ({"jobs": 4, "operators": ("",)}, "operators entry"),
        ({"jobs": 4, "operators": ("compare-swap", "compare-swap")}, "duplicate"),
    ],
)
def test_judgment_r2_refuses_malformed_fields(kwargs: dict, match: str):
    with pytest.raises(ValueError, match=match):
        JudgmentR2(**kwargs)


# --- JudgmentR3 (reserved) ----------------------------------------------------


def test_judgment_r3_untouched_form_builds():
    r3 = JudgmentR3(mechanism="uncovered-line", target="pkg/mod.py")
    assert r3.to_dict() == {"mechanism": "uncovered-line", "target": "pkg/mod.py"}


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"mechanism": "", "target": "pkg/mod.py"}, "mechanism"),
        ({"mechanism": "uncovered-line", "target": ""}, "target"),
    ],
)
def test_judgment_r3_refuses_malformed_fields(kwargs: dict, match: str):
    with pytest.raises(ValueError, match=match):
        JudgmentR3(**kwargs)


# --- Judgment ------------------------------------------------------------


def test_judgment_refuses_when_none_of_r1_r2_r3_are_given():
    with pytest.raises(ValueError, match="declares none of r1/r2/r3"):
        Judgment()


def test_judgment_to_dict_includes_only_the_populated_members():
    r2 = JudgmentR2(jobs=2, operators=("compare-swap",))
    r3 = JudgmentR3(mechanism="uncovered-line", target="pkg/mod.py")

    assert Judgment(r2=r2).to_dict() == {"r2": r2.to_dict()}
    assert Judgment(r3=r3).to_dict() == {"r3": r3.to_dict()}
    assert Judgment(r2=r2, r3=r3).to_dict() == {"r2": r2.to_dict(), "r3": r3.to_dict()}


# --- Verdict: scope/enforcement -----------------------------------------------


def test_verdict_refuses_an_unknown_scope():
    with pytest.raises(ValueError, match="scope must be one of"):
        Verdict(
            **{**BASE_VERDICT, "scope": "S9"},
            outcome=Outcome.PASS, declared_rigor=("R0",), claims=(r0_pass(),),
        )


def test_verdict_refuses_an_unknown_enforcement():
    with pytest.raises(ValueError, match="enforcement must be one of"):
        Verdict(
            **{**BASE_VERDICT, "enforcement": "advisory-ish"},
            outcome=Outcome.PASS, declared_rigor=("R0",), claims=(r0_pass(),),
        )


# --- Verdict: the judgment.r1 <-> R1-coverage correspondence -----------------


def test_verdict_refuses_judgment_present_without_a_resolved_lane():
    with pytest.raises(ValueError, match="no lane resolved"):
        Verdict(
            lane="package",
            commit="c" * 40,
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
            started="2026-08-07T09:00:00+00:00",
            ended="2026-08-07T09:00:01+00:00",
            assay_version="0.1.0",
            judgment=Judgment(r1=JudgmentR1(**BASE_R1_POLICY)),
        )


def test_verdict_refuses_judgment_r1_present_without_an_r1_coverage_claim():
    with pytest.raises(ValueError, match="no R1 claim rendered a coverage payload"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.PASS,
            declared_rigor=("R0",),
            claims=(r0_pass(),),
            judgment=Judgment(r1=JudgmentR1(**BASE_R1_POLICY)),
        )


def test_verdict_refuses_an_r1_coverage_claim_without_judgment_r1():
    with pytest.raises(ValueError, match="judgment.r1 is absent"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.PASS,
            declared_rigor=("R0", "R1"),
            claims=(r0_pass(), r1_pass_claim()),
        )


def test_verdict_accepts_the_matched_r1_coverage_and_judgment_pair():
    verdict = Verdict(
        **BASE_VERDICT,
        outcome=Outcome.PASS,
        declared_rigor=("R0", "R1"),
        claims=(r0_pass(), r1_pass_claim()),
        judgment=Judgment(r1=JudgmentR1(**BASE_R1_POLICY)),
    )
    assert verdict.judgment.r1.language == "python"
