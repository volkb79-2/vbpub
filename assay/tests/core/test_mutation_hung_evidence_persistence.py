"""B107: preserve resource evidence from a liveness hang through R2 output.

The evidence must survive both durable surfaces: the mutation-state record
used by ``--resume`` and the candidate event in the progress stream.
"""

from __future__ import annotations

from conftest import completed_process, process_tree_evidence_dict

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay import liveness, mutation_store
from assay.adapters.python import PythonAdapter
from assay.errors import Outcome
from assay.mutation import MutationTarget, run_mutation
from assay.runner import execute_command

_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    return a, b\n"
)
_TARGETS = (MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3})),)
_SNAPSHOT = {
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


def _cpu_evidence(*, decision: str = "cpu-without-progress") -> dict:
    """(P4) The evidence `LivenessRunner.cpu_evidence` emits for a CPU kill --
    the second policy `mutation_store` learned to validate."""
    assert decision in liveness.CPU_HANG_DECISIONS
    payload = {
        "schema_version": 1,
        "policy": liveness.CPU_PROGRESS_POLICY,
        "decision": decision,
        "candidate_pid": 4242,
        "candidate_cpu_source": liveness.CPU_PROGRESS_SOURCE,
        "wall_elapsed_s": 95.0,
        "candidate_cpu_s": 71.0,
        "cpu_since_last_completed_event_s": 70.0,
        "required_cpu_s": 70.0,
        "completed_events": 0,
        "event_count": 0,
        "wall_since_last_completed_event_s": 95.0,
        "test_nodeid": None,
        "per_test_baseline_s": None,
        "per_test_cpu_s": None,
        "resources": _SNAPSHOT,
        "trace_compacted": False,
    }
    if decision == "per-test-cpu-bound":
        payload.update(
            test_nodeid="tests/test_x.py::test_loop",
            per_test_baseline_s=3.0,
            per_test_cpu_s=61.0,
            required_cpu_s=60.0,  # max(10 x 3.0, 60)
        )
    return payload


def _evidence() -> dict:
    samples = [
        {
            "wall_elapsed_s": float(elapsed),
            "eligible_elapsed_s": float(elapsed),
            "eligible_interval_s": 0.0 if elapsed == 0 else 1.0,
            "candidate_cpu_s": 3.0,
            "event_count": 0,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "resource_interval": "unknown" if elapsed == 0 else "clear",
            "resource_deltas": {},
            **({"previous_resources": None} if elapsed == 0 else {}),
            "resources": _SNAPSHOT,
        }
        for elapsed in range(32)
    ]
    return {
        "schema_version": 1,
        "policy": "pressure-adjusted-idle-v1",
        "decision": "idle-hang",
        "candidate_pid": 4242,
        "candidate_cpu_source": "process-tree-cpu-seconds",
        "wall_elapsed_s": 31.0,
        "eligible_elapsed_s": 31.0,
        "idle_eligible_s": 31.0,
        "required_idle_eligible_s": 15.0,
        "required_cpu_growth_window_s": 30.0,
        "required_cpu_growth_floor_s": 1.0,
        "candidate_session_finish_seen": False,
        "session_finish_eligible_s": None,
        "trace_complete": True,
        "trace_truncated": False,
        "samples": samples,
    }


def _repo(tmp_path: Path) -> GitRepo:
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    return repo


def test_hung_resource_evidence_is_written_to_state_and_progress(tmp_path: Path):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"
    progress_path = tmp_path / "progress.jsonl"
    evidence = _evidence()

    def baseline_runner(argv, *, env, cwd, timeout):
        return completed_process(list(argv), returncode=0)

    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=baseline_runner,
    )
    assert baseline.outcome is Outcome.PASS

    def hung_runner(argv, *, env, cwd, timeout):
        error = liveness.LivenessHungExpired(
            cmd=list(argv),
            timeout=timeout,
            output=b"",
            stderr=b"",
            resource_evidence=evidence,
        )
        error.process_tree_evidence = process_tree_evidence_dict(
            leader_status="timeout-killed"
        )
        raise error

    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(make_lane(argv=("pytest", "-q"))),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=hung_runner,
            clock=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=progress_path,
        )

    assert not isinstance(result, str)
    assert len(result.hung) == result.total > 0

    state_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(state_root.glob("*.json"))
    ]
    assert len(state_records) == result.total
    assert all(record["outcome_bucket"] == "hung" for record in state_records)
    assert all(
        record["liveness_resource_evidence"] == evidence for record in state_records
    )

    progress_records = [
        json.loads(line)
        for line in progress_path.read_text(encoding="utf-8").splitlines()
    ]
    candidate_records = [
        record for record in progress_records if record.get("event") == "candidate"
    ]
    assert len(candidate_records) == result.total
    assert all(record["outcome_bucket"] == "hung" for record in candidate_records)
    assert all(
        record["liveness_resource_evidence"] == evidence for record in candidate_records
    )


# --- (P4/S3) the two NEW hung decisions persist, resume and rejudge -------
#
# `mutation_store._valid_hung_resource_evidence` learned a SECOND policy in P4
# (`liveness.CPU_PROGRESS_POLICY`, decisions `cpu-without-progress` and
# `per-test-cpu-bound`). A record carrying one of them must be written by the
# sweep, re-read by `--resume` and dropped by `--rejudge-outcome hung`, exactly
# like the idle one -- so every direction is pinned here, against the SHIPPED
# validator rather than a hand-built shape.


def _run_hung(
    tmp_path: Path,
    evidence: dict,
    *,
    resume: bool = False,
    process_runner=None,
    **extra,
):
    """One two-candidate sweep whose candidates are all `hung` with
    *evidence*; the repo, state root and progress file are reused across the
    calls of one test (`tmp_path` is that test's own directory)."""
    repo_path = tmp_path / "repo"
    if repo_path.exists():
        repo = GitRepo(path=repo_path)
    else:
        repo = _repo(tmp_path)
    state_root = tmp_path / "state"
    progress_path = tmp_path / "progress.jsonl"
    lane = make_lane(argv=("pytest", "-q"))

    def baseline_runner(argv, *, env, cwd, timeout):
        return completed_process(list(argv), returncode=0)

    baseline = execute_command(lane, cwd=repo.path, process_runner=baseline_runner)
    assert baseline.outcome is Outcome.PASS

    def hung_runner(argv, *, env, cwd, timeout):
        error = liveness.LivenessHungExpired(
            cmd=list(argv),
            timeout=timeout,
            output=b"",
            stderr=b"",
            resource_evidence=evidence,
        )
        error.process_tree_evidence = process_tree_evidence_dict(
            leader_status="timeout-killed"
        )
        raise error

    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir(exist_ok=True)
    with prepared_snapshot(repo, scratch_root=scratch_root) as prepared:
        return run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=hung_runner if process_runner is None else process_runner,
            clock=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc),
            state_root=state_root,
            progress_artifact=progress_path,
            resume=resume,
            **extra,
        )


def _state_records(tmp_path: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((tmp_path / "state").glob("*.json"))
    ]


def _alternating_idle_evidence() -> dict:
    """A long trace with alternating clear/stalled intervals and a clear
    trailing CPU window. The alternating prefix has no same-kind neighbors to
    merge; compaction must retain a valid bounded summary for resume."""
    trace = liveness._ResourceTrace()
    previous = _SNAPSHOT
    trace.reset(
        {
            "wall_elapsed_s": 0.0,
            "eligible_elapsed_s": 0.0,
            "eligible_interval_s": 0.0,
            "candidate_cpu_s": 3.0,
            "event_count": 0,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "resource_interval": "unknown",
            "resource_deltas": {},
            "previous_resources": None,
            "resources": previous,
        }
    )
    eligible = 0.0
    for wall in range(1, 1101):
        # Stall every other sample through t=1040, then leave 59 clear
        # intervals so the retained tail proves the complete CPU window.
        throttled = min(wall // 2, 520)
        resources = {
            **_SNAPSHOT,
            "cgroup_cpu": {
                "nr_throttled": throttled,
                "throttled_usec": throttled * 100,
            },
        }
        kind, deltas = liveness.compare_resource_snapshots(previous, resources)
        if kind == "clear":
            eligible += 1.0
        sample = {
            "wall_elapsed_s": float(wall),
            "eligible_elapsed_s": eligible,
            "eligible_interval_s": 1.0 if kind == "clear" else 0.0,
            "candidate_cpu_s": 3.0,
            "event_count": 0,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "resource_interval": kind,
            "resource_deltas": deltas,
            "resources": resources,
        }
        trace.append(sample)
        previous = resources

    samples = list(trace.samples)
    assert len(samples) <= liveness._LIVENESS_RESOURCE_TRACE_MAX_SAMPLES
    assert trace.compacted is True
    assert any(
        sample["resource_interval"] == "stalled"
        and sample["eligible_interval_s"] > 0
        for sample in samples
    ), "compaction must preserve clear time inside a mixed stalled summary"
    return {
        "schema_version": 1,
        "policy": liveness.IDLE_HANG_POLICY,
        "decision": "idle-hang",
        "candidate_pid": 4242,
        "candidate_cpu_source": "process-tree-cpu-seconds",
        "wall_elapsed_s": float(wall),
        "eligible_elapsed_s": eligible,
        "idle_eligible_s": eligible,
        "required_idle_eligible_s": 15.0,
        "required_cpu_growth_window_s": liveness._HUNG_CPU_WINDOW_S,
        "required_cpu_growth_floor_s": liveness._HUNG_CPU_GROWTH_FLOOR_S,
        "candidate_session_finish_seen": False,
        "session_finish_eligible_s": None,
        "trace_complete": True,
        "trace_truncated": False,
        "trace_compacted": trace.compacted,
        "trace_samples_merged": trace.merged,
        "samples": samples,
    }


def test_long_alternating_trace_idle_and_cpu_hangs_persist_and_resume(
    tmp_path: Path,
) -> None:
    """The 1024-row cap also has to fit the state-record limit and survive
    mutation-store validation for both trace-carrying idle evidence and the
    separate CPU-evidence policy."""
    idle_evidence = _alternating_idle_evidence()
    assert len(idle_evidence["samples"]) <= liveness._LIVENESS_RESOURCE_TRACE_MAX_SAMPLES
    assert len(json.dumps(idle_evidence).encode("utf-8")) < mutation_store.MUTATION_STATE_RECORD_LIMIT
    assert mutation_store._valid_hung_resource_evidence(idle_evidence) is True

    cpu_evidence = _cpu_evidence()
    cpu_evidence["trace_compacted"] = True
    assert mutation_store._valid_hung_resource_evidence(cpu_evidence) is True

    finish_evidence = dict(idle_evidence)
    finish_evidence.update(
        decision="session-finish-hang",
        candidate_session_finish_seen=True,
        session_finish_eligible_s=(
            idle_evidence["eligible_elapsed_s"]
            - liveness._HUNG_SESSION_FINISH_GRACE_S
        ),
        required_idle_eligible_s=liveness._HUNG_SESSION_FINISH_GRACE_S,
    )
    assert mutation_store._valid_hung_resource_evidence(finish_evidence) is True

    for name, evidence in (
        ("idle", idle_evidence),
        ("session-finish", finish_evidence),
        ("cpu", cpu_evidence),
    ):
        case = tmp_path / name
        case.mkdir()
        first = _run_hung(case, evidence, resume=True)
        assert len(first.hung) == first.total > 0
        state_paths = sorted((case / "state").glob("*.json"))
        assert state_paths
        assert all(
            path.stat().st_size <= mutation_store.MUTATION_STATE_RECORD_LIMIT
            for path in state_paths
        )
        assert all(
            record["liveness_resource_evidence"] == evidence
            for record in _state_records(case)
        )

        def never_runs(argv, *, env, cwd, timeout):
            raise AssertionError("a valid hung record must resume, not rerun")

        resumed = _run_hung(
            case, evidence, resume=True, process_runner=never_runs
        )
        assert len(resumed.hung) == resumed.total == first.total


@pytest.mark.parametrize("decision", sorted(liveness.CPU_HANG_DECISIONS))
def test_a_cpu_hang_record_is_persisted_and_resumes_as_is(
    tmp_path: Path, decision: str
) -> None:
    """A CPU-without-progress kill writes its record, and a second sweep
    RESUMES it instead of re-executing -- proven by a runner that would raise
    an AssertionError if a candidate ran again at all."""
    evidence = _cpu_evidence(decision=decision)
    assert mutation_store._valid_hung_resource_evidence(evidence) is True
    first = _run_hung(tmp_path, evidence, resume=True)
    assert len(first.hung) == first.total > 0
    for record in _state_records(tmp_path):
        assert record["outcome_bucket"] == "hung"
        assert record["liveness_resource_evidence"] == evidence

    def never_runs(argv, *, env, cwd, timeout):
        raise AssertionError("a valid CPU-hang record must resume, not rerun")

    resumed = _run_hung(tmp_path, evidence, resume=True, process_runner=never_runs)
    assert len(resumed.hung) == resumed.total == first.total
    for record in _state_records(tmp_path):
        assert record["liveness_resource_evidence"] == evidence


def test_a_cpu_hang_record_below_its_own_bound_is_rejected_and_reexecuted(
    tmp_path: Path,
) -> None:
    """The validator is the cache boundary: a CPU-policy record whose measured
    value does not reach its stated bound proves no hang (a writer bug or a
    hand-edited file), so `--resume` refuses it and the candidate really runs
    again."""
    evidence = _cpu_evidence()
    first = _run_hung(tmp_path, evidence, resume=True)
    assert len(first.hung) == first.total

    for path in sorted((tmp_path / "state").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        corrupt = dict(record["liveness_resource_evidence"])
        corrupt["cpu_since_last_completed_event_s"] = corrupt["required_cpu_s"] - 1.0
        record["liveness_resource_evidence"] = corrupt
        path.write_text(json.dumps(record), encoding="utf-8")

    calls: list[bool] = []

    def decide(argv, *, env, cwd, timeout):
        calls.append(True)
        error = liveness.LivenessHungExpired(
            cmd=list(argv),
            timeout=timeout,
            output=b"",
            stderr=b"",
            resource_evidence=evidence,
        )
        error.process_tree_evidence = process_tree_evidence_dict(
            leader_status="timeout-killed"
        )
        raise error

    resumed = _run_hung(tmp_path, evidence, resume=True, process_runner=decide)
    # Every candidate executed at least once (B179 then gives each `hung`
    # result its one serial tail retry, so the count can be higher) -- what
    # matters is that a refused record was NOT replayed.
    assert len(calls) >= first.total
    assert len(resumed.hung) == resumed.total


def test_rejudge_outcome_hung_reexecutes_a_valid_cpu_hang_record(
    tmp_path: Path,
) -> None:
    """`--rejudge-outcome hung` drops the record BEFORE the validator runs, so
    a perfectly valid CPU-hang verdict is still re-executed on demand -- the
    operator's way to re-judge a bucket whose cause may have changed (a new
    bound, a strengthened suite)."""
    evidence = _cpu_evidence()
    first = _run_hung(tmp_path, evidence, resume=True)
    assert len(first.hung) == first.total

    calls: list[bool] = []

    def decide(argv, *, env, cwd, timeout):
        calls.append(True)
        error = liveness.LivenessHungExpired(
            cmd=list(argv),
            timeout=timeout,
            output=b"",
            stderr=b"",
            resource_evidence=evidence,
        )
        error.process_tree_evidence = process_tree_evidence_dict(
            leader_status="timeout-killed"
        )
        raise error

    rejudged = _run_hung(
        tmp_path,
        evidence,
        resume=True,
        process_runner=decide,
        rejudge_outcomes=frozenset({"hung"}),
    )
    assert len(calls) >= first.total
    assert len(rejudged.hung) == rejudged.total
