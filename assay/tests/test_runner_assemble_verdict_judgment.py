"""``assemble_verdict``'s P16 extension: *judgment* is threaded straight
through to :class:`~assay.verdict.Verdict`, and an R1 claim carrying a
``coverage`` payload with no ``judgment.r1`` policy is refused BEFORE
construction (``ERROR``/``BAD_LANE_CONFIG``) -- the identical shape the
existing missing-claims/missing-evidence guards already use, restated for
this package's own gap (sol finding 2): a bare ``ValueError`` from
:class:`~assay.verdict.Verdict` itself is not an :class:`AssayError` and no
caller catches it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane

from assay import runner
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import Claim, Coverage, Judgment, JudgmentR1


def _r0_pass_result(tmp_path: Path):
    lane = make_lane(name="package", rigor=("R0", "R1"), argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 0, 1, tzinfo=timezone.utc),
    )
    return lane, runner.execute_command(lane, cwd=tmp_path, clock=clock)


def _r1_pass_claim() -> Claim:
    coverage = Coverage(
        covered=1, changed_executable=1, pct=100.0, considered=1,
        exclusion_capability="reported",
        missing_lines={}, files_missing_coverage=(),
    )
    return Claim(
        rigor="R1", source="computed", status=Outcome.PASS,
        verified_by_assay=True, coverage=coverage,
    )


def _r1_judgment() -> Judgment:
    return Judgment(
        r1=JudgmentR1(
            language="python", source_roots=("src",), coverage_format="coverage-py-json",
            coverage_artifact="cov.json", fail_under=100.0, allow_excluded=False,
            base="a" * 40,
        )
    )


def test_an_r1_coverage_claim_without_judgment_is_refused_before_construction(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)

    with pytest.raises(AssayError, match="no judgment.r1 policy was supplied") as excinfo:
        runner.assemble_verdict(
            lane=lane,
            commit="1" * 40,
            result=result,
            claims=(runner.build_r0_claim(result), _r1_pass_claim()),
            assay_version="0.1.0",
        )
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_an_r1_coverage_claim_with_judgment_builds_and_carries_it_through(
    tmp_path: Path,
):
    lane, result = _r0_pass_result(tmp_path)
    judgment = _r1_judgment()

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="2" * 40,
        result=result,
        claims=(runner.build_r0_claim(result), _r1_pass_claim()),
        assay_version="0.1.0",
        judgment=judgment,
    )

    assert verdict.judgment is judgment
    assert verdict.scope == lane.scope
    assert verdict.enforcement == lane.enforcement


def test_an_r0_only_verdict_needs_no_judgment(tmp_path: Path):
    lane = make_lane(name="package", rigor=("R0",), argv=("/bin/sh", "-c", "exit 0"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 16, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 16, 5, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="3" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    assert verdict.judgment is None
    assert verdict.scope == lane.scope
    assert verdict.enforcement == lane.enforcement
