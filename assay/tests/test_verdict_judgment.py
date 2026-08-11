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
    CanaryResult,
    Claim,
    Coverage,
    Helper,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR3,
    JudgmentResolved,
    MutantOutcome,
    Mutation,
    Verdict,
)
from assay.errors import Outcome, ReasonCode

#: sha256(b"<=") -- the replacement half of a `compare-swap` Lt->LtE site.
#: Hand-computed, not read back from the code under test (A-067): a hash the
#: producer supplies to a test that then asserts the producer's own hash
#: proves only that the function is deterministic.
_SHA_LTE = "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"

#: (P33/V5-1) the lane facts every computed tier above R0 shares. Hoisted out
#: of `BASE_R1_POLICY` because they are facts about what was judged, not about
#: R1's own policy -- which is precisely why an `R0,R2` lane could record none
#: of them under v4.
BASE_RESOLVED = dict(
    language="python",
    source_roots=("src",),
    base="a" * 40,
)

BASE_R1_POLICY = dict(
    coverage_format="coverage-py-json",
    coverage_artifact="cov.json",
    fail_under=100.0,
    allow_excluded=False,
)

#: (P33/V5-4) `kill_attribution` is REQUIRED on every `JudgmentR2`, and it is
#: derived from `kill_signal_artifact`'s presence -- which P33 refuses at
#: config load, so `unattributed` is what every lane in this build renders.
BASE_R2_POLICY = dict(kill_attribution="unattributed")

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
        exclusion_capability="reported",
        missing_lines={}, files_missing_coverage=(),
    )
    return Claim(
        rigor="R1", source="computed", status=Outcome.PASS,
        verified_by_assay=True, coverage=coverage,
    )


def r2_pass_claim(*, survivor_operator: str | None = None) -> Claim:
    survived = ()
    if survivor_operator is not None:
        survived = (
            MutantOutcome(
                path="a.py",
                lineno=1,
                start_byte=4,
                end_byte=5,
                replacement_sha256=_SHA_LTE,
                operator=survivor_operator,
                description="x->y",
            ),
        )
        status, reason_code = Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED
    else:
        status, reason_code = Outcome.PASS, None
    killed = (
        ()
        if survived
        else (
            MutantOutcome(
                path="a.py",
                lineno=1,
                start_byte=4,
                end_byte=5,
                replacement_sha256=_SHA_LTE,
                operator="python:compare-swap",
                description="Lt->LtE",
            ),
        )
    )
    mutation = Mutation(
        candidate_count=1, total=1, killed=killed, survived=survived
    )
    return Claim(
        rigor="R2", source="computed", status=status,
        verified_by_assay=True, reason_code=reason_code, mutation=mutation,
    )


def r3_pass_claim(*, mechanism: str = "uncovered-line") -> Claim:
    canary = CanaryResult(
        mechanism=mechanism,
        target="a.py",
        description="x",
        control_outcome=Outcome.PASS,
        transformed_outcome=Outcome.FAIL,
        expected_reason_code=ReasonCode.UNCOVERED_LINES,
        observed_reason_code=ReasonCode.UNCOVERED_LINES,
    )
    return Claim(
        rigor="R3", source="computed", status=Outcome.PASS,
        verified_by_assay=True, canary=canary,
    )


# --- Coverage: the new arithmetic invariants (P16) ---------------------------


def test_coverage_refuses_pct_disagreeing_with_covered_and_changed_executable():
    Coverage(covered=1, changed_executable=2, pct=50.0, considered=1, exclusion_capability="reported",
              missing_lines={"a.py": frozenset({1})}, files_missing_coverage=())  # untouched form

    with pytest.raises(ValueError, match="does not agree with"):
        Coverage(covered=1, changed_executable=2, pct=75.0, considered=1, exclusion_capability="reported",
                  missing_lines={"a.py": frozenset({1})}, files_missing_coverage=())


def test_coverage_refuses_a_zero_over_zero_pct_that_is_not_100():
    with pytest.raises(ValueError, match="does not agree with"):
        Coverage(covered=0, changed_executable=0, pct=0.0, considered=0,
                  exclusion_capability="reported",
                  missing_lines={}, files_missing_coverage=())


def test_coverage_refuses_missing_lines_total_disagreeing_with_the_summary():
    with pytest.raises(ValueError, match="must sum to the summary"):
        Coverage(covered=1, changed_executable=2, pct=50.0, considered=1, exclusion_capability="reported",
                  missing_lines={}, files_missing_coverage=())


def test_coverage_refuses_overlapping_missing_and_excluded_lines():
    with pytest.raises(ValueError, match="exactly one classification"):
        Coverage(
            covered=0, changed_executable=1, pct=0.0, considered=1,
            exclusion_capability="reported",
            missing_lines={"a.py": frozenset({1})}, files_missing_coverage=(),
            excluded_lines={"a.py": frozenset({1})}, files_with_excluded_lines=("a.py",),
        )


def test_coverage_refuses_overlapping_missing_and_unclassified_lines():
    with pytest.raises(ValueError, match="exactly one classification"):
        Coverage(
            covered=0, changed_executable=1, pct=0.0, considered=1,
            exclusion_capability="reported",
            missing_lines={"a.py": frozenset({1})}, files_missing_coverage=(),
            unclassified_lines={"a.py": frozenset({1})},
            files_with_unclassified_lines=("a.py",),
        )


def test_coverage_refuses_overlapping_excluded_and_unclassified_lines():
    with pytest.raises(ValueError, match="exactly one classification"):
        Coverage(
            covered=0, changed_executable=0, pct=100.0, considered=1,
            exclusion_capability="reported",
            missing_lines={}, files_missing_coverage=(),
            excluded_lines={"a.py": frozenset({1})}, files_with_excluded_lines=("a.py",),
            unclassified_lines={"a.py": frozenset({1})},
            files_with_unclassified_lines=("a.py",),
        )


# --- Coverage: each files_* summary names its own detail (P16 review) --------
#
# The arithmetic above binds the TOTALS to the detail. These bind the
# per-file SUMMARIES to it: without them a summary could name a file the
# detail never mentions, or stay empty while the detail names several, and
# a consumer reading "which files have a problem" -- the entire reason
# A-096 added these pairs -- would be answered by a field bound to nothing.


def test_coverage_refuses_files_with_excluded_lines_that_omits_a_named_file():
    with pytest.raises(ValueError, match="files_with_excluded_lines"):
        Coverage(
            covered=0, changed_executable=0, pct=100.0, considered=1,
            exclusion_capability="reported",
            missing_lines={}, files_missing_coverage=(),
            excluded_lines={"a.py": frozenset({1})}, files_with_excluded_lines=(),
        )


def test_coverage_refuses_files_with_excluded_lines_naming_an_unlisted_file():
    with pytest.raises(ValueError, match="files_with_excluded_lines"):
        Coverage(
            covered=0, changed_executable=0, pct=100.0, considered=1,
            exclusion_capability="reported",
            missing_lines={}, files_missing_coverage=(),
            excluded_lines={"a.py": frozenset({1})},
            files_with_excluded_lines=("a.py", "b.py"),
        )


def test_coverage_refuses_files_with_unclassified_lines_that_omits_a_named_file():
    with pytest.raises(ValueError, match="files_with_unclassified_lines"):
        Coverage(
            covered=0, changed_executable=0, pct=100.0, considered=1,
            exclusion_capability="reported",
            missing_lines={}, files_missing_coverage=(),
            unclassified_lines={"a.py": frozenset({1})},
            files_with_unclassified_lines=(),
        )


def test_coverage_refuses_files_missing_coverage_that_contributes_no_missing_line():
    """A CONTAINMENT, not an equality: a file with no coverage-artifact entry
    has every changed line recorded as missing, so it must appear in
    ``missing_lines`` -- while a file that DOES have an entry may contribute
    missing lines without belonging in this summary."""
    # The honest containment builds: "b.py" contributes missing lines from a
    # real artifact entry and is correctly absent from the summary.
    Coverage(
        covered=0, changed_executable=2, pct=0.0, considered=2,
        exclusion_capability="reported",
        missing_lines={"a.py": frozenset({1}), "b.py": frozenset({2})},
        files_missing_coverage=("a.py",),
    )

    with pytest.raises(ValueError, match="contribute no line"):
        Coverage(
            covered=0, changed_executable=1, pct=0.0, considered=2,
            exclusion_capability="reported",
            missing_lines={"a.py": frozenset({1})},
            files_missing_coverage=("a.py", "b.py"),
        )


# --- Claim: a judged status carries the payload it judged (P16 review) -------
#
# The converse of the three NO_MEASUREMENT rules: those forbid a payload
# where nothing was measured, these forbid a judged status where nothing
# was. Deleting the payload is the cheapest possible evasion of `assay
# verify`'s R1/R2/R3 re-derivation -- there is then nothing to re-derive,
# the rollup still agrees, and a PASS backed by no evidence at all is
# accepted.


def test_claim_refuses_an_r1_pass_or_fail_carrying_no_coverage():
    for status, reason_code in (
        (Outcome.PASS, None),
        (Outcome.FAIL, ReasonCode.UNCOVERED_LINES),
    ):
        with pytest.raises(ValueError, match="without a coverage payload"):
            Claim(
                rigor="R1", source="computed", status=status,
                verified_by_assay=True, reason_code=reason_code,
            )
    # NO_MEASUREMENT stays the one payload-free R1 claim (A-025/A-090).
    Claim(
        rigor="R1", source="computed", status=Outcome.NO_MEASUREMENT,
        verified_by_assay=True, reason_code=ReasonCode.DIRTY_TREE,
    )


def test_claim_refuses_an_r2_pass_carrying_no_mutation():
    with pytest.raises(ValueError, match="PASS without a mutation payload"):
        Claim(rigor="R2", source="computed", status=Outcome.PASS, verified_by_assay=True)


@pytest.mark.parametrize(
    "status,reason_code",
    [
        (Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED),
        (Outcome.INCONCLUSIVE, ReasonCode.NO_MUTANTS),
    ],
)
def test_claim_refuses_a_mutation_only_reason_code_carrying_no_mutation(
    status: Outcome, reason_code: ReasonCode
):
    """Neither code is in ``execute_command``'s baseline vocabulary, so a
    claim reusing a failed baseline's outcome can never carry one."""
    with pytest.raises(ValueError, match="without a mutation payload"):
        Claim(
            rigor="R2", source="computed", status=status,
            verified_by_assay=True, reason_code=reason_code,
        )


def test_an_r2_claim_reusing_a_failed_baseline_still_needs_no_mutation():
    """The legitimate payload-free R2 claim (A-116): the baseline never
    passed, so mutation never began and the claim reuses the baseline's own
    outcome verbatim."""
    Claim(
        rigor="R2", source="computed", status=Outcome.FAIL,
        verified_by_assay=True, reason_code=ReasonCode.COMMAND_FAILED,
    )


@pytest.mark.parametrize(
    "status,reason_code",
    [
        (Outcome.PASS, None),
        (Outcome.FAIL, ReasonCode.CANARY_SURVIVED),
        (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE),
    ],
)
def test_claim_refuses_a_judged_r3_status_carrying_no_canary(
    status: Outcome, reason_code: ReasonCode | None
):
    """``judge_canary`` returns exactly these three, and
    ``build_canary_claim`` attaches the result it judged to every one."""
    with pytest.raises(ValueError, match="without a canary payload"):
        Claim(
            rigor="R3", source="computed", status=status,
            verified_by_assay=True, reason_code=reason_code,
        )


def test_an_r3_claim_whose_machinery_failed_needs_no_canary():
    """ERROR/BUDGET_EXCEEDED describe the canary machinery never producing a
    result, not a judgement of one -- so they stay representable."""
    Claim(
        rigor="R3", source="computed", status=Outcome.ERROR,
        verified_by_assay=True, reason_code=ReasonCode.EXEC_FAILED,
    )


# --- JudgmentR1 --------------------------------------------------------------


def test_judgment_r1_untouched_form_builds():
    policy = JudgmentR1(**BASE_R1_POLICY)
    assert policy.coverage_format == "coverage-py-json"
    assert policy.to_dict()["fail_under"] == 100.0
    # P33/V5-1: the three hoisted keys are GONE from R1, not merely optional.
    # An R1 policy that still accepted them would leave two places to record
    # one fact, which is the shape v5 exists to remove.
    assert set(policy.to_dict()) == {
        "coverage_format",
        "coverage_artifact",
        "fail_under",
        "allow_excluded",
    }


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"coverage_format": ""}, "coverage_format"),
        ({"coverage_artifact": ""}, "coverage_artifact"),
        ({"fail_under": "100"}, "must be a number"),
        ({"fail_under": True}, "must be a number"),
        ({"fail_under": 101.0}, "between 0 and 100"),
        ({"fail_under": -1.0}, "between 0 and 100"),
        ({"allow_excluded": "false"}, "must be a boolean"),
    ],
)
def test_judgment_r1_refuses_malformed_fields(overrides: dict, match: str):
    with pytest.raises(ValueError, match=match):
        JudgmentR1(**{**BASE_R1_POLICY, **overrides})


# --- JudgmentResolved (P33/V5-1) ---------------------------------------------


def test_judgment_resolved_untouched_form_builds():
    resolved = JudgmentResolved(**BASE_RESOLVED)
    assert resolved.language == "python"
    assert resolved.to_dict()["base"] == "a" * 40


def test_judgment_resolved_omits_base_when_none_rather_than_nulling_it():
    """A-051's discipline, one field over: absent means "no tier here reads a
    comparison commit", which is a different statement from `base: null`."""
    resolved = JudgmentResolved(language="python", source_roots=("src",))
    assert "base" not in resolved.to_dict()


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"language": ""}, "language"),
        ({"source_roots": []}, "non-empty tuple"),
        ({"source_roots": ()}, "non-empty tuple"),
        ({"source_roots": ("",)}, "source_roots entry"),
        ({"base": ""}, "base"),
    ],
)
def test_judgment_resolved_refuses_malformed_fields(overrides: dict, match: str):
    with pytest.raises(ValueError, match=match):
        JudgmentResolved(**{**BASE_RESOLVED, **overrides})


# --- JudgmentR2 (reserved) ----------------------------------------------------


def test_judgment_r2_untouched_form_builds():
    r2 = JudgmentR2(
        jobs=4,
        max_mutants=50,
        operators=("python:compare-swap", "python:boolop-swap"),
        **BASE_R2_POLICY,
    )
    assert r2.to_dict() == {
        "jobs": 4,
        "max_mutants": 50,
        "operators": ["python:compare-swap", "python:boolop-swap"],
        "kill_attribution": "unattributed",
    }
    # The two P34-reserved paths are OMITTED, never nulled (A-051/A-230b).
    assert "kill_signal_artifact" not in r2.to_dict()
    assert "equivalence_artifact" not in r2.to_dict()


def test_judgment_r2_kill_attribution_and_its_artifact_cannot_disagree():
    """A-223(b): attribution is DERIVED from `kill_signal_artifact`'s
    presence, so the model refuses the two states in which they disagree --
    the same reason A-036 derives `argv_modified` from `argv_appended`."""
    with pytest.raises(ValueError, match="only\n?.*consistent value here is 'declared'"):
        JudgmentR2(
            jobs=1,
            max_mutants=50,
            operators=("python:compare-swap",),
            kill_attribution="unattributed",
            kill_signal_artifact=".assay/kill-signal.txt",
        )
    with pytest.raises(ValueError, match="no kill_signal_artifact"):
        JudgmentR2(
            jobs=1,
            max_mutants=50,
            operators=("python:compare-swap",),
            kill_attribution="declared",
        )


def test_judgment_r2_refuses_an_unknown_kill_attribution():
    with pytest.raises(ValueError, match="kill_attribution must be one of"):
        JudgmentR2(
            jobs=1,
            max_mutants=50,
            operators=("python:compare-swap",),
            kill_attribution="probably",
        )


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"jobs": "4", "max_mutants": 50, "operators": ("python:compare-swap",)}, "must be an integer"),
        ({"jobs": True, "max_mutants": 50, "operators": ("python:compare-swap",)}, "must be an integer"),
        ({"jobs": 0, "max_mutants": 50, "operators": ("python:compare-swap",)}, ">= 1"),
        ({"jobs": 4, "max_mutants": 50, "operators": []}, "non-empty tuple"),
        ({"jobs": 4, "max_mutants": 50, "operators": ()}, "non-empty tuple"),
        # P21 work item 2: the vocabulary is CLOSED in the model now, so an
        # empty string is refused as an unknown operator rather than merely
        # as an empty one -- v3 accepted any non-empty string here while the
        # shipped schema's own enum rejected it.
        ({"jobs": 4, "max_mutants": 50, "operators": ("",)}, "unknown operator"),
        ({"jobs": 4, "max_mutants": 50, "operators": ("invented-swap",)}, "unknown operator"),
        ({"jobs": 4, "max_mutants": 50, "operators": ("python:compare-swap", "python:compare-swap")}, "duplicate"),
        # P21/A-163: the declared ceiling is bounded at both ends.
        ({"jobs": 4, "max_mutants": "50", "operators": ("python:compare-swap",)}, "must be an integer"),
        ({"jobs": 4, "max_mutants": True, "operators": ("python:compare-swap",)}, "must be an integer"),
        ({"jobs": 4, "max_mutants": 0, "operators": ("python:compare-swap",)}, "1..10,000"),
        ({"jobs": 4, "max_mutants": 10_001, "operators": ("python:compare-swap",)}, "1..10,000"),
    ],
)
def test_judgment_r2_refuses_malformed_fields(kwargs: dict, match: str):
    with pytest.raises(ValueError, match=match):
        JudgmentR2(**{**kwargs, **BASE_R2_POLICY})


@pytest.mark.parametrize(
    "operators",
    [("sql:drop-check",), ("go:compare-swap",), ("python:compare-swap", "sql:drop-check")],
)
def test_judgment_r2_operators_may_name_any_language_the_catalogue_knows(operators):
    """(P33/V5-2) The MODEL closes the catalogue; it does not close the
    LANGUAGE, because the language lives in a sibling object it cannot see
    from here. `Verdict` owns the prefix-equals-resolved-language rule, and
    the test below proves it. Splitting the two is what lets a SQL lane's own
    policy build at all."""
    r2 = JudgmentR2(
        jobs=1, max_mutants=50, operators=operators, **BASE_R2_POLICY
    )
    assert r2.to_dict()["operators"] == list(operators)


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
    """(P33/A-223g) v4 expressed this as `minProperties: 1`, which silently
    stopped meaning it once `resolved` became a fourth key: a judgment holding
    only `resolved` would have satisfied the count while judging nothing."""
    with pytest.raises(ValueError, match="declares none of r1/r2/r3"):
        Judgment(resolved=JudgmentResolved(**BASE_RESOLVED))


def test_judgment_to_dict_includes_only_the_populated_members():
    resolved = JudgmentResolved(**BASE_RESOLVED)
    r2 = JudgmentR2(
        jobs=2, max_mutants=50, operators=("python:compare-swap",), **BASE_R2_POLICY
    )
    r3 = JudgmentR3(mechanism="uncovered-line", target="pkg/mod.py")
    r3_only = JudgmentResolved(language="python", source_roots=("src",))

    assert Judgment(resolved=resolved, r2=r2).to_dict() == {
        "resolved": resolved.to_dict(),
        "r2": r2.to_dict(),
    }
    assert Judgment(resolved=r3_only, r3=r3).to_dict() == {
        "resolved": r3_only.to_dict(),
        "r3": r3.to_dict(),
    }
    assert Judgment(resolved=resolved, r2=r2, r3=r3).to_dict() == {
        "resolved": resolved.to_dict(),
        "r2": r2.to_dict(),
        "r3": r3.to_dict(),
    }


# --- Judgment: the conditional base (P33/A-223a, A-227) ----------------------


def test_judgment_requires_a_base_when_r1_or_r2_is_present():
    """Both tiers scope a diff to a resolved comparison commit, so a judgment
    that carries one and records no base is unre-derivable."""
    for tier in (
        {"r1": JudgmentR1(**BASE_R1_POLICY)},
        {
            "r2": JudgmentR2(
                jobs=1,
                max_mutants=50,
                operators=("python:compare-swap",),
                **BASE_R2_POLICY,
            )
        },
    ):
        with pytest.raises(ValueError, match="records no base"):
            Judgment(
                resolved=JudgmentResolved(language="python", source_roots=("src",)),
                **tier,
            )


def test_judgment_forbids_a_base_when_only_r3_is_present():
    """The only-if half (A-227). `JUDGE_FIELDS_BY_RIGOR` gives R3 no base, so
    an r3-only judgment recording one describes a comparison nothing in it
    made. Unreachable for this producer -- A-062 refuses `judge.base` on an
    R0,R3 lane as inert config -- and reachable for any foreign document,
    which is the population `verify.py` exists for."""
    with pytest.raises(ValueError, match="neither r1 nor r2"):
        Judgment(
            resolved=JudgmentResolved(**BASE_RESOLVED),
            r3=JudgmentR3(mechanism="uncovered-line", target="pkg/mod.py"),
        )


def test_judgment_refuses_a_resolved_that_is_not_a_judgment_resolved():
    with pytest.raises(ValueError, match="must be a JudgmentResolved"):
        Judgment(resolved={"language": "python"}, r1=JudgmentR1(**BASE_R1_POLICY))


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
            judgment=Judgment(
                resolved=JudgmentResolved(**BASE_RESOLVED),
                r1=JudgmentR1(**BASE_R1_POLICY),
            ),
        )


def test_verdict_refuses_judgment_r1_present_without_an_r1_coverage_claim():
    with pytest.raises(ValueError, match="no R1 claim rendered a coverage payload"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.PASS,
            declared_rigor=("R0",),
            claims=(r0_pass(),),
            judgment=Judgment(
                resolved=JudgmentResolved(**BASE_RESOLVED),
                r1=JudgmentR1(**BASE_R1_POLICY),
            ),
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
        judgment=Judgment(
            resolved=JudgmentResolved(**BASE_RESOLVED),
            r1=JudgmentR1(**BASE_R1_POLICY),
        ),
    )
    # P33/V5-1: the language now lives on `resolved`, where an R0,R2 lane can
    # record it too.
    assert verdict.judgment.resolved.language == "python"


# --- Verdict: the judgment.r2 <-> R2-mutation correspondence (P19/A-148) ----


def test_verdict_refuses_judgment_r2_present_without_an_r2_mutation_claim():
    with pytest.raises(ValueError, match="no R2 claim rendered a mutation payload"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.PASS,
            declared_rigor=("R0",),
            claims=(r0_pass(),),
            judgment=Judgment(
                resolved=JudgmentResolved(**BASE_RESOLVED),
                r2=JudgmentR2(
                    jobs=1,
                    max_mutants=50,
                    operators=("python:compare-swap",),
                    **BASE_R2_POLICY,
                ),
            ),
        )


def test_verdict_refuses_an_r2_mutation_claim_without_judgment_r2():
    verdict_kwargs = dict(
        **BASE_VERDICT,
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.MUTANTS_SURVIVED,
        declared_rigor=("R0", "R2"),
        claims=(r0_pass(), r2_pass_claim(survivor_operator="python:compare-swap")),
    )
    with pytest.raises(ValueError, match="judgment.r2 is absent"):
        Verdict(**verdict_kwargs)


def test_verdict_accepts_the_matched_r2_mutation_and_judgment_pair():
    verdict = Verdict(
        **BASE_VERDICT,
        outcome=Outcome.PASS,
        declared_rigor=("R0", "R2"),
        claims=(r0_pass(), r2_pass_claim()),
        judgment=Judgment(
            resolved=JudgmentResolved(**BASE_RESOLVED),
            r2=JudgmentR2(
                jobs=1,
                max_mutants=50,
                operators=("python:compare-swap",),
                **BASE_R2_POLICY,
            ),
        ),
    )
    assert verdict.judgment.r2.jobs == 1


def test_verdict_refuses_a_survivor_naming_an_operator_judgment_r2_never_declared():
    with pytest.raises(ValueError, match="never selected"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.MUTANTS_SURVIVED,
            declared_rigor=("R0", "R2"),
            claims=(r0_pass(), r2_pass_claim(survivor_operator="python:compare-swap")),
            judgment=Judgment(
                resolved=JudgmentResolved(**BASE_RESOLVED),
                r2=JudgmentR2(
                    jobs=1,
                    max_mutants=50,
                    operators=("python:boolop-swap",),
                    **BASE_R2_POLICY,
                ),
            ),
        )


# --- Verdict: the judgment.r3 <-> R3-canary correspondence (P19/A-148) -----


def test_verdict_refuses_judgment_r3_present_without_an_r3_canary_claim():
    with pytest.raises(ValueError, match="no R3 claim rendered a canary payload"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.PASS,
            declared_rigor=("R0",),
            claims=(r0_pass(),),
            judgment=Judgment(
                resolved=JudgmentResolved(language="python", source_roots=("src",)),
                r3=JudgmentR3(mechanism="uncovered-line", target="pkg/mod.py"),
            ),
        )


def test_verdict_refuses_an_r3_canary_claim_without_judgment_r3():
    with pytest.raises(ValueError, match="judgment.r3 is absent"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.PASS,
            declared_rigor=("R0", "R3"),
            claims=(r0_pass(), r3_pass_claim()),
        )


def test_verdict_accepts_the_matched_r3_canary_and_judgment_pair():
    verdict = Verdict(
        **BASE_VERDICT,
        outcome=Outcome.PASS,
        declared_rigor=("R0", "R3"),
        claims=(r0_pass(), r3_pass_claim()),
        judgment=Judgment(
            # P33/A-223a: an R0,R3 judgment records NO base -- R3 reads none.
            resolved=JudgmentResolved(language="python", source_roots=("src",)),
            # P21/A-152: the payload's own target must EQUAL this one now.
            # Under v3 these two could name different files and nothing in
            # the artifact could tell, which is the gap A-152 recorded as
            # accepted-and-waiting-for-v4.
            r3=JudgmentR3(mechanism="uncovered-line", target="a.py"),
        ),
    )
    assert verdict.judgment.r3.target == "a.py"


# --- Verdict: P33's cross-object invariants ----------------------------------


def _sql_mutant(*, operator: str, kill_signal: str | None = None) -> MutantOutcome:
    return MutantOutcome(
        path="schema/001.sql",
        lineno=3,
        start_byte=10,
        end_byte=20,
        replacement_sha256="c" * 64,
        operator=operator,
        description="drop the constraint",
        kill_signal=kill_signal,
    )


def _r2_verdict(*, mutation: Mutation, policy: JudgmentR2, language: str, **overrides):
    """One R0,R2 verdict, so each invariant below is a same-document
    differential rather than a comparison between two different fixtures."""
    status, reason_code = (
        (Outcome.FAIL, ReasonCode.MUTANTS_SURVIVED)
        if mutation.survived
        else (Outcome.INCONCLUSIVE, ReasonCode.ALL_MUTANTS_EQUIVALENT)
        if mutation.equivalent and not mutation.killed
        else (Outcome.PASS, None)
    )
    return Verdict(
        **BASE_VERDICT,
        outcome=status,
        reason_code=reason_code,
        declared_rigor=("R0", "R2"),
        claims=(
            r0_pass(),
            Claim(
                rigor="R2",
                source="computed",
                status=status,
                verified_by_assay=True,
                reason_code=reason_code,
                mutation=mutation,
            ),
        ),
        judgment=Judgment(
            resolved=JudgmentResolved(
                language=language, source_roots=("schema",), base="a" * 40
            ),
            r2=policy,
        ),
        **overrides,
    )


def test_verdict_refuses_an_operator_whose_prefix_is_not_the_resolved_language():
    """(P33/V5-2, invariant 1) The schema closes each language's vocabulary
    but has no `$data`, so it cannot relate an operator to the language
    recorded elsewhere. Without this a Python lane records a full set of SQL
    operators it could not possibly have applied, and every layer agrees the
    document is well formed."""
    sql_policy = JudgmentR2(
        jobs=1, max_mutants=50, operators=("sql:drop-check",), **BASE_R2_POLICY
    )
    mutation = Mutation(
        candidate_count=1, total=1, killed=(_sql_mutant(operator="sql:drop-check"),)
    )
    # The control: a genuinely SQL lane builds.
    assert _r2_verdict(mutation=mutation, policy=sql_policy, language="sql")
    # The defect: the same policy and payload under a python resolution.
    with pytest.raises(ValueError, match="another language|judgment.resolved.language"):
        _r2_verdict(mutation=mutation, policy=sql_policy, language="python")


def test_verdict_refuses_equivalent_mutants_with_no_declared_equivalence_artifact():
    """(P33/V5-3, invariant 2) Equivalence is proven by comparing the declared
    artifact's bytes; with none declared the claim was inferred from something
    else, which is what A-209's both-present pattern forbids."""
    mutation = Mutation(
        candidate_count=1,
        total=1,
        equivalent=(_sql_mutant(operator="sql:drop-check"),),
    )
    paired = JudgmentR2(
        jobs=1,
        max_mutants=50,
        operators=("sql:drop-check",),
        equivalence_artifact=".assay/schema-dump.sql",
        **BASE_R2_POLICY,
    )
    assert _r2_verdict(mutation=mutation, policy=paired, language="sql")
    unpaired = JudgmentR2(
        jobs=1, max_mutants=50, operators=("sql:drop-check",), **BASE_R2_POLICY
    )
    with pytest.raises(ValueError, match="declares no\n?.*equivalence_artifact"):
        _r2_verdict(mutation=mutation, policy=unpaired, language="sql")


def test_verdict_refuses_an_unattributed_run_carrying_a_kill_signal():
    """(P33/V5-4, invariant 4) The clause the schema deliberately leaves open:
    a `kill_signal` on a KILLED entry is locally legal, so only a layer that
    can see `kill_attribution` can refuse it."""
    clean = Mutation(
        candidate_count=1, total=1, killed=(_sql_mutant(operator="sql:drop-check"),)
    )
    policy = JudgmentR2(
        jobs=1, max_mutants=50, operators=("sql:drop-check",), **BASE_R2_POLICY
    )
    assert _r2_verdict(mutation=clean, policy=policy, language="sql")
    signalled = Mutation(
        candidate_count=1,
        total=1,
        killed=(_sql_mutant(operator="sql:drop-check", kill_signal="23514"),),
    )
    with pytest.raises(ValueError, match="has no mechanism to name"):
        _r2_verdict(mutation=signalled, policy=policy, language="sql")


def test_verdict_refuses_a_declared_attribution_leaving_a_kill_unexplained():
    """Invariant 4's other clause: attribution that covers only some kills is
    not attribution."""
    policy = JudgmentR2(
        jobs=1,
        max_mutants=50,
        operators=("sql:drop-check",),
        kill_attribution="declared",
        kill_signal_artifact=".assay/kill-signal.txt",
    )
    explained = Mutation(
        candidate_count=1,
        total=1,
        killed=(_sql_mutant(operator="sql:drop-check", kill_signal="23514"),),
    )
    assert _r2_verdict(mutation=explained, policy=policy, language="sql")
    unexplained = Mutation(
        candidate_count=1, total=1, killed=(_sql_mutant(operator="sql:drop-check"),)
    )
    with pytest.raises(ValueError, match="carry no\n?.*kill_signal"):
        _r2_verdict(mutation=unexplained, policy=policy, language="sql")


def test_mutation_refuses_a_kill_signal_outside_the_killed_bucket():
    """(P33/A-223e) A kill signal names the mechanism that refused a mutant;
    a survived, crashed, budget-stopped or provably-inert mutant was refused
    by nothing. Checked on every non-killed bucket, not just one."""
    for bucket in ("survived", "crashed", "budget_exceeded", "equivalent"):
        with pytest.raises(ValueError, match="only a killed mutant was refused"):
            Mutation(
                candidate_count=1,
                total=1,
                **{
                    bucket: (
                        _sql_mutant(operator="sql:drop-check", kill_signal="23514"),
                    )
                },
            )


def test_verdict_refuses_a_helper_entry_with_no_correspondingly_judged_claim():
    """(P33/V5-5, A-223c) Only the observable direction. The converse -- a
    claim produced with a helper requires an entry -- has no readable
    antecedent in the artifact bytes, so P34 owns it."""
    mutation = Mutation(
        candidate_count=1, total=1, killed=(_sql_mutant(operator="sql:drop-check"),)
    )
    policy = JudgmentR2(
        jobs=1, max_mutants=50, operators=("sql:drop-check",), **BASE_R2_POLICY
    )
    helper = Helper(
        role="mutation-sites",
        tool="assay-sql-sites",
        resolved_path="/opt/assay-helpers/bin/assay-sql-sites",
        identity="assay-sql-sites 0.1.0",
    )
    # An R2 mutation payload is exactly what a mutation-sites helper produces.
    assert _r2_verdict(
        mutation=mutation, policy=policy, language="sql", helpers=(helper,)
    )
    # A statement-positions helper needs an R1 coverage payload, and this
    # verdict has none.
    positions = Helper(
        role="statement-positions",
        tool="assay-go-positions",
        resolved_path="/opt/assay-helpers/bin/assay-go-positions",
        identity="assay-go-positions 0.1.0",
    )
    with pytest.raises(ValueError, match="carries no an R1 claim|statement-positions"):
        _r2_verdict(
            mutation=mutation, policy=policy, language="sql", helpers=(positions,)
        )


def test_verdict_refuses_an_empty_helpers_array():
    """(A-230a) Omission is the emission default; `helpers: []` would assert a
    known-empty fact nothing witnessed."""
    mutation = Mutation(
        candidate_count=1, total=1, killed=(_sql_mutant(operator="sql:drop-check"),)
    )
    policy = JudgmentR2(
        jobs=1, max_mutants=50, operators=("sql:drop-check",), **BASE_R2_POLICY
    )
    with pytest.raises(ValueError, match="present but empty"):
        _r2_verdict(mutation=mutation, policy=policy, language="sql", helpers=())


def test_verdict_refuses_a_canary_payload_whose_mechanism_disagrees_with_judgment_r3():
    with pytest.raises(ValueError, match="must name the same mechanism"):
        Verdict(
            **BASE_VERDICT,
            outcome=Outcome.PASS,
            declared_rigor=("R0", "R3"),
            claims=(r0_pass(), r3_pass_claim(mechanism="uncovered-line")),
            judgment=Judgment(
                resolved=JudgmentResolved(language="python", source_roots=("src",)),
                r3=JudgmentR3(mechanism="import-break", target="pkg/mod.py"),
            ),
        )
