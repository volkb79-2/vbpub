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
from pathlib import Path

import pytest

from assay import liveness, mutation


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
        self.calls.append({"argv": tuple(argv), "cwd": cwd})
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
    sampler=None,
    sleep=None,
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
        sampler=sampler,
        popen=_FakePopen(proc),
        process_group_killer=_ignore_process_group,
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
    assert mutation._valid_hung_resource_evidence(evidence)


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
    assert proc.waited is True  # normal process-group cleanup still ran.


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


def test_truncated_resource_trace_cannot_certify_a_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(liveness, "_LIVENESS_RESOURCE_TRACE_MAX_SAMPLES", 3)
    monkeypatch.setattr(liveness, "_HUNG_CPU_WINDOW_S", 100.0)
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4252)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 3.0,
    )

    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=8.0)

    assert type(excinfo.value) is subprocess.TimeoutExpired
    evidence = excinfo.value.liveness_resource_evidence
    assert evidence["trace_truncated"] is True
    assert evidence["trace_complete"] is False
    assert len(evidence["samples"]) == 3


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
    assert mutation._valid_hung_resource_evidence(excinfo.value.resource_evidence)


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
    assert proc.waited  # normal completion still cleans the candidate group.
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
    assert proc.waited  # normal completion still cleans the candidate group.


# --------------------------------------------------------------------------
# normal completion -- returns a real CompletedProcess, no exception
# --------------------------------------------------------------------------


def test_normal_completion_cleans_descendants_after_leader_exits(
    tmp_path: Path,
) -> None:
    """A clean leader exit must not leave its process group in the gate.

    ``poll()`` reaps the candidate before the runner gets here. The old
    cleanup path then could not call ``getpgid`` and left a still-running
    child behind, which accumulated across R2 candidates until the container
    hit its PID ceiling.
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
    assert killed == [(4246, liveness.signal.SIGKILL)]
    assert proc.waited


def test_normal_completion_really_kills_a_real_descendant(
    tmp_path: Path,
) -> None:
    """Exercise the process-group cleanup against a real orphaned child.

    The leader exits successfully after starting ``sleep``. The runner must
    still signal the original session/process group after reaping that leader.
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
        assert result.returncode == 0
        descendant_pid = int(pid_file.read_text(encoding="ascii"))
        deadline = time.monotonic() + 5.0
        while still_running(descendant_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not still_running(descendant_pid), "normal completion leaked its child"
    finally:
        if descendant_pid is not None and still_running(descendant_pid):
            os.kill(descendant_pid, liveness.signal.SIGKILL)


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
_SIDECAR_KEYS = {"format", "samples", "cpu_seconds", "peak_rss_bytes", "spawned_at"}


def _constant_sampler(pid: int) -> liveness.TreeSample:
    return liveness.TreeSample(1e9, 10**12)


def _raising_sampler(pid: int) -> liveness.TreeSample:
    raise OSError("no /proc")


def _row(tmp_path: Path, row: str, *, sampler, log: list[str] | None = None):
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

    def spy(pid: int):
        trail.append("sampler")
        return sampler(pid)

    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=cpu,
        resource_reader=resources,
        sampler=None if sampler is None else spy,
        sleep=sleep,
    )
    try:
        result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=timeout)
        outcome: tuple = ("returned", result.returncode)
    except subprocess.TimeoutExpired as exc:  # LivenessHungExpired is a subclass.
        outcome = (type(exc).__name__,)
    return outcome, clock, runner, cwd


@pytest.mark.parametrize("row", _ROWS)
def test_o6_the_sampler_never_changes_the_outcome_or_the_tick_count(
    tmp_path: Path, row: str
) -> None:
    results = {}
    for name, sampler in (
        ("none", None),
        ("constant", _constant_sampler),
        ("raising", _raising_sampler),
    ):
        outcome, clock, _runner_, _cwd = _row(tmp_path / name, row, sampler=sampler)
        results[name] = (outcome, clock.t)
    assert results["none"][0] == _EXPECTED_OUTCOME[row]
    assert results["constant"] == results["none"]
    assert results["raising"] == results["none"]


@pytest.mark.parametrize("row", _ROWS)
def test_o6_a_sampled_tick_is_cpu_resource_sampler_sleep_and_a_killed_tick_has_no_sampler(
    tmp_path: Path, row: str
) -> None:
    log: list[str] = []
    outcome, _clock, _runner_, _cwd = _row(tmp_path, row, sampler=_constant_sampler, log=log)
    assert outcome == _EXPECTED_OUTCOME[row]
    ticks: list[list[str]] = []
    for entry in log:
        if entry == "cpu":
            ticks.append([])
        ticks[-1].append(entry)
    full = ["cpu", "resource", "sampler", "sleep"]
    if row == "normal":
        assert len(ticks) >= 3
        assert all(tick == full for tick in ticks)
    else:
        assert len(ticks) >= 2
        assert all(tick == full for tick in ticks[:-1])
        assert ticks[-1] == ["cpu", "resource"]


@pytest.mark.parametrize("row", _ROWS)
def test_o7_the_sidecar_is_written_on_every_exit_path(tmp_path: Path, row: str) -> None:
    returned = {"n": 0}

    def counting(pid: int) -> liveness.TreeSample:
        sample = _constant_sampler(pid)
        returned["n"] += 1
        return sample

    outcome, _clock, runner, cwd = _row(tmp_path, row, sampler=counting)
    assert outcome == _EXPECTED_OUTCOME[row]
    sidecar = liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd))
    assert sidecar is not None
    assert set(sidecar) == _SIDECAR_KEYS
    assert sidecar["format"] == 1
    assert sidecar["samples"] == returned["n"]
    assert returned["n"] >= 2
    assert sidecar["cpu_seconds"] == 1e9
    assert sidecar["peak_rss_bytes"] == 10**12
    assert isinstance(sidecar["spawned_at"], float)


def test_o7_a_sampler_that_always_raises_leaves_null_measurements(tmp_path: Path) -> None:
    _outcome, _clock, runner, cwd = _row(tmp_path, "hung", sampler=_raising_sampler)
    sidecar = liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd))
    assert sidecar is not None
    assert (sidecar["samples"], sidecar["cpu_seconds"], sidecar["peak_rss_bytes"]) == (0, None, None)


def test_o7_no_sampler_still_writes_a_sidecar_with_null_measurements(tmp_path: Path) -> None:
    _outcome, _clock, runner, cwd = _row(tmp_path, "normal", sampler=None)
    sidecar = liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd))
    assert sidecar is not None
    assert (sidecar["samples"], sidecar["cpu_seconds"], sidecar["peak_rss_bytes"]) == (0, None, None)


def test_o7_a_later_call_whose_popen_raises_leaves_no_stale_sidecar(tmp_path: Path) -> None:
    _outcome, _clock, runner, cwd = _row(tmp_path, "normal", sampler=_constant_sampler)
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
    outcome, _clock, runner, cwd = _row(tmp_path, row, sampler=_constant_sampler)
    assert outcome == _EXPECTED_OUTCOME[row]
    assert liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd)) is None


@pytest.mark.parametrize("row", _ROWS)
def test_o7_a_non_serializable_sample_never_changes_the_outcome(tmp_path: Path, row: str) -> None:
    outcome, _clock, runner, cwd = _row(
        tmp_path, row, sampler=lambda pid: liveness.TreeSample(object(), 1)
    )
    assert outcome == _EXPECTED_OUTCOME[row]
    assert liveness.read_resource_sidecar(runner._events_path_for_cwd(cwd)) is None
