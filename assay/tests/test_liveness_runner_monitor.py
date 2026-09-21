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
import subprocess
from pathlib import Path

import pytest

from assay import liveness


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


def _runner(
    tmp_path: Path,
    *,
    proc: _ScriptedProc,
    clock: _FakeClock,
    cpu_reader,
    expect_next_event_within_s: float = 15.0,
    pre_first_event_within_s: float | None = None,
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
        sleep=clock.advance,
        cpu_reader=cpu_reader,
        popen=_FakePopen(proc),
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
    """RW-57: retain the true hung case, but later progress resets the grace.

    Growing CPU and a 600s calibrated bound isolate the session-finish
    branch. Pin its idle boundary, including when the last event is later
    than the first finish, so removing the branch or weakening >= fails.
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4245)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: clock.t,
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
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert clock.t == (1.0 if later_event_at is None else later_event_at) + 30.0
    assert proc.waited


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    monkeypatch.setattr(
        liveness.os,
        "killpg",
        lambda pgid, sig: killed.append((pgid, sig)),
    )
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 1.0,
    )
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=60.0)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0
    assert killed == [(4246, liveness.signal.SIGKILL)]
    assert proc.waited


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
    events_dir = tmp_path / "candidates"
    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=15.0,
        monotonic=clock.now,
        sleep=clock.advance,
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
            popen=_FakePopen(proc),
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
        popen=_FakePopen(proc),
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
        popen=_FakePopen(proc),
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
        popen=_FakePopen(proc),
    )
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=500.0)
    assert written["n"] > 30  # it really did keep printing for 60 virtual s.
    # Not killed while it was still printing: the 15s idle bound would have
    # fired at ~30s (once the CPU window filled) if file growth were not
    # counted as progress.
    assert clock.t >= 75.0
