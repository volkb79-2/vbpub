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


def test_a_record_whose_source_hash_contradicts_its_own_filename_raises(tmp_path):
    """(B021) The record's OWN filename is `candidate_id(job)`, which already
    hashes in `source_sha256` among other fields (mutation.py's own
    derivation) -- so a record found at that path whose `source_sha256`
    disagrees with the path it's filed under is not "the source changed"
    (that would produce a DIFFERENT candidate id and this record simply
    would not be found at all); it is the record contradicting its own
    identity, evidence of corruption or hand-editing, which
    `_load_validated_state_record` now raises on rather than silently
    treating as absent -- the corrected disposition, the reverse of what
    this test asserted before B021."""
    repo = _repo(tmp_path)
    state_root = tmp_path / "state"
    _run(repo, tmp_path, state_root=state_root)
    record = next((state_root / ".assay/mutation-state").glob("*.json"))
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    record.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MutationStateError, match="stale source_sha256"):
        _run(repo, tmp_path, state_root=state_root)


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


def test_manifest_merge_refuses_a_candidate_assigned_to_the_wrong_shard():
    """(B012/B023 remediation) A document cannot claim a shard index its own
    listed candidates do not actually hash to -- `"a" * 64` deterministically
    assigns to shard 0/2 (asserted above), so filing it under shard 1/2 must
    be refused rather than silently merged, even though the two documents
    together still satisfy every OTHER check (exact index coverage, one
    schema/lane/commit, no repeated id)."""
    documents = [
        {
            "schema_version": 1,
            "lane": "lane",
            "commit": "a" * 40,
            "shard_index": 0,
            "shard_count": 2,
            "candidate_ids": [],
        },
        {
            "schema_version": 1,
            "lane": "lane",
            "commit": "a" * 40,
            "shard_index": 1,
            "shard_count": 2,
            "candidate_ids": ["a" * 64],
        },
    ]
    with pytest.raises(MutationStateError, match="hashes to shard 0/2"):
        merge_mutation_shards(documents)


def test_manifest_merge_refuses_a_duplicate_shard_index():
    """(B012/B023 remediation, round-2 finding F) `covered_pairs` is a set,
    so two documents both claiming shard 0/2 (with disjoint candidate ids,
    so the non-disjoint check does not fire first) used to merge silently --
    "exact shard-index coverage" must mean exactly one document per index,
    not merely that every index was seen at least once."""
    documents = [
        {
            "schema_version": 1,
            "lane": "lane",
            "commit": "a" * 40,
            "shard_index": 0,
            "shard_count": 2,
            "candidate_ids": [],
        },
        {
            "schema_version": 1,
            "lane": "lane",
            "commit": "a" * 40,
            "shard_index": 0,
            "shard_count": 2,
            "candidate_ids": ["a" * 64],
        },
        {
            "schema_version": 1,
            "lane": "lane",
            "commit": "a" * 40,
            "shard_index": 1,
            "shard_count": 2,
            "candidate_ids": [],
        },
    ]
    with pytest.raises(MutationStateError, match="0/2 is present in more than one document"):
        merge_mutation_shards(documents)


def test_manifest_merge_refuses_all_empty_shards():
    """(B012/B023 remediation) Every required (shard_index, shard_count) pair
    being present says only that a document was filed for each slot, never
    that any of them did work -- three empty shards must not merge into
    'complete coverage of zero candidates' (A-278's lesson)."""
    documents = [
        {
            "schema_version": 1,
            "lane": "lane",
            "commit": "a" * 40,
            "shard_index": index,
            "shard_count": 3,
            "candidate_ids": [],
        }
        for index in range(3)
    ]
    with pytest.raises(MutationStateError, match="zero candidates"):
        merge_mutation_shards(documents)
