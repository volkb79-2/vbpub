from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome, ReasonCode
from assay.mutation import (
    MutationStateError,
    MutationTarget,
    merge_mutation_shards,
    run_mutation,
    select_mutation_shard,
)
from assay.runner import execute_command


_TEXT = (
    "def flags():\n"
    "    a = True\n"
    "    b = True\n"
    "    return a, b\n"
)
_TARGETS = (MutationTarget(path="pkg/flags.py", text=_TEXT, lines=frozenset({2, 3})),)


def _repo(tmp_path: Path) -> GitRepo:
    repo = GitRepo(path=tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    repo.git("config", "user.email", "assay-tests@example.com")
    repo.git("config", "user.name", "assay tests")
    repo.write("pkg/flags.py", _TEXT)
    repo.commit_all("add flags")
    return repo


def _run(repo, tmp_path, *, state_root):
    scratch = tmp_path / f"scratch-{len(list(tmp_path.iterdir()))}"
    scratch.mkdir(exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(list(argv), returncode=0),
    )
    assert baseline.outcome is Outcome.PASS

    def decide(argv, *, env, cwd, timeout):
        text = (Path(cwd) / "pkg/flags.py").read_text(encoding="utf-8")
        returncode = 1 if "a = False" in text else 0
        return subprocess.CompletedProcess(list(argv), returncode=returncode)

    with prepared_snapshot(repo, scratch_root=scratch) as prepared:
        return run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(make_lane(argv=("pytest", "-q"))),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=decide,
            clock=lambda: datetime.now(timezone.utc),
            state_project_root=state_root,
            resume=True,
        )


def test_completed_candidates_are_resumed_without_reexecution(tmp_path):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"
    first = _run(repo, tmp_path, state_root=state_root)
    assert not isinstance(first, str)
    assert first.total == 2

    executed: list[str] = []

    def counting_runner(argv, *, env, cwd, timeout):
        if Path(cwd) != repo.path:
            executed.append((Path(cwd) / "pkg/flags.py").read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(list(argv), returncode=0)

    baseline = execute_command(
        make_lane(argv=("pytest", "-q")),
        cwd=repo.path,
        process_runner=counting_runner,
    )
    resume_scratch = tmp_path / "resume-scratch"
    resume_scratch.mkdir()
    with prepared_snapshot(repo, scratch_root=resume_scratch) as prepared:
        resumed = run_mutation(
            baseline=baseline,
            prepared=prepared,
            plan=make_plan(make_lane(argv=("pytest", "-q"))),
            deadline=make_deadline(),
            targets=_TARGETS,
            adapter=PythonAdapter(),
            jobs=1,
            max_mutants=10,
            operators=("python:bool-const-flip",),
            process_runner=counting_runner,
            clock=lambda: datetime.now(timezone.utc),
            state_project_root=state_root,
            resume=True,
        )

    assert not isinstance(resumed, str)
    assert executed == []
    assert resumed.total == 2
    assert len(resumed.killed) == 1


def test_a_changed_source_invalidates_the_old_record(tmp_path):
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"
    _run(repo, tmp_path, state_root=state_root)
    record = next((state_root / ".assay/mutation-state").glob("*.json"))
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")

    result = _run(repo, tmp_path, state_root=state_root)
    assert not isinstance(result, str)
    assert result.total == 2


def test_shards_partition_candidate_ids_exactly():
    identities = [f"{index:064x}" for index in range(20)]
    selected = [
        select_mutation_shard(identities, index=index, count=4)
        for index in range(4)
    ]
    assert sorted(position for positions in selected for position in positions) == list(range(20))
    assert all(len(positions) > 0 for positions in selected)


def test_manifest_merge_refuses_duplicate_and_missing_shards():
    documents = [
        {
            "schema_version": 1,
            "lane": "lane",
            "commit": "a" * 40,
            "shard_index": index,
            "shard_count": 2,
            "candidate_ids": ["a" * 64] if index == 0 else [],
        }
        for index in range(2)
    ]
    assert merge_mutation_shards(documents) == ("a" * 64,)
    duplicate = [documents[0], dict(documents[0], shard_index=1, candidate_ids=["a" * 64])]
    with pytest.raises(MutationStateError):
        merge_mutation_shards(duplicate)
    with pytest.raises(MutationStateError):
        merge_mutation_shards([documents[0]])
