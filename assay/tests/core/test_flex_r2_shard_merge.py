"""RG-265 review B1 -- the strict whole-lane merge of shard stores.

``--merge-state`` is a permissive union: a missing record is simply executed.
That is right for carry-over, and wrong for a shard merge, where a missing,
mistyped or half-copied shard store must not turn into a green whole-lane
verdict. ``--merge-shard-store`` therefore requires exactly one store per
shard ``0..N-1``, each written by ``--shard K/N`` under the current judge and
plan, complete, and holding nothing else -- or refuses before anything runs.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from conftest import GitRepo
from test_flex_r2_campaigns import (
    _BLIND,
    _STRICT_AB,
    _seed,
    _stable_resource_counters,
)

from assay import mutation
from assay.cli import main

_AUTOUSE = (_stable_resource_counters,)


def _assay(repo: GitRepo, tmp_path: Path, state: Path, *extra: str, name: str):
    progress = tmp_path / f"{name}.jsonl"
    verdict = tmp_path / f"{name}-verdict.json"
    err = io.StringIO()
    code = main(
        [
            "run", "unit", "--file", str(repo.path / "assay.toml"),
            "--state-dir", str(state), "--progress", str(progress),
            "--verdict-json", str(verdict), "--resume", *extra,
        ],
        stdout=io.StringIO(),
        stderr=err,
    )
    events = (
        [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]
        if progress.exists()
        else []
    )
    return code, events, err.getvalue(), verdict


def _executed(events: list[dict]) -> int:
    return sum(1 for event in events if event.get("event") == "candidate")


def _shards(repo: GitRepo, tmp_path: Path, count: int = 2) -> list[Path]:
    stores = []
    for index in range(count):
        store = tmp_path / f"s{index}"
        _assay(repo, tmp_path, store, "--shard", f"{index}/{count}", name=f"shard{index}")
        stores.append(store)
    return stores


def _merge(repo: GitRepo, tmp_path: Path, stores: list[Path], name: str = "merge"):
    args: list[str] = []
    for store in stores:
        args += ["--merge-shard-store", str(store)]
    return _assay(repo, tmp_path, tmp_path / f"{name}-main", *args, name=name)


def _refused(code: int, events: list[dict], err: str, fragment: str) -> None:
    assert code != 0
    assert fragment in err, err
    assert _executed(events) == 0


def test_a_complete_set_of_shards_merges_without_executing_anything(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    for store in stores:
        manifest = json.loads((store / mutation.SHARD_MANIFEST_NAME).read_text())
        assert manifest["schema"] == "assay-shard-store/1"
        assert manifest["shard_count"] == 2
    code, events, err, verdict = _merge(git_repo, tmp_path, stores)
    assert code == 0, err
    assert _executed(events) == 0
    merge = next(event for event in events if event.get("event") == "shard_merge")
    assert merge["shard_count"] == 2
    assert sum(merge["records_per_store"].values()) == 2
    assert json.loads(verdict.read_text())["outcome"] == "PASS"


def test_an_absent_store_is_refused_by_name(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    [first, _second] = _shards(git_repo, tmp_path)
    code, events, err, _ = _merge(git_repo, tmp_path, [first, tmp_path / "s9"])
    _refused(code, events, err, "s9: is not an existing directory")


def test_an_empty_store_without_a_manifest_is_refused(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    [first, _second] = _shards(git_repo, tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    code, events, err, _ = _merge(git_repo, tmp_path, [first, empty])
    _refused(code, events, err, "has no shard.manifest")


def test_a_store_named_twice_is_refused(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    [first, _second] = _shards(git_repo, tmp_path)
    code, events, err, _ = _merge(git_repo, tmp_path, [first, first])
    _refused(code, events, err, "is named twice")


def test_two_copies_of_one_shard_are_refused(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    [first, _second] = _shards(git_repo, tmp_path)
    copy = tmp_path / "copy-of-0"
    shutil.copytree(first, copy)
    code, events, err, _ = _merge(git_repo, tmp_path, [first, copy])
    _refused(code, events, err, "shard 0/2 is also")


def test_too_few_stores_for_the_shard_count_are_refused(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    [first, _second] = _shards(git_repo, tmp_path)
    code, events, err, _ = _merge(git_repo, tmp_path, [first])
    _refused(code, events, err, "written as one of 2 shards, but 1 stores are merged")


def test_a_store_of_another_judge_is_refused(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    git_repo.write("tests/judge.sh", _STRICT_AB + "# changed\n")
    git_repo.commit_all("judge change")
    code, events, err, _ = _merge(git_repo, tmp_path, stores)
    _refused(code, events, err, "(foreign judge)")


def test_a_store_planned_over_another_candidate_set_is_refused(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    path = stores[0] / mutation.SHARD_MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest["plan_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    code, events, err, _ = _merge(git_repo, tmp_path, stores)
    _refused(code, events, err, "planned over a different candidate set")


def test_the_manifest_candidate_total_is_pinned(git_repo: GitRepo, tmp_path: Path):
    """Review F3-R2: the manifest's total must agree with its shard's plan."""
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    path = stores[0] / mutation.SHARD_MANIFEST_NAME
    manifest = json.loads(path.read_text())
    declared = manifest["candidate_total"]
    manifest["candidate_total"] = declared + 1
    path.write_text(json.dumps(manifest))
    code, events, err, _ = _merge(git_repo, tmp_path, stores)
    _refused(code, events, err,
             f"manifest declares {declared + 1} candidate(s), its plan has {declared}")


def test_an_incomplete_shard_is_refused_not_executed(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    [record] = [path for store in stores for path in sorted(store.glob("*.json"))][:1]
    record.unlink()
    code, events, err, _ = _merge(git_repo, tmp_path, stores)
    _refused(code, events, err, "is incomplete, 1 of")


def test_a_record_of_another_shard_is_refused(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    # Plant a record of one shard in the other shard's store.
    donor, receiver = (
        (stores[1], stores[0]) if list(stores[1].glob("*.json")) else (stores[0], stores[1])
    )
    receiver_index = stores.index(receiver)
    [record] = sorted(donor.glob("*.json"))[:1]
    shutil.copy(record, receiver / record.name)
    code, events, err, _ = _merge(git_repo, tmp_path, stores)
    _refused(code, events, err, f"outside shard {receiver_index}/2")


def test_a_record_that_does_not_resume_is_refused(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _BLIND)
    [only] = _shards(git_repo, tmp_path, count=1)
    [record] = sorted(only.glob("*.json"))
    payload = json.loads(record.read_text())
    payload["carry_over"] = {"schema_version": 1}
    record.write_text(json.dumps(payload))
    code, events, err, _ = _merge(git_repo, tmp_path, [only])
    _refused(code, events, err, "does not resume under this judge")


@pytest.mark.parametrize(
    "extra, fragment",
    [
        (("--shard", "0/2"), "cannot be combined"),
        (("--exclude-candidates", "{list}"), "cannot be combined"),
    ],
)
def test_the_strict_merge_is_the_whole_lane_run(
    git_repo: GitRepo, tmp_path: Path, extra, fragment
):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    listing = tmp_path / "list.txt"
    listing.write_text("", encoding="utf-8")
    args: list[str] = []
    for store in stores:
        args += ["--merge-shard-store", str(store)]
    code, events, err, _ = _assay(
        git_repo, tmp_path, tmp_path / "m",
        *args, *(part.format(list=listing) for part in extra), name="combo",
    )
    _refused(code, events, err, fragment)


def test_the_strict_merge_needs_resume(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    err = io.StringIO()
    code = main(
        [
            "run", "unit", "--file", str(git_repo.path / "assay.toml"),
            "--state-dir", str(tmp_path / "m"), "--merge-shard-store", str(stores[0]),
            "--merge-shard-store", str(stores[1]),
        ],
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code != 0 and "require --resume" in err.getvalue()


# --------------------------------------------------------------------------
# Malformed inputs the strict merge must name, not trust
# --------------------------------------------------------------------------


def _validate(stores, jobs=()):
    return mutation._validate_shard_stores(
        stores,
        jobs=list(jobs),
        judge="a" * 64,
        campaign_deadline_sha256=None,
        load_record=lambda _job, _raw: None,
    )


def _manifest(store: Path, **overrides) -> None:
    document = {
        "schema": "assay-shard-store/1",
        "shard_index": 0,
        "shard_count": 1,
        "judge_sha256": "a" * 64,
        "plan_sha256": mutation.plan_sha256([]),
        "candidate_total": 0,
    }
    document.update(overrides)
    store.mkdir(parents=True, exist_ok=True)
    (store / mutation.SHARD_MANIFEST_NAME).write_text(json.dumps(document))


def test_the_number_of_stores_is_bounded():
    with pytest.raises(mutation.ShardStoreMergeError, match=r"needs 1\.\.\d+ stores, got 0"):
        _validate([])


@pytest.mark.parametrize(
    "write, message",
    [
        (lambda store: (store / "shard.manifest").write_bytes(b"\xff"), "is not JSON"),
        (lambda store: (store / "shard.manifest").write_text("[]"), "is not a assay-shard-store/1"),
        (lambda store: _manifest(store, schema="other/1"), "is not a assay-shard-store/1"),
        (lambda store: _manifest(store, shard_index=True), "is not a assay-shard-store/1"),
        (lambda store: _manifest(store, judge_sha256=7), "is not a assay-shard-store/1"),
    ],
)
def test_a_malformed_manifest_is_refused(tmp_path: Path, write, message):
    store = tmp_path / "s"
    store.mkdir()
    write(store)
    with pytest.raises(mutation.ShardStoreMergeError, match=message):
        _validate([store])


def test_a_symlinked_manifest_is_unreadable(tmp_path: Path):
    _manifest(tmp_path / "real")
    store = tmp_path / "s"
    store.mkdir()
    (store / "shard.manifest").symlink_to(tmp_path / "real" / "shard.manifest")
    with pytest.raises(mutation.ShardStoreMergeError, match="is unreadable"):
        _validate([store])


def test_a_shard_index_outside_the_count_is_refused(tmp_path: Path):
    _manifest(tmp_path / "s", shard_index=3)
    with pytest.raises(mutation.ShardStoreMergeError, match="shard index 3 is outside 0..0"):
        _validate([tmp_path / "s"])


def test_more_than_twenty_problems_are_summarised(tmp_path: Path):
    stores = []
    for index in range(22):
        store = tmp_path / f"s{index}"
        _manifest(store, shard_count=22, shard_index=index, judge_sha256="b" * 64)
        stores.append(store)
    with pytest.raises(mutation.ShardStoreMergeError, match="... 2 more"):
        _validate(stores)


def test_flex_options_refuse_a_strict_merge_with_a_selection():
    with pytest.raises(ValueError, match="cannot be combined with a candidate selection"):
        mutation.FlexOptions(
            shard_stores=(Path("/s"),), only_candidates=frozenset({"a" * 64})
        )


def test_run_mutation_refuses_a_strict_merge_inside_a_shard(tmp_path: Path):
    with pytest.raises(ValueError, match="shard_stores is the whole-lane merge"):
        mutation.run_mutation(
            baseline=None, prepared=None, plan=None, deadline=None, targets=(),
            adapter=None, jobs=1, max_mutants=1, operators=(), process_runner=None,
            clock=None, resume=True, state_root=tmp_path,
            flex=mutation.FlexOptions(shard_stores=(tmp_path,)),
            shard_index=0, shard_count=2,
        )


def test_unreadable_and_corrupt_records_are_named(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    records = [path for store in stores for path in sorted(store.glob("*.json"))]
    first, second = records
    target = tmp_path / "elsewhere.json"
    target.write_bytes(first.read_bytes())
    first.unlink()
    first.symlink_to(target)
    second.write_text("{not json", encoding="utf-8")
    code, events, err, _ = _merge(git_repo, tmp_path, stores)
    _refused(code, events, err, "is unreadable")
    assert "is invalid" in err


def test_the_strict_merge_runs_without_a_progress_stream(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    stores = _shards(git_repo, tmp_path)
    args: list[str] = []
    for store in stores:
        args += ["--merge-shard-store", str(store)]
    code = main(
        ["run", "unit", "--file", str(git_repo.path / "assay.toml"),
         "--state-dir", str(tmp_path / "m"), "--resume", *args],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == 0
    assert len(list((tmp_path / "m").glob("*.json"))) == 2
