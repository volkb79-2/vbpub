"""O1 -- a passing, unmodified baseline is MANDATORY before any mutant is
generated or submitted; baseline failure, crash, or timeout stops there and
renders the corresponding non-PASS reason, with NO ``Mutation`` payload at
all (A-116's own baseline-conditional presence rule).

The negative this defends (O1, verbatim): *deleting the baseline guard
submits mutant work and can award credit when the original suite is
already red.* Proven here not merely by asserting ``mutation is None``
(which a lucky implementation could satisfy by accident) but by proving
the injected process runner was invoked EXACTLY ONCE -- for the baseline
only -- and that no scratch directory was ever created, so "no mutant work
submitted" is a genuine, checked fact rather than an assumption.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, fixed_clock, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome, ReasonCode
from assay.mutation import MutationTarget, build_mutation_claim, run_mutation
from assay.runner import execute_command

MOMENT_A = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 8, 7, 12, 0, 1, tzinfo=timezone.utc)

#: A real, valid target -- if the baseline guard were deleted, this would
#: generate a genuine, submittable mutant (a compare-swap on `a > b`).
_TARGETS = (
    MutationTarget(
        path="pkg/mod.py",
        text="def f(a, b):\n    if a > b:\n        return True\n    return False\n",
        lines=frozenset({2}),
    ),
)

_OPERATORS = ("python:compare-swap",)


def _baseline(lane, project_root, process_runner, clock=None):
    """P18 work item 4: the baseline is now obtained BY THE CALLER (the
    exact R0 :func:`~assay.runner.execute_command` result
    :func:`~assay.runner.run_lane` already produces in the real pipeline)
    and handed to :func:`~assay.mutation.run_mutation`, never re-run by it
    -- this helper is every test in this module's own stand-in for that
    already-obtained :class:`~assay.runner.CommandResult`. *clock* omitted
    (``None``) uses ``execute_command``'s own real default.
    """
    kwargs = {} if clock is None else {"clock": clock}
    return execute_command(
        lane, cwd=project_root, process_runner=process_runner, **kwargs
    )


class _RecordingProcessRunner:
    """Records every call it receives (argv, cwd) and delegates the actual
    outcome to *decide* -- lets a test assert BOTH the mapped outcome AND
    "how many times, against which cwd, was the process boundary crossed
    at all" (O1's own "stops before any mutant" claim needs the second
    half, not only the first)."""

    def __init__(self, decide) -> None:
        self.calls: list[Path] = []
        self._decide = decide

    def __call__(self, argv, *, env, cwd, timeout):
        self.calls.append(Path(cwd))
        return self._decide(argv, env=env, cwd=cwd, timeout=timeout)


def _always_pass(argv, *, env, cwd, timeout):
    return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")


def _always_fail(argv, *, env, cwd, timeout):
    return subprocess.CompletedProcess(list(argv), returncode=1, stdout="", stderr="")


def _always_crash(argv, *, env, cwd, timeout):
    raise FileNotFoundError(2, "No such file or directory", argv[0])


def _always_timeout(argv, *, env, cwd, timeout):
    raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)


# --- a red baseline stops before any mutant --------------------------------


#: `run_mutation`'s own baseline-conditional presence rule (A-116) short-
#: circuits BEFORE `prepared`/`plan`/`deadline` are ever touched -- a
#: non-PASS baseline returns `None` before any P22/process boundary, so
#: every test in this section passes `None` for all three and never builds
#: a real snapshot: the point under test is that nothing beyond the
#: baseline call happens at all, which a real prepared seed could not make
#: more true.


def test_a_failing_baseline_stops_before_any_mutant_and_renders_fail(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    recorder = _RecordingProcessRunner(_always_fail)
    baseline = _baseline(
        lane, tmp_path / "proj", recorder, fixed_clock(MOMENT_A, MOMENT_B)
    )

    mutation = run_mutation(
        baseline=baseline,
        prepared=None,
        plan=None,
        deadline=None,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        max_mutants=50,
        operators=_OPERATORS,
        process_runner=recorder,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert baseline.outcome is Outcome.FAIL
    assert baseline.reason_code is ReasonCode.COMMAND_FAILED
    assert mutation is None
    assert len(recorder.calls) == 1, "the process boundary must be crossed only ONCE"


def test_a_crashed_baseline_stops_before_any_mutant_and_renders_error(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    recorder = _RecordingProcessRunner(_always_crash)
    baseline = _baseline(
        lane, tmp_path / "proj", recorder, fixed_clock(MOMENT_A, MOMENT_B)
    )

    mutation = run_mutation(
        baseline=baseline,
        prepared=None,
        plan=None,
        deadline=None,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        max_mutants=50,
        operators=_OPERATORS,
        process_runner=recorder,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert baseline.outcome is Outcome.ERROR
    assert baseline.reason_code is ReasonCode.EXEC_FAILED
    assert mutation is None
    assert len(recorder.calls) == 1


def test_a_timed_out_baseline_stops_before_any_mutant_and_renders_budget_exceeded(
    tmp_path: Path,
):
    lane = make_lane(argv=("pytest", "-q"), budget="5m", budget_seconds=300.0)
    recorder = _RecordingProcessRunner(_always_timeout)
    baseline = _baseline(
        lane, tmp_path / "proj", recorder, fixed_clock(MOMENT_A, MOMENT_B)
    )

    mutation = run_mutation(
        baseline=baseline,
        prepared=None,
        plan=None,
        deadline=None,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        max_mutants=50,
        operators=_OPERATORS,
        process_runner=recorder,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert baseline.outcome is Outcome.BUDGET_EXCEEDED
    assert baseline.reason_code is ReasonCode.LANE_TIMEOUT
    assert mutation is None
    assert len(recorder.calls) == 1


def test_no_scratch_directory_is_created_for_a_red_baseline(tmp_path: Path):
    """The literal shape of "no mutant work submitted": nothing under
    *scratch_root* at all, not merely an empty ``Mutation``."""
    lane = make_lane(argv=("pytest", "-q"))
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    baseline = _baseline(
        lane, tmp_path / "proj", _always_fail, fixed_clock(MOMENT_A, MOMENT_B)
    )

    run_mutation(
        baseline=baseline,
        prepared=None,
        plan=None,
        deadline=None,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=2,
        max_mutants=50,
        operators=_OPERATORS,
        process_runner=_always_fail,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert list(scratch_root.iterdir()) == []


# --- a green baseline proceeds to mutants -----------------------------------


def test_a_passing_baseline_proceeds_to_generating_and_running_mutants(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    recorder = _RecordingProcessRunner(_always_pass)
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/mod.py", _TARGETS[0].text)
    repo.commit_all("add pkg")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    # No `clock=` override here: this test runs the baseline PLUS a real
    # mutant, so it needs more than the two fixed moments `fixed_clock`
    # supplies elsewhere in this module -- and nothing here asserts on
    # timestamps, so the real clock is exactly as good an oracle.
    baseline = _baseline(lane, repo.path, recorder, None)
    plan = make_plan(lane)
    deadline = make_deadline()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=50,
            operators=_OPERATORS,
            process_runner=recorder,
            clock=lambda: datetime.now(timezone.utc),
        )

    assert baseline.outcome is Outcome.PASS
    assert mutation is not None
    assert mutation.total == 1
    # the baseline call PLUS one call per generated mutant.
    assert len(recorder.calls) == 1 + mutation.total


# --- build_mutation_claim reuses the baseline's own (outcome, reason_code) --


@pytest.mark.parametrize(
    "decide, expected_outcome, expected_reason",
    [
        (_always_fail, Outcome.FAIL, ReasonCode.COMMAND_FAILED),
        (_always_crash, Outcome.ERROR, ReasonCode.EXEC_FAILED),
        (_always_timeout, Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT),
    ],
)
def test_the_r2_claim_reuses_the_baselines_own_outcome_and_reason_code_verbatim(
    tmp_path: Path, decide, expected_outcome, expected_reason
):
    lane = make_lane(argv=("pytest", "-q"))
    baseline = _baseline(
        lane, tmp_path / "proj", decide, fixed_clock(MOMENT_A, MOMENT_B)
    )
    mutation = run_mutation(
        baseline=baseline,
        prepared=None,
        plan=None,
        deadline=None,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=1,
        max_mutants=50,
        operators=_OPERATORS,
        process_runner=decide,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )
    assert mutation is None

    claim = build_mutation_claim(baseline, mutation)
    assert claim.rigor == "R2"
    assert claim.status is expected_outcome
    assert claim.reason_code is expected_reason
    assert claim.mutation is None
