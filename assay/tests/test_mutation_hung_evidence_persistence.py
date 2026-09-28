"""B107: preserve resource evidence from a liveness hang through R2 output.

The evidence must survive both durable surfaces: the mutation-state record
used by ``--resume`` and the candidate event in the progress stream.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay import liveness
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


def _evidence() -> dict:
    samples = [
        {
            "wall_elapsed_s": float(elapsed),
            "eligible_elapsed_s": float(elapsed),
            "eligible_interval_s": 0.0 if elapsed == 0 else 1.0,
            "candidate_cpu_s": 3.0,
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
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=baseline_runner,
    )
    assert baseline.outcome is Outcome.PASS

    def hung_runner(argv, *, env, cwd, timeout):
        raise liveness.LivenessHungExpired(
            cmd=list(argv),
            timeout=timeout,
            output=b"",
            stderr=b"",
            resource_evidence=evidence,
        )

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
