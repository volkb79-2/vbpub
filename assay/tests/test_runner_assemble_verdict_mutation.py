"""``assemble_verdict``'s P12 extension (A-119): *mutation_claim* is folded
into *claims* BEFORE the existing rigor-coverage guard and rollup run --
the identical shape P10's *evidence*/*declared_evidence* threading already
established, restated for the one R2 claim this package's own
:func:`~assay.mutation.build_mutation_claim` produces.

Backward compatibility (every caller through P10 never named this
parameter) is proven by ``tests/test_runner_verdict_fixtures.py`` and
``tests/test_runner_assemble_verdict_evidence.py`` staying green,
completely unmodified by this package -- this module only ADDS cases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane

from assay import runner
from assay.errors import Outcome, ReasonCode
from assay.verdict import Claim, MutantOutcome, Mutation

#: A PASSING R2 claim carries the buckets it passed on, and a
#: MUTANTS_SURVIVED one carries the survivor it names (P16 review) -- an R2
#: status read off buckets that are not in the artifact is exactly what
#: ``assay verify`` cannot re-derive.
_KILLED_EVERYTHING = Mutation(total=2, killed=2)
_ONE_SURVIVOR = Mutation(
    total=2,
    killed=1,
    survived=(
        MutantOutcome(
            path="pkg/a.py",
            lineno=3,
            operator="invert-comparison",
            description="a < b -> a >= b",
        ),
    ),
)


def _r0_pass_result(tmp_path: Path):
    lane = make_lane(name="package", rigor=("R0", "R2"), argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 0, 1, tzinfo=timezone.utc),
    )
    return lane, runner.execute_command(lane, cwd=tmp_path, clock=clock)


def test_a_passing_mutation_claim_alongside_a_passing_r0_claim_is_an_overall_pass(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)
    mutation_claim = Claim(
        rigor="R2", source="computed", status=Outcome.PASS,
        verified_by_assay=True, mutation=_KILLED_EVERYTHING,
    )

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="1" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        mutation_claim=mutation_claim,
    )

    assert verdict.outcome is Outcome.PASS
    assert len(verdict.claims) == 2
    assert {claim.rigor for claim in verdict.claims} == {"R0", "R2"}
    assert mutation_claim in verdict.claims


def test_a_failing_mutation_claim_makes_the_whole_verdict_fail_mutants_survived(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)
    mutation_claim = Claim(
        rigor="R2", source="computed", status=Outcome.FAIL,
        verified_by_assay=True, reason_code=ReasonCode.MUTANTS_SURVIVED,
        mutation=_ONE_SURVIVOR,
    )

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="2" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        mutation_claim=mutation_claim,
    )

    assert verdict.outcome is Outcome.FAIL
    assert verdict.reason_code is ReasonCode.MUTANTS_SURVIVED


def test_a_duplicate_rigor_between_claims_and_mutation_claim_is_refused(tmp_path: Path):
    """A caller that ALREADY folded the R2 claim into ``claims`` and ALSO
    passes ``mutation_claim`` separately -- this function never de-dupes
    silently; the existing ``Verdict``-level duplicate-rigor check (used
    elsewhere in this project for the identical shape) catches it."""
    lane, result = _r0_pass_result(tmp_path)
    r2_in_claims = Claim(
        rigor="R2", source="computed", status=Outcome.PASS,
        verified_by_assay=True, mutation=_KILLED_EVERYTHING,
    )
    r2_duplicate = Claim(
        rigor="R2", source="computed", status=Outcome.PASS,
        verified_by_assay=True, mutation=_KILLED_EVERYTHING,
    )

    with pytest.raises(ValueError, match="more than one claim for rigor level"):
        runner.assemble_verdict(
            lane=lane,
            commit="3" * 40,
            result=result,
            claims=(runner.build_r0_claim(result), r2_in_claims),
            assay_version="0.1.0",
            mutation_claim=r2_duplicate,
        )


def test_omitting_mutation_claim_still_defaults_to_none_and_only_the_r0_claim_is_kept(
    tmp_path: Path,
):
    # The exact backward-compatibility claim: a caller that names neither
    # this parameter nor evidence (every caller through P10) gets the
    # identical shape as before.
    lane, result = _r0_pass_result(tmp_path)
    lane = make_lane(name="package", rigor=("R0",), argv=("/bin/sh", "-c", "exit 0"), env={})

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="4" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    assert len(verdict.claims) == 1
    assert verdict.claims[0].rigor == "R0"
    assert verdict.outcome is Outcome.PASS
