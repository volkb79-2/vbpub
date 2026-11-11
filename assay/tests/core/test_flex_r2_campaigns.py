"""B527/B529/B530/B531 -- flexible native-R2 campaigns, end to end.

The 2026-11-11 ciu campaign lost 355 finished candidates to a worker-count
change: the judge identity folded the whole lane file, so raising
``jobs_max`` re-planned all 627 candidates and the new judge overwrote 21
records of the old one. This module measures, with the REAL CLI on real
repositories:

* B529 -- resource settings of ``assay.toml``/``run-gate.toml`` keep the
  judge identity; argv, env, the per-candidate budget and test files still
  invalidate (B088 holds); resource-sensitive buckets re-execute when the
  resources changed;
* B527 -- a changed judge never overwrites another judge's record: the old
  one is archived byte-identical under ``judges/<judge>/`` and resumes again
  when its judge comes back;
* B530 -- ``--carry-over`` reuses finished records across a judge change,
  with provenance, separately counted, and refuses what does not match;
* B531 -- ``--only-candidates``/``--exclude-candidates`` and
  ``assay plan --list-pending``.
"""

from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath

import pytest
from conftest import GitRepo

from assay import _judge_projection, isolation, mutation, verdict
from assay.cli import main
from assay.resource_limits import ResourceLimitCounters


@pytest.fixture(autouse=True)
def _stable_resource_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    zero = ResourceLimitCounters(
        pids_max=0,
        memory_oom=0,
        memory_max=0,
        memory_oom_kill=0,
        memory_oom_group_kill=0,
    )
    monkeypatch.setattr(mutation, "_read_candidate_resource_counters", lambda: zero)


_LANE = """\
schema_version = 2

# a comment that must not matter
[lanes.unit]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "tests/judge.sh"]
env = { MODE = "one" }
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.unit.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
budget_per_candidate = "30s"
"""

_RUN_GATE = """\
schema_version = 1

[lanes.unit]
kind = "assay"
assay_lane = "unit"
environment = "tester"
budget = "20m"

[lanes.unit.resources]
cpus = "2"
memory = "1g"
"""

#: Every mutant survives.
_BLIND = "exit 0\n"
#: Kills the `a` mutant only.
_STRICT_A = "grep -q '^a = False$' pkg/flags.py && exit 1\nexit 0\n"
#: Kills both mutants.
_STRICT_AB = (
    "grep -q '^a = False$' pkg/flags.py && exit 1\n"
    "grep -q '^b = False$' pkg/flags.py && exit 1\nexit 0\n"
)


def _seed(repo: GitRepo, judge: str, *, sites: int = 1, lane: str = _LANE) -> None:
    names = "ab"[:sites]
    repo.write("assay.toml", lane)
    repo.write("run-gate.toml", _RUN_GATE)
    repo.write("pkg/flags.py", "".join(f"{n} = True\n" for n in names))
    repo.write("tests/judge.sh", judge)
    repo.commit_all("lane")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", "".join(f"{n} = False\n" for n in names))
    repo.commit_all("base flags")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", "".join(f"{n} = True\n" for n in names))
    repo.commit_all("restore flags")


def _run(
    repo: GitRepo,
    state_dir: Path,
    progress: Path,
    *extra: str,
    verdict: Path | None = None,
    resume: bool = True,
) -> tuple[int, list[dict]]:
    argv = [
        "run",
        "unit",
        "--file",
        str(repo.path / "assay.toml"),
        "--state-dir",
        str(state_dir),
        "--progress",
        str(progress),
        *(["--resume"] if resume else []),
        *(["--verdict-json", str(verdict)] if verdict is not None else []),
        *extra,
    ]
    code = main(argv, stdout=io.StringIO(), stderr=io.StringIO())
    events = (
        [json.loads(line) for line in progress.read_text(encoding="utf-8").splitlines()]
        if progress.exists()
        else []
    )
    return code, events


def _executed(events: list[dict]) -> list[str]:
    return [event["outcome_bucket"] for event in events if event.get("event") == "candidate"]


def _event(events: list[dict], name: str) -> dict | None:
    return next((event for event in events if event.get("event") == name), None)


def _records(state_dir: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(state_dir.glob("*.json"))
    ]


def _commit(repo: GitRepo, path: str, text: str, message: str) -> None:
    repo.write(path, text)
    repo.commit_all(message)


# --- B529: resource settings are not judge identity ---------------------------


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        ("assay.toml", 'max_mutants = 10', 'max_mutants = 20'),
        ("assay.toml", 'jobs = 1\n', 'jobs_max = 3\n'),
        ("assay.toml", 'budget = "2m"', 'budget = "9m"'),
        ("assay.toml", '# a comment that must not matter', '# reworded comment'),
        ("run-gate.toml", 'memory = "1g"', 'memory = "3g"'),
        ("run-gate.toml", 'cpus = "2"', 'cpus = "4"'),
        ("run-gate.toml", 'budget = "20m"', 'budget = "8h"'),
    ],
)
def test_a_resource_change_keeps_finished_records(
    git_repo: GitRepo, tmp_path: Path, path: str, old: str, new: str
):
    _seed(git_repo, _STRICT_A)
    state = tmp_path / "state"
    code, first = _run(git_repo, state, tmp_path / "1.jsonl")
    assert _executed(first) == ["killed"]
    judge = _event(first, "candidates")["judge_sha256"]

    text = (git_repo.path / path).read_text(encoding="utf-8")
    assert old in text
    _commit(git_repo, path, text.replace(old, new), f"resource change in {path}")

    code, second = _run(git_repo, state, tmp_path / "2.jsonl")
    assert _event(second, "candidates")["judge_sha256"] == judge
    assert _event(second, "resume")["resumed_total"] == 1
    assert _executed(second) == []


@pytest.mark.parametrize(
    ("path", "old", "new"),
    [
        ("assay.toml", 'MODE = "one"', 'MODE = "two"'),
        ("assay.toml", 'budget_per_candidate = "30s"', 'budget_per_candidate = "40s"'),
        ("assay.toml", '"tests/judge.sh"]', '"tests/judge.sh", "x"]'),
        ("run-gate.toml", 'environment = "tester"', 'environment = "other"'),
        ("tests/judge.sh", "exit 0\n", "exit 0\n# changed\n"),
    ],
)
def test_a_judging_change_still_invalidates(
    git_repo: GitRepo, tmp_path: Path, path: str, old: str, new: str
):
    _seed(git_repo, _STRICT_A)
    state = tmp_path / "state"
    _code, first = _run(git_repo, state, tmp_path / "1.jsonl")
    judge = _event(first, "candidates")["judge_sha256"]

    text = (git_repo.path / path).read_text(encoding="utf-8")
    assert old in text
    _commit(git_repo, path, text.replace(old, new), f"judging change in {path}")

    _code, second = _run(git_repo, state, tmp_path / "2.jsonl")
    assert _event(second, "candidates")["judge_sha256"] != judge
    assert _executed(second) == ["killed"]


def test_resource_sensitive_records_re_execute_when_resources_change(
    git_repo: GitRepo, tmp_path: Path
):
    """A `budget_exceeded` record may be an artifact of the old resources."""
    slow = "grep -q '^a = False$' pkg/flags.py && sleep 20\nexit 0\n"
    lane = _LANE.replace('budget_per_candidate = "30s"', 'budget_per_candidate = "1s"')
    _seed(git_repo, slow, lane=lane)
    state = tmp_path / "state"
    _code, first = _run(git_repo, state, tmp_path / "1.jsonl")
    (slow_record,) = _records(state)
    assert slow_record["outcome_bucket"] in mutation.RESOURCE_SENSITIVE_BUCKETS
    resource = _event(first, "candidates")["resource_sha256"]
    assert _records(state)[0]["resource_sha256"] == resource

    # Unchanged resources: the timeout is reused.
    _code, same = _run(git_repo, state, tmp_path / "2.jsonl")
    assert _event(same, "resume")["resumed_total"] == 1
    assert _executed(same) == []

    text = (git_repo.path / "run-gate.toml").read_text(encoding="utf-8")
    _commit(git_repo, "run-gate.toml", text.replace('memory = "1g"', 'memory = "4g"'), "mem")
    _code, changed = _run(git_repo, state, tmp_path / "3.jsonl")
    resume = _event(changed, "resume")
    assert resume["resource_rejudged_total"] == 1
    assert resume["resumed_total"] == 0
    assert len(_executed(changed)) == 1, "the timeout is re-executed under new resources"
    assert _event(changed, "candidates")["resource_sha256"] != resource


def test_projection_keeps_judging_keys_and_removes_resource_keys():
    raw = (
        _LANE
        + '\n[isolation.limits]\nmax_blob_bytes = 5\n'
        + '\n[lanes.unit.judge.canary]\nbudget_per_attempt = "1m"\nmechanism = "x"\n'
    ).encode()
    document = __import__("tomllib").loads(raw.decode())
    removed = _judge_projection.project_lane_document(document)
    lane = document["lanes"]["unit"]
    assert "budget" not in lane
    assert set(lane["judge"]["mutation"]) == {"operators", "budget_per_candidate"}
    assert lane["judge"]["canary"] == {"mechanism": "x"}
    assert lane["env"] == {"MODE": "one"}
    assert "isolation" not in document or "limits" not in document["isolation"]
    assert removed["lanes"]["unit"]["mutation"] == {"jobs": 1, "max_mutants": 10}
    assert removed["lanes"]["unit"]["lane"] == {"budget": "2m"}
    assert removed["lanes"]["unit"]["canary"] == {"budget_per_attempt": "1m"}
    assert removed["isolation"] == {"limits": {"max_blob_bytes": 5}}


def test_projection_of_run_gate_keeps_environment_and_observer():
    raw = _RUN_GATE.replace(
        'budget = "20m"\n', 'budget = "20m"\nresource_observer = "cgprofile"\n'
    ).encode()
    document = __import__("tomllib").loads(raw.decode())
    removed = _judge_projection.project_run_gate_document(document)
    assert document["lanes"]["unit"] == {
        "kind": "assay",
        "assay_lane": "unit",
        "environment": "tester",
        "resource_observer": "cgprofile",
    }
    assert removed["lanes"]["unit"]["budget"] == "20m"
    assert removed["lanes"]["unit"]["resources"] == {"cpus": "2", "memory": "1g"}


def test_unparseable_config_is_digested_by_its_bytes():
    first = _judge_projection.project_config("assay", b"not = [toml")
    second = _judge_projection.project_config("assay", b"not = [toml ")
    assert ":raw:" in first.digest
    assert first.digest != second.digest
    with pytest.raises(ValueError):
        _judge_projection.project_config("other", b"")


def test_projected_tree_digest_differs_from_legacy_and_tracks_overrides():
    entry = isolation._Entry(
        path=PurePosixPath("assay.toml"),
        mode=isolation._MODE_REGULAR,
        oid="a" * 40,
        size=1,
        target=None,
    )
    other = isolation._Entry(
        path=PurePosixPath("t.py"),
        mode=isolation._MODE_REGULAR,
        oid="b" * 40,
        size=1,
        target=None,
    )
    manifest = isolation._Manifest(entries=(entry, other), directories=(), omitted=())
    changed = isolation._Manifest(
        entries=(entry, isolation._Entry(path=PurePosixPath("t.py"), mode=other.mode,
                                         oid="c" * 40, size=1, target=None)),
        directories=(),
        omitted=(),
    )
    legacy = isolation._manifest_sha256(manifest)
    projected = isolation._manifest_sha256(manifest, content_overrides={"assay.toml": "p1"})
    assert projected != legacy
    assert projected == isolation._manifest_sha256(
        manifest, content_overrides={"assay.toml": "p1"}
    )
    assert projected != isolation._manifest_sha256(
        manifest, content_overrides={"assay.toml": "p2"}
    )
    # a non-overridden leaf still judges
    assert projected != isolation._manifest_sha256(
        changed, content_overrides={"assay.toml": "p1"}
    )
    # a changed oid of the OVERRIDDEN leaf does not matter
    moved = isolation._Manifest(
        entries=(isolation._Entry(path=entry.path, mode=entry.mode, oid="d" * 40,
                                  size=1, target=None), other),
        directories=(),
        omitted=(),
    )
    assert projected == isolation._manifest_sha256(
        moved, content_overrides={"assay.toml": "p1"}
    )
    assert isolation._manifest_sha256(
        manifest, identity_exclude=("t.py",), content_overrides={"assay.toml": "p1"}
    ) != projected


# --- B527: no generation is ever overwritten ----------------------------------


def test_a_changed_judge_archives_the_old_record_and_resumes_it_on_return(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    _code, first = _run(git_repo, state, tmp_path / "1.jsonl")
    judge_a = _event(first, "candidates")["judge_sha256"]
    (record_path,) = state.glob("*.json")
    original = record_path.read_bytes()

    _commit(git_repo, "tests/judge.sh", _STRICT_A, "strengthen")
    _code, second = _run(git_repo, state, tmp_path / "2.jsonl")
    judge_b = _event(second, "candidates")["judge_sha256"]
    assert judge_b != judge_a
    assert _executed(second) == ["killed"]
    archived = state / "judges" / judge_a / record_path.name
    assert archived.read_bytes() == original, "judge A's record must survive byte-identical"
    assert json.loads(record_path.read_text())["judge_sha256"] == judge_b

    _commit(git_repo, "tests/judge.sh", _BLIND, "revert the strengthening")
    _code, third = _run(git_repo, state, tmp_path / "3.jsonl")
    assert _event(third, "candidates")["judge_sha256"] == judge_a
    resume = _event(third, "resume")
    assert resume["resumed_total"] == 1
    assert resume["promoted_total"] == 1
    assert _executed(third) == []
    assert record_path.read_bytes() == original
    assert json.loads(
        (state / "judges" / judge_b / record_path.name).read_text()
    )["outcome_bucket"] == "killed"


# --- B530: carry-over ---------------------------------------------------------


def test_carry_over_imports_finished_records_with_provenance(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    _code, first = _run(git_repo, state, tmp_path / "1.jsonl")
    judge_a = _event(first, "candidates")["judge_sha256"]
    assert _executed(first) == ["survived"]

    _commit(git_repo, "tests/judge.sh", _BLIND + "# operator says: irrelevant\n", "doc")
    code, second = _run(
        git_repo,
        state,
        tmp_path / "2.jsonl",
        "--carry-over",
        str(state),
        "--carry-over-reason",
        "comment-only test change",
    )
    judge_b = _event(second, "candidates")["judge_sha256"]
    assert judge_b != judge_a
    carry = _event(second, "carry_over")
    assert carry["imported_total"] == 1
    assert carry["copied_total"] == 0
    assert carry["from_judge_counts"] == {judge_a: 1}
    assert carry["reason"] == "comment-only test change"
    assert _event(second, "candidates")["imported_total"] == 1
    assert _executed(second) == [], "an imported record is not re-executed"
    (record,) = _records(state)
    assert record["judge_sha256"] == judge_a, "the executing judge is never rewritten"
    assert record["carry_over"]["from_judge_sha256"] == judge_a
    assert record["carry_over"]["into_judge_sha256"] == judge_b
    assert record["carry_over"]["reason"] == "comment-only test change"
    assert record["carry_over"]["source"] == str(state)
    assert mutation.record_effective_judge(record) == judge_b

    # The next attempt resumes it without asking again.
    _code, third = _run(git_repo, state, tmp_path / "3.jsonl")
    assert _event(third, "resume")["resumed_total"] == 1
    assert _executed(third) == []


def test_carry_over_copies_same_judge_records_from_another_store(
    git_repo: GitRepo, tmp_path: Path
):
    """The shard-merge shape: a sibling store of the same judge."""
    _seed(git_repo, _STRICT_A)
    shard = tmp_path / "shard-0"
    _run(git_repo, shard, tmp_path / "1.jsonl")
    main_store = tmp_path / "main"
    _code, merged = _run(
        git_repo,
        main_store,
        tmp_path / "2.jsonl",
        "--carry-over",
        str(shard),
        "--carry-over-reason",
        "shard merge",
    )
    carry = _event(merged, "carry_over")
    assert (carry["imported_total"], carry["copied_total"]) == (0, 1)
    assert _executed(merged) == []
    (record,) = _records(main_store)
    assert "carry_over" not in record


def test_carry_over_refuses_a_record_filed_under_the_wrong_identity(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    _run(git_repo, state, tmp_path / "1.jsonl")
    (record_path,) = state.glob("*.json")
    record = json.loads(record_path.read_text())
    record["operator"] = "python:compare-swap"  # contradicts the candidate id
    source = tmp_path / "source"
    source.mkdir()
    (source / record_path.name).write_text(json.dumps(record))

    _commit(git_repo, "tests/judge.sh", _STRICT_A, "strengthen")
    fresh = tmp_path / "fresh"
    _code, second = _run(
        git_repo, fresh, tmp_path / "2.jsonl",
        "--carry-over", str(source), "--carry-over-reason", "test",
    )
    carry = _event(second, "carry_over")
    assert carry["imported_total"] == 0
    assert carry["refused_total"] == 1
    assert "stale operator" in carry["refused_sample"][0]["reason"]
    assert _executed(second) == ["killed"]


def test_carry_over_does_not_import_unresolved_buckets(git_repo: GitRepo, tmp_path: Path):
    slow = "grep -q '^a = False$' pkg/flags.py && sleep 20\nexit 0\n"
    lane = _LANE.replace('budget_per_candidate = "30s"', 'budget_per_candidate = "1s"')
    _seed(git_repo, slow, lane=lane)
    state = tmp_path / "state"
    _run(git_repo, state, tmp_path / "1.jsonl")
    # The fixture's per-candidate timeout ends in `budget_exceeded` (B251
    # keeps budget cleanup in its own bucket; before it the killed shell was
    # recorded `crashed`); the other unresolved buckets are pinned per bucket
    # below.
    assert _records(state)[0]["outcome_bucket"] == "budget_exceeded"
    _commit(git_repo, "tests/judge.sh", _STRICT_A, "strengthen")
    _code, second = _run(
        git_repo, state, tmp_path / "2.jsonl",
        "--carry-over", str(state), "--carry-over-reason", "test",
    )
    carry = _event(second, "carry_over")
    assert carry["imported_total"] == 0
    assert carry["refused_sample"][0]["reason"] == (
        "bucket budget_exceeded is not carried over")
    assert _executed(second) == ["killed"]


@pytest.mark.parametrize("bucket", sorted(verdict.MUTATION_BUCKETS))
def test_only_killed_and_survived_are_importable(bucket: str):
    """Each unresolved bucket separately (review M7a): adding only one of
    `crashed`/`hung`/`budget_exceeded`/`equivalent` must fail here."""
    expected = bucket in ("killed", "survived")
    assert mutation._carry_over_importable(bucket, frozenset()) is expected
    assert mutation._carry_over_importable(bucket, frozenset({bucket})) is False


@pytest.mark.parametrize(
    "extra",
    [
        ("--carry-over", "{state}"),
        ("--carry-over-reason", "why"),
    ],
)
def test_carry_over_needs_both_flags(git_repo: GitRepo, tmp_path: Path, extra):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    state.mkdir()
    code, _events = _run(
        git_repo, state, tmp_path / "1.jsonl", *(part.format(state=state) for part in extra)
    )
    assert code == 2


def test_carry_over_needs_resume(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    state.mkdir()
    code, _events = _run(
        git_repo, state, tmp_path / "1.jsonl",
        "--carry-over", str(state), "--carry-over-reason", "why",
        resume=False,
    )
    assert code == 2


# --- B531: targeted candidate sets --------------------------------------------


def _ids(repo: GitRepo, tmp_path: Path, state: Path, *extra: str) -> list[str]:
    out = io.StringIO()
    code = main(
        [
            "plan", "unit", "--file", str(repo.path / "assay.toml"),
            "--list-pending", "--state-dir", str(state), *extra,
        ],
        stdout=out,
        stderr=io.StringIO(),
    )
    assert code == 0
    return out.getvalue().split()


def test_exclude_candidates_is_partial_and_the_rest_completes_on_resume(
    git_repo: GitRepo, tmp_path: Path
):
    _seed(git_repo, _STRICT_AB, sites=2)
    state = tmp_path / "state"
    state.mkdir()
    pending = _ids(git_repo, tmp_path, state)
    assert len(pending) == 2
    exclude = tmp_path / "exclude.txt"
    exclude.write_text(f"# left for host B\n{pending[1]}  some/path.py:1\n")

    verdict_path = tmp_path / "v1.json"
    code, first = _run(
        git_repo, state, tmp_path / "1.jsonl",
        "--exclude-candidates", str(exclude), verdict=verdict_path,
    )
    selection = _event(first, "selection")
    assert (selection["executing_total"], selection["unselected_total"]) == (1, 1)
    assert _executed(first) == ["killed"]
    assert _event(first, "end")["unselected_total"] == 1
    verdict = json.loads(verdict_path.read_text())
    (r2,) = [claim for claim in verdict["claims"] if claim["rigor"] == "R2"]
    assert r2["status"] != "PASS"
    assert r2["detail"].startswith("PARTIAL: 1 of 2 candidates")
    assert code != 0
    assert main(["verify", str(verdict_path)], stdout=io.StringIO(), stderr=io.StringIO()) == 0

    assert _ids(git_repo, tmp_path, state) == [pending[1]]

    verdict2 = tmp_path / "v2.json"
    code, second = _run(git_repo, state, tmp_path / "2.jsonl", verdict=verdict2)
    assert _executed(second) == ["killed"]
    assert _event(second, "resume")["resumed_total"] == 1
    assert code == 0
    (r2,) = [c for c in json.loads(verdict2.read_text())["claims"] if c["rigor"] == "R2"]
    assert r2["status"] == "PASS"
    assert _ids(git_repo, tmp_path, state) == []


def test_only_candidates_combines_with_shard(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_AB, sites=2)
    state = tmp_path / "state"
    state.mkdir()
    everything = _ids(git_repo, tmp_path, state)
    only = tmp_path / "only.txt"
    only.write_text("\n".join(everything) + "\n")
    executed = 0
    for index in range(2):
        shard_state = tmp_path / f"shard-{index}"
        _code, events = _run(
            git_repo, shard_state, tmp_path / f"s{index}.jsonl",
            "--shard", f"{index}/2", "--only-candidates", str(only),
        )
        executed += len(_executed(events))
        assert _event(events, "selection")["unselected_total"] == 0
    assert executed == 2


def test_unknown_selection_ids_are_refused(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_A)
    state = tmp_path / "state"
    only = tmp_path / "only.txt"
    only.write_text("f" * 64 + "\n")
    code, _events = _run(git_repo, state, tmp_path / "1.jsonl", "--only-candidates", str(only))
    assert code == 2


def test_a_malformed_selection_file_is_refused_before_work(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_A)
    state = tmp_path / "state"
    bad = tmp_path / "bad.txt"
    bad.write_text("not-an-id\n")
    code, events = _run(git_repo, state, tmp_path / "1.jsonl", "--exclude-candidates", str(bad))
    assert code == 2
    assert _event(events, "candidates") is None


def test_list_pending_with_judge_counts_only_that_judge(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _STRICT_A)
    state = tmp_path / "state"
    _code, first = _run(git_repo, state, tmp_path / "1.jsonl")
    judge = _event(first, "candidates")["judge_sha256"]
    assert _ids(git_repo, tmp_path, state) == []
    assert _ids(git_repo, tmp_path, state, "--judge", judge) == []
    assert len(_ids(git_repo, tmp_path, state, "--judge", "0" * 64)) == 1


def test_list_pending_requires_a_state_dir(git_repo: GitRepo):
    _seed(git_repo, _STRICT_A)
    code = main(
        ["plan", "unit", "--file", str(git_repo.path / "assay.toml"), "--list-pending"],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert code == 2


def test_merge_state_copies_this_judges_records_and_ignores_others(
    git_repo: GitRepo, tmp_path: Path
):
    """`--merge-state` is the shard merge: same judge only, no reason needed."""
    _seed(git_repo, _STRICT_AB, sites=2)
    shards = [tmp_path / f"shard-{index}" for index in range(2)]
    for index, shard in enumerate(shards):
        _run(git_repo, shard, tmp_path / f"s{index}.jsonl", "--shard", f"{index}/2")
    merged_store = tmp_path / "main"
    merge_args = [arg for shard in shards for arg in ("--merge-state", str(shard))]
    verdict = tmp_path / "merged.json"
    code, merged = _run(git_repo, merged_store, tmp_path / "m.jsonl", *merge_args,
                        verdict=verdict)
    carry = _event(merged, "carry_over")
    assert (carry["copied_total"], carry["imported_total"]) == (2, 0)
    assert carry["reason"] is None
    assert _executed(merged) == []
    assert code == 0
    assert len(_records(merged_store)) == 2

    # A different judge: the shard records are ignored, not imported.
    _commit(git_repo, "tests/judge.sh", _STRICT_AB + "# new judge\n", "judge change")
    other = tmp_path / "other"
    _code, again = _run(git_repo, other, tmp_path / "o.jsonl", *merge_args)
    carry = _event(again, "carry_over")
    assert (carry["copied_total"], carry["imported_total"]) == (0, 0)
    assert carry["refused_total"] == 2
    assert all(
        "another judge in merge source" in item["reason"]
        for item in carry["refused_sample"]
    )
    assert sorted(_executed(again)) == ["killed", "killed"]


def test_merge_state_needs_resume(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    state.mkdir()
    code, _events = _run(
        git_repo, state, tmp_path / "1.jsonl", "--merge-state", str(state), resume=False
    )
    assert code == 2


def test_campaign_analysis_counts_imported_records_separately(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B530: the closeout names what this judge did NOT execute."""
    monkeypatch.chdir(git_repo.path)
    _seed(git_repo, _BLIND)
    state = tmp_path / "state"
    _run(git_repo, state, tmp_path / "1.jsonl")
    _commit(git_repo, "tests/judge.sh", _BLIND + "# irrelevant\n", "doc")
    progress = tmp_path / "2.jsonl"
    verdict = tmp_path / "v.json"
    _run(git_repo, state, progress, "--carry-over", str(state),
         "--carry-over-reason", "doc-only", verdict=verdict)
    head = git_repo.head()
    common = [
        "analyze", "campaign", "unit", "--file", str(git_repo.path / "assay.toml"),
        "--expected-commit", head, "--progress", str(progress),
        "--state-dir", str(state), "--verdict", str(verdict),
    ]
    out = io.StringIO()
    main([*common, "--format", "json"], stdout=out, stderr=io.StringIO())
    document = json.loads(out.getvalue())
    assert "state" in document, json.dumps(document)[:3000]
    assert document["state"]["imported"]["count"] == 1
    assert document["state"]["imported"]["reasons"] == ["doc-only"]
    assert document["state"]["stale_judge"]["count"] == 0
    assert document["state"]["counted"] == 1
    text = io.StringIO()
    main([*common, "--format", "text"], stdout=text, stderr=io.StringIO())
    assert "carry-over: 1 counted result(s) were IMPORTED" in text.getvalue()
