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
from assay import mutation
from assay.mutation import (
    Mutation,
    MutantOutcome,
    MutationTarget,
    collect_mutation_sites,
    run_mutation,
)
from assay import mutation as mutation_module
from assay.mutation import collect_mutation_sites
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
        assert len(event["candidate_id"]) == 64
        assert event["operator"] == "python:bool-const-flip"
        assert event["replacement_sha256"]
        assert isinstance(event["elapsed_seconds"], float)
    assert events[1]["outcome_bucket"] == "killed"
    assert events[2]["outcome_bucket"] == "survived"


def test_resume_reuses_completed_records_without_rerunning(tmp_path):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    progress_path = tmp_path / ".assay" / "lane.progress.jsonl"
    lane = make_lane(argv=("pytest", "-q"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    calls: list[str] = []

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        calls.append(text)
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        first = run_mutation(
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
            state_project_root=state_root,
            resume=True,
        )
    assert first.total == 2
    assert len(list((state_root / ".assay" / "mutation-state").glob("*.json"))) == 2
    assert len(calls) == 2

    calls.clear()
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        resumed = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("completed candidates must not run again")
            ),
            clock=lambda: datetime.now(timezone.utc),
            progress_artifact=None,
            state_project_root=state_root,
            resume=True,
        )

    assert calls == []
    assert resumed.total == 2
    assert {item.identity for item in first.killed} == {
        item.identity for item in resumed.killed
    }
    assert {item.identity for item in first.survived} == {
        item.identity for item in resumed.survived
    }


def test_resume_ignores_a_stale_record_when_source_changes(tmp_path):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state-root"
    state_root.mkdir()
    lane = make_lane(argv=("pytest", "-q"))
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def decide(argv, *, env, cwd, timeout):
        if Path(cwd) == repo.path:
            return subprocess.CompletedProcess(list(argv), returncode=0)
        text = (Path(cwd) / "pkg" / "flags.py").read_text(encoding="utf-8")
        if "a = False" in text:
            return subprocess.CompletedProcess(list(argv), returncode=1)
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(lane, cwd=repo.path, process_runner=decide)
    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            state_project_root=state_root,
            resume=True,
        )

    stale_path = next((state_root / ".assay" / "mutation-state").glob("*.json"))
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    stale["source_sha256"] = "0" * 64
    stale_path.write_text(json.dumps(stale), encoding="utf-8")

    calls: list[str] = []

    def deciding_recorder(*args, **kwargs):
        text = (Path(kwargs["cwd"]) / "pkg" / "flags.py").read_text(encoding="utf-8")
        calls.append(text)
        return decide(*args, **kwargs)

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        result = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(lane),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=2,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=deciding_recorder,
            clock=lambda: datetime.now(timezone.utc),
            state_project_root=state_root,
            resume=True,
        )

    assert calls != [], "a stale source hash must be rerun, never trusted"
    assert result.total == 2


def test_operator_and_shard_selection_are_deterministic_and_disjoint(tmp_path):
    text = (
        "def f(x, y):\n"
        "    a = True\n"
        "    if not x:\n"
        "        c = x > y\n"
        "    d = bool(y)\n"
    )
    target = MutationTarget(path="pkg/mixed.py", text=text, lines=frozenset({2, 3, 4, 5, 6}))
    both = collect_mutation_sites(
        (target,),
            adapter=PythonAdapter(),
            operators=(
                "python:compare-swap",
                "python:bool-const-flip",
                "python:falsy-swap",
        ),
        limit=10,
    )
    assert isinstance(both, tuple) and len(both) >= 2
    filtered_only = collect_mutation_sites(
        (target,),
        adapter=PythonAdapter(),
        operators=("python:compare-swap",),
        limit=10,
    )
    assert {job.site.operator for job in filtered_only} == {"python:compare-swap"}

    identities = [mutation.candidate_id(job) for job in both]
    selected = {
        index: mutation.select_mutation_shard(identities, index=index, count=2)
        for index in range(2)
    }
    assert set(selected[0]).isdisjoint(selected[1])
    assert sorted(selected[0] + selected[1]) == list(range(len(both)))
    assert selected == {
        index: mutation_module.select_mutation_shard(identities, index=index, count=2)
        for index in range(2)
    }


def _shard_summary(index: int, count: int, candidate_ids: list[str]):
    return {
        "schema_version": 1,
        "lane": "package",
        "commit": "a" * 40,
        "shard_index": index,
        "shard_count": count,
        "candidate_ids": candidate_ids,
    }


def test_shard_merge_accepts_exact_disjoint_exhaustive_coverage():
    # These specific digests are not arbitrary: `merge_mutation_shards` now
    # (B012/B023 remediation) recomputes each candidate's own deterministic
    # shard assignment and refuses a document that claims the wrong one, so
    # "4" * 64 must actually assign to shard 0/2 and "1" * 64 to shard 1/2.
    ids = ["4" * 64, "1" * 64]
    merged = mutation_module.merge_mutation_shards(
        [_shard_summary(0, 2, ids[:1]), _shard_summary(1, 2, ids[1:])]
    )
    assert merged == tuple(ids)


@pytest.mark.parametrize(
    "documents",
    [
        lambda: [_shard_summary(0, 2, ["1" * 64]), _shard_summary(1, 2, ["1" * 64])],
        lambda: [_shard_summary(0, 2, ["1" * 64]), _shard_summary(1, 2, ["1" * 64]), _shard_summary(0, 2, [])],
        lambda: [_shard_summary(0, 2, ["1" * 64])],
    ],
)
def test_shard_merge_refuses_duplicate_or_missing_input(documents):
    with pytest.raises(mutation.MutationStateError):
        mutation_module.merge_mutation_shards(documents())


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


def test_shard_candidate_ids_are_validated_for_disjointness_and_shape():
    candidate = mutation_module.candidate_id(
        collect_mutation_sites(
            _TARGETS,
            adapter=PythonAdapter(),
            operators=("python:bool-const-flip",),
            limit=1,
        )[0]
    )
    outcome = MutantOutcome(
        path="pkg/mod.py",
        lineno=1,
        start_byte=0,
        end_byte=1,
        replacement_sha256="0" * 64,
        operator="python:bool-const-flip",
        description="True->False",
    )
    payload = Mutation(
        candidate_count=1,
        total=1,
        killed=(outcome,),
        candidate_ids=(candidate,),
    )
    assert payload.to_dict()["candidate_ids"] == [candidate]

    with pytest.raises(ValueError, match="candidate_ids contains a duplicate"):
        Mutation(
            candidate_count=1,
            total=1,
            killed=(outcome,),
            candidate_ids=(candidate, candidate),
        )
    with pytest.raises(ValueError, match="candidate_ids entry must be"):
        Mutation(
            candidate_count=1,
            total=1,
            killed=(outcome,),
            candidate_ids=("short",),
        )


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


def _write_plan_fixture(tmp_path: Path) -> Path:
    """Same fixture as `test_plan_reports_candidates_without_executing`,
    factored out so the `--shard`/`--operators` CLI-level tests below don't
    duplicate its setup."""
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
    return project.root / "assay.toml"


def test_plan_accepts_a_valid_shard(tmp_path):
    """(B012 remediation) `assay plan --shard` has its own dry bounds-check
    block, independent of `assay run`'s -- exercised here through the
    installed CLI at the zero-based index the config/schema/docs all use."""
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out = io.StringIO()
    exit_code = main(["plan", "package", "--shard", "0/2", "--file", str(path)], stdout=out)
    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["shard"] == "0/2"


def test_plan_refuses_an_out_of_range_shard_with_a_clean_exit_not_a_crash(tmp_path):
    """(B012 remediation, D-6) The dry bounds-check call in `_cmd_plan` used
    to sit outside any try/except, so an out-of-range `--shard` raised a
    bare `ValueError` uncaught by `main()`'s `except AssayError` -- a
    traceback, not an exit code."""
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    exit_code = main(["plan", "package", "--shard", "5/2", "--file", str(path)], stdout=out, stderr=err)
    assert exit_code != 0
    assert "shard index 5 is outside" in err.getvalue()
    assert out.getvalue() == ""


def test_plan_refuses_a_malformed_shard_spelling(tmp_path):
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    exit_code = main(["plan", "package", "--shard", "not-a-shard", "--file", str(path)], stdout=out, stderr=err)
    assert exit_code != 0
    assert "--shard must have the form INDEX/COUNT" in err.getvalue()


def test_plan_refuses_an_unknown_operator_with_a_clean_exit_not_a_crash(tmp_path):
    """(B012 remediation, D-6) `_cmd_plan`'s own `--operators` validation
    raises `LaneConfigError` too -- confirming the missing import fix covers
    both `_cmd_run` and `_cmd_plan`, which validate independently."""
    from assay.cli import main

    path = _write_plan_fixture(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    exit_code = main(
        ["plan", "package", "--operators", "bogus:does-not-exist", "--file", str(path)],
        stdout=out,
        stderr=err,
    )
    assert exit_code != 0
    assert "unknown mutation operators" in err.getvalue()


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
