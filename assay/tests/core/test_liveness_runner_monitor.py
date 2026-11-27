"""B091/RW-33, P7 A3 -- :class:`~assay.liveness.LivenessRunner`'s monitoring
loop: the `hung` vs. `budget_exceeded` vs. normal-completion classification,
the process-tree CPU-growth rule, and the "any /proc failure reads as
growing" requirement RW-33 states explicitly.

Every DECISION test below drives the loop with a FAKE clock (`_FakeClock`,
`monotonic`/`sleep` both operate on one virtual counter so no test ever
waits out a real 15s/30s/60s threshold), a FAKE `popen` (`_FakePopen`/
`_ScriptedProc`, so `proc.pid`/`proc.poll()` are fully scripted) and a FAKE
`cpu_reader` (a plain callable, scripted per test) -- proving the loop's own
arithmetic, never real timing or real `/proc` parsing.

Two tests near the bottom use a REAL `subprocess.Popen` (the real
`popen=subprocess.Popen` default) and the REAL default `tree_cpu_seconds`
reader against a genuine child process, with ONLY the clock/sleep faked --
proving the real launch/kill/CPU-sampling machinery actually works together,
not merely that the scripted fakes agree with themselves (AUTHORING.md
§3b.A: a test that only proves a fake matches a fake would not fail if the
real wiring were wrong).

Every new conditional the monitoring loop introduces is exercised here with
the OTHER optional parameter at ITS default (BRIEF-1's own lesson, restated
because A1 and A2 both caught a real bug exactly this way): a CPU-growing
idle candidate is not hung (`test_cpu_growing_prevents_hung_even_when_idle`);
a `/proc` failure with an otherwise BUSY candidate still reads as growing,
never hung, until budget (`test_proc_read_failure_never_declares_hung`); an
unbounded lane (`timeout=None`) never expires on elapsed budget alone
(`test_unbounded_timeout_never_expires_on_budget_alone`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from assay import liveness, mutation_store


class _FakeClock:
    """One virtual counter serving both `monotonic()` and `sleep()` -- a
    "sleep" here means "advance the loop's own notion of elapsed time",
    never a real wait, so a 30s CPU window costs zero real seconds.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.sleep_calls = 0

    def now(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.sleep_calls += 1
        self.t += dt


class _ScriptedProc:
    """A fake `Popen` handle whose `poll()` returns `None` until
    `.finish(returncode)` is called (never, for every `hung`/`budget_exceeded`
    test below -- the loop's own kill is what ends it) or, for the normal-
    completion tests, from construction.
    """

    def __init__(self, pid: int, *, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.waited = False

    def finish(self, returncode: int) -> None:
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waited = True
        return self.returncode


class _FakePopen:
    def __init__(self, proc: _ScriptedProc) -> None:
        self._proc = proc
        self.calls: list[dict[str, object]] = []

    def __call__(self, argv, *, env, cwd, stdout, stderr, start_new_session):
        self.calls.append({"argv": tuple(argv), "cwd": cwd, "env": dict(env)})
        return self._proc


def _ignore_process_group(_pgid: int, _sig: int) -> None:
    """Fake-Popen tests use synthetic PIDs and must never signal host work."""


def _clear_resource_snapshot() -> dict:
    return {
        "schema_version": 1,
        "status": "available",
        "cgroup_identity": "fixture-cgroup",
        "host_psi": {
            "cpu": {"some": 0},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_psi": {
            "cpu": {"some": 0},
            "memory": {"some": 0, "full": 0},
            "io": {"some": 0, "full": 0},
        },
        "cgroup_cpu": {"nr_throttled": 0, "throttled_usec": 0},
    }


def _runner(
    tmp_path: Path,
    *,
    proc: _ScriptedProc,
    clock: _FakeClock,
    cpu_reader,
    resource_reader=None,
    expect_next_event_within_s: float = 15.0,
    pre_first_event_within_s: float | None = None,
    sleep=None,
    cpu_without_progress_bound_s: float | None = None,
    per_test_cpu_baseline_s=None,
    per_test_xdist: bool = False,
    advisory_budget_s: float | None = None,
) -> tuple[liveness.LivenessRunner, Path]:
    events_dir = tmp_path / "candidates"
    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=expect_next_event_within_s,
        # (B091 round-1 B2) `None` -- the constructor default -- is what
        # every pre-existing test below keeps passing, so each of them
        # exercises the new bound-selection conditional with this parameter
        # at ITS default (the two bounds equal, i.e. the pre-B2 behaviour).
        pre_first_event_within_s=pre_first_event_within_s,
        monotonic=clock.now,
        sleep=sleep if sleep is not None else clock.advance,
        cpu_reader=cpu_reader,
        resource_reader=(
            resource_reader
            if resource_reader is not None
            else lambda pid: _clear_resource_snapshot()
        ),
        popen=_FakePopen(proc),
        process_group_killer=_ignore_process_group,
        cpu_without_progress_bound_s=cpu_without_progress_bound_s,
        per_test_cpu_baseline_s=per_test_cpu_baseline_s,
        per_test_xdist=per_test_xdist,
        advisory_budget_s=advisory_budget_s,
    )
    cwd = tmp_path / "cand"
    cwd.mkdir()
    return runner, cwd


def _scripted_events(
    clock: _FakeClock,
    events_path: Path,
    script: list[tuple[float, dict]],
    *,
    proc: _ScriptedProc | None = None,
    finish_at: tuple[float, int] | None = None,
    cpu: float = 3.0,
):
    """A `cpu_reader` that doubles as the candidate PROCESS: on each tick it
    appends whichever scripted plugin records the virtual clock has now
    reached (the real candidate's own pytest would write these), optionally
    exits the process, and reports a FLAT CPU reading.

    Flat CPU is the point: every shape B2 is about -- a container fixture
    coming up, a DB migration, a slow import -- is I/O-bound, so the
    CPU-growth guard offers no protection and the idle bound alone decides.
    """
    pending = list(script)

    def reader(pid: int) -> float:
        while pending and pending[0][0] <= clock.t:
            record = pending.pop(0)[1]
            events_path.parent.mkdir(parents=True, exist_ok=True)
            with events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**record, "t": clock.t}) + "\n")
        if finish_at is not None and proc is not None and clock.t >= finish_at[0]:
            proc.finish(finish_at[1])
        return cpu

    return reader


# --------------------------------------------------------------------------
# idle + no CPU growth -> hung
# --------------------------------------------------------------------------


def test_idle_with_flat_cpu_is_hung(tmp_path: Path) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4242)  # never finishes on its own.
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,  # constant -- never grows.
        expect_next_event_within_s=15.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert excinfo.value.timeout == 600.0
    assert proc.waited  # `_kill` reaped it.


def test_unarmed_liveness_runner_keeps_idle_rule_and_exit_hygiene(
    tmp_path: Path,
) -> None:
    """Without P4 progress bounds, the injected runner still has its
    pre-P4 idle detection and `ASSAY_LIVENESS_EXIT` behavior."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4243)
    popen = _FakePopen(proc)
    cwd = tmp_path / "candidate"
    cwd.mkdir()
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates",
        expect_next_event_within_s=15.0,
        monotonic=clock.now,
        sleep=clock.advance,
        cpu_reader=lambda _pid: 0.0,
        resource_reader=lambda _pid: _clear_resource_snapshot(),
        popen=popen,
        process_group_killer=_ignore_process_group,
        cpu_without_progress_bound_s=None,
        per_test_cpu_baseline_s=None,
        advisory_budget_s=None,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("python", "-m", "pytest", "-q"), env={}, cwd=cwd, timeout=90.0)

    assert excinfo.value.resource_evidence["decision"] == "idle-hang"
    assert runner.cpu_without_progress_bound_s is None
    assert runner._per_test_cpu_baseline_s == {}
    assert runner.advisory_budget_s is None
    assert popen.calls[0]["env"][liveness.ASSAY_LIVENESS_EXIT_ENV] == "1"
    # `hung` requires >= 30s of CPU history (the trailing window) as well as
    # the 15s idle threshold -- the loop must not fire before both are
    # satisfied.
    assert clock.t >= 30.0


def test_hung_result_retains_complete_time_aligned_resource_evidence(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4247)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    evidence = excinfo.value.resource_evidence
    assert evidence["schema_version"] == 1
    assert evidence["policy"] == "pressure-adjusted-idle-v1"
    assert evidence["decision"] == "idle-hang"
    assert evidence["trace_complete"] is True
    assert evidence["idle_eligible_s"] >= evidence["required_idle_eligible_s"]
    samples = evidence["samples"]
    assert len(samples) >= 31
    assert [sample["wall_elapsed_s"] for sample in samples] == sorted(
        sample["wall_elapsed_s"] for sample in samples
    )
    assert all(sample["resources"]["status"] == "available" for sample in samples)
    assert samples[-1]["candidate_cpu_s"] == 3.0
    assert mutation_store._valid_hung_resource_evidence(evidence)


def test_progress_reset_retains_prior_snapshot_for_first_trace_interval(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4255)
    events_path: Path
    wrote_event = False

    def cpu_reader(pid: int) -> float:
        nonlocal wrote_event
        if not wrote_event:
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                '{"event":"test","nodeid":"test_first"}\n', encoding="utf-8"
            )
            wrote_event = True
        return 3.0

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=cpu_reader,
    )
    events_path = runner._events_path_for_cwd(cwd)
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    first = excinfo.value.resource_evidence["samples"][0]
    assert first["resource_interval"] == "clear"
    assert first["previous_resources"]["status"] == "available"
    assert liveness.compare_resource_snapshots(
        first["previous_resources"], first["resources"]
    ) == ("clear", {})


def test_external_memory_stalls_do_not_expire_a_progressing_candidate(
    tmp_path: Path,
) -> None:
    """B107: pressure pauses the idle clock; the same flat-CPU candidate
    continues when progress resumes, rather than becoming `hung` at 30s.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4248)
    events_path: Path
    pending = [(50.0, {"event": "session_start"})]
    pending.extend(
        (float(tick), {"event": "test", "nodeid": f"test-{tick}"})
        for tick in range(52, 101, 2)
    )
    pending.append((100.0, {"event": "session_finish"}))

    def cpu_reader(pid: int) -> float:
        while pending and pending[0][0] <= clock.t:
            record = pending.pop(0)[1]
            events_path.parent.mkdir(parents=True, exist_ok=True)
            with events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**record, "t": clock.t}) + "\n")
        if clock.t >= 101.0:
            proc.finish(0)
        return 3.0  # no CPU growth: pressure and events are the only signals.

    def pressured_resources(pid: int) -> dict:
        sample = _clear_resource_snapshot()
        if 1.0 <= clock.t <= 50.0:
            sample["host_psi"]["memory"]["full"] = int(clock.t * 1_000_000)
        return sample

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=cpu_reader,
        resource_reader=pressured_resources,
        expect_next_event_within_s=15.0,
    )
    events_path = runner._events_path_for_cwd(cwd)
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert result.returncode == 0
    assert clock.t >= 101.0
    assert proc.waited is False  # poll already reaped the completed leader.


def test_pressure_only_candidate_is_incomplete_at_budget_not_classified_hung(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4250)

    def pressured_resources(pid: int) -> dict:
        sample = _clear_resource_snapshot()
        sample["host_psi"]["memory"]["full"] = int(clock.t * 1_000_000)
        return sample

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
        resource_reader=pressured_resources,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=45.0)

    assert type(excinfo.value) is subprocess.TimeoutExpired
    assert clock.t == 45.0
    evidence = excinfo.value.liveness_resource_evidence
    assert evidence["decision"] == "configured-budget-expired"
    assert evidence["trace_complete"] is True
    assert any(sample["resource_interval"] == "stalled" for sample in evidence["samples"])


def test_deadlock_is_detectable_after_pressure_ends(tmp_path: Path) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4251)

    def resources(pid: int) -> dict:
        sample = _clear_resource_snapshot()
        if clock.t <= 10.0:
            sample["host_psi"]["memory"]["full"] = int(clock.t * 1_000_000)
        else:
            sample["host_psi"]["memory"]["full"] = 10_000_000
        return sample

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
        resource_reader=resources,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    assert clock.t >= 40.0
    assert excinfo.value.resource_evidence["decision"] == "idle-hang"
    assert excinfo.value.resource_evidence["trace_complete"] is True
    samples = excinfo.value.resource_evidence["samples"]
    assert any(sample["resource_interval"] == "stalled" for sample in samples)
    assert all(
        sample["eligible_interval_s"] == 0
        for sample in samples
        if sample["resource_interval"] in ("stalled", "unknown")
    )


def test_past_the_sample_cap_the_trace_is_compacted_not_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(P4/S1) The retired behaviour: past
    `_LIVENESS_RESOURCE_TRACE_MAX_SAMPLES` the trace was marked truncated and
    `trace_complete` became False, which disabled EVERY hang verdict for a
    candidate that ran long (about 17 minutes of 1 s polls) -- including one
    that went idle afterwards.

    Now the trace is COMPACTED: rows are merged in place, the artifact stays
    bounded, `trace_complete` stays True, and the hang verdict still fires --
    asserted here at a far longer virtual run than the cap allows and with
    `_valid_hung_resource_evidence` (the resume-time reader) still accepting
    the stored evidence.
    """
    monkeypatch.setattr(liveness, "_LIVENESS_RESOURCE_TRACE_MAX_SAMPLES", 8)
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4252)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
        expect_next_event_within_s=15.0,
    )

    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    evidence = excinfo.value.resource_evidence
    # An idle hang at 30s with a 1s poll is 31 samples: far past the 8-row cap.
    assert clock.t >= 30.0
    assert evidence["trace_truncated"] is False
    assert evidence["trace_complete"] is True
    assert evidence["trace_compacted"] is True
    assert len(evidence["samples"]) <= 8
    assert mutation_store._valid_hung_resource_evidence(evidence)


def test_counter_reset_starts_a_fresh_idle_evidence_window(tmp_path: Path) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4253)
    cwd = tmp_path / "cand"
    events_path = liveness.candidate_events_path(tmp_path / "candidates", cwd)
    finish_written = False

    def cpu_reader(pid: int) -> float:
        nonlocal finish_written
        if clock.t >= 1.0 and not finish_written:
            events_path.write_text(
                '{"event":"session_finish","pid":4253}\n', encoding="utf-8"
            )
            finish_written = True
        return 3.0

    def resources(pid: int) -> dict:
        snapshot = _clear_resource_snapshot()
        snapshot["host_psi"]["memory"]["full"] = (
            10_000_000 if clock.t < 21.0 else 0
        )
        return snapshot

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=cpu_reader,
        resource_reader=resources,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=100.0)

    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "session-finish-hang"
    assert clock.t >= 51.0
    assert evidence["idle_eligible_s"] >= 30.0
    assert evidence["samples"][0]["resource_interval"] == "unknown"
    assert evidence["samples"][0]["previous_resources"]["host_psi"]["memory"]["full"] == 10_000_000


def test_unavailable_resource_evidence_never_certifies_a_hung_candidate(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4249)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
        resource_reader=lambda pid: {
            "schema_version": 1,
            "status": "unavailable",
            "reason": "psi-unreadable",
        },
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=45.0)
    assert type(excinfo.value) is subprocess.TimeoutExpired
    evidence = excinfo.value.liveness_resource_evidence
    assert evidence["decision"] == "configured-budget-expired"
    assert evidence["trace_complete"] is False
    assert evidence["samples"][-1]["resources"]["reason"] == "psi-unreadable"


@pytest.mark.parametrize(
    ("reader_kind", "reason"),
    [("raises", "resource-reader-failed:OSError"), ("non-mapping", "resource-reader-failed:TypeError")],
)
def test_resource_reader_failures_remain_explicitly_incomplete(
    tmp_path: Path, reader_kind: str, reason: str
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4254)

    def resource_reader(pid: int):
        if reader_kind == "raises":
            raise OSError("fixture read failed")
        return []

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
        resource_reader=resource_reader,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=40.0)

    evidence = excinfo.value.liveness_resource_evidence
    assert evidence["decision"] == "configured-budget-expired"
    assert evidence["trace_complete"] is False
    assert evidence["samples"][-1]["resources"]["reason"] == reason


def test_idle_threshold_is_inclusive_at_the_exact_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(P7 session 5 -- a real mutant-table gap this test closes) The test
    above only pins a LOWER bound (`clock.t >= 30.0`), because with the
    default 30s CPU-growth window and a 15s `expect_next_event_within_s`,
    `idle_for` is always already far past its own threshold (30 > 15) by
    the time `cpu_growing` first becomes decidable -- so a `>=` -> `>`
    off-by-one on `idle_for >= self._expect_next_event_within_s` is
    INVISIBLE to that test: it would still fire one tick later and still
    satisfy `clock.t >= 30.0`. Planted (P7 REPORT's mutant table) and
    confirmed silently uncaught by the whole `test_liveness*` suite before
    this test was added.

    This test shrinks `_HUNG_CPU_WINDOW_S` (monkeypatched, module-level)
    below `expect_next_event_within_s` so the CPU-growth condition resolves
    FIRST, making the idle threshold itself the last-satisfied, genuinely
    boundary-determining condition -- then pins hung firing at EXACTLY the
    tick `idle_for` first reaches the threshold, not one tick later. A
    strict `>` mutant fires at `clock.t == 6.0` instead; this test would
    then fail on the `== 5.0` assertion.
    """
    monkeypatch.setattr(liveness, "_HUNG_CPU_WINDOW_S", 2.0)
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4244)  # never finishes on its own.
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,  # constant -- never grows.
        expect_next_event_within_s=5.0,
    )
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert proc.waited
    # `idle_for` reaches exactly 5.0 at tick t=5 (poll_interval_s=1.0,
    # `last_progress_at` pinned at the start); `cpu_growing` is already
    # decidable (False, flat CPU) from t=2 onward (the shrunk 2.0s window)
    # -- so `idle_for`'s own threshold is the one still being crossed right
    # at t=5.0, and a correct `>=` fires on that exact tick.
    assert clock.t == 5.0


def test_cpu_growing_prevents_hung_even_when_idle(tmp_path: Path) -> None:
    """(BRIEF-1's own lesson, the OTHER optional parameter at its default.)
    Same idle (no `test`/stdout progress) shape as the test above, but the
    CPU reader reports steady growth -- RW-33 is explicit that a CPU-
    spinning mutant is NOT hung, it must hit the ordinary budget ceiling
    instead, and this is the direct regression test for that: reverting the
    `cpu_growing` gate would make this raise `LivenessHungExpired` too.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4243)
    calls = {"n": 0}

    def growing_cpu_reader(pid: int) -> float:
        calls["n"] += 1
        return calls["n"] * 2.0  # always higher than the last sample.

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=growing_cpu_reader,
        expect_next_event_within_s=15.0,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=45.0)
    assert type(excinfo.value) is subprocess.TimeoutExpired  # NOT the Hung subclass.
    assert proc.waited


def test_cpu_growth_floor_is_inclusive_at_the_exact_boundary(tmp_path: Path) -> None:
    """(P7 session 5 -- a second real mutant-table gap this test closes)
    Neither test above pins the `_HUNG_CPU_GROWTH_FLOOR_S` (1.0) boundary
    EXACTLY: the flat-CPU test uses a delta of 0.0 (well under the floor
    either way) and the growing-CPU test uses a delta of 2.0 per sample
    (well over). A `>=` -> `>` off-by-one on `cpu_growing = (cpu_now -
    baseline_cpu) >= _HUNG_CPU_GROWTH_FLOOR_S` is invisible to both.
    Planted (P7 REPORT's mutant table) and confirmed silently uncaught by
    the whole `test_liveness*` suite before this test was added.

    `cpu_reader` holds the tree-CPU reading flat at `0.0` for the first 30
    calls (samples `t=0..29`), then steps to exactly `1.0` from the 31st
    call onward (`t=30` and later) -- so at `t=30..34` the loop's own
    30s-trailing baseline is still a `0.0` sample (from `t=0..4`) while
    `cpu_now` is `1.0`: a delta of EXACTLY `1.0`, the floor itself. Under
    the correct `>=`, that delta counts as "growing" (`cpu_growing=True`),
    so `hung` never fires and the loop runs out its ordinary elapsed
    budget instead -- a plain `subprocess.TimeoutExpired`, NOT the `hung`
    subclass, at exactly `clock.t == 35.0`. A strict `>` mutant reads the
    same `1.0` delta as "flat" and raises `LivenessHungExpired` at
    `clock.t == 30.0` instead.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4245)  # never finishes on its own.
    calls = {"n": 0}

    def stepped_cpu_reader(pid: int) -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] <= 30 else 1.0

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=stepped_cpu_reader,
        expect_next_event_within_s=15.0,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=35.0)
    assert type(excinfo.value) is subprocess.TimeoutExpired  # NOT the Hung subclass.
    assert clock.t == 35.0
    assert proc.waited


def test_cpu_sample_history_matches_full_reverse_scan_over_long_virtual_run() -> None:
    """B095: trimming keeps the exact newest comparable window-edge sample.

    This drives 20,000 virtual polls, including missing `/proc` readings,
    irregular intervals, and monotone CPU deltas on both sides of the growth
    floor. Each classification is compared with an unbounded reverse-list
    search; the deque stays bounded by the 30s window and 250ms minimum step.
    """
    window_s = 30.0
    history = liveness._CpuSampleHistory(window_s)
    full_history: list[tuple[float, float]] = []
    now = 0.0
    previous_size = 0
    max_retained = 0

    for index in range(20_000):
        now += 0.25 + (0.25 if index % 11 == 0 else 0.0)
        if index % 97 == 0:
            # `/proc` failures contribute no sample, and therefore cannot
            # become evidence that the process stopped using CPU.
            cpu_now = None
            cpu_growing = True
            assert len(history._samples) == previous_size
        else:
            # Monotone stepped readings retain the full-history oracle while
            # varying the growth on either side of the 1.0-second floor.
            cpu_now = float(index // 37 + index // 97) / 4.0
            full_history.append((now, cpu_now))
            baseline = next(
                (
                    sample_cpu
                    for sample_t, sample_cpu in reversed(full_history)
                    if now - sample_t >= window_s
                ),
                None,
            )
            actual_baseline = history.add(now, cpu_now)
            assert actual_baseline == baseline
            expected_cpu_growing = (
                True
                if baseline is None
                else (cpu_now - baseline) >= liveness._HUNG_CPU_GROWTH_FLOOR_S
            )
            cpu_growing = (
                True
                if actual_baseline is None
                else (cpu_now - actual_baseline) >= liveness._HUNG_CPU_GROWTH_FLOOR_S
            )
            assert cpu_growing is expected_cpu_growing
            assert len(history._samples) <= 121
            previous_size = len(history._samples)
            max_retained = max(max_retained, previous_size)

        if cpu_now is None:
            assert cpu_growing is True

    assert len(full_history) > 19_000
    assert max_retained <= 121


# --------------------------------------------------------------------------
# /proc failure -> "still growing", never hung on missing data (RW-33)
# --------------------------------------------------------------------------


def test_proc_read_failure_never_declares_hung(tmp_path: Path) -> None:
    """Every `cpu_reader` call raises (simulating `/proc/<pid>/stat` being
    unreadable, e.g. the pid already gone in a race). RW-33's explicit
    requirement: this must NEVER be read as proof of no growth -- the
    candidate runs to its plain elapsed budget and gets `budget_exceeded`,
    never `hung`, even though it is genuinely idle (no test events, no
    stdout growth) the entire time.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4244)

    def failing_cpu_reader(pid: int) -> float:
        raise OSError("no such process")

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=failing_cpu_reader,
        expect_next_event_within_s=15.0,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=20.0)
    assert type(excinfo.value) is subprocess.TimeoutExpired
    assert clock.t >= 20.0


# --------------------------------------------------------------------------
# session_finish grace requires a full grace with no events/output progress
# --------------------------------------------------------------------------


@pytest.mark.parametrize("later_event_at", [None, 20.0])
def test_session_finish_then_still_alive_is_hung_after_a_full_idle_grace(
    tmp_path: Path,
    later_event_at: float | None,
) -> None:
    """RW-57: retain the true CPU-quiet hung case, but later progress resets it.

    A 600s calibrated bound isolates the session-finish branch. Pin its idle
    boundary, including when the last event is later than the first finish,
    and keep CPU flat so removing the required quiet-CPU check is observable.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4245)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
        expect_next_event_within_s=600.0,
    )
    events_path = runner._events_path_for_cwd(cwd)

    # A real caller only starts polling AFTER launch, so write the
    # `session_finish` record before the first poll tick -- the loop must
    # still pick it up on ITS first read.
    def sleep_and_maybe_write(dt: float) -> None:
        clock.advance(dt)
        if clock.sleep_calls == 1:
            events_path.write_text(
                '{"event": "session_finish", "exitstatus": 0, "t": 0.0}\n',
                encoding="utf-8",
            )
        if clock.t == later_event_at:
            with events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": "test", "t": clock.t}) + "\n")

    runner._sleep = sleep_and_maybe_write  # type: ignore[assignment]
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert clock.t == (1.0 if later_event_at is None else later_event_at) + 30.0
    assert proc.waited
    assert mutation_store._valid_hung_resource_evidence(excinfo.value.resource_evidence)


def test_session_finish_grace_does_not_classify_growing_cpu_as_hung(
    tmp_path: Path,
) -> None:
    """RW-57: CPU growth through the post-finish grace remains incomplete."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4248)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: clock.t,
        expect_next_event_within_s=600.0,
    )
    events_path = runner._events_path_for_cwd(cwd)

    def write_finish_on_first_poll(dt: float) -> None:
        clock.advance(dt)
        if clock.sleep_calls == 1:
            events_path.write_text(
                '{"event": "session_finish", "exitstatus": 0, "t": 0.0}\n',
                encoding="utf-8",
            )

    runner._sleep = write_finish_on_first_poll  # type: ignore[assignment]
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=40.0)

    assert type(excinfo.value) is subprocess.TimeoutExpired
    assert clock.t == 40.0
    assert proc.waited
    evidence = excinfo.value.liveness_resource_evidence
    assert evidence["decision"] == "configured-budget-expired"
    assert evidence["candidate_session_finish_seen"] is True
    assert (
        evidence["samples"][-1]["candidate_cpu_s"]
        > evidence["samples"][0]["candidate_cpu_s"]
    )


def test_finish_after_resource_reset_and_tree_cpu_drop_stays_incomplete(
    tmp_path: Path,
) -> None:
    """A departing busy child can lower the tree sum despite continuing work.

    Combine a finish event, one unavailable resource observation, and a CPU
    reading that grows before and after a child exits. Neither the resource
    reset nor the discontinuity may supply a quiet trailing CPU window.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4251)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 100.0 + clock.t if clock.t < 32 else clock.t - 32,
        resource_reader=lambda pid: (
            {"schema_version": 1, "status": "unavailable", "reason": "fixture"}
            if clock.t == 10
            else _clear_resource_snapshot()
        ),
        expect_next_event_within_s=600.0,
    )
    events_path = runner._events_path_for_cwd(cwd)

    def write_finish(dt: float) -> None:
        clock.advance(dt)
        if clock.t == 1:
            events_path.write_text(
                '{"event": "session_finish", "exitstatus": 0, "t": 1.0}\n',
                encoding="utf-8",
            )

    runner._sleep = write_finish
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=60.0)

    assert type(excinfo.value) is subprocess.TimeoutExpired
    assert clock.t == 60.0
    assert excinfo.value.liveness_resource_evidence["decision"] == "configured-budget-expired"


def test_xdist_session_finishes_do_not_hang_a_progressing_candidate(
    tmp_path: Path,
) -> None:
    """Round-2 B6: one file merges controller and worker sessions, without pids.

    Worker A finishes at t=5; worker B keeps emitting tests every 2s through
    t=200 with growing process-tree CPU and a 600s calibrated bound. All
    three sessions finish before the controller exits normally at t=203.
    The old finish-only grace kills this healthy candidate at t=35.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4246)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: clock.t,
        expect_next_event_within_s=600.0,
    )
    events_path = runner._events_path_for_cwd(cwd)

    def sleep_and_write(dt: float) -> None:
        clock.advance(dt)
        record = None
        if clock.t in (1.0, 2.0, 3.0):
            record = {"event": "session_start"}
        elif clock.t in (5.0, 201.0, 202.0):
            record = {"event": "session_finish", "exitstatus": 0}
        elif 6.0 <= clock.t <= 200.0 and clock.t % 2.0 == 0.0:
            record = {
                "event": "test", "nodeid": f"test_tail_{int(clock.t)}",
                "outcome": "passed", "duration_s": 2.0,
            }
        if record is not None:
            with events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**record, "t": clock.t}) + "\n")
        if clock.t == 203.0:
            proc.finish(0)

    runner._sleep = sleep_and_write
    result = runner(("pytest", "-n", "2", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert result.returncode == 0
    assert clock.t == 203.0
    assert proc.waited is False  # poll already reaped the completed leader.
    events = [json.loads(line)["event"] for line in events_path.read_text().splitlines()]
    assert events.count("session_start") == 3
    assert events.count("session_finish") == 3
    assert events.count("test") == 98


def test_xdist_worker_finish_is_ignored_until_owner_finishes(
    tmp_path: Path,
) -> None:
    """B097: stamped worker completion cannot arm the owner's grace timer."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4247)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: clock.t,
        expect_next_event_within_s=600.0,
    )
    events_path = runner._events_path_for_cwd(cwd)

    def sleep_and_write(dt: float) -> None:
        clock.advance(dt)
        record = None
        if clock.t == 1.0:
            record = {
                "event": "session_finish",
                "pid": 5001,
                "xdist_worker": "gw0",
            }
        elif clock.t == 2.0:
            record = {
                "event": "test",
                "pid": 5002,
                "xdist_worker": "gw1",
                "nodeid": "test_tail",
            }
        elif clock.t == 40.0:
            record = {"event": "session_finish", "pid": 4247}
        if record is not None:
            with events_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**record, "t": clock.t}) + "\n")
        if clock.t == 70.0:
            proc.finish(0)

    runner._sleep = sleep_and_write
    result = runner(("pytest", "-n", "2", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert result.returncode == 0
    # Without candidate-pid ownership, the worker finish at t=1 would arm
    # the old finish-only branch and kill at t=31, long before the owner
    # finish and normal process exit.
    assert clock.t == 70.0
    assert proc.waited is False  # poll already reaped the completed leader.


# --------------------------------------------------------------------------
# normal completion -- returns a real CompletedProcess, no exception
# --------------------------------------------------------------------------


def test_normal_completion_does_not_signal_reaped_process_group(
    tmp_path: Path,
) -> None:
    """A reaped numeric PGID must not be signaled after normal completion.

    The runner uses subreaper descendant enumeration after leader exit; it
    does not risk signaling an unrelated process if the old PGID is reused.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4246, returncode=0)  # already "exited".
    killed: list[tuple[int, int]] = []
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 1.0,
    )
    runner._process_group_killer = lambda pgid, sig: killed.append((pgid, sig))
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=60.0)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0
    assert killed == []
    assert proc.waited is False  # the leader was already reaped by poll().


def test_normal_completion_with_real_descendant_is_recorded_as_crashed(
    tmp_path: Path,
) -> None:
    """A candidate that leaves a child has no trustworthy functional result.

    The runner kills and reaps the child after the leader exits and attaches
    evidence so the mutation classifier records this attempt as crashed.
    """
    pid_file = tmp_path / "descendant.pid"
    cwd = tmp_path / "candidate"
    cwd.mkdir()
    script = (
        "import subprocess, sys; "
        "child = subprocess.Popen(['/bin/sleep', '300']); "
        "open(sys.argv[1], 'w', encoding='ascii').write(str(child.pid))"
    )
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=10.0,
        poll_interval_s=0.01,
        resource_reader=lambda pid: _clear_resource_snapshot(),
    )

    def still_running(pid: int) -> bool:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            return False
        # A zombie has exited and cannot consume CPU or block the gate. Its
        # parent is the test process's init/subreaper, not this runner.
        return stat.rsplit(")", 1)[1].lstrip()[0] != "Z"

    descendant_pid: int | None = None
    try:
        result = runner(
            (sys.executable, "-c", script, str(pid_file)),
            env=os.environ.copy(),
            cwd=cwd,
            timeout=10.0,
        )
        evidence = result.process_tree_evidence
        assert evidence["outliving_descendants"] >= 1
        assert evidence["descendants_signalled"] >= 1
        assert evidence["clean"] is True
        descendant_pid = int(pid_file.read_text(encoding="ascii"))
        deadline = time.monotonic() + 5.0
        while Path(f"/proc/{descendant_pid}").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not Path(f"/proc/{descendant_pid}").exists(), (
            "Assay must reap, not merely kill, a detached candidate descendant"
        )
    finally:
        if descendant_pid is not None and still_running(descendant_pid):
            os.kill(descendant_pid, liveness.signal.SIGKILL)


def test_normal_completion_records_and_reaps_a_setsid_descendant(tmp_path: Path) -> None:
    pid_file = tmp_path / "setsid-descendant.pid"
    cwd = tmp_path / "candidate"
    cwd.mkdir()
    script = (
        "import subprocess,sys; "
        "child=subprocess.Popen(['/bin/sleep','300'],start_new_session=True); "
        "open(sys.argv[1],'w',encoding='ascii').write(str(child.pid))"
    )
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=10.0,
        poll_interval_s=0.01,
        resource_reader=lambda pid: _clear_resource_snapshot(),
    )
    result = runner(
        (sys.executable, "-c", script, str(pid_file)),
        env=os.environ.copy(),
        cwd=cwd,
        timeout=10.0,
    )
    assert result.process_tree_evidence["outliving_descendants"] >= 1
    assert result.process_tree_evidence["clean"] is True
    descendant_pid = int(pid_file.read_text(encoding="ascii"))
    assert not Path(f"/proc/{descendant_pid}").exists()


def test_timeout_reaps_a_setsid_descendant_before_r2_returns(tmp_path: Path) -> None:
    pid_file = tmp_path / "timeout-setsid-descendant.pid"
    cwd = tmp_path / "candidate"
    cwd.mkdir()
    script = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen(['/bin/sleep','300'],start_new_session=True); "
        "open(sys.argv[1],'w',encoding='ascii').write(str(child.pid)); "
        "time.sleep(30)"
    )
    runner = liveness.LivenessRunner(
        events_dir=tmp_path / "events",
        expect_next_event_within_s=60.0,
        poll_interval_s=0.01,
        resource_reader=lambda pid: _clear_resource_snapshot(),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        runner(
            (sys.executable, "-c", script, str(pid_file)),
            env=os.environ.copy(),
            cwd=cwd,
            timeout=0.5,
        )
    descendant_pid = int(pid_file.read_text(encoding="ascii"))
    assert not Path(f"/proc/{descendant_pid}").exists()


# --------------------------------------------------------------------------
# unbounded lane (timeout=None) -- the OTHER optional parameter, budget off
# --------------------------------------------------------------------------


def test_unbounded_timeout_never_expires_on_budget_alone(tmp_path: Path) -> None:
    """`timeout=None` (B067's "unbounded") must never raise plain
    `TimeoutExpired` from elapsed time -- only `hung` (idle + flat CPU) or a
    genuine exit can end an unbounded candidate. Proven by running the fake
    clock WELL past any real budget a lane could plausibly declare, with the
    CPU reader reporting steady growth throughout (so `hung` cannot fire
    either), then finishing the process normally.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4247)  # not yet finished.
    calls = {"n": 0}

    def growing_cpu_reader(pid: int) -> float:
        calls["n"] += 1
        if calls["n"] == 5:
            proc.finish(0)  # exits partway through -- proves the loop kept polling.
        return calls["n"] * 2.0

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=growing_cpu_reader,
        expect_next_event_within_s=15.0,
    )
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=None)
    assert result.returncode == 0


# --------------------------------------------------------------------------
# real subprocess + real tree_cpu_seconds, only the clock/sleep faked
# --------------------------------------------------------------------------


def test_real_subprocess_thread_join_style_hang_is_killed_and_classified_hung(
    tmp_path: Path,
) -> None:
    """A REAL child (`sleep 300`) monitored with the REAL default
    `tree_cpu_seconds` reader -- only `monotonic`/`sleep` are faked, so the
    30s/15s thresholds cost no real wall-clock time. `sleep` burns ~0 CPU
    and writes nothing, exactly the B090 incident's post-summary hang shape
    (RW-33's own worked example): the loop must kill the real process group
    and raise `LivenessHungExpired`, and the child must actually be dead
    afterward -- not merely have an exception raised while it keeps running.
    """
    clock = _FakeClock()
    launched: list[subprocess.Popen] = []

    def spy_popen(argv, **kwargs):
        proc = subprocess.Popen(argv, **kwargs)
        launched.append(proc)
        return proc

    events_dir = tmp_path / "candidates"
    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=15.0,
        monotonic=clock.now,
        sleep=clock.advance,
        resource_reader=lambda pid: _clear_resource_snapshot(),
        popen=spy_popen,
    )
    cwd = tmp_path / "cand"
    cwd.mkdir()
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("sleep", "300"), env={}, cwd=cwd, timeout=600.0)
    assert len(launched) == 1
    # The real process must be dead: `wait(timeout=0)` either returns
    # immediately (already reaped) or raises `TimeoutExpired` if somehow
    # still alive, which would fail this assertion outright.
    assert launched[0].wait(timeout=5.0) is not None


def test_real_subprocess_normal_completion_captures_real_output(
    tmp_path: Path,
) -> None:
    """A REAL, fast child (`printf`) that exits on its own -- proves the
    `CompletedProcess` the loop returns on normal completion actually reads
    the real stdout FILE back (not a fake), decoded as text.
    """
    clock = _FakeClock()

    def completed_real_child(argv: tuple[str, ...], **kwargs: object) -> subprocess.Popen[str]:
        # Let the tiny real child finish before the monitor starts advancing
        # virtual time. A fake sleep cannot yield to the OS scheduler, so
        # polling a still-running real process with this clock made the test
        # depend on which process the host happened to schedule first.
        proc = subprocess.Popen(argv, **kwargs)
        proc.wait()
        return proc

    events_dir = tmp_path / "candidates"
    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=15.0,
        monotonic=clock.now,
        sleep=clock.advance,
        popen=completed_real_child,
        resource_reader=lambda pid: _clear_resource_snapshot(),
    )
    cwd = tmp_path / "cand"
    cwd.mkdir()
    result = runner(
        ("/bin/sh", "-c", "printf 'hello from a real child\\n'"),
        env={},
        cwd=cwd,
        timeout=30.0,
    )
    assert result.returncode == 0
    assert result.stdout == "hello from a real child\n"


# --------------------------------------------------------------------------
# (B091 round-1 blocker B2, RW-49/D3) the two bounds
# --------------------------------------------------------------------------


def test_a_slow_module_fixture_is_not_hung_under_a_gap_calibrated_bound(
    tmp_path: Path,
) -> None:
    """The reviewer's end-to-end reproduction, as a loop-level regression.

    A candidate whose module-scoped fixture idles 40s I/O-bound, then runs
    the test that asserts on the mutated function and FAILS (an honest
    kill), then exits 1. Under the gap-derived bound (the 42.5s baseline's
    worst gap was 40s, so `3 x 40 = 120`) the monitor must leave it alone
    and return the real exit status.

    The same run under the pre-B2 bound -- 15.0, which is what
    `max(3 x slowest_test_s, 15)` collapsed to because the only `call`
    report was 0.4ms long -- is killed as `hung`, destroying a real kill.
    Both halves are asserted here, so the calibration cannot regress
    silently in either direction.
    """
    script = [
        (1.0, {"event": "session_start"}),
        (40.0, {"event": "phase", "when": "setup", "duration_s": 39.0}),
        (41.0, {"event": "test", "when": "call", "outcome": "failed"}),
    ]

    def run(expect_next_event_within_s: float):
        clock = _FakeClock()
        proc = _ScriptedProc(pid=909)
        events_dir = tmp_path / f"candidates-{expect_next_event_within_s}"
        cwd = tmp_path / f"cand-{expect_next_event_within_s}"
        cwd.mkdir()
        reader = _scripted_events(
            clock,
            liveness.candidate_events_path(events_dir, cwd),
            script,
            proc=proc,
            finish_at=(42.0, 1),
        )
        runner = liveness.LivenessRunner(
            events_dir=events_dir,
            expect_next_event_within_s=expect_next_event_within_s,
            pre_first_event_within_s=expect_next_event_within_s,
            monotonic=clock.now,
            sleep=clock.advance,
            cpu_reader=reader,
            resource_reader=lambda pid: _clear_resource_snapshot(),
            popen=_FakePopen(proc),
            process_group_killer=_ignore_process_group,
        )
        return runner(("pytest", "-q"), env={}, cwd=cwd, timeout=127.5)

    completed = run(120.0)
    assert completed.returncode == 1  # the suite killed the mutant, honestly.

    with pytest.raises(liveness.LivenessHungExpired):
        run(15.0)


def test_the_pre_first_event_bound_governs_until_the_first_event_arrives(
    tmp_path: Path,
) -> None:
    """A candidate that never produces a single plugin event is judged by
    `pre_first_event_within_s` alone -- `expect_next_event_within_s` must
    not reach it. Pinned in BOTH directions with the two bounds far apart,
    so swapping them, or applying the wrong one, fails here.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=707)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,  # flat.
        expect_next_event_within_s=600.0,  # generous; must NOT apply.
        pre_first_event_within_s=15.0,
    )
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=500.0)
    # 30s of flat-CPU history is still required before any kill.
    assert clock.t >= 30.0

    clock2 = _FakeClock()
    proc2 = _ScriptedProc(pid=708)
    runner2, cwd2 = _runner(
        tmp_path / "second",
        proc=proc2,
        clock=clock2,
        cpu_reader=lambda pid: 3.0,
        expect_next_event_within_s=15.0,  # tight; must NOT apply.
        pre_first_event_within_s=600.0,
    )
    # Never `hung`: the only bound in force is the generous pre-first one,
    # so this candidate reaches its plain elapsed budget instead -- which is
    # `budget_exceeded`, a different and honest outcome.
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner2(("pytest", "-q"), env={}, cwd=cwd2, timeout=200.0)
    assert not isinstance(excinfo.value, liveness.LivenessHungExpired)


def test_the_steady_state_bound_takes_over_once_an_event_has_arrived(
    tmp_path: Path,
) -> None:
    """The mirror of the test above: with the SAME generous pre-first bound,
    one `session_start` record is enough to hand the decision to
    `expect_next_event_within_s`, and a candidate that then goes quiet is
    `hung` on the tight bound.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=606)
    events_dir = tmp_path / "candidates"
    cwd = tmp_path / "cand"
    cwd.mkdir()
    reader = _scripted_events(
        clock,
        liveness.candidate_events_path(events_dir, cwd),
        [(1.0, {"event": "session_start"})],
    )
    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=15.0,
        pre_first_event_within_s=600.0,
        monotonic=clock.now,
        sleep=clock.advance,
        cpu_reader=reader,
        resource_reader=lambda pid: _clear_resource_snapshot(),
        popen=_FakePopen(proc),
        process_group_killer=_ignore_process_group,
    )
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=500.0)
    assert clock.t < 600.0  # the tight bound, not the generous one, decided.


def test_a_setup_phase_record_counts_as_progress(tmp_path: Path) -> None:
    """(B091 round-1 B2) The plugin now records `setup`/`teardown` as
    `phase` events, and the monitor must treat them as activity -- that is
    the whole mechanism by which a suite that is doing fixture work, rather
    than running test bodies, stays alive. A candidate emitting ONLY
    `phase` records (never a `test` one) must not be `hung`.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=505)
    events_dir = tmp_path / "candidates"
    cwd = tmp_path / "cand"
    cwd.mkdir()
    reader = _scripted_events(
        clock,
        liveness.candidate_events_path(events_dir, cwd),
        [
            (1.0, {"event": "session_start"}),
            (10.0, {"event": "phase", "when": "setup"}),
            (20.0, {"event": "phase", "when": "teardown"}),
            (30.0, {"event": "phase", "when": "setup"}),
            (40.0, {"event": "phase", "when": "teardown"}),
        ],
        proc=proc,
        finish_at=(45.0, 0),
    )
    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=15.0,
        monotonic=clock.now,
        sleep=clock.advance,
        cpu_reader=reader,
        resource_reader=lambda pid: _clear_resource_snapshot(),
        popen=_FakePopen(proc),
        process_group_killer=_ignore_process_group,
    )
    completed = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=300.0)
    assert completed.returncode == 0


def test_stdout_file_growth_alone_counts_as_progress(tmp_path: Path) -> None:
    """(B091/RW-33, and round-1 B2's disclosure) The plugin-inactive
    fallback: growth of the candidate's stdout or stderr FILE is progress
    even with no events file at all. It is measured to be inert for a real
    pytest lane (default global capture writes nothing to the real fds until
    the run ends, which is why CONSUMERS.md now says a plugin-less lane gets
    coarse liveness only) -- but the signal itself must work, or the
    fallback would be a claim rather than a mechanism.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=404)
    events_dir = tmp_path / "candidates"
    cwd = tmp_path / "cand"
    cwd.mkdir()
    stdout_path = liveness.candidate_events_path(events_dir, cwd).with_suffix(".stdout")
    written = {"n": 0}

    def reader(pid: int) -> float:
        # A candidate that prints, unbuffered, every tick until t=60 -- and
        # then goes silent. No events file is ever created.
        if clock.t <= 60.0:
            written["n"] += 1
            with stdout_path.open("a", encoding="utf-8") as stream:
                stream.write("still here\n")
        return 3.0  # flat CPU throughout.

    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=15.0,
        monotonic=clock.now,
        sleep=clock.advance,
        cpu_reader=reader,
        resource_reader=lambda pid: _clear_resource_snapshot(),
        popen=_FakePopen(proc),
        process_group_killer=_ignore_process_group,
    )
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=500.0)
    assert written["n"] > 30  # it really did keep printing for 60 virtual s.
    # Not killed while it was still printing: the 15s idle bound would have
    # fired at ~30s (once the CPU window filled) if file growth were not
    # counted as progress.
    assert clock.t >= 75.0


# --------------------------------------------------------------------------
# B111 (O6/O7): the diagnostic resource sampler and its sidecar. The sampler
# is a fourth per-tick input that must never classify anything (CD4).
# --------------------------------------------------------------------------

_ROWS = ("normal", "hung", "timeout")
_EXPECTED_OUTCOME = {
    "normal": ("returned", 0),
    "hung": ("LivenessHungExpired",),
    "timeout": ("TimeoutExpired",),
}
# B206: the sidecar keeps the spawn time only; every resource number comes
# from the cgprofile observer.
_SIDECAR_KEYS = {"format", "spawned_at"}


def _row(tmp_path: Path, row: str, *, log: list[str] | None = None):
    """Run one scripted row and return ``(outcome, clock, runner, cwd)``.

    ``normal`` finishes on the third tick; ``hung`` is idle with flat CPU;
    ``timeout`` is busy (growing CPU) until the 45 s budget expires.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=5100)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    ticks = {"n": 0}
    if row == "normal":
        inner = _scripted_events(clock, events_path, [], proc=proc, finish_at=(2.0, 0))
        timeout = 600.0
    elif row == "hung":

        def inner(pid: int) -> float:
            return 3.0

        timeout = 600.0
    else:

        def inner(pid: int) -> float:
            ticks["n"] += 1
            return ticks["n"] * 2.0

        timeout = 45.0
    trail = log if log is not None else []

    def cpu(pid: int) -> float:
        trail.append("cpu")
        return inner(pid)

    def resources(pid: int) -> dict:
        trail.append("resource")
        return _clear_resource_snapshot()

    def sleep(dt: float) -> None:
        trail.append("sleep")
        clock.advance(dt)

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=cpu,
        resource_reader=resources,
        sleep=sleep,
    )
    try:
        result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=timeout)
        outcome: tuple = ("returned", result.returncode)
    except subprocess.TimeoutExpired as exc:  # LivenessHungExpired is a subclass.
        outcome = (type(exc).__name__,)
    return outcome, clock, runner, cwd


@pytest.mark.parametrize("row", _ROWS)
def test_o6_a_tick_is_cpu_then_resource_then_sleep_and_a_killed_tick_stops_after_resource(
    tmp_path: Path, row: str
) -> None:
    """B206: no resource sampler runs. The liveness tick reads only the CPU
    progress and the pressure snapshot (control inputs), then sleeps."""
    log: list[str] = []
    outcome, _clock, _runner_, _cwd = _row(tmp_path, row, log=log)
    assert outcome == _EXPECTED_OUTCOME[row]
    ticks: list[list[str]] = []
    for entry in log:
        if entry == "cpu":
            ticks.append([])
        ticks[-1].append(entry)
    full = ["cpu", "resource", "sleep"]
    if row == "normal":
        assert len(ticks) >= 3
        assert all(tick == full for tick in ticks)
    else:
        assert len(ticks) >= 2
        assert all(tick == full for tick in ticks[:-1])
        assert ticks[-1] == ["cpu", "resource"]


def test_o6_the_runner_takes_no_sampler_argument() -> None:
    with pytest.raises(TypeError):
        liveness.LivenessRunner(  # type: ignore[call-arg]
            events_dir=Path("/nonexistent"), expect_next_event_within_s=1.0, sampler=lambda pid: None
        )


@pytest.mark.parametrize("row", _ROWS)
def test_o7_the_sidecar_is_written_on_every_exit_path_with_the_spawn_time_only(
    tmp_path: Path, row: str
) -> None:
    outcome, _clock, runner, cwd = _row(tmp_path, row)
    assert outcome == _EXPECTED_OUTCOME[row]
    sidecar = liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd))
    assert sidecar is not None
    assert set(sidecar) == _SIDECAR_KEYS
    assert sidecar["format"] == 1
    assert isinstance(sidecar["spawned_at"], float)


def test_o7_a_later_call_whose_popen_raises_leaves_no_stale_sidecar(tmp_path: Path) -> None:
    _outcome, _clock, runner, cwd = _row(tmp_path, "normal")
    events_path = runner._events_path_for_cwd(cwd)
    assert liveness.read_resource_sidecar(events_path) is not None

    def refuse(*args, **kwargs):
        raise OSError("popen failed")

    second = liveness.LivenessRunner(
        events_dir=tmp_path / "candidates", expect_next_event_within_s=15.0, popen=refuse
    )
    with pytest.raises(OSError, match="popen failed"):
        second(("pytest", "-q"), env={}, cwd=cwd, timeout=1.0)
    assert liveness.read_resource_sidecar(events_path) is None
    assert not events_path.with_suffix(liveness.RESOURCE_SIDECAR_SUFFIX).exists()


@pytest.mark.parametrize("row", _ROWS)
def test_o7_a_failing_sidecar_write_never_changes_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: str
) -> None:
    def refuse(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(liveness.os, "replace", refuse)
    outcome, _clock, runner, cwd = _row(tmp_path, row)
    assert outcome == _EXPECTED_OUTCOME[row]
    assert liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd)) is None


# --------------------------------------------------------------------------
# P4/S1: CPU-without-progress -- the bound that replaces the per-candidate
# wall budget as the stop for a candidate that BURNS CPU but completes
# nothing. Progress here is a completed plugin event and NOTHING else.
# --------------------------------------------------------------------------


def _append_event(events_path: Path, record: dict) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")


def test_a_spinning_silent_candidate_is_hung_on_cpu_not_on_budget(
    tmp_path: Path,
) -> None:
    """P4/S1's first required test: a candidate that spins at full CPU and
    writes NOTHING. Before P4 every verdict reachable here was
    `budget_exceeded` (RW-33 was explicit that a CPU-spinning mutant is "not
    hung"); after P4 it is `hung`, decided by the process-tree CPU it burned
    since its (never arriving) first completed event.

    Driven entirely by fakes (a virtual clock and a scripted CPU series), so
    the 60 s bound costs no real seconds and no host load.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7001)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: clock.t * 0.9,  # ~90% of one core, forever.
        cpu_without_progress_bound_s=70.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "cpu-without-progress"
    assert evidence["policy"] == liveness.CPU_PROGRESS_POLICY
    assert evidence["candidate_cpu_source"] == liveness.CPU_PROGRESS_SOURCE
    assert evidence["completed_events"] == 0
    assert evidence["cpu_since_last_completed_event_s"] >= evidence["required_cpu_s"]
    assert evidence["required_cpu_s"] == 70.0
    assert mutation_store._valid_hung_resource_evidence(evidence)
    # NOT the idle rule: the idle bound here is 15 s, and the CPU bound (70 s
    # of CPU at 0.9 CPU-s per wall second) is the one that decided.
    assert clock.t >= 70.0 / 0.9


def test_output_growth_does_not_reset_the_cpu_without_progress_bound(
    tmp_path: Path,
) -> None:
    """P4/S1's second required test, and the review's finding H1: a mutant
    that loops while LOGGING must not defer this bound. Output growth still
    defers the IDLE rule (that rule is unchanged) -- which is exactly why the
    candidate is not ended by idleness and why the CPU bound has to catch it.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7002)
    stdout_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand").with_suffix(".stdout")
    written = {"n": 0}

    def reader(pid: int) -> float:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("a", encoding="utf-8") as stream:
            stream.write("spam\n")
        written["n"] += 1
        return clock.t * 0.9

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        cpu_without_progress_bound_s=70.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "cpu-without-progress"
    assert evidence["completed_events"] == 0
    assert written["n"] > 30  # it really did keep printing.


def test_cpu_without_progress_ignores_never_clearing_pressure(
    tmp_path: Path,
) -> None:
    """P4/S1's third required test (review finding H2): a paused clock never
    fires under persistent pressure. This bound has NO pressure term at all,
    so a candidate whose every resource sample reports pressure -- which keeps
    the idle clock at zero forever -- is still ended when it burns the CPU.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7003)

    def pressured(pid: int) -> dict:
        sample = _clear_resource_snapshot()
        sample["host_psi"]["memory"]["full"] = int((clock.t + 1) * 1_000_000)
        return sample

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: clock.t * 2.0,
        resource_reader=pressured,
        cpu_without_progress_bound_s=70.0,
        # A 600s idle bound: the idle rule cannot fire even if it wanted to.
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "cpu-without-progress"
    assert evidence["cpu_since_last_completed_event_s"] >= 35.0
    # CPU evidence is its own policy, never validated against the idle trace:
    # it names the numbers that killed the candidate, and the kill happened
    # despite persistent pressure -- the point of a CPU-based bound.
    assert evidence["policy"] == liveness.CPU_PROGRESS_POLICY
    assert evidence["candidate_cpu_source"] == liveness.CPU_PROGRESS_SOURCE
    assert evidence["required_cpu_s"] == 70.0
    assert mutation_store._valid_hung_resource_evidence(evidence)


def test_a_slow_test_below_the_cpu_bound_survives(tmp_path: Path) -> None:
    """P4/S1's fourth required test: a genuinely slow but progressing test is
    never mistaken for a hang -- and, because the CPU it burns is what the
    bound measures, burning CPU on a test that COMPLETES resets the window."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7004)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")

    def reader(pid: int) -> float:
        # One slowly progressing test: heavy CPU, but a completed `test`
        # event every 40 virtual seconds -- well inside the 70 s bound.
        if clock.t > 0 and clock.t % 40.0 == 0.0:
            _append_event(
                events_path,
                {
                    "event": "test",
                    "nodeid": f"t{int(clock.t)}",
                    "pid": pid,
                    "duration_s": 39.0,
                    "outcome": "passed",
                },
            )
        return clock.t * 0.95

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        cpu_without_progress_bound_s=70.0,
    )
    result = None

    def stop_at_200(dt: float) -> None:
        clock.advance(dt)
        if clock.t >= 200.0:
            proc.finish(0)

    runner._sleep = stop_at_200  # type: ignore[assignment]
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert result.returncode == 0
    assert clock.t >= 200.0


def test_child_exit_never_lowers_the_cpu_series(tmp_path: Path) -> None:
    """P4/S1's fifth required test (design §7.1's own UNVERIFIED item).

    `tree_cpu_seconds` sums the LIVE tree, so a busy descendant exiting
    lowers the reading. If that were taken at face value a spinning candidate
    would be handed a "it burned no CPU" verdict exactly when it burned the
    most. The runner therefore banks the peak when the series dips: the same
    virtual CPU history that dips below its own peak still reaches the bound.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7005)
    # Rises to 40 CPU-s by t=20 (a child was doing the work), then the child
    # exits and the tree's sum drops to 2 s and grows slowly from there.
    def reader(pid: int) -> float:
        if clock.t <= 20.0:
            return clock.t * 2.0
        return 2.0 + (clock.t - 20.0) * 0.5

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        cpu_without_progress_bound_s=70.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "cpu-without-progress"
    # 40 banked at t=20 plus the slow growth after it, never 2.0 + something
    # that would have to start over from the dip.
    assert evidence["candidate_cpu_s"] >= 70.0
    assert evidence["cpu_since_last_completed_event_s"] >= 70.0


def test_the_runner_never_invents_a_cpu_bound_of_its_own() -> None:
    """A hand-built runner (another module, a test) gets NO CPU bound: the
    value is derived from a measured baseline and must be passed in, never
    defaulted to something plausible."""
    runner = liveness.LivenessRunner(
        events_dir=Path("/tmp/assay-no-such-dir"), expect_next_event_within_s=1.0
    )
    assert runner.cpu_without_progress_bound_s is None


def test_wall_backstop_stops_an_armed_candidate_without_completed_events(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7006)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda _pid: 0.0,
        cpu_without_progress_bound_s=2.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessNoProgressWallExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=30.0)
    assert excinfo.value.wall_bound_s == 4 * 2.0
    assert excinfo.value.wall_seconds >= excinfo.value.wall_bound_s
    assert clock.t >= excinfo.value.wall_bound_s
    assert proc.waited


def test_completed_events_redate_the_wall_backstop(tmp_path: Path) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7007)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    reader = _scripted_events(
        clock,
        events_path,
        [(2.0, {"event": "phase"}), (7.0, {"event": "phase"}),
         (12.0, {"event": "phase"}), (17.0, {"event": "phase"})],
        proc=proc,
        finish_at=(24.0, 0),
    )
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        cpu_without_progress_bound_s=2.0,
        expect_next_event_within_s=600.0,
    )
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=40.0)
    assert result.returncode == 0
    assert clock.t >= 24.0


def test_session_finish_redates_the_wall_backstop_once(tmp_path: Path) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7011)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    reader = _scripted_events(
        clock,
        events_path,
        [(5.0, {"event": "session_finish"})],
    )
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        cpu_without_progress_bound_s=2.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessNoProgressWallExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=30.0)
    assert excinfo.value.wall_seconds >= 8.0
    assert clock.t >= 13.0


def test_wall_backstop_still_fires_after_cpu_becomes_unreadable(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7008)
    reads = {"count": 0}

    def cpu_reader(_pid: int) -> float:
        reads["count"] += 1
        if reads["count"] == 1:
            return 0.0
        raise OSError("scripted CPU source became unreadable")

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=cpu_reader,
        cpu_without_progress_bound_s=2.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessNoProgressWallExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=30.0)
    assert excinfo.value.wall_seconds >= 8.0
    assert reads["count"] > 1


def test_wall_backstop_is_disabled_without_a_calibrated_cpu_bound(
    tmp_path: Path,
) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7009)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda _pid: 0.0,
        cpu_without_progress_bound_s=None,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=8.0)
    assert type(excinfo.value) is subprocess.TimeoutExpired
    assert clock.t == 8.0


def test_lane_deadline_precedes_the_wall_backstop(tmp_path: Path) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7010)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda _pid: 0.0,
        cpu_without_progress_bound_s=2.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=5.0)
    assert type(excinfo.value) is subprocess.TimeoutExpired
    assert excinfo.value.timeout == 5.0
    assert clock.t == 5.0


# --------------------------------------------------------------------------
# P4/S2: the per-test CPU bound. `pytest_runtest_logstart` dates each test's
# own window; a candidate whose single in-flight test burns
# `max(10 x its baseline duration, 60 s)` CPU seconds is killed as `hung`.
# --------------------------------------------------------------------------


def test_a_looping_test_is_caught_near_its_own_per_test_bound(
    tmp_path: Path,
) -> None:
    """The operator's own item (A): a looping test is caught at about its
    normal duration, not at a whole-suite budget. The baseline says this test
    takes 3 s, so the bound is max(10 x 3, 60) = 60 CPU seconds; the candidate
    loops at ~1 CPU-s per virtual second with the test still in flight.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7101)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    started = {"done": False}

    def reader(pid: int) -> float:
        if not started["done"]:
            _append_event(
                events_path,
                {"event": "test_start", "nodeid": "tests/test_x.py::test_slow", "pid": pid},
            )
            started["done"] = True
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"tests/test_x.py::test_slow": 3.0},
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)

    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "per-test-cpu-bound"
    assert evidence["policy"] == liveness.CPU_PROGRESS_POLICY
    assert evidence["test_nodeid"] == "tests/test_x.py::test_slow"
    assert evidence["per_test_baseline_s"] == 3.0
    assert evidence["required_cpu_s"] == 60.0  # the floor, not 30 s.
    assert evidence["per_test_cpu_s"] >= 60.0
    assert evidence["cpu_since_last_completed_event_s"] >= 60.0
    assert mutation_store._valid_hung_resource_evidence(evidence)
    # Caught at its own bound, not at any suite-length budget.
    assert clock.t >= 60.0


def test_a_slow_test_under_its_own_bound_survives(tmp_path: Path) -> None:
    """The same shape, one CPU second per virtual second, but the test FINISHES
    before its bound: it is not a hang, and the CPU it burned is then part of
    a completed unit rather than a stall."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7102)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    wrote: dict[str, bool] = {"start": False}

    def reader(pid: int) -> float:
        if not wrote["start"]:
            _append_event(
                events_path,
                {"event": "test_start", "nodeid": "t", "pid": pid},
            )
            wrote["start"] = True
        # (P4 fix 1) The test COMPLETES at t=30, well inside the 60 s bound,
        # and the records below are the real order a pytest item emits: the
        # `call` report, then teardown, then the `test_finish` that closes the
        # window (`call`/`teardown` alone must not -- see finding 1).
        if clock.t == 30.0 and not wrote.get("finished"):
            wrote["finished"] = True
            _append_event(
                events_path,
                {"event": "test", "when": "call", "nodeid": "t", "outcome": "passed", "pid": pid},
            )
            _append_event(
                events_path,
                {"event": "phase", "when": "teardown", "nodeid": "t", "pid": pid},
            )
            _append_event(
                events_path, {"event": "test_finish", "nodeid": "t", "pid": pid}
            )
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t": 1.0},
        expect_next_event_within_s=600.0,
    )

    def stop_at_120(dt: float) -> None:
        clock.advance(dt)
        if clock.t >= 120.0:
            proc.finish(0)

    runner._sleep = stop_at_120  # type: ignore[assignment]
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert result.returncode == 0


def test_an_unknown_nodeid_gets_no_per_test_bound(tmp_path: Path) -> None:
    """A nodeid the baseline never measured has no baseline duration to
    multiply, so there is no per-test bound for it. Inventing one would be a
    guessed bound; S1 is the backstop instead."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7103)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    wrote = {"n": 0}

    def reader(pid: int) -> float:
        if wrote["n"] == 0:
            _append_event(
                events_path,
                {"event": "test_start", "nodeid": "not-in-baseline", "pid": pid},
            )
            wrote["n"] = 1
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"some-other-test": 1.0},
        cpu_without_progress_bound_s=100.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    # S1 decided it, NOT a fabricated per-test bound.
    assert evidence["decision"] == "cpu-without-progress"
    assert evidence["required_cpu_s"] == 100.0
    assert evidence["test_nodeid"] is None
    assert mutation_store._valid_hung_resource_evidence(evidence)


def test_xdist_disables_the_per_test_bound(tmp_path: Path) -> None:
    """Under `-n` several tests run concurrently in several processes, so
    "exactly one test in flight" is false by construction: the per-test bound
    is skipped entirely and S1 remains."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7104)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    wrote = {"n": 0}

    def reader(pid: int) -> float:
        if wrote["n"] == 0:
            _append_event(
                events_path,
                {"event": "test_start", "nodeid": "t", "pid": pid},
            )
            wrote["n"] = 1
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t": 0.1},  # would bound at 60 CPU-s
        per_test_xdist=True,
        cpu_without_progress_bound_s=100.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "cpu-without-progress"
    assert evidence["required_cpu_s"] == 100.0
    assert verify_per_test_never_armed(evidence)
    assert clock.t >= 100.0


def verify_per_test_never_armed(evidence) -> bool:
    """The per-test decision appears nowhere in an xdist run's evidence."""
    return evidence["decision"] != liveness.per_test_cpu_bound_decision


def test_the_per_test_bound_requires_exactly_one_in_flight_test(
    tmp_path: Path,
) -> None:
    """A file that opened a second test before the first completed proves two
    tests were in flight, so no single window can be attributed -- the
    per-test bound disables itself for the rest of that file."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7105)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    wrote = {"n": 0}

    def reader(pid: int) -> float:
        if wrote["n"] == 0:
            for nodeid in ("a", "b"):
                _append_event(
                    events_path, {"event": "test_start", "nodeid": nodeid, "pid": pid}
                )
            wrote["n"] = 1
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"a": 0.1, "b": 0.1},
        cpu_without_progress_bound_s=110.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert excinfo.value.resource_evidence["decision"] == "cpu-without-progress"
    assert clock.t >= 110.0


def test_the_plugin_writes_a_test_start_event_with_the_nodeid() -> None:
    """The plugin source is the writer of this event, so the event's shape is
    asserted from the SHIPPED source, not from a hand-built record."""
    source = liveness._PLUGIN_SOURCE
    assert "def pytest_runtest_logstart(nodeid, location):" in source
    assert '{"event": "test_start", "nodeid": nodeid}' in source
    # The hook body must stay exception-safe: a bug in the plugin may never
    # change a test outcome.
    block = source.split("def pytest_runtest_logstart(nodeid, location):", 1)[1]
    block = block.split("def ", 1)[0]
    assert "except Exception" in block


# --------------------------------------------------------------------------
# P4 fix round 1, finding 1: a test's own window is dated by `test_start` and
# closed ONLY by that test's `test_finish`. Before this, the reader never
# cleared `in_flight`, so (a) the `setup` report closed the window before the
# test body ever ran (it is in `COMPLETED_EVENT_KINDS`) and (b) any suite with
# two tests disabled the bound for the whole file. Every scripted record set
# below is the REAL emission order of a pytest run -- logstart, setup, call,
# teardown, logfinish -- never a hand-picked subset of it.
# --------------------------------------------------------------------------


def _real_runtest_records(
    nodeid: str, *, t: float, finished: bool = True
) -> list[tuple[float, dict]]:
    """The five records one pytest item emits, in that order and at *t*."""
    records = [
        (t, {"event": "test_start", "nodeid": nodeid}),
        (t, {"event": "phase", "when": "setup", "nodeid": nodeid}),
        (t, {"event": "test", "when": "call", "nodeid": nodeid, "outcome": "passed"}),
        (t, {"event": "phase", "when": "teardown", "nodeid": nodeid}),
    ]
    if finished:
        records.append((t, {"event": "test_finish", "nodeid": nodeid}))
    return records


def test_a_real_two_test_emission_order_keeps_a_window_for_the_second_test(
    tmp_path: Path,
) -> None:
    """The review's first fatal state: a suite with two tests must NOT disable
    the bound. Test `a` runs to completion in the real order; test `b` then
    spins inside its `call`. Only the SECOND test's own window may bound it,
    and the first test's window must be gone by then."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7110)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    pending = [(1.0, {"event": "session_start"})]
    pending.extend(_real_runtest_records("t::a", t=2.0))
    pending.append((3.0, {"event": "test_start", "nodeid": "t::b"}))

    def reader(pid: int) -> float:
        while pending and pending[0][0] <= clock.t:
            _append_event(events_path, {**pending.pop(0)[1], "pid": pid})
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t::a": 1.0, "t::b": 1.0},
        cpu_without_progress_bound_s=1000.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "per-test-cpu-bound", (
        "with the real emission order the per-test bound must stay armed for "
        "a suite of two or more tests; got "
        f"{evidence['decision']!r}"
    )
    assert evidence["test_nodeid"] == "t::b"
    assert evidence["per_test_cpu_s"] >= 60.0
    # b's window opens at t=3 (cpu=3) and bounds at 60 CPU-s: never before
    # the first test has finished.
    assert clock.t >= 63.0


def test_the_window_stays_open_across_setup_and_a_slow_call(
    tmp_path: Path,
) -> None:
    """The review's second fatal state: the `setup` report used to close the
    window before the body ran. Here the fixture is tiny and the `call` body
    spins: the window must still be open (and still be the one that fires)."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7111)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    pending = [(1.0, {"event": "session_start"})]
    pending.extend(_real_runtest_records("t::slow", t=2.0, finished=False)[:2])

    def reader(pid: int) -> float:
        while pending and pending[0][0] <= clock.t:
            _append_event(events_path, {**pending.pop(0)[1], "pid": pid})
        # From here the test body spins: no further record until it is killed.
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t::slow": 1.0},
        cpu_without_progress_bound_s=1000.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "per-test-cpu-bound", (
        "a test that spins inside its own `call` must be bounded by its own "
        f"window; got {evidence['decision']!r}"
    )
    assert evidence["test_nodeid"] == "t::slow"
    assert clock.t >= 61.0


def test_progress_reader_uses_session_owner_pid_and_ignores_nested_pytest(
    tmp_path: Path,
) -> None:
    """A pytest wrapper can spawn the session-owning interpreter as a child.

    Its stamped session pid owns progress even when it differs from the pid
    Assay spawned. A nested pytest's records still do not close or overlap
    that owner's per-test window.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7113)
    events_path = liveness.candidate_events_path(
        tmp_path / "candidates", tmp_path / "cand"
    )
    records = [
        {"event": "session_start", "pid": 8110},
        {"event": "test_start", "nodeid": "t::owned", "pid": 8110},
        {"event": "phase", "when": "setup", "nodeid": "t::owned", "pid": 8110},
        # The nested interpreter sees the same nodeid but cannot close the
        # owning session's test window.
        {"event": "test_finish", "nodeid": "t::owned", "pid": 8111},
    ]
    written = {"done": False}

    def reader(_spawned_pid: int) -> float:
        if not written["done"]:
            for record in records:
                _append_event(events_path, record)
            written["done"] = True
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t::owned": 1.0},
        cpu_without_progress_bound_s=1000.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("timeout", "3600", "python", "-m", "pytest", "-q"), env={}, cwd=cwd, timeout=90.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "per-test-cpu-bound"
    assert evidence["test_nodeid"] == "t::owned"
    assert evidence["completed_events"] == 1
    assert evidence["per_test_cpu_s"] >= 60.0
    assert clock.t >= 60.0


def test_a_rerun_of_one_nodeid_opens_a_new_window(tmp_path: Path) -> None:
    """The window is keyed on the `test_start` RECORD, not on the nodeid: a
    suite that runs one nodeid twice (a retry, a repeated parameter) must give
    the second run its own window, counted from its own start, never from the
    first run's."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7112)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    pending = [(1.0, {"event": "session_start"})]
    pending.extend(_real_runtest_records("t::again", t=2.0))
    # A rerun of the SAME nodeid, 50 CPU-s later, and it then spins alone.
    pending.append((52.0, {"event": "test_start", "nodeid": "t::again"}))

    def reader(pid: int) -> float:
        while pending and pending[0][0] <= clock.t:
            _append_event(events_path, {**pending.pop(0)[1], "pid": pid})
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t::again": 1.0},
        cpu_without_progress_bound_s=1000.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "per-test-cpu-bound"
    assert evidence["test_nodeid"] == "t::again"
    # Restamped at 52 CPU-s, bounded 60 CPU-s later: NOT at 60 CPU-s total.
    assert evidence["per_test_cpu_s"] >= 60.0
    assert clock.t >= 112.0


def test_per_test_window_starts_when_cpu_first_becomes_readable(
    tmp_path: Path,
) -> None:
    """A start read while the process-tree CPU source is unavailable must not
    leave that test permanently without its own bound. Date the window at the
    first readable series value, then measure the full per-test CPU allowance
    from there."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7114)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    reads = {"count": 0}

    def reader(pid: int) -> float:
        if reads["count"] == 0:
            _append_event(
                events_path,
                {"event": "test_start", "nodeid": "t::late-cpu", "pid": pid},
            )
        reads["count"] += 1
        if reads["count"] <= 2:
            raise OSError("scripted CPU series not readable yet")
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t::late-cpu": 1.0},
        cpu_without_progress_bound_s=1000.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "per-test-cpu-bound"
    assert evidence["test_nodeid"] == "t::late-cpu"
    assert evidence["per_test_cpu_s"] >= 60.0
    # First readable CPU is at t=2; the full 60 CPU-s allowance starts there.
    assert clock.t >= 62.0


def _reader_with_session_finish_after_59_cpu_s(
    clock: _FakeClock, events_path: Path
) -> Callable[[int], float]:
    written = {"start": False, "finish": False}

    def reader(pid: int) -> float:
        if not written["start"]:
            _append_event(events_path, {"event": "session_start", "pid": pid})
            _append_event(
                events_path, {"event": "test_start", "nodeid": "t::late", "pid": pid}
            )
            written["start"] = True
        if clock.t >= 59.0 and not written["finish"]:
            _append_event(events_path, {"event": "session_finish", "pid": pid})
            written["finish"] = True
        return clock.t

    return reader


def test_session_finish_restarts_cpu_bound_and_closes_test_window_for_teardown(
    tmp_path: Path,
) -> None:
    """Teardown work after `session_finish` receives one full CPU bound from
    that event. The CPU and per-test windows were already almost exhausted;
    both must be reset once when the finish first appears."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7115)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=_reader_with_session_finish_after_59_cpu_s(clock, events_path),
        per_test_cpu_baseline_s={"t::late": 1.0},
        cpu_without_progress_bound_s=60.0,
        expect_next_event_within_s=600.0,
    )

    def finish_after_50_cpu_s_of_teardown(dt: float) -> None:
        clock.advance(dt)
        if clock.t >= 110.0:
            proc.finish(0)

    runner._sleep = finish_after_50_cpu_s_of_teardown  # type: ignore[assignment]
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert result.returncode == 0
    # The finish is consumed at t=60; the candidate exits after 50 more CPU-s.
    assert clock.t >= 110.0


def test_session_finish_does_not_reopen_a_missing_test_finish_window(
    tmp_path: Path,
) -> None:
    """A test that omitted `test_finish` must not regain its per-test cap
    during session teardown. Teardown here exceeds the 60 CPU-s per-test
    floor but remains under the full 180 CPU-s bound dated at session_finish.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7117)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=_reader_with_session_finish_after_59_cpu_s(clock, events_path),
        per_test_cpu_baseline_s={"t::late": 0.1},
        cpu_without_progress_bound_s=180.0,
        expect_next_event_within_s=600.0,
    )

    def finish_after_120_cpu_s_of_teardown(dt: float) -> None:
        clock.advance(dt)
        if clock.t >= 180.0:
            proc.finish(0)

    runner._sleep = finish_after_120_cpu_s_of_teardown  # type: ignore[assignment]
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=300.0)
    assert result.returncode == 0
    assert clock.t >= 180.0


def test_cpu_spin_after_session_finish_stops_after_a_full_new_bound(
    tmp_path: Path,
) -> None:
    """The session-finish reset is one-shot: a post-finish spin still trips
    the CPU-without-progress bound after one full allowance from the finish."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7116)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=_reader_with_session_finish_after_59_cpu_s(clock, events_path),
        per_test_cpu_baseline_s={"t::late": 1.0},
        cpu_without_progress_bound_s=60.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "cpu-without-progress"
    assert evidence["test_nodeid"] is None
    assert evidence["cpu_since_last_completed_event_s"] >= 60.0
    # The finish is consumed at t=60 and the CPU bound fires 60 CPU-s later.
    assert clock.t >= 120.0


def test_a_test_start_without_a_finish_still_disables_the_per_test_bound(
    tmp_path: Path,
) -> None:
    """The must-fail CONTROL for the fix: `a` runs its whole setup/call/
    teardown in the real order but never emits `test_finish`, then `b` starts.
    Two tests really were in flight (the file cannot prove otherwise), so the
    per-test bound must stay OFF for that file -- a fix that cleared the
    window on `teardown` instead of on `test_finish` would pass the tests
    above and fail this one."""
    clock = _FakeClock()
    proc = _ScriptedProc(pid=7113)
    events_path = liveness.candidate_events_path(tmp_path / "candidates", tmp_path / "cand")
    pending = [(1.0, {"event": "session_start"})]
    pending.extend(_real_runtest_records("t::a", t=2.0, finished=False))
    pending.append((3.0, {"event": "test_start", "nodeid": "t::b"}))

    def reader(pid: int) -> float:
        while pending and pending[0][0] <= clock.t:
            _append_event(events_path, {**pending.pop(0)[1], "pid": pid})
        return clock.t

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=reader,
        per_test_cpu_baseline_s={"t::a": 1.0, "t::b": 1.0},
        cpu_without_progress_bound_s=110.0,
        expect_next_event_within_s=600.0,
    )
    with pytest.raises(liveness.LivenessHungExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    evidence = excinfo.value.resource_evidence
    assert evidence["decision"] == "cpu-without-progress"
    assert evidence["test_nodeid"] is None
    assert verify_per_test_never_armed(evidence)
    assert clock.t >= 110.0


def _write_stamped_records(
    events_path: Path, records: list[dict], *, pid: int
) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps({**record, "pid": pid}) + "\n")


def test_the_reader_clears_in_flight_only_on_the_matching_test_finish(
    tmp_path: Path,
) -> None:
    """The reader half of finding 1: the window is closed by the matching
    `test_finish` and by nothing else. Its setup/call/teardown reports are
    COMPLETED events for the CPU bound but do not end the test, and a
    `test_finish` for another nodeid must not clear the open window."""
    events_path = tmp_path / "reader-finish.ndjson"
    reader = liveness._EventProgressReader(100)

    _write_stamped_records(
        events_path,
        [{"event": "session_start"}, {"event": "test_start", "nodeid": "t::a"}]
        + [
            {"event": "phase", "when": "setup", "nodeid": "t::a"},
            {"event": "test", "when": "call", "nodeid": "t::a"},
            {"event": "phase", "when": "teardown", "nodeid": "t::a"},
        ],
        pid=100,
    )
    reader.read(events_path)
    assert reader.in_flight is not None, (
        "setup, call and teardown reports must not close a test's window"
    )
    assert reader.completed_events == 3

    _write_stamped_records(
        events_path, [{"event": "test_finish", "nodeid": "t::a"}], pid=100
    )
    reader.read(events_path)
    assert reader.in_flight is None
    assert reader.overlapping_tests is False
    # `test_finish` is NOT a completed event: the CPU bound's marker must not
    # move on it.
    assert reader.completed_events == 3

    _write_stamped_records(
        events_path,
        [
            {"event": "test_start", "nodeid": "t::b"},
            {"event": "test_finish", "nodeid": "t::a"},
        ],
        pid=100,
    )
    reader.read(events_path)
    assert reader.in_flight is not None, (
        "a test_finish for a DIFFERENT nodeid must not close the open window"
    )

    _write_stamped_records(
        events_path, [{"event": "test_finish", "nodeid": "t::b"}], pid=100
    )
    reader.read(events_path)
    assert reader.in_flight is None


def test_the_reader_tracks_two_real_tests_without_overlap(tmp_path: Path) -> None:
    """The reader sees exactly the sequence a real two-test run writes; the
    second test's start must NOT be reported as an overlap, and both windows
    must be closed by their own finishes."""
    events_path = tmp_path / "reader-two-tests.ndjson"
    records: list[dict] = [{"event": "session_start"}]
    for nodeid in ("t::a", "t::b"):
        records.extend(
            record for _t, record in _real_runtest_records(nodeid, t=1.0)
        )
    _write_stamped_records(events_path, records, pid=100)
    reader = liveness._EventProgressReader(100)
    count, saw_finish = reader.read(events_path)
    assert (count, saw_finish) == (len(records), False)
    assert reader.in_flight is None
    assert reader.overlapping_tests is False
    assert reader.completed_events == 6  # two tests x setup/call/teardown
    # The forwarded baseline stream and `tests_completed` are unchanged by the
    # extra records: exactly the two `call` events remain.
    assert liveness.count_test_events(events_path) == 2
    assert [record["nodeid"] for record in liveness.baseline_test_events(events_path)] == [
        "t::a",
        "t::b",
    ]
    # Every record is counted for the progress heartbeat, `test_finish`
    # included: nothing here may look like a stall.
    assert liveness._read_events_progress(events_path, 0)[0] == len(records)


def test_a_rerun_of_one_nodeid_is_not_an_overlap_in_the_reader(
    tmp_path: Path,
) -> None:
    """A nodeid the suite runs twice reports two `test_start`s, and neither
    follows an unfinished one: that is a rerun, not two tests in flight."""
    events_path = tmp_path / "reader-rerun.ndjson"
    records: list[dict] = [{"event": "session_start"}]
    for _t, record in _real_runtest_records("t::again", t=1.0):
        records.append(record)
    records.append({"event": "test_start", "nodeid": "t::again"})
    _write_stamped_records(events_path, records, pid=100)
    reader = liveness._EventProgressReader(100)
    reader.read(events_path)
    assert reader.overlapping_tests is False
    assert reader.in_flight is not None, "the rerun's window is open"


def test_the_plugin_writes_a_test_finish_event_in_the_real_hook() -> None:
    """The plugin source is the writer of this event, and pytest calls
    `pytest_runtest_logfinish` once per item after its whole runtest protocol
    (teardown included) -- the identity the reader closes a window on."""
    source = liveness._PLUGIN_SOURCE
    assert liveness.TEST_FINISH_EVENT == "test_finish"
    assert "def pytest_runtest_logfinish(nodeid, location):" in source
    assert '{"event": "test_finish", "nodeid": nodeid}' in source
    assert liveness.TEST_FINISH_EVENT not in liveness.COMPLETED_EVENT_KINDS
    block = source.split("def pytest_runtest_logfinish(nodeid, location):", 1)[1]
    block = block.split("def ", 1)[0]
    assert "except Exception" in block


def test_a_real_two_test_pytest_run_feeds_the_reader_a_finish_per_test(
    tmp_path: Path,
) -> None:
    """The end-to-end proof a fake cannot give: a REAL pytest process runs two
    tests with the MATERIALIZED plugin, writes its own events file, and the
    reader must see one open-then-closed window per test (`in_flight` back to
    ``None``, no overlap) whatever the plugin's own timing is."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_two.py").write_text(
        "def test_one():\n    assert True\n\n"
        "def test_two():\n    assert True\n",
        encoding="utf-8",
    )
    liveness_dir = tmp_path / "liveness"
    assert liveness.materialize_liveness_plugin(liveness_dir).is_file()
    events_path = tmp_path / "events.ndjson"
    child_env = dict(os.environ)
    child_env[liveness.ASSAY_LIVENESS_EVENTS_ENV] = str(events_path)
    child_env.pop(liveness.ASSAY_LIVENESS_EXIT_ENV, None)
    child_env["PYTHONPATH"] = os.pathsep.join(
        [str(liveness_dir), child_env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            liveness.LIVENESS_PLUGIN_MODULE_NAME,
            str(project / "test_two.py"),
        ],
        cwd=project,
        env=child_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=600.0,
    )
    assert result.returncode == 0, result.stderr
    records = [
        json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    kinds = [record["event"] for record in records]
    assert kinds.count("test_start") == 2
    assert kinds.count("test_finish") == 2
    # The real per-test order, anchored on the two nodeids pytest reported.
    nodeids = [record["nodeid"] for record in records if record.get("event") == "test"]
    assert len(nodeids) == 2
    for nodeid in nodeids:
        own = [
            (index, record)
            for index, record in enumerate(records)
            if record.get("nodeid") == nodeid
            and record.get("event") in {"test_start", "phase", "test", "test_finish"}
        ]
        kinds = [record["event"] for _index, record in own]
        # logstart, then setup (and teardown) phases, then call, then logfinish.
        assert kinds[0] == "test_start"
        assert kinds[-1] == "test_finish"
        call_at = kinds.index("test")
        assert any(
            record.get("when") == "setup" for _index, record in own[:call_at]
        )
        assert any(
            record.get("when") == "teardown" for _index, record in own[call_at:]
        )
    pid = records[0]["pid"]
    reader = liveness._EventProgressReader(pid)
    assert reader.read(events_path) == (len(records), True)
    assert reader.in_flight is None, (
        "the reader must close both windows on the two real test_finish records"
    )
    assert reader.overlapping_tests is False
    assert reader.completed_events == 6
    assert liveness.count_test_events(events_path) == 2
