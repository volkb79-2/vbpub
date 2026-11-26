"""(P4/S3) The per-candidate budget as ADVISORY vs BINDING.

Operator item (C) of `p4-operator-input-20261125.md` / design
`docs/design/no-time-budgets/DESIGN.md` section 3: ``budget_per_candidate``
may only be removed as a stop where a no-progress watch actually exists. The
watch is armed per RUN, from three facts -- liveness injected, the baseline
produced plugin events, the candidate's CPU is readable -- and this module
pins the two regimes the runner can be built in, plus the last fact's
fallback:

* ARMED (``advisory_budget_s`` set, CPU readable): overrunning the budget
  WARNs once and the candidate CONTINUES; only a progress bound ends it.
* NOT ARMED at all (``advisory_budget_s`` is ``None``): the budget binds
  exactly as it did before P4 -- the caller passes it as ``timeout``.
* ARMED in intent but the CPU was never readable: the runner could not
  really watch, so it falls back to BINDING and says so on the raised
  exception.

Every test drives the loop with the fakes from
``test_liveness_runner_monitor`` (virtual clock, scripted `Popen`, scripted
CPU reader), so no threshold costs real time and no host load can change an
outcome.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_liveness_runner_monitor import (
    _FakeClock,
    _ScriptedProc,
    _append_event,  # re-exported for symmetry with the S1/S2 tests
    _runner,
)

from assay import liveness


def _spinning_reader(clock: _FakeClock, *, finish_at: float | None = None):
    """A CPU reader that always GROWS, so neither the idle rule nor the CPU
    bounds can fire -- the ONLY bound left is the per-candidate budget,
    exactly the shape S3 is about.
    """

    def reader(pid: int) -> float:
        return 1.0 + clock.t

    return reader


def test_armed_budget_is_advisory_and_the_candidate_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ARMED: the candidate overruns a 90 s budget and keeps running to its
    own completion at 200 s. It must NOT raise, and it must say once -- not
    once per poll -- that the budget is advisory for this run.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7201)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=_spinning_reader(clock),
        # A 600 s idle bound: the idle rule cannot decide this candidate.
        expect_next_event_within_s=600.0,
        advisory_budget_s=90.0,
        cpu_without_progress_bound_s=None,
        per_test_cpu_baseline_s=None,
    )

    def stop_at_200(dt: float) -> None:
        clock.advance(dt)
        if clock.t >= 200.0:
            proc.finish(0)

    runner._sleep = stop_at_200  # type: ignore[assignment]
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    assert result.returncode == 0
    assert clock.t >= 200.0  # it really did run past the budget.
    assert proc.waited is False  # never killed.
    assert runner.advisory_exceeded_at_s is not None
    assert runner.advisory_exceeded_at_s >= 90.0
    err = capsys.readouterr().err
    assert err.count("budget_per_candidate") == 1, err
    assert "ADVISORY" in err
    assert "bucket 'hung'" in err


def test_without_an_advisory_budget_the_same_overrun_ends_the_candidate(
    tmp_path: Path,
) -> None:
    """NOT ARMED: the identical candidate shape, with the budget passed as
    the ordinary ``timeout`` (which is what an unarmed run does). It ends at
    the budget with plain ``TimeoutExpired`` -- never `hung`, never a WARN.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7202)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=_spinning_reader(clock),
        expect_next_event_within_s=600.0,
        advisory_budget_s=None,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=90.0)

    assert type(excinfo.value) is subprocess.TimeoutExpired
    assert clock.t == 90.0
    assert proc.waited is True  # killed, as before P4.
    assert (
        excinfo.value.liveness_resource_evidence["decision"]
        == "configured-budget-expired"
    )
    assert not hasattr(excinfo.value, "liveness_watch_unarmed_reason")


def test_an_unreadable_cpu_series_falls_back_to_a_binding_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The third arming precondition -- a readable CPU series -- is answered
    by the FIRST read. When every read fails, no CPU bound could ever fire,
    so the advisory budget must become binding again rather than let a
    candidate run with nothing watching it. The exception names that fact.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7203)

    def unreadable(pid: int) -> float:
        raise OSError("no /proc entry")

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=unreadable,
        expect_next_event_within_s=600.0,
        advisory_budget_s=90.0,
        # Both progress bounds present: they too are inert without CPU.
        cpu_without_progress_bound_s=70.0,
        per_test_cpu_baseline_s={"t": 1.0},
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    assert isinstance(excinfo.value, subprocess.TimeoutExpired)
    assert not isinstance(excinfo.value, liveness.LivenessHungExpired)
    assert excinfo.value.liveness_watch_unarmed_reason == "candidate-cpu-unreadable"
    assert (
        excinfo.value.liveness_resource_evidence["decision"]
        == "budget-expired-watch-unarmed"
    )
    assert clock.t == 90.0
    # No advisory WARN: it never became advisory.
    assert capsys.readouterr().err == ""


def test_a_readable_series_after_an_initial_failure_still_arms(
    tmp_path: Path,
) -> None:
    """A transient read failure must not permanently demote the budget. The
    first successful read is what arms it, and the candidate then continues
    past the budget exactly as in the armed case.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7204)
    reads = {"n": 0}

    def flaky(pid: int) -> float:
        reads["n"] += 1
        if reads["n"] < 3:
            raise OSError("transient")
        return 1.0 + clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=flaky,
        expect_next_event_within_s=600.0,
        advisory_budget_s=90.0,
    )

    def stop_at_150(dt: float) -> None:
        clock.advance(dt)
        if clock.t >= 150.0:
            proc.finish(0)

    runner._sleep = stop_at_150  # type: ignore[assignment]
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert result.returncode == 0
    assert runner.advisory_exceeded_at_s is not None


def test_plugin_events_observed_reads_the_baseline_itself(
    tmp_path: Path,
) -> None:
    """The one arming precondition only the baseline can answer: a plugin
    that never wrote anything (a `-p no:...` in addopts, an early collection
    error, a plugin that failed to import) leaves no progress signal, so the
    budget has to keep binding. Absent/empty/unreadable are all `False`;
    one real record is `True`.
    """
    absent = tmp_path / "never-written.jsonl"
    assert liveness.plugin_events_observed(None) is False
    assert liveness.plugin_events_observed(absent) is False

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert liveness.plugin_events_observed(empty) is False

    torn = tmp_path / "torn.jsonl"
    torn.write_text('{"event": "test", "nodeid": "x"', encoding="utf-8")
    assert liveness.plugin_events_observed(torn) is False

    present = tmp_path / "present.jsonl"
    _append_event(present, {"event": "session_start", "pid": 11})
    assert liveness.plugin_events_observed(present) is True

    directory = tmp_path / "a-directory"
    directory.mkdir()
    assert liveness.plugin_events_observed(directory) is False
