"""B129 / W10 step 1: characterization of the remaining inline guards in
``runner`` (deadlines, ``execute_plan`` timeout), ``mutation`` (run and
collect limits) and ``liveness`` (baseline event readers).

Pins accept and refuse, with the exact message. Values are chosen so each
operand of each guard alone decides the outcome. Run against the UNCHANGED
source; never edited to follow the refactor.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import make_lane, make_plan

from assay import liveness, mutation, runner
from assay.errors import Outcome

NOT_STRICT_INT = [True, False, 1.5, "1", None, b"1"]
#: Not finite-positive: a bool, a non-number, zero, negative, nan, an infinity.
NOT_FINITE_POSITIVE = [
    True,
    False,
    "1",
    b"1",
    [1],
    0,
    0.0,
    -0.0,
    -1,
    -1.5,
    math.nan,
    math.inf,
    -math.inf,
]
FINITE_POSITIVE = [1, 0.5, 1e-9, 10**6, 1e300]


def refusal(call, message: str, exc=ValueError) -> None:
    with pytest.raises(exc) as caught:
        call()
    assert str(caught.value) == message


# ---- LaneDeadline.start: budget finite-positive or None; clock real-finite -----


def _start(budget, now=1.0):
    return runner.LaneDeadline.start(budget_seconds=budget, monotonic=lambda: now)


@pytest.mark.parametrize("budget", FINITE_POSITIVE)
def test_deadline_start_accepts_a_positive_finite_budget(budget):
    assert _start(budget).expires_at == 1.0 + float(budget)


def test_deadline_start_accepts_none_as_unbounded():
    assert _start(None).expires_at == math.inf


@pytest.mark.parametrize("budget", NOT_FINITE_POSITIVE)
def test_deadline_start_refuses_everything_else(budget):
    refusal(
        lambda: _start(budget),
        f"budget_seconds must be a positive finite number or None, got {budget!r}",
    )


def test_deadline_start_lets_an_int_beyond_float_range_overflow():
    with pytest.raises(OverflowError):
        _start(10**400)


@pytest.mark.parametrize("now", [0, -5, 1.5, 12345678901234567890])
def test_deadline_start_accepts_any_finite_real_clock_reading(now):
    assert _start(1, now=now).expires_at == now + 1.0


@pytest.mark.parametrize(
    "now", [True, False, "1", None, b"1", math.nan, math.inf, -math.inf]
)
def test_deadline_start_refuses_a_non_real_or_non_finite_clock_reading(now):
    refusal(
        lambda: _start(1, now=now),
        f"monotonic clock returned invalid value {now!r}",
    )


def test_deadline_start_lets_a_clock_int_beyond_float_range_overflow():
    with pytest.raises(OverflowError):
        _start(1, now=10**400)


# ---- LaneDeadline.tightened: seconds finite-positive or None -------------------


def _deadline():
    return runner.LaneDeadline(expires_at=100.0, monotonic=lambda: 10.0)


def test_tightened_returns_the_same_deadline_for_none():
    deadline = _deadline()
    assert deadline.tightened(None) is deadline


@pytest.mark.parametrize("seconds", [1, 0.5, 1e-9, 5])
def test_tightened_accepts_a_positive_finite_number(seconds):
    assert _deadline().tightened(seconds).expires_at == 10.0 + float(seconds)


@pytest.mark.parametrize("seconds", NOT_FINITE_POSITIVE)
def test_tightened_refuses_everything_else(seconds):
    refusal(
        lambda: _deadline().tightened(seconds),
        f"tightened seconds must be a positive finite number or None, "
        f"got {seconds!r}",
    )


def test_tightened_lets_an_int_beyond_float_range_overflow():
    with pytest.raises(OverflowError):
        _deadline().tightened(10**400)


# ---- execute_plan: timeout positive finite or math.inf -------------------------


def _execute(tmp_path: Path, timeout):
    def run(argv, *, env, cwd, timeout):
        return subprocess.CompletedProcess(list(argv), returncode=0)

    return runner.execute_plan(
        make_plan(make_lane()), cwd=tmp_path, timeout=timeout, process_runner=run
    )


@pytest.mark.parametrize("timeout", [1, 0.5, 1e-9, 10**6, math.inf])
def test_execute_plan_accepts_a_positive_number_or_infinity(tmp_path, timeout):
    assert _execute(tmp_path, timeout).outcome is Outcome.PASS


@pytest.mark.parametrize(
    "timeout",
    [True, False, "1", None, b"1", [1], 0, 0.0, -0.0, -1, -1.5, math.nan, -math.inf],
)
def test_execute_plan_refuses_everything_else(tmp_path, timeout):
    refusal(
        lambda: _execute(tmp_path, timeout),
        f"timeout must be a positive finite number or math.inf, got {timeout!r}",
    )


def test_execute_plan_lets_an_int_beyond_float_range_overflow(tmp_path):
    with pytest.raises(OverflowError):
        _execute(tmp_path, 10**400)


# ---- mutation.collect_mutation_sites: limit strict int in range ---------------


def _collect(limit):
    return mutation.collect_mutation_sites(
        (), adapter=None, operators=("python:compare-swap",), limit=limit
    )


def test_collect_limit_accepts_one_and_the_ceiling():
    assert _collect(1) == ()
    assert _collect(mutation.MAX_CANDIDATE_CEILING) == ()


@pytest.mark.parametrize("limit", NOT_STRICT_INT)
def test_collect_limit_refuses_a_bool_or_non_int(limit):
    refusal(
        lambda: _collect(limit),
        f"collect_mutation_sites limit must be an integer, got {limit!r}",
    )


@pytest.mark.parametrize("limit", [0, -1, mutation.MAX_CANDIDATE_CEILING + 1])
def test_collect_limit_refuses_a_value_outside_the_range(limit):
    refusal(
        lambda: _collect(limit),
        f"collect_mutation_sites limit must be in 1..{mutation.MAX_CANDIDATE_CEILING}, "
        f"got {limit}",
    )


# ---- mutation.run_mutation: jobs, max_mutants, per-candidate budget ------------


def _run_args(**overrides):
    values = {
        "baseline": SimpleNamespace(
            outcome=Outcome.PASS,
            started="2026-09-26T00:00:00+00:00",
            ended="2026-09-26T00:00:01+00:00",
        ),
        "prepared": None,
        "plan": None,
        "deadline": None,
        "targets": (),
        "adapter": None,
        "jobs": 1,
        "max_mutants": 10,
        "operators": ("python:compare-swap",),
        "process_runner": lambda *_args, **_kwargs: None,
        "clock": lambda: "2026-09-26T00:00:00+00:00",
    }
    values.update(overrides)
    return values


class _UnsupportedAdapter:
    def generate_mutation_sites(self, *_args, **_kwargs):
        return mutation.UNSUPPORTED


def _run(**overrides):
    return lambda: mutation.run_mutation(**_run_args(**overrides))


def _accepted(**overrides):
    """Every validation passed iff the run reaches the adapter's answer."""
    target = mutation.MutationTarget(
        path="src/mod.py", text="value = 1\n", lines=frozenset({1})
    )
    return mutation.run_mutation(
        **_run_args(targets=(target,), adapter=_UnsupportedAdapter(), **overrides)
    )


@pytest.mark.parametrize("jobs", NOT_STRICT_INT)
def test_run_mutation_jobs_refuse_a_bool_or_non_int(jobs):
    refusal(_run(jobs=jobs), f"run_mutation jobs must be an integer, got {jobs!r}")


def test_run_mutation_jobs_accept_one_and_refuse_zero():
    assert _accepted(jobs=1) == mutation.UNSUPPORTED
    refusal(_run(jobs=0), "run_mutation jobs must be >= 1, got 0")


@pytest.mark.parametrize("max_mutants", NOT_STRICT_INT)
def test_run_mutation_max_mutants_refuse_a_bool_or_non_int(max_mutants):
    refusal(
        _run(max_mutants=max_mutants),
        f"run_mutation max_mutants must be an integer, got {max_mutants!r}",
    )


@pytest.mark.parametrize("max_mutants", [0, -1, 10_001])
def test_run_mutation_max_mutants_refuse_a_value_outside_the_range(max_mutants):
    refusal(
        _run(max_mutants=max_mutants),
        f"run_mutation max_mutants must be in 1..10,000, got {max_mutants}",
    )


@pytest.mark.parametrize("max_mutants", [1, 10_000])
def test_run_mutation_max_mutants_accept_the_range_ends(max_mutants):
    assert _accepted(max_mutants=max_mutants) == mutation.UNSUPPORTED


@pytest.mark.parametrize("budget", FINITE_POSITIVE)
def test_run_mutation_budget_accepts_none_or_a_positive_finite_number(budget):
    assert _accepted(budget_per_candidate_seconds=budget) == mutation.UNSUPPORTED
    assert _accepted(budget_per_candidate_seconds=None) == mutation.UNSUPPORTED


@pytest.mark.parametrize("budget", NOT_FINITE_POSITIVE)
def test_run_mutation_budget_refuses_everything_else(budget):
    refusal(
        _run(budget_per_candidate_seconds=budget),
        "run_mutation budget_per_candidate_seconds must be a positive finite "
        f"number or None, got {budget!r}",
    )


def test_run_mutation_budget_lets_an_int_beyond_float_range_overflow():
    with pytest.raises(OverflowError):
        _run(budget_per_candidate_seconds=10**400)()


# ---- liveness: baseline event readers skip a bool or non-number ----------------


def _events(tmp_path: Path, *records: dict) -> Path:
    path = tmp_path / "events.ndjson"
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return path


def _test_event(duration):
    return {
        "event": "test",
        "when": "call",
        "nodeid": "pkg/test_it.py::t",
        "outcome": "passed",
        "duration_s": duration,
        "t": 1.0,
    }


@pytest.mark.parametrize("bad", [True, False, "3", None, [3], {"a": 1}])
def test_slowest_test_skips_a_bool_or_non_number_duration(tmp_path, bad):
    path = _events(tmp_path, _test_event(0.5), _test_event(bad))
    assert liveness.baseline_slowest_test_s(path) == 0.5


@pytest.mark.parametrize("bad", [True, False, "3", None])
def test_slowest_test_is_none_when_only_bad_durations_exist(tmp_path, bad):
    assert liveness.baseline_slowest_test_s(_events(tmp_path, _test_event(bad))) is None


@pytest.mark.parametrize("duration", [0, 3, 0.25, 1e-9])
def test_slowest_test_accepts_an_int_or_float_duration(tmp_path, duration):
    path = _events(tmp_path, _test_event(duration))
    assert liveness.baseline_slowest_test_s(path) == duration


@pytest.mark.parametrize("bad", [True, False, "3", None, [3]])
def test_event_gaps_skip_a_bool_or_non_number_stamp(tmp_path, bad):
    # A counted ``True`` would read as t = 1.0 and shrink the worst gap to 3.0.
    path = _events(
        tmp_path,
        {"event": "session_start", "t": 0.0},
        {"event": "test", "t": bad},
        {"event": "test", "t": 4.0},
    )
    gaps = liveness.baseline_event_gaps(path)
    assert gaps is not None
    assert gaps.worst_gap_s == 4.0


def test_event_gaps_accept_int_and_float_stamps(tmp_path):
    path = _events(
        tmp_path,
        {"event": "session_start", "t": 0},
        {"event": "test", "t": 2.5},
        {"event": "test", "t": 5},
    )
    gaps = liveness.baseline_event_gaps(path)
    assert gaps is not None
    assert gaps.worst_gap_s == 2.5


def test_event_gaps_are_none_when_fewer_than_two_stamps_are_real_numbers(tmp_path):
    path = _events(
        tmp_path,
        {"event": "session_start", "t": 0.0},
        {"event": "test", "t": True},
        {"event": "test", "t": "4"},
    )
    assert liveness.baseline_event_gaps(path) is None
