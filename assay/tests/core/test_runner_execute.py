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

import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import fixed_clock, make_lane, timeout_expired

from assay import runner
from assay.contention import ContentionObservation
from assay.errors import Outcome, ReasonCode
from assay.mutation_witness import WITNESS_FD_ENV, ReceiptCapture
from assay.verdict import Claim, Coverage

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


@pytest.mark.parametrize(
    ("status", "resource_limit_hit", "expected_outcome"),
    [
        ("clear", False, Outcome.FAIL),
        ("affected", True, Outcome.ERROR),
        ("unknown", False, Outcome.ERROR),
    ],
)
def test_gate_mode_failed_r0_is_not_withheld_for_psi_or_throttling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    resource_limit_hit: bool,
    expected_outcome: Outcome,
):
    class Window:
        observation = ContentionObservation(
            status,
            resource_limit_hit=resource_limit_hit,
            reasons=(
                "cgroup_psi.cpu.some_us=5340037",
                "cgroup_cpu.throttled_usec=500",
            ),
        )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setenv("ASSAY_RESOURCE_OBSERVER_SOCKET", "/observer.sock")
    contention_module = getattr(runner, "contention", None)
    if contention_module is None:
        from assay import contention as contention_module

        monkeypatch.setattr(runner, "contention", contention_module, raising=False)
    monkeypatch.setattr(contention_module, "ContentionWindow", lambda **kwargs: Window())
    lane = make_lane(argv=("/bin/sh", "-c", "exit 9"))

    contention_kwargs = (
        {"classify_resource_contention": True}
        if "classify_resource_contention" in inspect.signature(runner.execute_command).parameters
        else {}
    )
    result = runner.execute_command(lane, cwd=tmp_path, **contention_kwargs)

    assert result.outcome is expected_outcome
    assert result.reason_code is (
        ReasonCode.EXEC_FAILED
        if expected_outcome is Outcome.ERROR
        else ReasonCode.COMMAND_FAILED
    )
    assert result.returncode == 9
    if expected_outcome is Outcome.ERROR:
        assert f"cgroup resource window was {status}" in result.stderr_tail
    else:
        assert result.stderr_tail == ""
    assert runner.build_r0_claim(result).status is expected_outcome


@pytest.mark.parametrize(
    ("status", "resource_limit_hit", "expected_outcome"),
    [
        ("clear", False, Outcome.PASS),
        ("affected", True, Outcome.ERROR),
        ("unknown", False, Outcome.PASS),
    ],
)
def test_gate_mode_pass_verdict_has_no_stderr_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    resource_limit_hit: bool,
    expected_outcome: Outcome,
):
    class Window:
        observation = ContentionObservation(
            status,
            resource_limit_hit=resource_limit_hit,
            reasons=(
                "cgroup_psi.cpu.some_us=5340037",
                "cgroup_cpu.throttled_usec=500",
            ),
        )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setenv("ASSAY_RESOURCE_OBSERVER_SOCKET", "/observer.sock")
    contention_module = getattr(runner, "contention", None)
    if contention_module is None:
        from assay import contention as contention_module

        monkeypatch.setattr(runner, "contention", contention_module, raising=False)
    monkeypatch.setattr(contention_module, "ContentionWindow", lambda **kwargs: Window())
    lane = make_lane(argv=("/bin/sh", "-c", "exit 0"))

    contention_kwargs = (
        {"classify_resource_contention": True}
        if "classify_resource_contention" in inspect.signature(runner.execute_command).parameters
        else {}
    )
    result = runner.execute_command(lane, cwd=tmp_path, **contention_kwargs)

    assert result.outcome is expected_outcome
    assert result.reason_code is (
        ReasonCode.EXEC_FAILED if expected_outcome is Outcome.ERROR else None
    )
    assert getattr(result, "resource_contention", None) is Window.observation
    if expected_outcome is Outcome.ERROR:
        assert "R0 result withheld" in result.stderr_tail
    else:
        assert result.stderr_tail is None


@pytest.mark.parametrize(
    ("value", "expected"), [(None, False), ("1", True), ("yes", False)]
)
def test_gate_contention_attribution_requires_the_private_opt_in(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
):
    if value is None:
        monkeypatch.delenv("ASSAY_GATE_REQUIRE_RESOURCE_OBSERVATION", raising=False)
    else:
        monkeypatch.setenv("ASSAY_GATE_REQUIRE_RESOURCE_OBSERVATION", value)

    assert runner._gate_contention_attribution_enabled() is expected


def _coverage_claim(status: Outcome) -> Claim:
    coverage = Coverage(
        covered=1 if status is Outcome.PASS else 0,
        executable=1,
        pct=100.0 if status is Outcome.PASS else 0.0,
        considered=1,
        exclusion_capability="reported",
        missing_lines=(
            {} if status is Outcome.PASS else {"pkg/mod.py": frozenset({1})}
        ),
        files_missing_coverage=(),
    )
    return Claim(
        rigor="R1",
        source="computed",
        status=status,
        verified_by_assay=True,
        reason_code=(
            None if status is Outcome.PASS else ReasonCode.UNCOVERED_LINES
        ),
        coverage=coverage,
    )


def test_r1_claim_ignores_psi_and_throttling_but_retains_direct_limits():
    adjust = getattr(runner, "_withhold_r1_claim_for_contention", None)
    assert callable(adjust), "R1 must retain the R0 resource window for attribution"

    pressured_failure = adjust(
        _coverage_claim(Outcome.FAIL),
        ContentionObservation("clear", reasons=("cgroup_cpu.throttled_usec=1",)),
        diagnostics=None,
    )
    assert pressured_failure.status is Outcome.FAIL
    assert pressured_failure.reason_code is ReasonCode.UNCOVERED_LINES
    assert pressured_failure.coverage is not None

    affected_pass = _coverage_claim(Outcome.PASS)
    assert adjust(
        affected_pass,
        ContentionObservation("clear", reasons=("cgroup_cpu.throttled_usec=1",)),
        diagnostics=None,
    ) is affected_pass

    direct_limit_pass = adjust(
        _coverage_claim(Outcome.PASS),
        ContentionObservation("affected", resource_limit_hit=True),
        diagnostics=None,
    )
    assert direct_limit_pass.status is Outcome.ERROR

    unknown_pass = adjust(
        _coverage_claim(Outcome.PASS),
        ContentionObservation("unknown"),
        diagnostics=None,
    )
    assert unknown_pass.status is Outcome.PASS

    no_observation = _coverage_claim(Outcome.FAIL)
    assert adjust(no_observation, None, diagnostics=None) is no_observation

    already_infrastructure = Claim(
        rigor="R1",
        source="computed",
        status=Outcome.ERROR,
        verified_by_assay=True,
        reason_code=ReasonCode.EXEC_FAILED,
    )
    assert adjust(
        already_infrastructure,
        ContentionObservation("unknown"),
        diagnostics=None,
    ) is already_infrastructure

    with pytest.raises(TypeError, match="claim must be an R1 Claim"):
        adjust(object(), ContentionObservation("clear"), diagnostics=None)


def test_r1_contention_adjustment_is_wired_after_coverage_judgement():
    source = inspect.getsource(runner._run_prepared_lane)
    assert "_withhold_r1_claim_for_contention(" in source
    assert source.index("r1_claim = evaluate_r1(") < source.index(
        "r1_claim = _withhold_r1_claim_for_contention("
    ) < source.index("claims += (r1_claim,)")


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


def test_process_tree_oserror_is_lane_fatal_not_a_candidate_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only the typed spawn error is candidate-level for native R2.

    An unexpected OSError from the process-tree runner indicates a failure in
    Assay's execution/cleanup boundary. It must not become a reusable
    ``crashed`` candidate record.
    """
    from assay.errors import AssayError
    from assay.process_tree import ProcessTreeProcessRunner

    def raise_monitor_error(self, argv, *, env, cwd, timeout):
        raise OSError("procfs monitor failed")

    monkeypatch.setattr(ProcessTreeProcessRunner, "__call__", raise_monitor_error)
    lane = make_lane(argv=("/bin/true",))

    with pytest.raises(AssayError, match="native R2 process execution infrastructure failed"):
        runner.execute_command(
            lane,
            cwd=tmp_path,
            process_runner=ProcessTreeProcessRunner(),
            clock=fixed_clock(MOMENT_A, MOMENT_B),
        )


# --- an expired budget is BUDGET_EXCEEDED/LANE_TIMEOUT, no real wait ---------


def _raise_timeout(argv, *, env, cwd, timeout):
    raise timeout_expired(cmd=list(argv), timeout=timeout)


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


def _raise_liveness_hung(argv, *, env, cwd, timeout):
    from assay import liveness

    evidence = {
        "schema_version": 1,
        "policy": "pressure-adjusted-idle-v1",
        "decision": "idle-hang",
        "candidate_pid": 4242,
        "candidate_cpu_source": "process-tree-cpu-seconds",
        "wall_elapsed_s": 31.0,
        "eligible_elapsed_s": 31.0,
        "idle_eligible_s": 31.0,
        "required_idle_eligible_s": 15.0,
        "required_cpu_growth_window_s": 30.0,
        "required_cpu_growth_floor_s": 1.0,
        "candidate_session_finish_seen": False,
        "session_finish_eligible_s": None,
        "trace_complete": True,
        "trace_truncated": False,
        "samples": [],
    }
    raise liveness.LivenessHungExpired(
        cmd=list(argv), timeout=timeout, output=b"", stderr=b"", resource_evidence=evidence
    )


def test_liveness_hung_expired_is_budget_exceeded_candidate_hung(tmp_path: Path) -> None:
    """(B091/RW-33, P7 A3) `liveness.LivenessHungExpired` -- a
    `subprocess.TimeoutExpired` subclass `LivenessRunner`'s own monitoring
    loop raises ONLY for an idle-stall kill -- must map to
    `ReasonCode.CANDIDATE_HUNG`, not the plain `LANE_TIMEOUT` a genuine
    elapsed-budget expiry gets. This is the ONE `isinstance` check
    `_execute_plan_inner`'s except-clause gained this session; the test
    right above (`test_budget_expiry_is_budget_exceeded_lane_timeout_via_
    injection`) is this test's own negative -- a PLAIN `TimeoutExpired`
    (the base class, not the subclass) must still map to `LANE_TIMEOUT`,
    proving the branch actually discriminates rather than always picking one
    side.
    """
    lane = make_lane(budget="5m", budget_seconds=300.0)

    result = runner.execute_command(
        lane,
        cwd=tmp_path,
        process_runner=_raise_liveness_hung,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert result.reason_code is ReasonCode.CANDIDATE_HUNG
    assert result.returncode is None
    assert result.liveness_resource_evidence["decision"] == "idle-hang"


def _raise_timeout_with_bytes_output(argv, *, env, cwd, timeout):
    # (B027) Reproduces the EXACT object CPython hands back on this path:
    # `TimeoutExpired.stdout`/`.stderr` are `bytes`, never decoded, even
    # though the real `default_process_runner` passes `text=True` --
    # CPython's timeout path does not run through the same text-decode step
    # a normal `CompletedProcess` return does.
    raise timeout_expired(
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


def test_default_process_runner_tolerates_undecodable_child_output(tmp_path: Path):
    """(B027 round 2, N-W2) `_bounded_tail`'s own docstring claims undecodable
    child bytes have "already become U+FFFD" by the time they reach it --
    true only if the boundary that produces `str` actually replaces instead
    of raising. `default_process_runner`'s `subprocess.run(text=True)` had no
    `errors=`, so `subprocess`'s own strict decoder raised `UnicodeDecodeError`
    on the NORMAL completion path -- the one path B027's own new
    `_decode_timeout_stream` claims to match ("so a hung mutant's tail reads
    the same way a completed one's does"). Reverting `errors="replace"` must
    make this raise `UnicodeDecodeError` again."""
    result = runner.default_process_runner(
        ["/bin/sh", "-c", "printf '\\377\\376'"],
        env={},
        cwd=tmp_path,
        timeout=5.0,
    )
    assert result.returncode == 0
    assert "�" in result.stdout


def test_default_process_runner_passes_a_private_receipt_descriptor(tmp_path: Path):
    capture = ReceiptCapture()
    payload = b'{"ok":true}'
    frame = len(payload).to_bytes(4, "big") + payload
    script = (
        "import os; os.write(int(os.environ["
        + repr(WITNESS_FD_ENV)
        + "]), "
        + repr(frame)
        + ")"
    )
    try:
        result = runner.default_process_runner(
            (sys.executable, "-c", script),
            env={WITNESS_FD_ENV: str(capture.write_fd)},
            cwd=tmp_path,
            timeout=5.0,
        )
    finally:
        receipt = capture.finish()

    assert result.returncode == 0
    assert receipt == {"ok": True}


def test_the_declared_budget_seconds_is_what_is_passed_as_the_timeout(tmp_path: Path):
    lane = make_lane(budget="90s", budget_seconds=90.0)
    seen: dict[str, float] = {}

    def spy_runner(argv, *, env, cwd, timeout):
        seen["timeout"] = timeout
        raise timeout_expired(cmd=list(argv), timeout=timeout)

    runner.execute_command(
        lane, cwd=tmp_path, process_runner=spy_runner, clock=fixed_clock(MOMENT_A, MOMENT_B)
    )

    assert seen["timeout"] == 90.0
