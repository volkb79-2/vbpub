"""O1 — the R0 result mapping: exit 0 is PASS, nonzero is FAIL/COMMAND_FAILED,
a missing executable is ERROR/EXEC_FAILED, an expired budget is
BUDGET_EXCEEDED/LANE_TIMEOUT.

The negative this defends (from the handoff verbatim): *mapping ordinary
non-zero to EXEC_FAILED or universal PASS makes its hand-written expected-
verdict fixture differ field-for-field.* ``test_runner_verdict_fixtures.py``
is where that fixture comparison actually lives (O4); this module proves the
mapping itself, at the :func:`~assay.runner.execute_command` /
:func:`~assay.runner.build_r0_claim` level, including the two failure causes
that AUTHORING.md §3b.A forbids proving with a real missing binary or a real
wall-clock wait: both are exercised here only through the INJECTED
``process_runner`` seam.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane

from assay import runner
from assay.errors import Outcome, ReasonCode

MOMENT_A = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 8, 7, 12, 0, 1, tzinfo=timezone.utc)


# --- exit 0 is PASS -----------------------------------------------------------


def test_exit_zero_is_pass_with_no_reason_code(tmp_path: Path):
    lane = make_lane(argv=("/bin/sh", "-c", "exit 0"))

    result = runner.execute_command(
        lane, cwd=tmp_path, clock=fixed_clock(MOMENT_A, MOMENT_B)
    )

    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.returncode == 0
    assert result.started == "2026-08-07T12:00:00+00:00"
    assert result.ended == "2026-08-07T12:00:01+00:00"


# --- nonzero exit is FAIL/COMMAND_FAILED, never EXEC_FAILED, never PASS ------


@pytest.mark.parametrize("code", [1, 2, 7, 42])
def test_nonzero_exit_is_fail_command_failed(tmp_path: Path, code: int):
    lane = make_lane(argv=("/bin/sh", "-c", f"exit {code}"))

    result = runner.execute_command(lane, cwd=tmp_path, clock=fixed_clock(MOMENT_A, MOMENT_B))

    assert result.outcome is Outcome.FAIL, "not a universal PASS"
    assert result.reason_code is ReasonCode.COMMAND_FAILED, "not mapped to EXEC_FAILED"
    assert result.returncode == code


def test_build_r0_claim_reflects_a_fail_result(tmp_path: Path):
    lane = make_lane(argv=("/bin/sh", "-c", "exit 3"))
    result = runner.execute_command(lane, cwd=tmp_path, clock=fixed_clock(MOMENT_A, MOMENT_B))

    claim = runner.build_r0_claim(result)

    assert claim.rigor == "R0"
    assert claim.source == "computed"
    assert claim.status is Outcome.FAIL
    assert claim.reason_code is ReasonCode.COMMAND_FAILED
    assert claim.verified_by_assay is True
    assert claim.coverage is None


# --- a missing executable is ERROR/EXEC_FAILED, via the INJECTED seam only ---


def _raise_file_not_found(argv, *, env, cwd, timeout):
    raise FileNotFoundError(2, "No such file or directory", argv[0])


def test_missing_executable_is_error_exec_failed_via_injection(tmp_path: Path):
    # No real missing binary: the fake process_runner raises directly, so this
    # proves the mapping without depending on the filesystem NOT containing
    # some path (AUTHORING.md §3b.E: filesystem is an input, control it).
    lane = make_lane(argv=("/opt/does-not-exist/tool",))

    result = runner.execute_command(
        lane,
        cwd=tmp_path,
        process_runner=_raise_file_not_found,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert result.returncode is None


def _raise_permission_error(argv, *, env, cwd, timeout):
    raise PermissionError(13, "Permission denied", argv[0])


def test_any_os_error_starting_the_process_is_also_exec_failed(tmp_path: Path):
    # EXEC_FAILED is "inability to START the process" (A-073) generally, not
    # only the FileNotFoundError shape of it -- a non-executable file (a real,
    # separate OSError subclass) is the same cause.
    lane = make_lane(argv=("/opt/not-executable",))

    result = runner.execute_command(
        lane,
        cwd=tmp_path,
        process_runner=_raise_permission_error,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED


# --- an expired budget is BUDGET_EXCEEDED/LANE_TIMEOUT, no real wait ---------


def _raise_timeout(argv, *, env, cwd, timeout):
    raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)


def test_budget_expiry_is_budget_exceeded_lane_timeout_via_injection(tmp_path: Path):
    # No real wall-clock wait: the fake raises TimeoutExpired immediately,
    # regardless of the lane's actual declared budget (300s here) -- proving
    # the MAPPING, not the wait, matches AUTHORING.md §3b.A's "best: remove
    # the wait" guidance exactly.
    lane = make_lane(budget="5m", budget_seconds=300.0)

    result = runner.execute_command(
        lane,
        cwd=tmp_path,
        process_runner=_raise_timeout,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert result.reason_code is ReasonCode.LANE_TIMEOUT
    assert result.returncode is None


def _raise_timeout_with_bytes_output(argv, *, env, cwd, timeout):
    # (B027) Reproduces the EXACT object CPython hands back on this path:
    # `TimeoutExpired.stdout`/`.stderr` are `bytes`, never decoded, even
    # though the real `default_process_runner` passes `text=True` --
    # CPython's timeout path does not run through the same text-decode step
    # a normal `CompletedProcess` return does.
    raise subprocess.TimeoutExpired(
        cmd=list(argv),
        timeout=timeout,
        output=b"partial stdout before the hang\n",
        stderr=b"partial stderr\n",
    )


def test_budget_expiry_with_bytes_output_does_not_crash(tmp_path: Path):
    """(B027) A mutant-induced timeout used to crash with `AttributeError:
    'bytes' object has no attribute 'encode'` inside `_bounded_tail`, because
    `TimeoutExpired.stdout`/`.stderr` are `bytes` on this path -- no verdict
    was ever produced, and a stale artifact from a prior run could be
    mistaken for the current one. Reverting the fix (decoding
    `exc.stdout`/`exc.stderr` before `_bounded_tail`) must make this test
    raise `AttributeError` again, not just pass against the fix."""
    lane = make_lane(budget="5m", budget_seconds=300.0)

    result = runner.execute_command(
        lane,
        cwd=tmp_path,
        process_runner=_raise_timeout_with_bytes_output,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert result.reason_code is ReasonCode.LANE_TIMEOUT
    assert result.returncode is None
    assert result.stdout_tail == "partial stdout before the hang\n"
    assert result.stderr_tail == "partial stderr\n"


def test_the_declared_budget_seconds_is_what_is_passed_as_the_timeout(tmp_path: Path):
    lane = make_lane(budget="90s", budget_seconds=90.0)
    seen: dict[str, float] = {}

    def spy_runner(argv, *, env, cwd, timeout):
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)

    runner.execute_command(
        lane, cwd=tmp_path, process_runner=spy_runner, clock=fixed_clock(MOMENT_A, MOMENT_B)
    )

    assert seen["timeout"] == 90.0
