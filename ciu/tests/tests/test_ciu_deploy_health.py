"""
Tests for ``ciu.deploy_pkg.health.wait_for_gate_per_target`` (CIU-QOL-8 / O2).

The bug this primitive fixes: today, ``wait_for_gate`` (unchanged, still
tested in ``test_ciu_deploy_pkg.py``) gives every target ONE shared
deadline. If one target legitimately needs 240s and another is broken and
should fail fast at 5s, a shared deadline forces a choice between masking
the broken target's failure for 240s, or spuriously failing the slow-but-
healthy target at 5s. ``wait_for_gate_per_target`` gives each target its
OWN deadline within a single poll loop.

All fixtures use an injected fake clock/sleep_fn (never real time.sleep or
time.monotonic) per AUTHORING.md 3b.A: the fake clock is a mutable "sim
time" that only advances when ``sleep_fn`` is called — this both keeps the
tests instantaneous in wall-clock terms and makes every timing claim below
verifiable by construction, not by inference from real elapsed time.

NOTE: as of this package, ``deploy.py``'s ``run_container_health_gate``/
``resolve_selection_health_containers`` do not yet call this function (see
this package's LOG — the deploy.py wiring, O3, is BLOCKED because it would
change ``resolve_selection_health_containers``'s return type and
``run_container_health_gate``'s signature in ways that break several
existing tests outside this package's ``scope.touch``). This file proves
the new primitive itself is correct, in isolation, per the handoff's Work
item 2 and its REQUIRED combined-axis fixture (review_focus item 1).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.deploy_pkg.health import wait_for_gate, wait_for_gate_per_target  # noqa: E402


def _fake_clock_and_sleep():
    """A deterministic fake clock that only advances via sleep_fn.

    Multiple clock() calls with no intervening sleep_fn() call return the
    SAME instant (mirrors a real monotonic clock read multiple times within
    one tick) — this is what makes the deadline arithmetic in
    ``wait_for_gate_per_target`` (which reads clock() once per target in a
    dict comprehension) deterministic in a test.
    """
    sim_time = [0.0]
    slept: list[float] = []

    def clock() -> float:
        return sim_time[0]

    def sleep_fn(interval_s: float) -> None:
        slept.append(interval_s)
        sim_time[0] += interval_s

    return clock, sleep_fn, slept


class TestWaitForGatePerTarget:
    def test_no_overrides_matches_wait_for_gate_for_a_uniform_selection(self):
        """Regression bar: every target sharing one timeout must behave
        identically to the original ``wait_for_gate`` primitive."""
        calls: list[int] = []

        def check() -> dict[str, str]:
            calls.append(1)
            if len(calls) < 2:
                return {"a": "starting", "b": "starting"}
            return {"a": "healthy", "b": "healthy"}

        clock, sleep_fn, slept = _fake_clock_and_sleep()
        passed, summary = wait_for_gate_per_target(
            check,
            {"a": 10.0, "b": 10.0},
            interval_s=1.0,
            sleep_fn=sleep_fn,
            clock=clock,
        )
        assert passed is True
        assert summary["healthy"] == ["a", "b"]
        assert len(calls) == 2
        assert slept == [1.0]

    def test_empty_target_timeouts_passes_without_polling(self):
        calls: list[int] = []

        def check() -> dict[str, str]:
            calls.append(1)
            return {}

        clock, sleep_fn, slept = _fake_clock_and_sleep()
        passed, summary = wait_for_gate_per_target(
            check, {}, sleep_fn=sleep_fn, clock=clock
        )
        assert passed is True
        assert calls == []
        assert slept == []

    def test_single_target_resolves_early_when_healthy_before_its_deadline(self):
        clock, sleep_fn, slept = _fake_clock_and_sleep()
        calls: list[int] = []

        def check() -> dict[str, str]:
            calls.append(1)
            # Healthy on the very first poll (sim_time == 0), long before
            # its own 5s deadline.
            return {"svc": "healthy"}

        passed, summary = wait_for_gate_per_target(
            check, {"svc": 5.0}, interval_s=1.0, sleep_fn=sleep_fn, clock=clock
        )
        assert passed is True
        assert summary["healthy"] == ["svc"]
        assert len(calls) == 1  # resolved on the first tick, never polled again
        assert slept == []  # never had to sleep at all

    def test_single_target_never_healthy_locks_in_at_its_own_deadline(self):
        clock, sleep_fn, slept = _fake_clock_and_sleep()

        def check() -> dict[str, str]:
            return {"svc": "starting"}

        passed, summary = wait_for_gate_per_target(
            check, {"svc": 5.0}, interval_s=1.0, sleep_fn=sleep_fn, clock=clock
        )
        assert passed is False
        assert summary["pending"] == ["svc"]
        # 5 ticks of interval_s=1.0 to reach the 5.0s deadline.
        assert slept == [1.0, 1.0, 1.0, 1.0, 1.0]

    def test_false_pass_attack_fast_broken_does_not_wait_behind_slow_legitimate(self):
        """THE required combined-axis fixture (review_focus item 1).

        One fast, genuinely broken service (``health_timeout`` == 5s, NEVER
        becomes healthy) and one slow, legitimate service (``health_timeout``
        == 240s, becomes healthy at simulated t=200s) in the SAME gate call.

        Must prove, via clock progression (never real wall time):
          - the overall gate still FAILS (the broken service is never
            healthy);
          - the fast-broken target's bucket is resolved/locked at its OWN
            5s deadline — NOT held open until the slow target's 240s
            deadline, and NOT until the slow target actually resolves at
            200s;
          - the slow-legitimate target is polled all the way to its actual
            resolution at 200s, independent of the fast target's earlier
            resolution (i.e. the loop does not shut down early just because
            one target finished).
        """
        clock, sleep_fn, slept = _fake_clock_and_sleep()
        check_calls: list[float] = []

        def check() -> dict[str, str]:
            # Record the simulated instant of every poll (proves the
            # algorithm keeps polling BOTH targets every tick, per the
            # handoff's construction: "do not try to skip already-resolved
            # ones in the check_fn call itself").
            check_calls.append(clock())
            return {
                "fast-broken": "unhealthy",  # never becomes healthy, ever
                "slow-legit": "healthy" if clock() >= 200.0 else "starting",
            }

        passed, summary = wait_for_gate_per_target(
            check,
            {"fast-broken": 5.0, "slow-legit": 240.0},
            interval_s=5.0,
            sleep_fn=sleep_fn,
            clock=clock,
        )

        # Overall gate still fails: the broken service never became healthy.
        assert passed is False
        assert summary["unhealthy"] == ["fast-broken"]
        assert summary["healthy"] == ["slow-legit"]

        # The fast target's bucket was locked in at simulated t=5s (its OWN
        # deadline), not at t=240s (the slow ceiling) and not at t=200s
        # (when the slow target actually resolved). Proven by exact
        # sim-time bookkeeping, not wall-clock inference:
        assert 5.0 in check_calls  # a poll happened exactly at the fast deadline
        assert max(check_calls) == 200.0  # loop ran on to the slow resolution...
        assert 240.0 not in check_calls  # ...and never all the way to 240s

        # The loop needed exactly 200s / 5s-interval == 40 ticks to resolve
        # the slow target — proof this ran on clock progression to 200s,
        # not the fast target's 5s, and not the slow ceiling of 240s.
        assert len(slept) == 40
        assert all(step == 5.0 for step in slept)

        # And this whole scenario, "simulating" 200 seconds of elapsed
        # time, executes in a real test process in well under a second —
        # because clock/sleep_fn are both fakes, never real time.sleep or
        # time.monotonic.

    def test_check_fn_polls_every_target_every_tick_even_after_one_resolves(self):
        """A resolved target is not dropped from the check_fn's own targets;
        only its own status is frozen in the returned ``resolved`` map."""
        clock, sleep_fn, slept = _fake_clock_and_sleep()
        seen_keys: list[frozenset] = []

        def check() -> dict[str, str]:
            seen_keys.append(frozenset(["fast", "slow"]))
            return {
                "fast": "unhealthy",
                "slow": "healthy" if clock() >= 10.0 else "starting",
            }

        wait_for_gate_per_target(
            check,
            {"fast": 2.0, "slow": 10.0},
            interval_s=2.0,
            sleep_fn=sleep_fn,
            clock=clock,
        )
        # Every recorded call still covers both targets — check_fn is
        # always asked to classify ALL targets, every tick.
        assert all(keys == {"fast", "slow"} for keys in seen_keys)
        assert len(seen_keys) == 6  # sim_time 0, 2, 4, 6, 8, 10


class TestWaitForGatePerTargetAgainstWaitForGate:
    def test_uniform_timeouts_produce_the_same_result_as_wait_for_gate(self):
        """A selection where no target declares an override must be
        indistinguishable from today's single-shared-deadline behavior."""

        def make_check():
            calls = []

            def check():
                calls.append(1)
                if len(calls) < 3:
                    return {"a": "starting", "b": "starting"}
                return {"a": "healthy", "b": "healthy"}

            return check

        old_clock = iter([0.0, 1.0, 2.0, 3.0]).__next__
        old_passed, old_summary = wait_for_gate(
            make_check(),
            timeout_s=10.0,
            interval_s=1.0,
            sleep_fn=lambda s: None,
            clock=old_clock,
        )

        new_clock, new_sleep_fn, _ = _fake_clock_and_sleep()
        new_passed, new_summary = wait_for_gate_per_target(
            make_check(),
            {"a": 10.0, "b": 10.0},
            interval_s=1.0,
            sleep_fn=new_sleep_fn,
            clock=new_clock,
        )

        assert old_passed == new_passed is True
        assert old_summary == new_summary
