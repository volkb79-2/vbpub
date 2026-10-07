"""W10 step 1: characterization of `Verdict._check_judgment_matches_claims` (I3, producer side).

Pins, against UNCHANGED source and byte for byte, the "policy present iff rN
attempted" rule for all four tiers, both directions:

- ``orphan``: ``judgment.rN`` is recorded but the claim never attempted the tier;
- ``missing``: the claim attempted the tier but ``judgment.rN`` is absent.

Each pair is pinned with the operand that decides it changed alone (claim
absent, claim present but payload-free, policy present, policy absent). R1's
and R2's wider "attempted" terminals are pinned too. These tests must never be
edited after the I3 helper lands (W10 brief, step 1).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane, native_mutation, native_outcome

from assay import runner
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import (
    CanaryAttempt,
    CanaryResult,
    Claim,
    Coverage,
    Judgment,
    JudgmentR1,
    JudgmentR2,
    JudgmentR3,
    JudgmentR4,
    JudgmentResolved,
    RedFirstResult,
    SnapshotPolicy,
    Verdict,
)

_SHA_LTE = "b60080dc8b8982d2a2bff6f8f3715c1939614dc553cd223ef21832b88c815866"

R1_ORPHAN = (
    "judgment.r1 is present but no R1 claim rendered a coverage "
    "payload or one of BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED -- "
    "a policy is recorded for a judgment that never happened"
)
R1_MISSING = (
    "the R1 claim rendered a coverage payload or one of "
    "BRANCH_UNAVAILABLE/TARGET_NOT_MEASURED but judgment.r1 is "
    "absent -- an independent consumer cannot re-derive R1's "
    "status without the policy that decided it"
)
R2_ORPHAN = (
    "judgment.r2 is present but no R2 claim rendered a mutation "
    "payload or an unsupported-capability terminal -- a policy is "
    "recorded for a judgment that never happened"
)
R2_MISSING = (
    "the R2 claim rendered a mutation payload or an "
    "unsupported-capability terminal but judgment.r2 is absent -- "
    "an independent consumer cannot re-derive R2's status without "
    "the policy that decided it"
)
R3_ORPHAN = (
    "judgment.r3 is present but no R3 claim rendered a canary "
    "payload -- a policy is recorded for a judgment that never "
    "happened"
)
R3_MISSING = (
    "the R3 claim rendered a canary payload but judgment.r3 "
    "is absent -- an independent consumer cannot re-derive R3's "
    "status without the policy that decided it"
)
R4_ORPHAN = (
    "judgment.r4 is present but no R4 claim rendered a red_first "
    "payload -- a policy is recorded for a judgment that never "
    "happened"
)
R4_MISSING = (
    "the R4 claim rendered a red_first payload but judgment.r4 is "
    "absent -- an independent consumer cannot re-derive R4's "
    "status without the policy that decided it"
)

def refusal(call, message: str, exc=ValueError) -> None:
    with pytest.raises(exc) as info:
        call()
    assert str(info.value) == message


RESOLVED = JudgmentResolved(language="python", source_roots=("src",), base="a" * 40)
R1_POLICY = JudgmentR1(
    coverage_format="coverage-py-json",
    coverage_artifact="cov.json",
    fail_under=100.0,
    allow_excluded=False,
)
R2_POLICY = JudgmentR2(
    jobs=1,
    max_mutants=50,
    operators=("python:compare-swap",),
    kill_attribution="unattributed",
    cold_witness_kills=False,
)
R3_POLICY = JudgmentR3(mechanism="uncovered-line", targets=("a.py",))
R4_POLICY = JudgmentR4(
    tests=("tests/test_fix.py",),
    broken_commit="a" * 40,
    broken_commit_source="declared",
)


def _claim0() -> Claim:
    return Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)


def _claim1() -> Claim:
    coverage = Coverage(
        covered=2,
        executable=2,
        pct=100.0,
        considered=1,
        exclusion_capability="reported",
        missing_lines={},
        files_missing_coverage=(),
    )
    return Claim(
        rigor="R1",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
        coverage=coverage,
    )


def _claim2() -> Claim:
    killed = (
        native_outcome(
            path="a.py",
            lineno=1,
            start_byte=4,
            end_byte=5,
            replacement_sha256=_SHA_LTE,
            operator="python:compare-swap",
            description="Lt->LtE",
        ),
    )
    mutation = native_mutation(candidate_count=1, total=1, killed=killed)
    return Claim(
        rigor="R2",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
        mutation=mutation,
    )


def _claim3() -> Claim:
    canary = CanaryResult(
        mechanism="uncovered-line",
        attempts=(
            CanaryAttempt(
                target="a.py",
                description="x",
                control_outcome=Outcome.PASS,
                transformed_outcome=Outcome.FAIL,
                expected_reason_code=ReasonCode.UNCOVERED_LINES,
                observed_reason_code=ReasonCode.UNCOVERED_LINES,
            ),
        ),
    )
    return Claim(
        rigor="R3",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
        canary=canary,
    )


def _claim4() -> Claim:
    red_first = RedFirstResult(
        broken_commit="a" * 40,
        tests=("tests/test_fix.py",),
        before_outcome=Outcome.FAIL,
        after_outcome=Outcome.PASS,
    )
    return Claim(
        rigor="R4",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
        red_first=red_first,
    )


def _verdict(claims, declared, judgment, **overrides) -> Verdict:
    values = dict(
        lane="package",
        commit="b" * 40,
        started="2026-08-07T09:00:00+00:00",
        ended="2026-08-07T09:00:01+00:00",
        assay_version="0.1.0",
        declared_evidence=(),
        argv_declared=("pytest", "-q"),
        argv_appended=(),
        argv_effective=("pytest", "-q"),
        env_declared={},
        env_effective={},
        env_passthrough=(),
        scope="S1",
        enforcement="gate",
        outcome=Outcome.PASS,
        declared_rigor=declared,
        claims=claims,
        judgment=judgment,
        snapshot_policy=SnapshotPolicy(selection="repository"),
    )
    values.update(overrides)
    return Verdict(**values)


RESOLVED_NO_BASE = JudgmentResolved(language="python", source_roots=("src",))


def _judgment(**policies) -> Judgment:
    """R3 and R4 alone never resolve a base; anything with R1 or R2 does."""
    resolved = RESOLVED if ("r1" in policies or "r2" in policies) else RESOLVED_NO_BASE
    return Judgment(resolved=resolved, **policies)


# --- R1 ---------------------------------------------------------------------


def test_r1_policy_and_coverage_claim_together_are_accepted():
    _verdict((_claim0(), _claim1()), ("R0", "R1"), _judgment(r1=R1_POLICY))


def test_r1_orphan_when_the_claim_is_absent():
    refusal(
        lambda: _verdict((_claim0(),), ("R0",), _judgment(r1=R1_POLICY)),
        R1_ORPHAN,
    )


def test_r1_missing_when_the_policy_is_absent_from_a_judgment():
    refusal(
        lambda: _verdict(
            (_claim0(), _claim1(), _claim2()),
            ("R0", "R1", "R2"),
            _judgment(r2=R2_POLICY),
        ),
        R1_MISSING,
    )


def test_r1_missing_when_there_is_no_judgment_at_all():
    refusal(
        lambda: _verdict((_claim0(), _claim1()), ("R0", "R1"), None),
        R1_MISSING,
    )


@pytest.mark.parametrize(
    "reason", [ReasonCode.BRANCH_UNAVAILABLE, ReasonCode.TARGET_NOT_MEASURED]
)
def test_r1_payload_free_terminals_count_as_attempted(reason):
    claim = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=True,
        reason_code=reason,
    )

    def build(judgment):
        return _verdict(
            (_claim0(), claim),
            ("R0", "R1"),
            judgment,
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=reason,
        )

    refusal(lambda: build(None), R1_MISSING)
    build(_judgment(r1=R1_POLICY))


def test_r1_payload_free_claim_with_another_reason_is_not_attempted():
    claim = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=True,
        reason_code=ReasonCode.EMPTY_COVERAGE,
    )

    def build(judgment):
        return _verdict(
            (_claim0(), claim),
            ("R0", "R1"),
            judgment,
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.EMPTY_COVERAGE,
        )

    build(None)
    refusal(lambda: build(_judgment(r1=R1_POLICY)), R1_ORPHAN)


# --- R2 ---------------------------------------------------------------------


def test_r2_policy_and_mutation_claim_together_are_accepted():
    _verdict((_claim0(), _claim2()), ("R0", "R2"), _judgment(r2=R2_POLICY))


def test_r2_orphan_when_the_claim_is_absent():
    refusal(
        lambda: _verdict((_claim0(),), ("R0",), _judgment(r2=R2_POLICY)),
        R2_ORPHAN,
    )


def test_r2_missing_when_the_policy_is_absent_from_a_judgment():
    refusal(
        lambda: _verdict(
            (_claim0(), _claim1(), _claim2()),
            ("R0", "R1", "R2"),
            _judgment(r1=R1_POLICY),
        ),
        R2_MISSING,
    )


def test_r2_missing_when_there_is_no_judgment_at_all():
    refusal(
        lambda: _verdict((_claim0(), _claim2()), ("R0", "R2"), None),
        R2_MISSING,
    )


def test_r2_unsupported_terminal_counts_as_attempted():
    claim = Claim(
        rigor="R2",
        source="computed",
        status=Outcome.INCONCLUSIVE,
        verified_by_assay=True,
        reason_code=ReasonCode.MUTATION_UNSUPPORTED,
    )

    def build(judgment):
        return _verdict(
            (_claim0(), claim),
            ("R0", "R2"),
            judgment,
            outcome=Outcome.INCONCLUSIVE,
            reason_code=ReasonCode.MUTATION_UNSUPPORTED,
        )

    refusal(lambda: build(None), R2_MISSING)
    build(_judgment(r2=R2_POLICY))


def test_r2_payload_free_claim_with_another_reason_is_not_attempted():
    claim = Claim(
        rigor="R2",
        source="computed",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=True,
        reason_code=ReasonCode.EMPTY_COVERAGE,
    )

    def build(judgment):
        return _verdict(
            (_claim0(), claim),
            ("R0", "R2"),
            judgment,
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.EMPTY_COVERAGE,
        )

    build(None)
    refusal(lambda: build(_judgment(r2=R2_POLICY)), R2_ORPHAN)


# --- R3 ---------------------------------------------------------------------


def test_r3_policy_and_canary_claim_together_are_accepted():
    _verdict((_claim0(), _claim3()), ("R0", "R3"), _judgment(r3=R3_POLICY))


def test_r3_orphan_when_the_claim_is_absent():
    refusal(
        lambda: _verdict((_claim0(),), ("R0",), _judgment(r3=R3_POLICY)),
        R3_ORPHAN,
    )


def test_r3_missing_when_the_policy_is_absent_from_a_judgment():
    refusal(
        lambda: _verdict(
            (_claim0(), _claim1(), _claim3()),
            ("R0", "R1", "R3"),
            _judgment(r1=R1_POLICY),
        ),
        R3_MISSING,
    )


def test_r3_missing_when_there_is_no_judgment_at_all():
    refusal(
        lambda: _verdict((_claim0(), _claim3()), ("R0", "R3"), None),
        R3_MISSING,
    )


# --- R4 ---------------------------------------------------------------------


def test_r4_policy_and_red_first_claim_together_are_accepted():
    _verdict((_claim0(), _claim4()), ("R0", "R4"), _judgment(r4=R4_POLICY))


def test_r4_orphan_when_the_claim_is_absent():
    refusal(
        lambda: _verdict((_claim0(),), ("R0",), _judgment(r4=R4_POLICY)),
        R4_ORPHAN,
    )


def test_r4_missing_when_the_policy_is_absent_from_a_judgment():
    refusal(
        lambda: _verdict(
            (_claim0(), _claim1(), _claim4()),
            ("R0", "R1", "R4"),
            _judgment(r1=R1_POLICY),
        ),
        R4_MISSING,
    )


def test_r4_missing_when_there_is_no_judgment_at_all():
    refusal(
        lambda: _verdict((_claim0(), _claim4()), ("R0", "R4"), None),
        R4_MISSING,
    )


# --- runner.assemble_verdict's R1 twin (the `claim_for`/`claim_carries` site) ---

RUNNER_R1_MISSING = (
    "lane 'package' rendered an R1 claim carrying a coverage "
    "payload, but no judgment.r1 policy was supplied -- an "
    "independent consumer cannot re-derive R1's status from "
    "coverage alone. Refusing before constructing an incomplete "
    "verdict."
)


def _assemble(tmp_path: Path, claims_of, judgment):
    lane = make_lane(
        name="package", rigor=("R0", "R1"), argv=("/bin/sh", "-c", "exit 0"), env={}
    )
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 0, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    return runner.assemble_verdict(
        lane=lane,
        commit="1" * 40,
        result=result,
        claims=claims_of(result),
        assay_version="0.1.0",
        judgment=judgment,
    )


def test_runner_refuses_an_r1_coverage_claim_without_judgment_r1_with_its_exact_text(
    tmp_path: Path,
):
    with pytest.raises(AssayError) as info:
        _assemble(
            tmp_path,
            lambda result: (runner.build_r0_claim(result), _claim1()),
            None,
        )
    assert str(info.value) == RUNNER_R1_MISSING
    assert info.value.outcome is Outcome.ERROR
    assert info.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_runner_accepts_an_r1_coverage_claim_with_judgment_r1(tmp_path: Path):
    verdict = _assemble(
        tmp_path,
        lambda result: (runner.build_r0_claim(result), _claim1()),
        _judgment(r1=R1_POLICY),
    )
    assert verdict.judgment.r1 is R1_POLICY


def test_runner_accepts_an_r0_only_claim_set_without_judgment(tmp_path: Path):
    def r0_only(result):
        return (runner.build_r0_claim(result),)

    lane = make_lane(name="package", rigor=("R0",), argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 5, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="1" * 40,
        result=result,
        claims=r0_only(result),
        assay_version="0.1.0",
    )
    assert verdict.judgment is None
