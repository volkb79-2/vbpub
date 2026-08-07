"""O4 — every branch this package's runner produces is compared, by ordinary
JSON loading, against a hand-written complete verdict fixture that also
validates independently against the packaged JSON Schema.

The negative this defends (verbatim): *changing the producer to emit a
constant PASS or omit one required field fails the independent fixture
comparison.*

Each verdict below is built through the REAL production path —
:func:`~assay.runner.execute_command`, :func:`~assay.runner.build_r0_claim`,
:func:`~assay.runner.assemble_verdict` — the exact same functions
``cli.py``'s ``run`` subcommand calls, with only the process boundary and
clock injected for determinism (AUTHORING.md §3b.A/E). Nothing here
constructs a ``Verdict`` "by hand" the way the writer tests do; the point of
O4 is that the PRODUCER's real output matches an independently authored
expectation.

The ``BUDGET_EXCEEDED`` branch reuses ``tests/fixtures/verdicts/
budget_exceeded.json`` — P01b's own hand-written fixture — rather than adding
a new file: it is already an R0-only, single-claim ``BUDGET_EXCEEDED``
verdict, so matching this package's lane/commit/timestamps to it exactly
proves the new runner reproduces what the schema already declared valid, with
zero changes to ``verdict.py`` or the schema (exactly what the handoff
predicted).
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane, runner_verdict_fixture, verdict_fixture, why_invalid
from jsonschema import Draft202012Validator

from assay import runner
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import Claim


def _validate(document: dict, validator: Draft202012Validator) -> None:
    assert why_invalid(validator, document) == []


def test_r0_only_pass_matches_the_hand_written_fixture(
    tmp_path: Path, validator: Draft202012Validator
):
    lane = make_lane(
        name="package",
        argv=("/bin/sh", "-c", "exit 0"),
        env={"MOCK_MODE": "true"},
    )
    clock = fixed_clock(
        datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 10, 0, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="a" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == runner_verdict_fixture("r0_pass")
    _validate(document, validator)


def test_r0_only_fail_command_failed_matches_the_hand_written_fixture(
    tmp_path: Path, validator: Draft202012Validator
):
    lane = make_lane(name="package", argv=("/bin/sh", "-c", "exit 7"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 10, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 10, 5, 1, tzinfo=timezone.utc),
    )

    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="b" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == runner_verdict_fixture("r0_fail_command_failed")
    _validate(document, validator)


def test_r0_only_error_exec_failed_missing_executable_matches_the_fixture(
    tmp_path: Path, validator: Draft202012Validator
):
    lane = make_lane(name="package", argv=("/opt/does-not-exist/tool", "--flag"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 10, 10, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 10, 10, 0, tzinfo=timezone.utc),
    )

    def raise_missing(argv, *, env, cwd, timeout):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    result = runner.execute_command(
        lane, cwd=tmp_path, process_runner=raise_missing, clock=clock
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="c" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == runner_verdict_fixture("r0_error_exec_failed_missing_executable")
    _validate(document, validator)


def test_r0_only_error_argv_append_rejected_matches_the_fixture(
    tmp_path: Path, validator: Draft202012Validator
):
    lane = make_lane(
        name="package",
        argv=("/bin/sh", "-c", "exit 0"),
        env={},
        allow_argv_append=False,
    )
    clock = fixed_clock(
        datetime(2026, 8, 7, 10, 15, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 10, 15, 0, tzinfo=timezone.utc),
    )

    result = runner.execute_command(
        lane, argv_append=("--extra", "flag"), cwd=tmp_path, clock=clock
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="d" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == runner_verdict_fixture("r0_error_argv_append_rejected")
    _validate(document, validator)


def test_r0_only_budget_exceeded_matches_p01bs_own_hand_written_fixture(
    tmp_path: Path, validator: Draft202012Validator
):
    # Reuses tests/fixtures/verdicts/budget_exceeded.json verbatim -- see the
    # module docstring for why no new fixture file is needed here.
    lane = make_lane(
        name="integration",
        argv=("pytest", "tests/integration", "-q"),
        env={"MOCK_MODE": "false"},
        budget="5m",
        budget_seconds=300.0,
    )
    clock = fixed_clock(
        datetime(2026, 8, 6, 9, 40, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 6, 9, 45, 0, tzinfo=timezone.utc),
    )

    def raise_timeout(argv, *, env, cwd, timeout):
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)

    result = runner.execute_command(
        lane, cwd=tmp_path, process_runner=raise_timeout, clock=clock
    )
    verdict = runner.assemble_verdict(
        lane=lane,
        commit="5" * 40,
        result=result,
        claims=(runner.build_r0_claim(result),),
        assay_version="0.1.0",
    )

    document = json.loads(verdict.to_json())
    assert document == verdict_fixture("BUDGET_EXCEEDED")
    _validate(document, validator)


# --- vacuity guard: a constant-PASS or field-dropping producer must fail -----


def test_a_hard_coded_constant_pass_would_fail_the_fail_fixture_comparison(
    tmp_path: Path,
):
    # Not a mutation of assay's own source (that happens for real in the LOG's
    # self-review); this documents in-suite that the comparison target is
    # sensitive to exactly the defect O4's negative names, by constructing
    # the WRONG verdict inline and asserting it does NOT match.
    lane = make_lane(name="package", argv=("/bin/sh", "-c", "exit 7"), env={})
    clock = fixed_clock(
        datetime(2026, 8, 7, 10, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 10, 5, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)

    wrong_claim = runner.build_r0_claim(result)
    # Simulate "the producer emits a constant PASS": force outcome/reason to
    # PASS regardless of the real (nonzero) result.
    forced_pass_document = json.loads(
        runner.assemble_verdict(
            lane=lane,
            commit="b" * 40,
            result=result,
            claims=(wrong_claim,),
            assay_version="0.1.0",
        ).to_json()
    )
    forced_pass_document["outcome"] = "PASS"
    del forced_pass_document["reason_code"]
    forced_pass_document["exit_code"] = 0
    forced_pass_document["claims"][0]["status"] = "PASS"
    del forced_pass_document["claims"][0]["reason_code"]

    assert forced_pass_document != runner_verdict_fixture("r0_fail_command_failed")


# --- assemble_verdict refuses a claims tuple that does not cover declared_rigor -


def test_assemble_verdict_refuses_before_constructing_an_incomplete_verdict(
    tmp_path: Path,
):
    # This build (through P04) only ever supplies an R0 claim; a lane
    # declaring rigor beyond R0 must be refused HERE -- not reach
    # Verdict's own internal invariant, which raises a bare ValueError no
    # caller catches (controller repair: relocated from a cli.py-only gate
    # that P05 had no scope.touch permission to update).
    lane = make_lane(name="package", rigor=("R0", "R1"), argv=("/bin/sh", "-c", "exit 0"))
    clock = fixed_clock(
        datetime(2026, 8, 7, 11, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 11, 0, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    r0_claim = runner.build_r0_claim(result)

    with pytest.raises(AssayError) as excinfo:
        runner.assemble_verdict(
            lane=lane,
            commit="c" * 40,
            result=result,
            claims=(r0_claim,),
            assay_version="0.1.0",
        )

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "R1" in str(excinfo.value)


def test_assemble_verdict_accepts_when_claims_cover_every_declared_level(
    tmp_path: Path,
):
    # The mirror case: once claims cover R0 AND R1, the guard must NOT fire
    # -- proves the guard checks coverage, not merely "more than one level
    # declared". A future R1-claim-producing package satisfies this the same
    # way this synthetic second claim does.
    lane = make_lane(name="package", rigor=("R0", "R1"), argv=("/bin/sh", "-c", "exit 0"))
    clock = fixed_clock(
        datetime(2026, 8, 7, 11, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 7, 11, 5, 1, tzinfo=timezone.utc),
    )
    result = runner.execute_command(lane, cwd=tmp_path, clock=clock)
    r0_claim = runner.build_r0_claim(result)
    r1_claim = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.PASS,
        verified_by_assay=True,
    )

    verdict = runner.assemble_verdict(
        lane=lane,
        commit="d" * 40,
        result=result,
        claims=(r0_claim, r1_claim),
        assay_version="0.1.0",
    )

    assert verdict.declared_rigor == ("R0", "R1")
