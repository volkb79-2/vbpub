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
) -> tuple[liveness.LivenessRunner, Path]:
    events_dir = tmp_path / "candidates"
    runner = liveness.LivenessRunner(
        events_dir=events_dir,
        expect_next_event_within_s=expect_next_event_within_s,
        monotonic=clock.now,
        sleep=clock.advance,
        cpu_reader=cpu_reader,
        popen=_FakePopen(proc),
    )
    cwd = tmp_path / "cand"
    cwd.mkdir()
    return runner, cwd


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
# session_finish seen, process still alive later -> hung (no CPU condition)
# --------------------------------------------------------------------------


def test_session_finish_then_still_alive_is_hung_regardless_of_cpu(
    tmp_path: Path,
) -> None:
    """Once `session_finish` is observed, RW-33's second `hung` clause fires
    30s later NO MATTER what the CPU tree is doing -- unlike the idle
    clause, this branch never consults `cpu_growing` at all (a process still
    alive well after deciding its own exit status is hung whether or not it
    is burning CPU, e.g. spinning inside a thread-join deadlock).
    """
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4245)
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: clock.t,  # growing every tick -- irrelevant here.
        expect_next_event_within_s=15.0,
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

    runner._sleep = sleep_and_maybe_write  # type: ignore[assignment]
    with pytest.raises(liveness.LivenessHungExpired):
        runner(("pytest", "-q"), env={}, cwd=cwd, timeout=600.0)
    assert clock.t >= 30.0


# --------------------------------------------------------------------------
# normal completion -- returns a real CompletedProcess, no exception
# --------------------------------------------------------------------------


def test_normal_completion_returns_completed_process(tmp_path: Path) -> None:
    clock = _FakeClock()
    proc = _ScriptedProc(pid=4246, returncode=0)  # already "exited".
    runner, cwd = _runner(
        tmp_path,
        proc=proc,
        clock=clock,
        cpu_reader=lambda pid: 1.0,
    )
    result = runner(("pytest", "-q"), env={}, cwd=cwd, timeout=60.0)
    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0
    assert not proc.waited  # `poll()` alone is enough; no kill, no reap needed.


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
