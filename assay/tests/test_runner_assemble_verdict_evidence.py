"""``assemble_verdict``'s P10 extension: *evidence* and *declared_evidence*
pass through, participate in the rollup exactly like a claim would, and the
new missing/surplus guard fires BEFORE :class:`~assay.verdict.Verdict`'s own
bare ``ValueError`` would (the identical reasoning P04's own rigor-coverage
guard already established for ``claims``).

Backward compatibility (every caller through P09 never named these two
parameters) is proven by ``tests/test_runner_verdict_fixtures.py`` staying
green, completely unmodified by this package -- this module only ADDS cases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane

from assay import runner
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import Evidence, EvidenceDeclaration


def _r0_pass_result(tmp_path: Path):
    lane = make_lane(name="package", argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 15, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 15, 0, 1, tzinfo=timezone.utc),
    )
    lane_and_result = lane, runner.execute_command(lane, cwd=tmp_path, clock=clock)
    return lane_and_result


def test_current_evidence_alongside_a_passing_r0_claim_is_an_overall_pass(tmp_path: Path):
    lane, result = _r0_pass_result(tmp_path)
    evidence = Evidence(
        source="attested",
        key="review",
        status=Outcome.PASS,
        verified_by_assay=False,
        producer="bot",
        attested_commit="a" * 40,
        reviewed_paths=("x.py",),
    )

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="1" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        evidence=(evidence,),
        declared_evidence=(EvidenceDeclaration(source="attested", key="review"),),
    )

    assert verdict.outcome is Outcome.PASS
    assert verdict.evidence == (evidence,)
    assert verdict.declared_evidence == (EvidenceDeclaration(source="attested", key="review"),)


def test_stale_evidence_downgrades_an_otherwise_passing_verdict_to_no_measurement(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)
    evidence = Evidence(
        source="attested",
        key="review",
        status=Outcome.NO_MEASUREMENT,
        verified_by_assay=False,
        reason_code=ReasonCode.STALE_ATTESTATION,
        producer="bot",
        attested_commit="a" * 40,
        reviewed_paths=("x.py",),
    )

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="2" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        evidence=(evidence,),
        declared_evidence=(EvidenceDeclaration(source="attested", key="review"),),
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.STALE_ATTESTATION


def test_an_unreadable_attestation_outranks_a_passing_r0_claim(tmp_path: Path):
    # A-110's payoff one layer up: a broken attestation is not merely
    # "ignored" -- it makes the WHOLE verdict ERROR, exactly as it should for
    # a structurally broken input, even though R0 itself passed cleanly.
    lane, result = _r0_pass_result(tmp_path)
    evidence = Evidence(
        source="attested",
        key="review",
        status=Outcome.ERROR,
        verified_by_assay=False,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="3" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
        evidence=(evidence,),
        declared_evidence=(EvidenceDeclaration(source="attested", key="review"),),
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_missing_declared_evidence_is_refused_before_constructing_an_incomplete_verdict(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)

    with pytest.raises(AssayError) as excinfo:
        runner.assemble_verdict(
            lane=lane,
            commit="4" * 40,
            result=result,
            claims=(runner.build_r0_claim(result),),
            assay_version="0.1.0",
            evidence=(),  # declared but never resolved
            declared_evidence=(EvidenceDeclaration(source="attested", key="review"),),
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "review" in str(excinfo.value)


def test_surplus_evidence_is_refused_before_constructing_an_incomplete_verdict(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)
    evidence = Evidence(
        source="attested",
        key="review",
        status=Outcome.PASS,
        verified_by_assay=False,
        producer="bot",
        attested_commit="a" * 40,
        reviewed_paths=("x.py",),
    )

    with pytest.raises(AssayError) as excinfo:
        runner.assemble_verdict(
            lane=lane,
            commit="5" * 40,
            result=result,
            claims=(runner.build_r0_claim(result),),
            assay_version="0.1.0",
            evidence=(evidence,),  # resolved but never declared
            declared_evidence=(),
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_omitting_evidence_entirely_still_defaults_to_empty_tuples(tmp_path: Path):
    # The exact backward-compatibility claim: a caller that names neither
    # parameter (every caller through P09) gets the identical shape as before.
    lane, result = _r0_pass_result(tmp_path)

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="6" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    assert verdict.evidence == ()
    assert verdict.declared_evidence == ()
    assert verdict.outcome is Outcome.PASS
