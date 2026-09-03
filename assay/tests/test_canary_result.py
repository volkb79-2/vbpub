"""O2/O3/A-109 -- :class:`~assay.verdict.CanaryAttempt`'s and
:class:`~assay.verdict.CanaryResult`'s own construction-time discipline (the
exact template :class:`~assay.verdict.Coverage` already established, restated
for canary's own named fields), and :func:`assay.canary.judge_attempt`/
:func:`assay.canary.judge_canary`'s pure judgement logic, tested directly at
the function level -- every one of A-109's terminal cases, independent of
which orchestration (Python or Go) produced the evidence.

**Schema v10 (B007/A-432)** split the v9 payload in two: the R3 payload is now
``CanaryResult = {mechanism, attempts}`` and each per-target record is a
``CanaryAttempt``. Every v9 rule below is restated on whichever half now owns
it -- the mechanism on the result, everything per-target on the attempt --
and the new rules (the disposition fork, the bound, the closed aggregation)
have their own sections at the end.

The negative this defends (O1/O3, verbatim): *a universal-PASS evaluator
makes the bad half pass; accepting any non-zero (or any reason_code) as
success lets an unrelated syntax/config error, or a failure for the wrong
cause, satisfy the oracle; treating transform failure or no-op as a
successful rejection makes its negative-control artifact PASS.* Each is
proven here as an EXPLICIT branch :func:`judge_canary` must resolve the
right way -- not merely implied by an end-to-end pipeline run.
"""

from __future__ import annotations

import pytest

from assay.canary import build_canary_claim, judge_attempt, judge_canary
from assay.errors import Outcome, ReasonCode
from assay.verdict import CanaryAttempt, CanaryResult, Claim


def make(
    *,
    target: str = "src/mod.py",
    description: str = "appended a never-called function",
    control_outcome: Outcome | None = Outcome.PASS,
    transformed_outcome: Outcome | None = Outcome.FAIL,
    expected_reason_code: ReasonCode | None = ReasonCode.UNCOVERED_LINES,
    observed_reason_code: ReasonCode | None = ReasonCode.UNCOVERED_LINES,
) -> CanaryAttempt:
    return CanaryAttempt(
        target=target,
        description=description,
        control_outcome=control_outcome,
        transformed_outcome=transformed_outcome,
        expected_reason_code=expected_reason_code,
        observed_reason_code=observed_reason_code,
    )


def wrap(*attempts: CanaryAttempt, mechanism: str = "uncovered-line") -> CanaryResult:
    """The v10 payload around one or more attempts. Defaults to the
    single-target shape every producer in this build emits."""
    return CanaryResult(
        mechanism=mechanism, attempts=attempts or (make(),)
    )


def skipped(target: str, why: str = "short_circuited") -> CanaryAttempt:
    return CanaryAttempt(
        target=target,
        description="would have appended a never-called function",
        disposition="not_attempted",
        not_attempted_reason=why,
    )


# --- CanaryAttempt's own construction-time discipline -------------------------


def test_the_honest_form_builds():
    attempt = make()
    assert attempt.control_outcome is Outcome.PASS
    assert attempt.transformed_outcome is Outcome.FAIL
    assert attempt.disposition == "attempted"


def test_an_empty_mechanism_is_refused():
    with pytest.raises(ValueError, match="mechanism must be a non-empty string"):
        CanaryResult(mechanism="", attempts=(make(),))


def test_an_empty_description_is_refused():
    with pytest.raises(ValueError, match="description must be a non-empty string"):
        CanaryAttempt(
            target="src/mod.py", description="", control_outcome=Outcome.PASS
        )


def test_a_non_outcome_control_outcome_is_refused():
    with pytest.raises(ValueError, match="control_outcome must be an Outcome"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome="PASS",
        )


def test_a_non_outcome_transformed_outcome_is_refused():
    with pytest.raises(ValueError, match="transformed_outcome must be an Outcome or None"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome=Outcome.PASS,
            transformed_outcome="FAIL",
        )


def test_expected_reason_code_must_be_a_fail_reason():
    """A canary always expects a SPECIFIC failure -- NO_MUTANTS or
    DIRTY_TREE (real reason codes, but not FAIL-shaped ones) can never be
    what a canary is configured to expect."""
    with pytest.raises(ValueError, match="must be a FAIL reason code"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome=Outcome.PASS,
            expected_reason_code=ReasonCode.DIRTY_TREE,
        )


def test_observed_reason_code_without_a_transformed_outcome_is_refused():
    with pytest.raises(ValueError, match="observed_reason_code requires a transformed_outcome"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome=Outcome.PASS,
            transformed_outcome=None,
            observed_reason_code=ReasonCode.UNCOVERED_LINES,
        )


def test_a_pass_transformed_outcome_cannot_carry_a_reason_code():
    with pytest.raises(ValueError, match="omitted when transformed_outcome is PASS"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome=Outcome.PASS,
            transformed_outcome=Outcome.PASS,
            observed_reason_code=ReasonCode.UNCOVERED_LINES,
        )


def test_a_non_pass_transformed_outcome_requires_a_reason_code():
    with pytest.raises(ValueError, match="observed_reason_code is required"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome=Outcome.PASS,
            transformed_outcome=Outcome.FAIL,
            observed_reason_code=None,
        )


def test_observed_reason_code_must_pair_with_its_own_transformed_outcome():
    """A CANARY_INCONCLUSIVE code (an INCONCLUSIVE-shaped reason) paired
    with a FAIL transformed_outcome is an internally inconsistent record --
    refused the same way Claim/Evidence refuse a mismatched (status,
    reason_code) pair."""
    with pytest.raises(ValueError, match="is not valid for transformed_outcome"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome=Outcome.PASS,
            transformed_outcome=Outcome.FAIL,
            observed_reason_code=ReasonCode.CANARY_INCONCLUSIVE,
        )


def test_to_dict_omits_absent_fields_never_nulls_them():
    payload = make(transformed_outcome=None, observed_reason_code=None).to_dict()

    assert "transformed_outcome" not in payload
    assert "observed_reason_code" not in payload
    assert payload["expected_reason_code"] == "UNCOVERED_LINES"


def test_to_dict_carries_every_present_field():
    payload = wrap(make()).to_dict()

    assert payload == {
        "mechanism": "uncovered-line",
        "attempts": [
            {
                # P21/A-152: the field that finally makes `judgment.r3.targets`
                # witnessable -- v3 could record the policy but nothing could
                # check it.
                "target": "src/mod.py",
                "description": "appended a never-called function",
                # B007/A-432: required on every attempt, and the discriminator
                # every other rule forks on.
                "disposition": "attempted",
                "control_outcome": "PASS",
                "transformed_outcome": "FAIL",
                "expected_reason_code": "UNCOVERED_LINES",
                "observed_reason_code": "UNCOVERED_LINES",
            }
        ],
    }


# --- CanaryResult: the v10 array, its bound and its disposition fork ----------


def test_a_not_attempted_entry_requires_a_closed_reason():
    with pytest.raises(ValueError, match="requires a not_attempted_reason"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            disposition="not_attempted",
            not_attempted_reason="because I said so",
        )


def test_a_not_attempted_entry_carries_no_run_field():
    with pytest.raises(ValueError, match="carries no run fields"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            disposition="not_attempted",
            not_attempted_reason="short_circuited",
            control_outcome=Outcome.PASS,
        )


def test_an_attempted_entry_carries_no_skip_reason():
    with pytest.raises(ValueError, match="on an attempted entry"):
        CanaryAttempt(
            target="src/mod.py",
            description="x",
            control_outcome=Outcome.PASS,
            transformed_outcome=Outcome.FAIL,
            observed_reason_code=ReasonCode.UNCOVERED_LINES,
            not_attempted_reason="short_circuited",
        )


def test_an_unknown_disposition_is_refused():
    with pytest.raises(ValueError, match="disposition must be one of"):
        CanaryAttempt(
            target="src/mod.py", description="x", disposition="maybe"
        )


def test_an_empty_attempts_array_is_refused():
    with pytest.raises(ValueError, match="between 1 and 8"):
        CanaryResult(mechanism="uncovered-line", attempts=())


def test_more_than_the_measured_bound_is_refused():
    """A-432's bound is MEASURED (~2.76 s of materialisation per target
    against the smallest documented lane budget), not chosen by taste."""
    attempts = tuple(make(target=f"src/mod{i}.py") for i in range(9))
    with pytest.raises(ValueError, match="between 1 and 8"):
        CanaryResult(mechanism="uncovered-line", attempts=attempts)


def test_two_records_for_one_target_are_refused():
    with pytest.raises(ValueError, match="more than once"):
        CanaryResult(
            mechanism="uncovered-line", attempts=(make(), make())
        )


# --- judge_attempt: A-109's terminal cases, one branch each -------------------


def test_a_correctly_caught_defect_is_pass():
    attempt = make(
        control_outcome=Outcome.PASS,
        transformed_outcome=Outcome.FAIL,
        expected_reason_code=ReasonCode.UNCOVERED_LINES,
        observed_reason_code=ReasonCode.UNCOVERED_LINES,
    )

    assert judge_attempt(attempt) == (Outcome.PASS, None)
    assert judge_canary(wrap(attempt)) == (Outcome.PASS, None)


def test_a_broken_control_is_inconclusive():
    """O1's negative: 'a broken baseline makes the good half fail' -- this
    is what proves the broken baseline is NOT silently ignored."""
    attempt = make(control_outcome=Outcome.FAIL, transformed_outcome=Outcome.FAIL)

    assert judge_attempt(attempt) == (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE)


@pytest.mark.parametrize(
    "control_outcome",
    [Outcome.FAIL, Outcome.ERROR, Outcome.NO_MEASUREMENT, Outcome.BUDGET_EXCEEDED, Outcome.INCONCLUSIVE],
)
def test_every_non_pass_control_outcome_is_inconclusive(control_outcome):
    attempt = make(control_outcome=control_outcome)

    assert judge_attempt(attempt) == (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE)


def test_a_transform_that_never_ran_is_inconclusive():
    """O3: a malformed transform or one that changed nothing to judge."""
    attempt = make(
        transformed_outcome=None,
        observed_reason_code=None,
    )

    assert judge_attempt(attempt) == (Outcome.INCONCLUSIVE, ReasonCode.CANARY_INCONCLUSIVE)


def test_an_unexpectedly_passing_bad_case_survives():
    """O3/O1: 'a universal-PASS evaluator makes the bad half pass' -- this
    is the branch that catches it: transformed_outcome PASS is FAIL/SURVIVED,
    never PASS."""
    attempt = make(transformed_outcome=Outcome.PASS, observed_reason_code=None)

    assert judge_attempt(attempt) == (Outcome.FAIL, ReasonCode.CANARY_SURVIVED)


def test_a_failure_for_the_wrong_cause_survives():
    """O1's own negative, made explicit: accepting ANY non-zero as success
    would wrongly pass this. observed differs from expected -- both are
    real, valid FAIL reasons, so 'any failure' is not enough."""
    attempt = make(
        expected_reason_code=ReasonCode.COMMAND_FAILED,
        observed_reason_code=ReasonCode.UNCOVERED_LINES,
    )

    assert judge_attempt(attempt) == (Outcome.FAIL, ReasonCode.CANARY_SURVIVED)


def test_an_unrelated_error_reason_also_survives_not_merely_a_different_fail_reason():
    """The observed reason need not even be FAIL-shaped -- an ERROR/EXEC_FAILED
    for an unrelated cause is exactly as much a 'survived' canary as a wrong
    FAIL reason (O1's negative names 'an unrelated syntax/config error')."""
    attempt = make(
        transformed_outcome=Outcome.ERROR,
        expected_reason_code=ReasonCode.COMMAND_FAILED,
        observed_reason_code=ReasonCode.EXEC_FAILED,
    )

    assert judge_attempt(attempt) == (Outcome.FAIL, ReasonCode.CANARY_SURVIVED)


# --- judge_canary: B007/A-432's aggregation over the ordered array ------------


def _caught(target: str) -> CanaryAttempt:
    return make(target=target)


def _survived(target: str) -> CanaryAttempt:
    return make(target=target, transformed_outcome=Outcome.PASS, observed_reason_code=None)


def test_one_target_is_the_v9_judgement_verbatim():
    """With one declared target `aggregation` is absent and judge_canary is
    judge_attempt -- which is what keeps every existing single-target lane's
    verdict BYTE-unchanged across the cut."""
    for attempt in (_caught("src/a.py"), _survived("src/a.py")):
        assert judge_canary(wrap(attempt)) == judge_attempt(attempt)


def test_any_passes_on_the_first_caught_probe():
    result = CanaryResult(
        mechanism="uncovered-line",
        attempts=(_caught("src/a.py"), skipped("src/b.py")),
    )
    assert judge_canary(result, aggregation="any") == (Outcome.PASS, None)


def test_any_fails_only_when_every_probe_survived():
    result = CanaryResult(
        mechanism="uncovered-line",
        attempts=(_survived("src/a.py"), _survived("src/b.py")),
    )
    assert judge_canary(result, aggregation="any") == (
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )


def test_all_fails_when_any_probe_survived():
    result = CanaryResult(
        mechanism="uncovered-line",
        attempts=(_caught("src/a.py"), _survived("src/b.py")),
    )
    assert judge_canary(result, aggregation="all") == (
        Outcome.FAIL,
        ReasonCode.CANARY_SURVIVED,
    )


def test_all_passes_only_when_every_probe_was_caught():
    result = CanaryResult(
        mechanism="uncovered-line",
        attempts=(_caught("src/a.py"), _caught("src/b.py")),
    )
    assert judge_canary(result, aggregation="all") == (Outcome.PASS, None)


@pytest.mark.parametrize("aggregation", ["any", "all"])
def test_an_inconclusive_attempt_is_terminal_in_both_modes(aggregation):
    """A-432: an INCONCLUSIVE attempt is TERMINAL, not aggregated -- the
    claim takes that outcome exactly as a single-target lane does today."""
    result = CanaryResult(
        mechanism="uncovered-line",
        attempts=(
            make(target="src/a.py", control_outcome=Outcome.FAIL),
            skipped("src/b.py", "earlier_target_terminal"),
        ),
    )
    assert judge_canary(result, aggregation=aggregation) == (
        Outcome.INCONCLUSIVE,
        ReasonCode.CANARY_INCONCLUSIVE,
    )


def test_an_unknown_aggregation_is_refused():
    with pytest.raises(ValueError, match="aggregation must be one of"):
        judge_canary(wrap(), aggregation="most")


# --- build_canary_claim: the R3 Claim wiring -----------------------------------


def test_build_canary_claim_wires_status_reason_and_the_canary_payload():
    result = wrap(make())

    claim = build_canary_claim(result)

    assert isinstance(claim, Claim)
    assert claim.rigor == "R3"
    assert claim.source == "computed"
    assert claim.verified_by_assay is True
    assert claim.status is Outcome.PASS
    assert claim.reason_code is None
    assert claim.canary is result


def test_build_canary_claim_on_a_survived_canary():
    result = wrap(make(transformed_outcome=Outcome.PASS, observed_reason_code=None))

    claim = build_canary_claim(result)

    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.CANARY_SURVIVED
    assert claim.canary.attempts[0].transformed_outcome is Outcome.PASS


def test_build_canary_claim_on_an_inconclusive_canary():
    result = wrap(make(control_outcome=Outcome.FAIL))

    claim = build_canary_claim(result)

    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.CANARY_INCONCLUSIVE
