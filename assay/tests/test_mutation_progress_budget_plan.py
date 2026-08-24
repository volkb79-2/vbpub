from __future__ import annotations

import json
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import time
import pytest

from conftest import GitRepo, Project, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.config import LaneConfigError, parse_duration
from assay.errors import Outcome
from assay.verify import verify_document
from assay.mutation import Mutation, MutantOutcome, MutationTarget, run_mutation
from assay.runner import execute_command


_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    return a, b\n"
)
_TARGETS = (
    MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3})),
)


def _repo(tmp_path):
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    return repo


def test_progress_events_are_emitted_for_baseline_and_every_candidate(tmp_path):
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        if "b = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        raise AssertionError(f"unexpected content: {text!r}")

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    assert baseline.outcome is Outcome.PASS

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=progress_path,
        )

    assert result is not None and not isinstance(result, str)
    lines = progress_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [event["candidate_index"] for event in events] == [-1, 0, 1]
    assert all(event["candidate_total"] == 2 for event in events[1:])
    assert events[0]["event"] == "baseline"
    for index, event in enumerate(events[1:], start=0):
        assert event["path"] == "pkg/flags.py"
        assert event["operator"] == "python:bool-const-flip"
        assert event["replacement_sha256"]
        assert isinstance(event["elapsed_seconds"], float)
    assert events[1]["outcome_bucket"] == "killed"
    assert events[2]["outcome_bucket"] == "survived"


def test_progress_artifact_path_is_constrained_in_verdict_model():
    outcome = MutantOutcome(
        path="pkg/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="0" * 64,
        operator="python:bool-const-flip",
        description="True->False",
    )
    with pytest.raises(ValueError, match="progress_artifact"):
        Mutation(candidate_count=1, total=1, killed=(outcome,), progress_artifact="../escape.jsonl")


def test_per_candidate_budget_marks_one_mutant_and_continues(tmp_path):
    repo = _repo(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    lane = make_lane(argv=("pytest", "-q"))
    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(budget_seconds=30.0),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            budget_per_candidate_seconds=0.05,
        )

    assert result is not None and not isinstance(result, str)
    assert len(result.budget_exceeded) == 1
    assert len(result.survived) == 1
    assert result.total == 2


def test_plan_reports_candidates_without_executing(tmp_path):
    project = Project(root=tmp_path / "proj")
    project.root.mkdir()
    (project.root / "pkg").mkdir()
    (project.root / "pkg" / "flags.py").write_text(_TEXT, encoding="utf-8")
    repo = GitRepo(path=project.root)
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write(
        "assay.toml",
        """
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = { MOCK_MODE = "true" }
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.package.judge.mutation]
jobs = 2
max_mutants = 10
operators = ["python:bool-const-flip"]
budget_per_candidate = "30s"
""",
    )
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", _TEXT.replace("a = True", "a = False"))
    repo.commit_all("base flag")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", _TEXT)
    repo.write("pkg/extra.py", "value = 1\n")
    repo.commit_all("restore flag and add extra")

    from assay.cli import main

    out = io.StringIO()
    exit_code = main(["plan", "package", "--file", str(project.root / "assay.toml")], stdout=out)
    payload = json.loads(out.getvalue())

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["candidate_count"] == 0
    assert payload["by_operator"] == {}
    assert payload["by_file"] == {}
    assert payload["estimated_wall_seconds"] == 0.0
    assert payload["estimated_serial_seconds"] == 0.0
    assert payload["estimated_wall_seconds"] == 0.0
    assert payload["candidates"] == []


def test_plan_config_requires_a_duration(tmp_path):
    project = Project(root=tmp_path / "proj")
    project.root.mkdir()
    (project.root / "pkg").mkdir()
    project.write(
        """
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["pytest", "-q"]
env = { MOCK_MODE = "true" }
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]
base = "main"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
budget_per_candidate = "nonsense"
"""
    )
    from assay.config import load_lane_file

    with pytest.raises(LaneConfigError, match="budget_per_candidate"):
        load_lane_file(project.root / "assay.toml")
