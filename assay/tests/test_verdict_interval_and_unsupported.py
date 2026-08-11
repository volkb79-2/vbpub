"""P21 work items 7 and 4 -- the two v4 rules JSON Schema structurally
cannot own, proven independently in the model AND in the raw verifier.

**Interval ordering (work item 7, A-182).** Draft 2020-12 has no ``$data``,
so ``ended >= started`` cannot be expressed in the shipped schema at all.
Claiming otherwise would be a hollow contract, so the schema is deliberately
silent and exactly two layers carry it. The comparison is on parsed
offset-aware INSTANTS: the canonical combined artifact runs
``12:00+01:00`` -> ``11:01+00:00``, a valid one-minute window whose LEXICAL
order runs backwards, so a string comparison rejects a correct artifact.

**Capability absence (work item 4, A-183).** ``MUTATION_UNSUPPORTED`` is
payload-free and ``NO_MUTANTS`` is not. Collapsing the two -- in either
direction -- turns "we cannot analyse this language" into measured evidence
that nothing was mutable.
"""

from __future__ import annotations

import pytest

from assay.errors import Outcome, ReasonCode
from assay.mutation import build_mutation_claim, judge_mutation
from assay.verdict import (
    Claim,
    Judgment,
    JudgmentR2,
    JudgmentResolved,
    Mutation,
    Verdict,
)
from assay.verify import verify_document

BASE = dict(
    lane="package",
    commit="c" * 40,
    assay_version="0.2.0",
    declared_rigor=("R0",),
    declared_evidence=(),
    argv_declared=("pytest", "-q"),
    argv_appended=(),
    argv_effective=("pytest", "-q"),
    env_declared={},
    env_effective={},
    scope="S1",
    enforcement="gate",
)

R0_PASS = Claim(rigor="R0", source="computed", status=Outcome.PASS, verified_by_assay=True)


def _verdict(started: str, ended: str) -> Verdict:
    return Verdict(
        **BASE, outcome=Outcome.PASS, started=started, ended=ended, claims=(R0_PASS,)
    )


# --- interval ordering ---------------------------------------------------


def test_an_offset_crossing_interval_that_reads_backwards_lexically_is_valid():
    """The positive witness that makes the negative below an oracle: a
    string comparison would REJECT this correct artifact."""
    started, ended = "2026-08-09T12:00:00+01:00", "2026-08-09T11:01:00+00:00"
    assert ended < started, "the fixture must really be lexically reversed"

    verdict = _verdict(started, ended)

    assert verdict.ended == ended
    assert verify_document(verdict.to_dict()) == []


def test_a_genuinely_reversed_instant_is_refused_by_the_model():
    with pytest.raises(ValueError, match="cannot run backwards"):
        _verdict("2026-08-09T11:00:00+00:00", "2026-08-09T10:59:59+00:00")


def test_a_genuinely_reversed_instant_is_refused_by_the_raw_verifier_too():
    """Independently, and worded differently: reconstruction alone would
    also catch this, which is exactly why the raw check needs its own
    reachable branch and its own message."""
    document = _verdict(
        "2026-08-09T11:00:00+00:00", "2026-08-09T11:00:01+00:00"
    ).to_dict()
    document["ended"] = "2026-08-09T10:59:59+00:00"

    failures = verify_document(document)

    assert any("interval runs backwards" in failure for failure in failures), failures


def test_equal_instants_across_different_offsets_are_accepted():
    """``>=``, not ``>``: a command fast enough to start and end within the
    clock's resolution has not run backwards."""
    verdict = _verdict("2026-08-09T12:00:00+01:00", "2026-08-09T11:00:00+00:00")

    assert verify_document(verdict.to_dict()) == []


@pytest.mark.parametrize(
    "value", ["2026-13-09T11:00:00+00:00", "2026-08-32T11:00:00+00:00"]
)
def test_an_impossible_calendar_value_is_refused(value: str):
    """The timestamp regex checks digit SHAPE; only parsing catches month 13
    or day 32."""
    with pytest.raises(ValueError, match="not a real timestamp"):
        _verdict(value, "2026-08-09T11:00:01+00:00")


def test_the_raw_verifier_reports_an_unparseable_timestamp_on_its_own():
    document = _verdict(
        "2026-08-09T11:00:00+00:00", "2026-08-09T11:00:01+00:00"
    ).to_dict()
    document["started"] = "2026-13-09T11:00:00+00:00"

    failures = verify_document(document)

    assert any("not a parseable timestamp" in failure for failure in failures), failures


# --- capability absence versus a supported empty analysis ----------------


def test_the_unsupported_marker_renders_a_payload_free_inconclusive_claim():
    claim = build_mutation_claim(object(), "UNSUPPORTED")

    assert claim.rigor == "R2"
    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.MUTATION_UNSUPPORTED
    assert claim.mutation is None


def test_a_supported_empty_analysis_renders_no_mutants_with_its_zero_payload():
    """The contrast that makes the distinction observable: same outcome
    class, different reason, and -- decisively -- a payload."""
    empty = Mutation(candidate_count=0, total=0)

    claim = build_mutation_claim(object(), empty)

    assert claim.status is Outcome.INCONCLUSIVE
    assert claim.reason_code is ReasonCode.NO_MUTANTS
    assert claim.mutation is empty


def test_judge_mutation_never_reads_the_baseline_for_the_marker():
    """``object()`` has no ``.outcome``: if the marker branch ever started
    consulting the baseline, this raises ``AttributeError`` loudly rather
    than silently re-deriving against a fabricated outcome."""
    assert judge_mutation(object(), "UNSUPPORTED") == (
        Outcome.INCONCLUSIVE,
        ReasonCode.MUTATION_UNSUPPORTED,
    )


def test_the_marker_claim_cannot_be_given_a_payload():
    with pytest.raises(ValueError, match="carries a mutation payload"):
        Claim(
            rigor="R2",
            source="computed",
            status=Outcome.INCONCLUSIVE,
            verified_by_assay=True,
            reason_code=ReasonCode.MUTATION_UNSUPPORTED,
            mutation=Mutation(candidate_count=0, total=0),
        )


def test_a_payload_free_no_mutants_claim_is_unconstructible():
    """The converse forgery: dropping the payload while keeping the reason
    would make a supported empty analysis indistinguishable from an adapter
    that never ran one."""
    with pytest.raises(ValueError, match="can only be read off the mutation buckets"):
        Claim(
            rigor="R2",
            source="computed",
            status=Outcome.INCONCLUSIVE,
            verified_by_assay=True,
            reason_code=ReasonCode.NO_MUTANTS,
        )


def test_the_unsupported_terminal_still_records_the_policy_it_applied():
    """A-183: jobs/max_mutants/operators were resolved and handed to
    discovery before the adapter reported absence, so the artifact records
    them -- a consumer needs the cap to interpret the result."""
    verdict = Verdict(
        **{**BASE, "declared_rigor": ("R0", "R2")},
        outcome=Outcome.INCONCLUSIVE,
        reason_code=ReasonCode.MUTATION_UNSUPPORTED,
        started="2026-08-09T11:00:00+00:00",
        ended="2026-08-09T11:00:01+00:00",
        claims=(
            R0_PASS,
            Claim(
                rigor="R2",
                source="computed",
                status=Outcome.INCONCLUSIVE,
                verified_by_assay=True,
                reason_code=ReasonCode.MUTATION_UNSUPPORTED,
            ),
        ),
        judgment=Judgment(
            resolved=JudgmentResolved(
                language="python", source_roots=("pkg",), base="a" * 40
            ),
            r2=JudgmentR2(
                jobs=2,
                max_mutants=50,
                operators=("python:compare-swap",),
                kill_attribution="unattributed",
            ),
        ),
    )

    assert verify_document(verdict.to_dict()) == []


def test_a_policy_recorded_for_a_baseline_that_never_ran_is_refused():
    """The other direction of the same correspondence: an R2 claim that
    merely propagated a failed baseline attempted nothing, so recording a
    policy for it would describe a judgment that never happened."""
    with pytest.raises(ValueError, match="never happened"):
        Verdict(
            **{**BASE, "declared_rigor": ("R0", "R2")},
            outcome=Outcome.FAIL,
            reason_code=ReasonCode.COMMAND_FAILED,
            started="2026-08-09T11:00:00+00:00",
            ended="2026-08-09T11:00:01+00:00",
            claims=(
                R0_PASS,
                Claim(
                    rigor="R2",
                    source="computed",
                    status=Outcome.FAIL,
                    verified_by_assay=True,
                    reason_code=ReasonCode.COMMAND_FAILED,
                ),
            ),
            judgment=Judgment(
                resolved=JudgmentResolved(
                    language="python", source_roots=("pkg",), base="a" * 40
                ),
                r2=JudgmentR2(
                    jobs=2,
                    max_mutants=50,
                    operators=("python:compare-swap",),
                    kill_attribution="unattributed",
                ),
            ),
        )
