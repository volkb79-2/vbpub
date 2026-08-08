"""O2/A-082/A-122 -- the injected executor factory receives ``max_workers``
EQUAL TO the caller's own declared ``jobs``, never a value derived from the
mutant count or the machine (``os.cpu_count()``), and every mutant is
submitted through the executor it returns; ``jobs=1`` and ``jobs=3``
produce IDENTICAL ordered result records.

The negative this defends (O2, verbatim): *constructing the executor with
mutant count or bypassing it for one task fails the recorded
bound/submission assertions without any wall-clock measurement.* Every
assertion below is at the construction/submission BOUNDARY -- no timing,
no sleeps, no elapsed-time comparison anywhere in this module
(AUTHORING.md §3b.A).
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import make_lane

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import MutationTarget, run_mutation
from assay.runner import execute_command

#: Five independent mutable sites (one per line -- each an unrelated
#: `Constant(bool)`), deliberately MORE than any `jobs` value used below,
#: so "receives max_workers=jobs" and "receives max_workers=mutant-count"
#: are two DIFFERENT, distinguishable numbers.
_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    c = True\n"
    "    d = True\n"
    "    e = True\n"
    "    return a, b, c, d, e\n"
)
_TARGETS = (
    MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3, 4, 5, 6})),
)


def _always_pass(argv, *, env, cwd, timeout):
    return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")


class _RecordingExecutor:
    """Wraps a REAL ``ThreadPoolExecutor`` and records every ``submit``
    call -- proves "every mutant is submitted through the executor"
    directly, rather than inferring it from the final result shape."""

    def __init__(self, jobs: int) -> None:
        self.jobs = jobs
        self.submitted = 0
        self._real = ThreadPoolExecutor(max_workers=jobs)

    def __enter__(self) -> "_RecordingExecutor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._real.__exit__(exc_type, exc_val, exc_tb)

    def submit(self, fn, *args):
        self.submitted += 1
        return self._real.submit(fn, *args)


def _materialize_project(root: Path) -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")


def _run_with_recording_factory(tmp_path: Path, jobs: int):
    lane = make_lane(argv=("pytest", "-q"))
    seen: list[_RecordingExecutor] = []
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    _materialize_project(project_root)
    scratch_root.mkdir()

    def factory(requested_jobs: int) -> _RecordingExecutor:
        executor = _RecordingExecutor(requested_jobs)
        seen.append(executor)
        return executor

    baseline = execute_command(lane, cwd=project_root, process_runner=_always_pass)
    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=_TARGETS,
        adapter=PythonAdapter(),
        jobs=jobs,
        operators=("bool-const-flip",),
        process_runner=_always_pass,
        executor_factory=factory,
    )
    assert baseline.outcome is Outcome.PASS
    assert mutation is not None
    return mutation, seen


# --- the factory receives EXACTLY jobs, never the mutant count -------------


def test_the_executor_factory_receives_exactly_jobs_not_mutant_count(tmp_path: Path):
    mutation, seen = _run_with_recording_factory(tmp_path, jobs=2)

    assert mutation.total == 5, "the fixture must generate MORE mutants than jobs"
    assert len(seen) == 1, "the executor is constructed exactly once per run"
    assert seen[0].jobs == 2
    assert seen[0].jobs != mutation.total


def test_a_different_jobs_value_is_reflected_exactly(tmp_path: Path):
    mutation, seen = _run_with_recording_factory(tmp_path, jobs=4)

    assert seen[0].jobs == 4
    assert seen[0].jobs != mutation.total


# --- every mutant is submitted through the returned executor ---------------


def test_every_mutant_is_submitted_through_the_returned_executor(tmp_path: Path):
    mutation, seen = _run_with_recording_factory(tmp_path, jobs=2)

    assert seen[0].submitted == mutation.total == 5


# --- no mutants, no executor construction at all ----------------------------


def test_the_executor_is_never_constructed_when_there_are_no_mutants(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    seen: list[int] = []
    project_root = tmp_path / "proj"
    scratch_root = tmp_path / "scratch"
    _materialize_project(project_root)
    scratch_root.mkdir()

    def factory(jobs: int):
        seen.append(jobs)
        raise AssertionError("the executor must never be constructed for zero mutants")

    baseline = execute_command(lane, cwd=project_root, process_runner=_always_pass)
    mutation = run_mutation(
        lane,
        baseline=baseline,
        project_root=project_root,
        repo_top=project_root,
        scratch_root=scratch_root,
        targets=(),  # nothing to mutate
        adapter=PythonAdapter(),
        jobs=3,
        operators=("bool-const-flip",),
        process_runner=_always_pass,
        executor_factory=factory,
    )

    assert baseline.outcome is Outcome.PASS
    assert mutation is not None
    assert mutation.total == 0
    assert seen == []


# --- jobs=1 and jobs=3 render IDENTICAL ordered records ---------------------


def test_jobs_1_and_jobs_3_produce_identical_ordered_records(tmp_path: Path):
    """The real default executor (a genuine ``ThreadPoolExecutor``) both
    times -- proving the RESULT is independent of the actual concurrency
    bound, never a claim about wall-clock speed."""
    lane = make_lane(argv=("pytest", "-q"))

    def run(jobs: int):
        project_root = tmp_path / f"proj-{jobs}"
        scratch_root = tmp_path / f"scratch-{jobs}"
        _materialize_project(project_root)
        scratch_root.mkdir()
        baseline = execute_command(lane, cwd=project_root, process_runner=_always_pass)
        mutation = run_mutation(
            lane,
            baseline=baseline,
            project_root=project_root,
        repo_top=project_root,
            scratch_root=scratch_root,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=jobs,
            operators=("bool-const-flip",),
            process_runner=_always_pass,
        )
        assert baseline.outcome is Outcome.PASS
        return mutation

    serial = run(1)
    parallel = run(3)

    assert serial.to_dict() == parallel.to_dict()


# --- jobs is validated BEFORE the executor boundary (P18 work item 5) ------


def test_jobs_zero_is_rejected_before_the_executor_boundary(tmp_path: Path):
    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=tmp_path, process_runner=_always_pass)

    def factory(jobs: int):
        raise AssertionError("the executor must never be constructed for jobs=0")

    with pytest.raises(ValueError, match="jobs must be >= 1"):
        run_mutation(
            lane,
            baseline=baseline,
            project_root=tmp_path,
            repo_top=tmp_path,
            scratch_root=tmp_path,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=0,
            operators=("bool-const-flip",),
            executor_factory=factory,
        )


@pytest.mark.parametrize("bad_jobs", [True, False, "2", 1.5, None])
def test_a_non_integer_jobs_is_rejected(tmp_path: Path, bad_jobs):
    """``True``/``False`` are rejected too: ``bool`` is a subclass of
    ``int`` in Python, so a naive ``isinstance(jobs, int)`` check alone
    would silently accept ``jobs = true`` as ``1`` worker."""
    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=tmp_path, process_runner=_always_pass)

    with pytest.raises(ValueError, match="jobs must be an integer"):
        run_mutation(
            lane,
            baseline=baseline,
            project_root=tmp_path,
            repo_top=tmp_path,
            scratch_root=tmp_path,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=bad_jobs,
            operators=("bool-const-flip",),
        )


def test_jobs_validated_even_when_the_baseline_never_passed(tmp_path: Path):
    """Validation happens BEFORE the baseline check too -- a caller
    passing a bad ``jobs`` gets the same mechanical failure regardless of
    whether the baseline it also supplied would have short-circuited
    first."""
    from assay.errors import ReasonCode
    from assay.runner import CommandPlan, CommandResult

    lane = make_lane(argv=("pytest", "-q"))
    baseline = CommandResult(
        plan=CommandPlan(
            argv_declared=("pytest", "-q"),
            argv_appended=(),
            argv_effective=("pytest", "-q"),
            env_declared={},
            env_effective={},
        ),
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        returncode=1,
        started="2026-08-08T00:00:00+00:00",
        ended="2026-08-08T00:00:01+00:00",
    )

    with pytest.raises(ValueError, match="jobs must be >= 1"):
        run_mutation(
            lane,
            baseline=baseline,
            project_root=tmp_path,
            repo_top=tmp_path,
            scratch_root=tmp_path,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=-1,
            operators=("bool-const-flip",),
        )
