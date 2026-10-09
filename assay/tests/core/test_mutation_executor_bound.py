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
import json
import os
import stat
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay import isolation as isolation_module
from assay import mutation as mutation_module, runner as runner_module
from assay.errors import AssayError, Outcome, ReasonCode
from assay.mutation import MutationTarget, candidate_id, collect_mutation_sites, run_mutation
from assay.resource_limits import ResourceLimitCounters
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
_TEXT3 = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    c = True\n"
    "    return a, b, c\n"
)
_TARGETS3 = (
    MutationTarget(path="pkg/flags.py", text=_TEXT3, lines=frozenset({2, 3, 4})),
)
_TEXT5 = _TEXT
_TARGETS5 = _TARGETS


@pytest.fixture(autouse=True)
def _resource_counters_are_faked_for_executor_unit_tests(monkeypatch):
    """Keep executor mechanics independent of the host's cgroup namespace."""
    counters = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(
        mutation_module, "_read_candidate_resource_counters", lambda: counters
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


def _seed_repo(tmp_path: Path, name: str, text: str = _TEXT) -> GitRepo:
    repo = GitRepo(path=tmp_path / name)
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", text)
    repo.commit_all("add flags")
    return repo


def _run_with_recording_factory(tmp_path: Path, jobs: int):
    lane = make_lane(argv=("pytest", "-q"))
    seen: list[_RecordingExecutor] = []
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    def factory(requested_jobs: int) -> _RecordingExecutor:
        executor = _RecordingExecutor(requested_jobs)
        seen.append(executor)
        return executor

    baseline = execute_command(lane, cwd=repo.path, process_runner=_always_pass)
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
            jobs=jobs,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=_always_pass,
            clock=lambda: datetime.now(timezone.utc),
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
    repo = _seed_repo(tmp_path, "repo")
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()

    def factory(jobs: int):
        seen.append(jobs)
        raise AssertionError("the executor must never be constructed for zero mutants")

    baseline = execute_command(lane, cwd=repo.path, process_runner=_always_pass)
    plan = make_plan(lane)
    deadline = make_deadline()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        mutation = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=(),  # nothing to mutate
            adapter=PythonAdapter(),
            jobs=3,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=_always_pass,
            clock=lambda: datetime.now(timezone.utc),
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
        repo = _seed_repo(tmp_path, f"repo-{jobs}")
        scratch_root = tmp_path / f"scratch-{jobs}"
        scratch_root.mkdir()
        baseline = execute_command(lane, cwd=repo.path, process_runner=_always_pass)
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
                jobs=jobs,
                max_mutants=50,
                operators=("python:bool-const-flip",),
                process_runner=_always_pass,
                clock=lambda: datetime.now(timezone.utc),
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
            baseline=baseline,
            prepared=None,
            plan=None,
            deadline=None,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=0,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=_always_pass,
            clock=lambda: datetime.now(timezone.utc),
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
            baseline=baseline,
            prepared=None,
            plan=None,
            deadline=None,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=bad_jobs,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=_always_pass,
            clock=lambda: datetime.now(timezone.utc),
        )


def test_jobs_validated_even_when_the_baseline_never_passed(tmp_path: Path):
    """Validation happens BEFORE the baseline check too -- a caller
    passing a bad ``jobs`` gets the same mechanical failure regardless of
    whether the baseline it also supplied would have short-circuited
    first."""
    from assay.errors import ReasonCode
    from assay.runner import CommandPlan, CommandResult
    from pathlib import PurePosixPath

    baseline = CommandResult(
        plan=CommandPlan(
            argv_declared=("pytest", "-q"),
            argv_appended=(),
            argv_effective=("pytest", "-q"),
            env_declared={},
            env_effective={},
            env_passthrough=(),
            allow_argv_append=False,
            budget_seconds=60.0,
            project_prefix=PurePosixPath("."),
        ),
        outcome=Outcome.FAIL,
        reason_code=ReasonCode.COMMAND_FAILED,
        returncode=1,
        started="2026-08-08T00:00:00+00:00",
        ended="2026-08-08T00:00:01+00:00",
    )

    with pytest.raises(ValueError, match="jobs must be >= 1"):
        run_mutation(
            baseline=baseline,
            prepared=None,
            plan=None,
            deadline=None,
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=-1,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=_always_pass,
            clock=lambda: datetime.now(timezone.utc),
        )


def _candidate_names(targets):
    jobs = collect_mutation_sites(
        targets,
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=50,
    )
    assert jobs != "UNSUPPORTED"
    return tuple("abcde"[job.site.lineno - 2] for job in jobs)


def _decide5(repo_path: Path, behaviours):
    """Return a content-keyed ProcessRunner for the five independent sites."""
    del repo_path

    def process_runner(argv, *, env, cwd, timeout):
        del env, timeout
        source = (cwd / "pkg/flags.py").read_text(encoding="utf-8")
        changed = [
            name
            for name in "abcde"
            if f"{name} = False" in source
        ]
        if not changed:
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        assert len(changed) == 1, f"expected one mutated site, found {changed}"
        action = behaviours.get(changed[0])
        if action is not None:
            return action(argv, cwd)
        # Default: the suite fails, so the mutant is killed.
        return subprocess.CompletedProcess(list(argv), 1, "", "")

    return process_runner


def _run_queue_case(
    tmp_path: Path,
    monkeypatch,
    *,
    name: str,
    targets,
    text: str,
    jobs: int,
    process_runner,
    repo: GitRepo | None = None,
    state_root: Path | None = None,
    resume: bool = False,
    shard_index: int | None = None,
    shard_count: int | None = None,
    progress_events: list[dict] | None = None,
    executor_factory=None,
    expected_plan_sha256: str | None = None,
    campaign_deadline_sha256: str | None = None,
    budget_per_candidate_seconds: float | None = None,
    equivalence_artifact: str | None = None,
    baseline_equivalence: bytes | None = None,
    oom_counter=None,
    deadline=None,
    state_root_guard=None,
    state_root_fd=None,
    return_repo: bool = False,
):
    # Queue tests prove ordering and submission behavior; resource-limit
    # semantics have their own cgroup-bound integration tests and lane gate.
    counters = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(
        mutation_module, "_read_candidate_resource_counters", lambda: counters
    )
    repo = _seed_repo(tmp_path, name, text) if repo is None else repo
    scratch_root = tmp_path / f"{name}-scratch"
    scratch_root.mkdir()
    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=_always_pass)
    plan = make_plan(lane)
    deadline = make_deadline() if deadline is None else deadline
    progress_stream = None
    if progress_events is not None:
        progress_stream = mutation_module.ProgressStream(
            lambda event: progress_events.append(dict(event)),
            clock=lambda: datetime.now(timezone.utc),
        )
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=plan,
            deadline=deadline,
            targets=targets,
            adapter=PythonAdapter(),
            jobs=jobs,
            max_mutants=50,
            operators=("python:bool-const-flip",),
            process_runner=process_runner,
            clock=lambda: datetime.now(timezone.utc),
            executor_factory=executor_factory or mutation_module._default_executor_factory,
            state_root=state_root,
            state_root_fd=state_root_fd,
            state_root_guard=state_root_guard,
            resume=resume,
            expected_plan_sha256=expected_plan_sha256,
            campaign_deadline_sha256=campaign_deadline_sha256,
            shard_index=shard_index,
            shard_count=shard_count,
            progress_stream=progress_stream,
            budget_per_candidate_seconds=budget_per_candidate_seconds,
            equivalence_artifact=equivalence_artifact,
            baseline_equivalence=baseline_equivalence,
            oom_counter=(lambda: 0) if oom_counter is None else oom_counter,
        )
    assert baseline.outcome is Outcome.PASS
    assert result is not None
    return (result, repo) if return_repo else result


def test_campaign_plan_mismatch_refuses_before_executor_or_candidate_state(
    tmp_path, monkeypatch
):
    state_root = tmp_path / "campaign-plan-state"
    executor_calls: list[int] = []

    def factory(jobs):
        executor_calls.append(jobs)
        raise AssertionError("a mismatched campaign plan must not launch candidates")

    with pytest.raises(mutation_module.CampaignPlanMismatchError, match="full mutation plan"):
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="campaign-plan-mismatch",
            targets=_TARGETS3,
            text=_TEXT3,
            jobs=2,
            process_runner=_decide5(tmp_path, {}),
            state_root=state_root,
            executor_factory=factory,
            expected_plan_sha256="f" * 64,
            campaign_deadline_sha256="a" * 64,
        )

    assert executor_calls == []
    assert list(state_root.glob("*.json")) == []


def test_candidate_state_record_is_bound_to_campaign_deadline_bytes(
    tmp_path, monkeypatch
):
    jobs = collect_mutation_sites(
        _TARGETS3,
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=50,
    )
    assert jobs != "UNSUPPORTED"
    plan_digest = mutation_module.plan_sha256([candidate_id(job) for job in jobs])
    deadline_digest = "a" * 64
    state_root = tmp_path / "campaign-state"

    result = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="campaign-state-binding",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=2,
        process_runner=_decide5(tmp_path, {}),
        state_root=state_root,
        expected_plan_sha256=plan_digest,
        campaign_deadline_sha256=deadline_digest,
    )

    assert result.total == 3
    records = [json.loads(path.read_text(encoding="utf-8")) for path in state_root.glob("*.json")]
    assert len(records) == 3
    assert {record["campaign_deadline_sha256"] for record in records} == {deadline_digest}


def test_resume_reexecutes_state_bound_to_different_campaign_deadline_bytes(
    tmp_path, monkeypatch
):
    repo = _seed_repo(tmp_path, "campaign-resume-binding", _TEXT3)
    state_root = tmp_path / "campaign-resume-state"
    jobs = collect_mutation_sites(
        _TARGETS3,
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=50,
    )
    assert jobs != "UNSUPPORTED"
    plan_digest = mutation_module.plan_sha256([candidate_id(job) for job in jobs])
    first_digest = "a" * 64
    second_digest = "b" * 64
    first, _ = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="campaign-resume-binding-first",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=2,
        process_runner=_decide5(tmp_path, {}),
        repo=repo,
        state_root=state_root,
        expected_plan_sha256=plan_digest,
        campaign_deadline_sha256=first_digest,
        return_repo=True,
    )
    assert first.total == 3

    calls: list[str] = []

    def pass_candidate(argv, cwd):
        calls.append(Path(cwd).name)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    progress_events: list[dict] = []
    resumed = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="campaign-resume-binding-second",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=2,
        process_runner=_decide5(
            tmp_path,
            dict.fromkeys(_candidate_names(_TARGETS3), pass_candidate),
        ),
        repo=repo,
        state_root=state_root,
        resume=True,
        expected_plan_sha256=plan_digest,
        campaign_deadline_sha256=second_digest,
        progress_events=progress_events,
    )

    assert resumed.total == 3
    assert len(calls) == 3
    resume_events = [event for event in progress_events if event.get("event") == "resume"]
    assert len(resume_events) == 1
    assert resume_events[0]["rejected_total"] == 3
    records = [json.loads(path.read_text(encoding="utf-8")) for path in state_root.glob("*.json")]
    assert {record["campaign_deadline_sha256"] for record in records} == {second_digest}


def test_lane_bound_timeout_closes_reservation_and_reexecutes_on_resume(
    tmp_path, monkeypatch
):
    repo = _seed_repo(tmp_path, "campaign-lane-timeout", _TEXT3)
    state_root = tmp_path / "campaign-lane-timeout-state"
    candidate_calls: list[str] = []

    def timeout_candidate(argv, cwd):
        candidate_calls.append(Path(cwd).name)
        raise subprocess.TimeoutExpired(list(argv), timeout=60.0, output=b"partial")

    class Reservation:
        def __init__(self):
            self.closes = 0

        def close(self):
            self.closes += 1

    reservations: list[Reservation] = []

    def arm(*_args, **_kwargs):
        reservation = Reservation()
        reservations.append(reservation)
        return reservation

    monkeypatch.setattr(mutation_module, "_arm_artifact_reservation", arm)
    first, _ = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="campaign-lane-timeout",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=1,
        process_runner=_decide5(tmp_path, {_candidate_names(_TARGETS3)[0]: timeout_candidate}),
        repo=repo,
        state_root=state_root,
        equivalence_artifact="equivalence.json",
        baseline_equivalence=b"baseline-equivalence",
        return_repo=True,
    )

    assert len(candidate_calls) == 1
    assert len(first.budget_exceeded) == 3
    assert reservations[0].closes == 1
    assert list(state_root.glob("*.json")) == []

    resumed_calls: list[str] = []

    def pass_candidate(argv, cwd):
        resumed_calls.append(Path(cwd).name)
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    resumed = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="campaign-lane-timeout-resume",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=1,
        process_runner=_decide5(
            tmp_path,
            dict.fromkeys(_candidate_names(_TARGETS3), pass_candidate),
        ),
        repo=repo,
        state_root=state_root,
        resume=True,
    )
    assert resumed.total == 3
    assert len(resumed_calls) == 3
    assert len(list(state_root.glob("*.json"))) == 3


def test_oom_during_candidate_execution_is_unclassified_and_unrecorded(
    tmp_path, monkeypatch
):
    values = iter((0, 0, 1))
    state_root = tmp_path / "oom-state"
    result = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="oom-candidate",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=1,
        process_runner=_decide5(tmp_path, {}),
        state_root=state_root,
        oom_counter=lambda: next(values),
    )

    assert len(result.budget_exceeded) == 3
    assert not result.killed
    assert not result.crashed
    assert list(state_root.glob("*.json")) == []


def test_deadline_expiring_during_snapshot_cleanup_leaves_candidate_unclassified(
    tmp_path, monkeypatch
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    remove_owned_tree = isolation_module._remove_owned_tree
    expired = False

    def cleanup_then_expire(root):
        nonlocal expired
        remove_owned_tree(root)
        if not expired:
            expired = True
            now[0] = 11.0

    monkeypatch.setattr(isolation_module, "_remove_owned_tree", cleanup_then_expire)
    state_root = tmp_path / "cleanup-deadline-state"
    result = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="cleanup-deadline",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=1,
        process_runner=_decide5(tmp_path, {}),
        state_root=state_root,
        deadline=deadline,
    )

    assert len(result.budget_exceeded) == 3
    assert not result.killed
    assert not result.survived
    assert list(state_root.glob("*.json")) == []


def test_deadline_is_rechecked_on_the_main_thread_before_candidate_state_commit(
    tmp_path, monkeypatch
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    real_wait = mutation_module.wait
    expired = False

    def wait_then_expire(futures, *, return_when):
        nonlocal expired
        done, pending = real_wait(futures, return_when=return_when)
        if done and not expired:
            expired = True
            now[0] = 11.0
        return done, pending

    monkeypatch.setattr(mutation_module, "wait", wait_then_expire)
    state_root = tmp_path / "main-thread-deadline-state"
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="main-thread-deadline",
            targets=_TARGETS3,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            state_root=state_root,
            deadline=deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert list(state_root.glob("*.json")) == []


def test_deadline_is_rechecked_after_state_guard_before_persisting_candidate(
    tmp_path, monkeypatch
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    guard_calls = 0

    def expire_on_candidate_guard():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            now[0] = 11.0

    state_root = tmp_path / "state-guard-deadline-state"
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="state-guard-deadline",
            targets=_TARGETS3,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            state_root=state_root,
            deadline=deadline,
            state_root_guard=expire_on_candidate_guard,
        )

    assert guard_calls == 2
    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert list(state_root.glob("*.json")) == []


def test_deadline_expiring_during_state_serialization_does_not_commit_candidate(
    tmp_path, monkeypatch
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    state_root = tmp_path / "serialization-deadline-state"
    real_dump = mutation_module.json.dump

    def dump_then_expire(document, stream, **kwargs):
        real_dump(document, stream, **kwargs)
        if isinstance(document, dict) and "candidate_id" in document:
            now[0] = 11.0

    monkeypatch.setattr(mutation_module.json, "dump", dump_then_expire)
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="serialization-deadline",
            targets=_TARGETS3,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            state_root=state_root,
            deadline=deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert list(state_root.glob("*.json")) == []
    assert list(state_root.glob(".state-*")) == []


def test_deadline_expiring_after_state_replace_removes_candidate_record(
    tmp_path, monkeypatch
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    state_root = tmp_path / "replace-deadline-state"
    real_replace = mutation_module.os.replace

    def replace_then_expire(source, destination, *args, **kwargs):
        result = real_replace(source, destination, *args, **kwargs)
        if Path(destination).parent == state_root:
            now[0] = 11.0
        return result

    monkeypatch.setattr(mutation_module.os, "replace", replace_then_expire)
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="replace-deadline",
            targets=_TARGETS3,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            state_root=state_root,
            deadline=deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert list(state_root.glob("*.json")) == []
    assert list(state_root.glob(".state-*")) == []


@pytest.mark.parametrize("descriptor_relative", [False, True])
def test_directory_sync_failure_rolls_back_new_candidate_record(
    tmp_path, monkeypatch, descriptor_relative
):
    state_root = tmp_path / f"directory-sync-state-{descriptor_relative}"
    state_root.mkdir()
    state_root_fd = (
        os.open(state_root, os.O_RDONLY | os.O_DIRECTORY)
        if descriptor_relative
        else None
    )
    real_fsync = mutation_module.os.fsync
    failed = False

    def fail_first_directory_sync(fd):
        nonlocal failed
        if stat.S_ISDIR(os.fstat(fd).st_mode) and not failed:
            failed = True
            raise OSError("injected directory sync failure")
        return real_fsync(fd)

    monkeypatch.setattr(mutation_module.os, "fsync", fail_first_directory_sync)
    try:
        with pytest.raises(OSError, match="injected directory sync failure"):
            _run_queue_case(
                tmp_path,
                monkeypatch,
                name=f"directory-sync-{descriptor_relative}",
                targets=_TARGETS3,
                text=_TEXT3,
                jobs=1,
                process_runner=_decide5(tmp_path, {}),
                state_root=state_root,
                state_root_fd=state_root_fd,
            )
    finally:
        if state_root_fd is not None:
            os.close(state_root_fd)

    assert failed
    assert list(state_root.glob("*.json")) == []
    assert list(state_root.glob(".state-*")) == []
    assert list(state_root.glob(".state-backup-*")) == []


@pytest.mark.parametrize("descriptor_relative", [False, True])
def test_guard_refusal_restores_preexisting_candidate_record(
    tmp_path, monkeypatch, descriptor_relative
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    state_root = tmp_path / f"existing-record-state-{descriptor_relative}"
    state_root.mkdir()
    one_target = (
        MutationTarget(path="pkg/flags.py", text=_TEXT3, lines=frozenset({2})),
    )
    _, repo = _run_queue_case(
        tmp_path,
        monkeypatch,
        name=f"existing-record-seed-{descriptor_relative}",
        targets=one_target,
        text=_TEXT3,
        jobs=1,
        process_runner=_decide5(tmp_path, {}),
        state_root=state_root,
        return_repo=True,
    )
    existing_records = list(state_root.glob("*.json"))
    assert len(existing_records) == 1
    old_record_path = existing_records[0]
    old_record = old_record_path.read_bytes()
    state_root_fd = (
        os.open(state_root, os.O_RDONLY | os.O_DIRECTORY)
        if descriptor_relative
        else None
    )
    real_replace = mutation_module.os.replace

    def replace_then_expire(source, destination, *args, **kwargs):
        result = real_replace(source, destination, *args, **kwargs)
        if Path(os.fspath(source)).name.startswith(".state-") and Path(
            os.fspath(destination)
        ).suffix == ".json":
            now[0] = 11.0
        return result

    monkeypatch.setattr(mutation_module.os, "replace", replace_then_expire)
    try:
        with pytest.raises(AssayError) as caught:
            _run_queue_case(
                tmp_path,
                monkeypatch,
                name=f"existing-record-rewrite-{descriptor_relative}",
                targets=one_target,
                text=_TEXT3,
                jobs=1,
                process_runner=_decide5(tmp_path, {}),
                repo=repo,
                state_root=state_root,
                state_root_fd=state_root_fd,
                deadline=deadline,
            )
    finally:
        if state_root_fd is not None:
            os.close(state_root_fd)

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert old_record_path.read_bytes() == old_record
    assert list(state_root.glob(".state-backup-*")) == []


def test_deadline_expiring_during_resume_merge_does_not_return_completed_result(
    tmp_path, monkeypatch
):
    state_root = tmp_path / "resume-merge-deadline-state"
    one_target = (
        MutationTarget(path="pkg/flags.py", text=_TEXT3, lines=frozenset({2})),
    )
    timeout_candidate = _candidate_names(one_target)[0]

    def candidate_timeout(argv, cwd):
        del cwd
        raise subprocess.TimeoutExpired(list(argv), timeout=1.0, output=b"partial")

    seeded, repo = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="resume-merge-seed",
        targets=one_target,
        text=_TEXT3,
        jobs=1,
        process_runner=_decide5(tmp_path, {timeout_candidate: candidate_timeout}),
        state_root=state_root,
        budget_per_candidate_seconds=1.0,
        return_repo=True,
    )
    assert len(seeded.budget_exceeded) == 1
    assert len(list(state_root.glob("*.json"))) == 1

    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    merge = mutation_module.merge_mutations
    calls = 0

    def merge_then_expire(payload, records):
        nonlocal calls
        assert len(records) == 1
        assert records[0]["outcome_bucket"] == "budget_exceeded"
        result = merge(payload, records)
        calls += 1
        now[0] = 11.0
        return result

    monkeypatch.setattr(mutation_module, "merge_mutations", merge_then_expire)
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="resume-merge-after-seed",
            targets=one_target,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            repo=repo,
            state_root=state_root,
            resume=True,
            budget_per_candidate_seconds=1.0,
            deadline=deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert calls == 1


def test_deadline_expiring_during_candidate_classification_emits_no_progress(
    tmp_path, monkeypatch
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    one_target = (
        MutationTarget(path="pkg/flags.py", text=_TEXT3, lines=frozenset({2})),
    )
    classify = mutation_module._classify_mutant_result
    progress_events = []

    def classify_then_expire(result):
        bucket = classify(result)
        now[0] = 11.0
        return bucket

    monkeypatch.setattr(mutation_module, "_classify_mutant_result", classify_then_expire)
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="candidate-classification-deadline",
            targets=one_target,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            progress_events=progress_events,
            deadline=deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert not any(event.get("event") == "candidate" for event in progress_events)


def test_deadline_expiring_during_final_bucket_does_not_return_completed_result(
    tmp_path, monkeypatch
):
    now = [0.0]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: now[0])
    one_target = (
        MutationTarget(path="pkg/flags.py", text=_TEXT3, lines=frozenset({2})),
    )
    classify = mutation_module._classify_mutant_result
    calls = 0

    def classify_then_expire(result):
        nonlocal calls
        bucket = classify(result)
        calls += 1
        if calls == 2:
            now[0] = 11.0
        return bucket

    monkeypatch.setattr(mutation_module, "_classify_mutant_result", classify_then_expire)
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="final-bucket-deadline",
            targets=one_target,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            deadline=deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert calls == 2


def test_termination_during_final_bucket_does_not_return_completed_result(
    tmp_path, monkeypatch
):
    termination = [False]
    deadline = make_deadline(budget_seconds=10.0, monotonic=lambda: 0.0)
    one_target = (
        MutationTarget(path="pkg/flags.py", text=_TEXT3, lines=frozenset({2})),
    )
    classify = mutation_module._classify_mutant_result
    calls = 0

    def classify_then_request_termination(result):
        nonlocal calls
        bucket = classify(result)
        calls += 1
        if calls == 2:
            termination[0] = True
        return bucket

    monkeypatch.setattr(
        runner_module, "termination_requested", lambda: termination[0]
    )
    monkeypatch.setattr(
        mutation_module,
        "_classify_mutant_result",
        classify_then_request_termination,
    )
    with pytest.raises(AssayError) as caught:
        _run_queue_case(
            tmp_path,
            monkeypatch,
            name="final-bucket-termination",
            targets=one_target,
            text=_TEXT3,
            jobs=1,
            process_runner=_decide5(tmp_path, {}),
            deadline=deadline,
        )

    assert caught.value.reason_code is ReasonCode.LANE_TIMEOUT
    assert calls == 2


class _OutstandingExecutor:
    def __init__(self, jobs: int, state: dict) -> None:
        self.jobs = jobs
        self.state = state
        self.real = ThreadPoolExecutor(max_workers=jobs)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.real.__exit__(*args)

    def submit(self, fn, *args):
        position = args[0]
        with self.state["lock"]:
            self.state["positions"].append(position)
            self.state["outstanding"] += 1
            self.state["max_outstanding"] = max(
                self.state["max_outstanding"], self.state["outstanding"]
            )

        def tracked(*inner_args):
            try:
                return fn(*inner_args)
            finally:
                with self.state["lock"]:
                    self.state["outstanding"] -= 1

        return self.real.submit(tracked, *args)


def test_work_queue_bounds_in_flight_and_submits_in_position_order(tmp_path, monkeypatch):
    state = {
        "lock": threading.Lock(),
        "outstanding": 0,
        "max_outstanding": 0,
        "positions": [],
    }
    executor_refs = []

    def factory(jobs):
        executor = _OutstandingExecutor(jobs, state)
        executor_refs.append(executor)
        return executor

    result = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="queue-bound",
        targets=_TARGETS5,
        text=_TEXT5,
        jobs=2,
        process_runner=_decide5(tmp_path, {}),
        executor_factory=factory,
    )
    assert result.total == 5
    assert len(executor_refs) == 1
    assert executor_refs[0].jobs == 2
    assert state["positions"] == list(range(5))
    assert state["max_outstanding"] <= 2


def test_completed_candidate_releases_a_slot_without_waiting_for_the_batch(
    tmp_path, monkeypatch
):
    names = _candidate_names(_TARGETS5)
    name_at = dict(enumerate(names))
    release_zero = threading.Event()
    zero_started = threading.Event()
    candidate_three_started = threading.Event()

    def hold_zero(argv, cwd):
        zero_started.set()
        if not release_zero.wait(10):
            raise AssertionError("position 0 was never released")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def observe_three(argv, cwd):
        candidate_three_started.set()
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    behaviours = {
        name_at[0]: hold_zero,
        name_at[3]: observe_three,
    }
    with ThreadPoolExecutor(max_workers=1) as driver:
        task = driver.submit(
            _run_queue_case,
            tmp_path,
            monkeypatch,
            name="queue-no-barrier",
            targets=_TARGETS5,
            text=_TEXT5,
            jobs=3,
            process_runner=_decide5(tmp_path, behaviours),
        )
        try:
            assert zero_started.wait(5), "position 0 did not start"
            assert candidate_three_started.wait(5), (
                "position 3 did not start while position 0 held its worker slot"
            )
            assert not release_zero.is_set()
        finally:
            release_zero.set()
        assert task.result(timeout=10).total == 5


def test_candidate_event_buffer_orders_events_and_flushes_after_gaps():
    buffer = mutation_module._CandidateEventBuffer(4)
    event_one = {"candidate_index": 1}
    event_two = {"candidate_index": 2}
    buffer.stage(2, event_two)
    assert buffer.drain_contiguous() == []
    buffer.resolve_without_event(0)
    buffer.stage(1, event_one)
    assert buffer.drain_contiguous() == [event_one, event_two]
    buffer.resolve_without_event(3)
    assert buffer.drain_contiguous() == []

    abnormal = mutation_module._CandidateEventBuffer(4)
    event_three = {"candidate_index": 3}
    abnormal.stage(1, event_one)
    abnormal.stage(3, event_three)
    assert abnormal.drain_contiguous() == []
    assert abnormal.drain_all_ascending() == [event_one, event_three]


def test_candidate_progress_stays_ordered_when_state_writes_complete_out_of_order(
    tmp_path, monkeypatch
):
    names = _candidate_names(_TARGETS3)
    name_at = dict(enumerate(names))
    release_zero = threading.Event()
    progress_events: list[dict] = []
    state_root = tmp_path / "queue-state"
    real_write = mutation_module._write_mutation_state_record
    jobs = collect_mutation_sites(
        _TARGETS3,
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=50,
    )
    assert jobs != "UNSUPPORTED"
    position_one_id = candidate_id(jobs[1])

    def hold_zero(argv, cwd):
        if not release_zero.wait(10):
            raise AssertionError("position 0 was never released")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def complete_one(argv, cwd):
        return subprocess.CompletedProcess(list(argv), 1, "", "")

    def release_zero_after_one_is_recorded(root, payload, **kwargs):
        real_write(root, payload, **kwargs)
        if payload["candidate_id"] == position_one_id:
            release_zero.set()

    monkeypatch.setattr(
        mutation_module,
        "_write_mutation_state_record",
        release_zero_after_one_is_recorded,
    )
    result = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="queue-progress-order",
        targets=_TARGETS3,
        text=_TEXT3,
        jobs=3,
        process_runner=_decide5(
            tmp_path,
            {name_at[0]: hold_zero, name_at[1]: complete_one},
        ),
        state_root=state_root,
        resume=True,
        progress_events=progress_events,
    )
    assert result.total == 3
    candidate_indices = [
        event["candidate_index"]
        for event in progress_events
        if event.get("event") == "candidate"
    ]
    assert candidate_indices == [0, 1, 2]
    assert len(list(state_root.glob("*.json"))) == 3


def test_queue_drains_all_futures_and_raises_lowest_position_fatal(
    tmp_path, monkeypatch
):
    futures: dict[int, Future] = {}
    submitted: list[int] = []
    all_submitted = threading.Event()
    position_one_observed = threading.Event()
    fault_zero = AssayError(
        "position zero fatal", outcome=Outcome.ERROR, reason_code=ReasonCode.GIT_FAILED
    )
    fault_one = AssayError(
        "position one fatal",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.MUTATION_DISCOVERY_FAILED,
    )
    fault_two = AssayError(
        "position two fatal", outcome=Outcome.ERROR, reason_code=ReasonCode.BAD_LANE_CONFIG
    )

    class InjectedExecutor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def submit(self, fn, position):
            del fn
            future = Future()
            futures[position] = future
            submitted.append(position)
            if position == 1:
                future.set_exception(fault_one)
            if len(submitted) == 3:
                all_submitted.set()
            return future

    real_wait = mutation_module.wait

    def observe_first_fatal(waiting, *, return_when):
        done, pending = real_wait(waiting, return_when=return_when)
        if futures.get(1) in done:
            position_one_observed.set()
        return done, pending

    monkeypatch.setattr(mutation_module, "wait", observe_first_fatal)
    with ThreadPoolExecutor(max_workers=1) as driver:
        task = driver.submit(
            _run_queue_case,
            tmp_path,
            monkeypatch,
            name="queue-fatal-order",
            targets=_TARGETS5,
            text=_TEXT5,
            jobs=3,
            process_runner=_decide5(tmp_path, {}),
            executor_factory=lambda _jobs: InjectedExecutor(),
        )
        assert all_submitted.wait(5)
        assert position_one_observed.wait(5)
        futures[0].set_exception(fault_zero)
        futures[2].set_exception(fault_two)
        with pytest.raises(AssayError) as caught:
            task.result(timeout=10)
    assert caught.value is fault_zero
    assert submitted == [0, 1, 2]


def test_lane_timeout_stops_submission_but_drains_completed_in_flight_work(
    tmp_path, monkeypatch
):
    names = _candidate_names(_TARGETS5)
    name_at = dict(enumerate(names))
    release = threading.Event()
    timeout_started = threading.Event()
    state = {
        "lock": threading.Lock(),
        "outstanding": 0,
        "max_outstanding": 0,
        "positions": [],
    }
    executor_refs = []

    def factory(jobs):
        executor = _OutstandingExecutor(jobs, state)
        executor_refs.append(executor)
        return executor

    def hold(argv, cwd):
        if not release.wait(10):
            raise AssertionError("held in-flight candidate was not released")
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    timeout_error = AssayError(
        "campaign deadline during candidate",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )
    real_execute_plan = runner_module.execute_plan

    def stop_position_one(plan, *, cwd, **kwargs):
        source = (cwd / "pkg/flags.py").read_text(encoding="utf-8")
        if f"{name_at[1]} = False" in source:
            timeout_started.set()
            raise timeout_error
        return real_execute_plan(plan, cwd=cwd, **kwargs)

    monkeypatch.setattr(runner_module, "execute_plan", stop_position_one)
    state_root = tmp_path / "timeout-state"
    progress_events: list[dict] = []
    with ThreadPoolExecutor(max_workers=1) as driver:
        task = driver.submit(
            _run_queue_case,
            tmp_path,
            monkeypatch,
            name="queue-timeout-drain",
            targets=_TARGETS5,
            text=_TEXT5,
            jobs=3,
            process_runner=_decide5(
                tmp_path,
                {name_at[0]: hold, name_at[2]: hold},
            ),
            state_root=state_root,
            resume=True,
            progress_events=progress_events,
            executor_factory=factory,
        )
        assert timeout_started.wait(5), "position 1 did not reach its deadline"
        release.set()
        result = task.result(timeout=10)
    assert len(executor_refs) == 1
    assert state["positions"] == [0, 1, 2]
    assert len(list(state_root.glob("*.json"))) == 2
    assert len(result.budget_exceeded) == 3
    assert [
        event["candidate_index"]
        for event in progress_events
        if event.get("event") == "candidate"
    ] == [0, 2]


def test_fatal_stops_submission_but_records_other_in_flight_results(
    tmp_path, monkeypatch
):
    names = _candidate_names(_TARGETS5)
    name_at = dict(enumerate(names))
    release = threading.Event()
    fatal_started = threading.Event()
    state = {
        "lock": threading.Lock(),
        "outstanding": 0,
        "max_outstanding": 0,
        "positions": [],
    }

    def factory(jobs):
        return _OutstandingExecutor(jobs, state)

    def hold(argv, cwd):
        if not release.wait(10):
            raise AssertionError("held in-flight candidate was not released")
        return subprocess.CompletedProcess(list(argv), 1, "", "")

    fatal = AssayError(
        "candidate infrastructure failure",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.GIT_FAILED,
    )
    real_execute_plan = runner_module.execute_plan

    def fail_position_one(plan, *, cwd, **kwargs):
        source = (cwd / "pkg/flags.py").read_text(encoding="utf-8")
        if f"{name_at[1]} = False" in source:
            fatal_started.set()
            raise fatal
        return real_execute_plan(plan, cwd=cwd, **kwargs)

    monkeypatch.setattr(runner_module, "execute_plan", fail_position_one)
    state_root = tmp_path / "fatal-state"
    progress_events: list[dict] = []
    with ThreadPoolExecutor(max_workers=1) as driver:
        task = driver.submit(
            _run_queue_case,
            tmp_path,
            monkeypatch,
            name="queue-fatal-drain",
            targets=_TARGETS5,
            text=_TEXT5,
            jobs=3,
            process_runner=_decide5(
                tmp_path,
                {name_at[0]: hold, name_at[2]: hold},
            ),
            state_root=state_root,
            resume=True,
            progress_events=progress_events,
            executor_factory=factory,
        )
        assert fatal_started.wait(5), "position 1 did not reach the fatal path"
        release.set()
        with pytest.raises(AssayError) as caught:
            task.result(timeout=10)
    assert caught.value is fatal
    assert state["positions"] == [0, 1, 2]
    assert len(list(state_root.glob("*.json"))) == 2
    assert [
        event["candidate_index"]
        for event in progress_events
        if event.get("event") == "candidate"
    ] == [0, 2]


def test_resumed_pending_indices_remain_monotonic_under_reverse_completion(
    tmp_path, monkeypatch
):
    state_root = tmp_path / "resume-order-state"
    all_jobs = collect_mutation_sites(
        _TARGETS5,
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=50,
    )
    assert all_jobs != "UNSUPPORTED"
    all_candidate_ids = [candidate_id(job) for job in all_jobs]
    shard_pair = next(
        (count, index)
        for count in range(2, 6)
        for index in range(count)
        if len(
            mutation_module.select_mutation_shard(
                all_candidate_ids, index=index, count=count
            )
        )
        == 2
    )
    first, repo = _run_queue_case(
        tmp_path,
        monkeypatch,
        name="queue-resume-order",
        targets=_TARGETS5,
        text=_TEXT5,
        jobs=1,
        process_runner=_decide5(tmp_path, {}),
        state_root=state_root,
        shard_index=shard_pair[1],
        shard_count=shard_pair[0],
        return_repo=True,
    )
    del first
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in state_root.glob("*.json")
    ]
    assert len(records) == 2
    completed_ids = {record["candidate_id"] for record in records}
    all_jobs = collect_mutation_sites(
        _TARGETS5,
        adapter=PythonAdapter(),
        operators=("python:bool-const-flip",),
        limit=50,
    )
    assert all_jobs != "UNSUPPORTED"
    pending_jobs = [job for job in all_jobs if candidate_id(job) not in completed_ids]
    pending_names = ["abcde"[job.site.lineno - 2] for job in pending_jobs]
    assert len(pending_names) == 3

    release_zero = threading.Event()
    release_one = threading.Event()
    completed: list[str] = []
    finished = {name: threading.Event() for name in pending_names}

    def complete_in_order(name, gate=None):
        def action(argv, cwd):
            if gate is not None and not gate.wait(10):
                raise AssertionError(f"candidate {name} was not released")
            completed.append(name)
            finished[name].set()
            return subprocess.CompletedProcess(list(argv), 1, "", "")

        return action

    behaviors = {
        pending_names[0]: complete_in_order(pending_names[0], release_zero),
        pending_names[1]: complete_in_order(pending_names[1], release_one),
        pending_names[2]: complete_in_order(pending_names[2]),
    }
    progress_events: list[dict] = []
    with ThreadPoolExecutor(max_workers=1) as driver:
        task = driver.submit(
            _run_queue_case,
            tmp_path,
            monkeypatch,
            name="queue-resume-order-second",
            repo=repo,
            targets=_TARGETS5,
            text=_TEXT5,
            jobs=3,
            process_runner=_decide5(tmp_path, behaviors),
            state_root=state_root,
            resume=True,
            progress_events=progress_events,
        )
        try:
            assert finished[pending_names[2]].wait(5)
            release_one.set()
            assert finished[pending_names[1]].wait(5)
            release_zero.set()
        finally:
            release_one.set()
            release_zero.set()
        task.result(timeout=10)
    assert completed == [pending_names[2], pending_names[1], pending_names[0]]
    assert [
        event["candidate_index"]
        for event in progress_events
        if event.get("event") == "candidate"
    ] == [0, 1, 2]
    assert len(list(state_root.glob("*.json"))) == 5
